"""Offline estimates from recorded usage and an explicit dated price catalog.

No network, currency conversion, invented missing usage, or monetary budget
enforcement. Provider invoices remain authoritative.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from .model import _json_loads


RATE_FIELDS = {"input", "output", "cached_input", "write_30m", "write_12h"}


def validate_catalog(catalog):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Price catalog requires schema_version 1")
    if any(not isinstance(catalog.get(k), str) or not catalog[k].strip()
           for k in ("currency", "as_of", "source_url", "base_url", "service_tier")):
        raise ValueError("Declare currency, as_of date, source URL, endpoint and service tier")
    date.fromisoformat(catalog["as_of"])
    if not catalog["source_url"].startswith("https://") or not catalog["base_url"].startswith("https://"):
        raise ValueError("Price provenance and provider endpoint must be HTTPS")
    if not isinstance(catalog.get("models"), dict) or not catalog["models"]:
        raise ValueError("Price catalog needs explicit model identities")
    for model, row in catalog["models"].items():
        if not isinstance(model, str) or not model or not isinstance(row, dict):
            raise ValueError("Invalid model price row")
        if type(row.get("long_context_above")) is not int or row["long_context_above"] < 1:
            raise ValueError("Each model needs a positive long-context input threshold")
        for band in ("short", "long"):
            if not isinstance(row.get(band), dict) or set(row[band]) != RATE_FIELDS:
                raise ValueError("Each price band needs all five token rates per million")
            for rate in row[band].values():
                try:
                    value = Decimal(str(rate))
                except (ValueError, InvalidOperation):
                    raise ValueError("Rates must be finite nonnegative numbers") from None
                if not value.is_finite() or value < 0:
                    raise ValueError("Rates must be finite nonnegative numbers")
    return catalog


def _count(usage, field):
    value = usage.get(field)
    if type(value) is not int or value < 0:
        raise ValueError("missing_or_invalid_" + field)
    return value


def estimate_call(event, catalog):
    """Return a token-only estimate, or explain precisely why it is unknown."""
    meta = event.get("response_metadata") or {}
    model = meta.get("model") or event.get("model")
    result = {"model": model, "role": event.get("role"), "call_ok": event.get("ok"),
              "estimated_cost": None, "currency": catalog["currency"]}
    if event.get("mode") not in (None, "configured_model"):
        return {**result, "status": "excluded_non_live_model"}
    try:
        if event.get("base_url") != catalog["base_url"]:
            raise ValueError("unmatched_or_unknown_endpoint")
        tier = meta.get("service_tier") or event.get("service_tier")
        if tier != catalog["service_tier"]:
            raise ValueError("unmatched_or_unknown_service_tier")
        if model not in catalog["models"]:
            raise ValueError("no_exact_model_price")
        usage = event.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("usage_not_reported")
        prompt, output = _count(usage, "prompt_tokens"), _count(usage, "completion_tokens")
        cached, writes = _count(usage, "cached_input_tokens"), _count(usage, "cache_write_tokens")
        # Zero total writes implies zero writes of either lifetime. Positive
        # writes need the actual split; never assume the cheaper lifetime.
        write30 = _count(usage, "cache_write_30m_tokens") if writes else usage.get("cache_write_30m_tokens", 0)
        write12 = _count(usage, "cache_write_12h_tokens") if writes else usage.get("cache_write_12h_tokens", 0)
        if (type(write30) is not int or type(write12) is not int or min(write30, write12) < 0
                or write30 + write12 != writes or cached + writes > prompt):
            raise ValueError("inconsistent_input_usage")
        if "total_tokens" in usage and _count(usage, "total_tokens") != prompt + output:
            raise ValueError("inconsistent_total_usage")
        if "reasoning_tokens" in usage and _count(usage, "reasoning_tokens") > output:
            raise ValueError("inconsistent_reasoning_usage")
        row = catalog["models"][model]
        band = "long" if prompt > row["long_context_above"] else "short"
        rates = {k: Decimal(str(v)) for k, v in row[band].items()}
        counts = {"input": prompt - cached - writes, "output": output, "cached_input": cached,
                  "write_30m": write30, "write_12h": write12}
        value = sum(Decimal(n) * rates[k] for k, n in counts.items()) / Decimal(1_000_000)
        return {**result, "status": "estimated", "estimated_cost": str(value),
                "price_band": band, "billed_token_categories": counts}
    except (ValueError, TypeError) as exc:
        return {**result, "status": "unknown", "reason": str(exc)}


def cost_ledger(events, catalog, *, expected_attempts=None):
    validate_catalog(catalog)
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        raise ValueError("Events must be an array of event objects")
    calls = [e for e in events if e.get("type") == "model_call"]
    if expected_attempts is not None and (type(expected_attempts) is not int or expected_attempts < len(calls)):
        raise ValueError("Expected attempts cannot be fewer than recorded model calls")
    rows = [estimate_call(e, catalog) for e in calls]
    estimated = [r for r in rows if r["status"] == "estimated"]
    subtotal = sum((Decimal(r["estimated_cost"]) for r in estimated), Decimal(0))
    unknown = sum(r["status"] == "unknown" for r in rows)
    unrecorded = None if expected_attempts is None else expected_attempts - len(calls)
    complete = unknown == 0 and unrecorded == 0
    return {"protocol": "token-cost-ledger-v1", "currency": catalog["currency"],
            "price_as_of": catalog["as_of"], "price_source": catalog["source_url"],
            "price_catalog_sha256": hashlib.sha256(json.dumps(catalog, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "recorded_model_calls": len(calls), "estimated_calls": len(estimated), "unknown_calls": unknown,
            "excluded_non_live_calls": sum(r["status"] == "excluded_non_live_model" for r in rows),
            "unrecorded_attempts": unrecorded, "coverage_complete": complete,
            "priced_calls_subtotal": str(subtotal),
            "estimated_total": str(subtotal) if complete and estimated else None,
            "notice": "Token-only estimate, not an invoice or spending limit. Missing usage is unknown; reasoning tokens are included in output tokens. Excludes taxes, regional premiums, hosted tools, storage, external data and local compute. Catalog rates must match the actual account and run date.",
            "calls": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, help="Saved model_call events JSON array")
    parser.add_argument("--prices", required=True, help="Explicit dated price catalog JSON")
    parser.add_argument("--expected-attempts", type=int, help="Episode model-call reservations, including missing/crashed calls")
    parser.add_argument("--output", required=True, help="New JSON path; existing files are preserved")
    args = parser.parse_args(argv)
    result = cost_ledger(_json_loads(Path(args.events).read_text()),
                         _json_loads(Path(args.prices).read_text()), expected_attempts=args.expected_attempts)
    with Path(args.output).open("x", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "calls"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
