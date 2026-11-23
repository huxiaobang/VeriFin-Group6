"""Validated offline prices and an opt-in, single-request daily-price adapter.

Identity, currency and listing timezone are supplied explicitly. A price row's
date is the provider's trading-session date, never a nearest-calendar substitute.
No API key is persisted, placed in evidence URLs or included in error messages.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .data import DataError, _NoRedirect, available_by, cutoff_time, parse_day, stable_id

PRICE_TYPES = {"close", "adjusted_close"}


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DataError("Price metadata requires " + field + ".")
    return value


def _url(value, field):
    _text(value, field)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise DataError(field + " must be a public HTTPS provenance URL without credentials.")
    if re.search(r"(?:api[_-]?key|token|secret|password)=", parsed.query, re.I):
        raise DataError(field + " must not contain an API key or token.")
    return value


def _instant(value, field):
    if not isinstance(value, str) or "T" not in value:
        raise DataError(field + " must be a precise timezone-aware timestamp.")
    return cutoff_time(value)


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise DataError("Prices must be finite positive numbers in base currency/share units.")
    return value


def validate_instrument(instrument):
    if not isinstance(instrument, dict):
        raise DataError("Instrument configuration must be an object.")
    for field in ("entity", "symbol", "exchange", "currency", "timezone"):
        _text(instrument.get(field), field)
    if not re.fullmatch(r"[A-Z]{3}", instrument["currency"]):
        raise DataError("Instrument currency must be an explicit three-letter code.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.^_-]{0,39}", instrument["symbol"]):
        raise DataError("Invalid provider instrument symbol.")
    if instrument.get("cik") is not None and not re.fullmatch(r"\d{10}", instrument["cik"]):
        raise DataError("Optional issuer CIK must contain ten digits.")
    try:
        ZoneInfo(instrument["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DataError("Instrument timezone must be an installed IANA timezone.") from exc


class PriceSnapshot:
    """Read schema version 1. The original JSON file itself is hash-addressed."""
    def __init__(self, payload, snapshot_hash=None, snapshot_path=None, mode="imported"):
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise DataError("Unsupported price snapshot schema.")
        self.provider = _text(payload.get("provider"), "provider")
        self.source_url = _url(payload.get("source_url"), "source_url")
        self.license = _text(payload.get("license"), "license")
        self.license_url = _url(payload.get("license_url"), "license_url")
        observed = _instant(payload.get("retrieved_at"), "retrieved_at")
        self.retrieved_at = payload["retrieved_at"]
        self.snapshot_hash = snapshot_hash or hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.snapshot_path, self.mode = snapshot_path, mode
        self.instruments = payload.get("instruments")
        if not isinstance(self.instruments, list) or not self.instruments:
            raise DataError("Price snapshot requires at least one instrument.")
        seen = set()
        for instrument in self.instruments:
            validate_instrument(instrument)
            price_type = instrument.get("price_type")
            if price_type not in PRICE_TYPES:
                raise DataError("Price type must be close or adjusted_close.")
            key = (instrument["entity"], price_type)
            if key in seen:
                raise DataError("Select one explicit listing per entity and price type; duplicate series are ambiguous.")
            seen.add(key)
            adjustment = instrument.get("adjustment")
            if not isinstance(adjustment, dict) or adjustment.get("splits") not in {"none", "adjusted", "unknown"} or adjustment.get("dividends") not in {"none", "adjusted", "unknown"}:
                raise DataError("Explicit split and dividend adjustment definitions are required.")
            if price_type == "adjusted_close" and adjustment["splits"] == adjustment["dividends"] == "none":
                raise DataError("An adjusted close must identify at least one adjustment.")
            if "adjusted" in (adjustment["splits"], adjustment["dividends"]):
                adjusted_as_of = _instant(adjustment.get("as_of"), "adjustment.as_of")
                if adjusted_as_of > observed:
                    raise DataError("Adjustment basis cannot postdate the snapshot observation.")
            else:
                adjusted_as_of = None
            rows = instrument.get("rows")
            if not isinstance(rows, list) or not rows:
                raise DataError("Price instrument requires trading-session rows.")
            dates = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise DataError("Price row must be an object.")
                session_date = parse_day(row.get("date"))
                if row["date"] in dates:
                    raise DataError("Duplicate trading-session date in price series.")
                dates.add(row["date"])
                _positive(row.get("value"))
                available = _instant(row.get("available_at"), "available_at")
                _text(row.get("availability_basis"), "availability_basis")
                if available.astimezone(ZoneInfo(instrument["timezone"])).date() < session_date:
                    raise DataError("A closing price cannot be available before its local trading date.")
                if available > observed or (adjusted_as_of and available < adjusted_as_of):
                    raise DataError("Price availability is inconsistent with observation or adjustment vintage.")
                if row.get("split_coefficient") is not None:
                    _positive(row["split_coefficient"])
                if row.get("dividend") is not None and (isinstance(row["dividend"], bool) or not isinstance(row["dividend"], (int, float)) or not math.isfinite(row["dividend"]) or row["dividend"] < 0):
                    raise DataError("Dividend event must be a finite non-negative number.")

    @classmethod
    def from_path(cls, path, expected_hash=None):
        path = Path(path)
        if path.stat().st_size > 30_000_000:
            raise DataError("Price import exceeds the 30 MB bound.")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_hash and digest != expected_hash:
            raise DataError("Price import hash mismatch.")
        return cls(json.loads(raw), digest, str(path.resolve()))

    def identities(self):
        return [{k: item.get(k) for k in ("entity", "cik", "symbol", "exchange", "currency", "timezone")}
                for item in self.instruments]

    def select(self, entity, start_date, end_date, cutoff=None, price_type="close"):
        if parse_day(start_date) > parse_day(end_date):
            raise DataError("Price window start must not follow end.")
        matches = [item for item in self.instruments if item["entity"] == entity and item["price_type"] == price_type]
        if len(matches) != 1:
            raise DataError("No unambiguous configured price series for this entity and price type.")
        instrument = matches[0]
        rows = [(i, row) for i, row in enumerate(instrument["rows"]) if available_by(row["available_at"], cutoff)]
        available = sorted(row["date"] for _, row in rows)
        if start_date not in available or end_date not in available:
            raise DataError("Exact endpoint trading prices are unavailable at the cutoff; no date substitution was made.")
        selected = [(i, row) for i, row in rows if start_date <= row["date"] <= end_date]
        adjustment = instrument["adjustment"]
        basis = "market:" + stable_id("", [self.provider, instrument["symbol"], instrument["exchange"], price_type, adjustment])
        evidence = []
        for index, row in sorted(selected, key=lambda pair: pair[1]["date"]):
            evidence.append({"id": stable_id("price_", [self.provider, instrument["symbol"], instrument["exchange"], price_type,
                                                      instrument["currency"], adjustment, row["date"]]),
                             "kind": "market_price", "entity": entity, "cik": instrument.get("cik"),
                             "metric": "provider_" + price_type, "value": row["value"],
                             "unit": instrument["currency"] + "/share", "currency": instrument["currency"], "scale": 1,
                             "basis": basis, "business_scope": "listed_equity:" + instrument["exchange"] + ":" + instrument["symbol"],
                             "period_start": None, "period_end": row["date"], "published_at": row["available_at"],
                             "publication_precision": "timestamp", "availability_basis": row["availability_basis"],
                             "source_url": self.source_url, "locator": row.get("source_locator") or f"instruments entity={entity}; price_type={price_type}; rows[{index}] date={row['date']}",
                             "snapshot_hash": self.snapshot_hash, "snapshot_path": self.snapshot_path,
                             "retrieved_at": self.retrieved_at, "mode": self.mode,
                             "provider": self.provider, "provider_symbol": instrument["symbol"], "exchange": instrument["exchange"],
                             "timezone": instrument["timezone"], "trading_date": row["date"], "price_type": price_type,
                             "adjustment": dict(adjustment), "license": self.license, "license_url": self.license_url,
                             "split_coefficient": row.get("split_coefficient"), "dividend": row.get("dividend")})
        return {"summary": f"Returned {len(evidence)} {price_type} observations for exact provider trading dates.",
                "evidence": evidence, "data": {"mode": self.mode, "provider": self.provider, "available_dates": available,
                    "price_type": price_type, "adjustment": adjustment, "timezone": instrument["timezone"],
                    "trading_calendar": "observed provider session dates only; missing sessions are not inferred holidays",
                    "license": self.license, "license_url": self.license_url,
                    "warning": "Price changes are descriptive, not event causality or a complete investor total return."}}


class AlphaVantageDailyAdapter:
    """One public documented endpoint, explicit identity mapping and permissions.

    Raw compact daily is default. Full history and adjusted daily require the
    caller to explicitly permit premium requests. No request is made on init.
    """
    def __init__(self, api_key=None, instruments=(), allow_network=False, allow_premium=False, outputsize="compact"):
        if outputsize not in {"compact", "full"}:
            raise DataError("Alpha Vantage outputsize must be compact or full.")
        self.api_key, self.allow_network, self.allow_premium, self.outputsize = api_key, allow_network, allow_premium, outputsize
        self.instruments = [dict(item) for item in instruments]
        for item in self.instruments:
            validate_instrument(item)
        if len({i["entity"] for i in self.instruments}) != len(self.instruments):
            raise DataError("One explicit Alpha Vantage listing per entity is required.")

    def fetch(self, entity, price_type, data, source="auto"):
        if price_type not in PRICE_TYPES:
            raise DataError("Unsupported price type.")
        matches = [i for i in self.instruments if i["entity"] == entity]
        if len(matches) != 1:
            raise DataError("An explicit provider symbol/currency/exchange/timezone mapping is required for this issuer.")
        instrument = matches[0]
        function = "TIME_SERIES_DAILY" if price_type == "close" else "TIME_SERIES_DAILY_ADJUSTED"
        public_url = "https://www.alphavantage.co/query?" + urlencode({"function": function, "symbol": instrument["symbol"], "outputsize": self.outputsize, "datatype": "json"})
        cached = data.cached_sec_json(public_url) if source != "live" else None
        if cached:
            payload, info = cached
        else:
            if source == "replay" or not self.allow_network or not data.allow_network:
                raise DataError("Live market access is disabled and no verified provider snapshot is cached.")
            if not self.api_key or self.api_key == "demo":
                raise DataError("A caller-supplied Alpha Vantage API key is required; no demo identity is substituted.")
            if (self.outputsize == "full" or price_type == "adjusted_close") and not self.allow_premium:
                raise DataError("Full history or adjusted daily needs explicit premium-request authorization and provider entitlement.")
            request_url = public_url + "&" + urlencode({"apikey": self.api_key})
            req = Request(request_url, headers={"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "VeriFin/0.1"})
            data.network_requests += 1
            try:
                with build_opener(_NoRedirect).open(req, timeout=data.timeout) as response:
                    raw = response.read(30_000_001)
            except HTTPError as exc:
                raise DataError(f"Market provider HTTP error {exc.code}; no retry was attempted.") from None
            except (URLError, OSError, TimeoutError, DataError):
                raise DataError("Market provider request failed or redirected; no retry was attempted.") from None
            if len(raw) > 30_000_000:
                raise DataError("Market response exceeds the 30 MB bound.")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise DataError("Market provider did not return valid JSON.") from None
            if isinstance(payload, dict) and any(k in payload for k in ("Error Message", "Note", "Information")):
                raise DataError("Market provider rejected the request or reported a quota/entitlement restriction; no prices were accepted.")
            if self.api_key.encode() in raw:
                raise DataError("Market response unexpectedly contains credentials; it was not saved.")
            info = {"url": public_url, "sha256": hashlib.sha256(raw).hexdigest(),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(), "mode": "live"}
            # Parse and validate before an unsuccessful body can enter the cache.
            snapshot = self._parse(payload, instrument, price_type, info)
            info["snapshot"] = str(data.save_live_bytes(raw, ".json"))
            data._remember_source(info, "json")
        return self._parse(payload, instrument, price_type, info)

    @staticmethod
    def _parse(payload, instrument, price_type, info):
        if not isinstance(payload, dict):
            raise DataError("Invalid market provider payload.")
        meta = payload.get("Meta Data", {})
        if meta.get("2. Symbol") != instrument["symbol"]:
            raise DataError("Market response instrument identity does not match the configured symbol.")
        provider_zone = meta.get("5. Time Zone") or meta.get("6. Time Zone")
        try:
            # Provider commonly reports US/Eastern, an IANA alias. Compare offsets
            # at several dates to reject obvious zone mismatches without guessing.
            configured, returned = ZoneInfo(instrument["timezone"]), ZoneInfo(provider_zone)
            dates = [datetime(2024, 1, 15), datetime(2024, 7, 15)]
            if any(d.replace(tzinfo=configured).utcoffset() != d.replace(tzinfo=returned).utcoffset() for d in dates):
                raise DataError("Market response timezone differs from the configured listing.")
        except (TypeError, ZoneInfoNotFoundError, ValueError) as exc:
            raise DataError("Market response timezone is missing or inconsistent.") from exc
        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict) or not series:
            raise DataError("Market response contains no daily observations.")
        observed = info["retrieved_at"]
        adjustment = {"splits": "adjusted" if price_type == "adjusted_close" else "none",
                      "dividends": "adjusted" if price_type == "adjusted_close" else "none"}
        if price_type == "adjusted_close":
            adjustment["as_of"] = observed
        rows = []
        for day, value in series.items():
            try:
                supplied = value["4. close" if price_type == "close" else "5. adjusted close"]
                if isinstance(supplied, bool):
                    raise ValueError("Boolean price")
                number = float(supplied)
                row = {"date": day, "value": number, "available_at": observed,
                       "source_locator": f"Time Series (Daily).{day}." + ("4. close" if price_type == "close" else "5. adjusted close"),
                       "availability_basis": "first observed in this frozen provider response; no point-in-time backdating"}
                if "8. split coefficient" in value:
                    row["split_coefficient"] = float(value["8. split coefficient"])
                if "7. dividend amount" in value:
                    row["dividend"] = float(value["7. dividend amount"])
            except (ValueError, TypeError, KeyError):
                raise DataError("Invalid numeric row in market provider response.") from None
            rows.append(row)
        payload = {"schema_version": 1, "provider": "Alpha Vantage", "source_url": info["url"],
                   "license": "Provider terms and account entitlement apply; no redistribution license is inferred.",
                   "license_url": "https://www.alphavantage.co/terms_of_service/", "retrieved_at": observed,
                   "instruments": [dict(instrument, price_type=price_type, adjustment=adjustment, rows=rows)]}
        return PriceSnapshot(payload, info["sha256"], info.get("snapshot"), info["mode"])



def market_window_gap(receipts, scope, evidence=()):
    """Describe missing price coverage after considering every eligible source."""
    failures, covered = {}, set()
    entities = scope.get("entities", [])
    for receipt in receipts:
        if receipt["tool"] != "market_prices":
            continue
        args = receipt.get("arguments", {})
        if not ("start_date" in args and "end_date" in args):
            continue
        detail = None
        if not receipt["ok"]:
            reason = receipt.get("error") or receipt.get("summary", "")
            if reason.startswith("Exact endpoint trading prices are unavailable"):
                detail = ("随包行情缺少精确区间端点" if receipt.get("data", {}).get("mode") == "replay"
                          else "所用行情缺少精确区间端点")
            elif reason == "No live market adapter is configured.":
                detail = "未配置实时行情来源"
            else:
                continue
        entity = args.get("entity") or (entities[0] if len(entities) == 1 else None)
        start, end = args["start_date"], args["end_date"]
        price_type = args.get("price_type", "close")
        cutoff = args.get("cutoff") or scope.get("cutoff")
        key = (entity.casefold() if entity else None, start, end, price_type, cutoff_time(cutoff))
        if detail is None:
            # Both exact endpoints were returned. A subsequent attempt to use
            # another source cannot make these observations disappear.
            covered.add(key)
            continue
        _, _, details = failures.pop(key, (None, None, []))
        failures[key] = (entity, cutoff, [*details, detail])
    for key, (entity, cutoff, details) in reversed(failures.items()):
        if key in covered:
            continue
        _, start, end, price_type, _ = key
        dates_by_series = _market_window_dates(evidence, entity, start, end, price_type, cutoff)
        if any({start, end} <= dates for dates in dates_by_series.values()):
            continue
        basis = "调整后收盘价" if price_type == "adjusted_close" else "收盘价"
        window = " ".join(part for part in (entity, f"{start} 至 {end}" if start != end else start) if part)
        coverage = (f"{window} 已取得部分{basis}，尚无法配对同口径的两个区间端点"
                    if dates_by_series else f"{window} 的{basis}未能取得")
        gap = "现有资料无法核验该区间的股价走势或按单日跌幅排序。" if start != end else ""
        reason = f"{coverage}（{'，'.join(dict.fromkeys(details))}）。{gap}"
        return {"tool": "market_prices", "kind": "market_window", "reason": reason}
    return None


def _market_window_dates(evidence, entity, start, end, price_type, cutoff):
    dates_by_series = {}
    if entity is None:
        return dates_by_series
    for row in evidence:
        day = row.get("trading_date") or row.get("period_end")
        if (row.get("kind") != "market_price" or row.get("stale")
                or (row.get("entity") or "").casefold() != entity.casefold()
                or row.get("price_type", "close") != price_type
                or not day or not start <= day <= end
                or not available_by(row.get("published_at"), cutoff)):
            continue
        # Use the same quote compatibility fields as the price-return tool.
        series = tuple(row.get(field) for field in ("business_scope", "basis", "metric", "currency", "unit"))
        dates_by_series.setdefault(series, set()).add(day)
    return dates_by_series
