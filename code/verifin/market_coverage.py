"""Qualify a reviewed raw-close answer using the price observations it actually cites."""
import re

from .data import DataError, available_by


_RAW_PRICE = re.compile(
    r"\b(?:raw|unadjusted)\s+(?:(?:regular[- ]session|closing|stock|share|daily|price|close-to-close)\s+)*"
    r"(?:closes?|closings?|closing prices?|prices?|returns?)\b"
    r"|(?:原始|未[经經]?[调調]整|[未不][复復][权權])(?:的)?(?:收[盘盤](?:[价價])?|[价價]格|股[价價]|收益(?:率)?|[回報报]酬率|回[报報](?:率)?)",
    re.I,
)
_NEGATED = re.compile(
    r"(?:\b(?:not(?: asking for)?|no(?: need for)?|instead of|rather than|(?:do not|don['’]t) (?:need|require|use))"
    r"(?:\s+(?:necessarily|verified|the|a|an))*|无需|不需要|不要|不用)\s*$", re.I)
_OPTIONAL = re.compile(r"^\s+(?:(?:are|is) not|(?:aren|isn)['’]t)\s+(?:required|needed|necessary)\b", re.I)
_BASIS_CUE = re.compile(r"\b(?:split[s-]?|dividends?|adjustments?|as[- ]traded|nominal)\b|拆股|拆分|分红|股息|[复復][权權]", re.I)


def _requires_unadjusted(question, classification):
    matches = list(_RAW_PRICE.finditer(question))
    if matches:
        # A generic label cannot weaken an express request; a negated mention
        # alone also cannot turn a descriptive question into a raw-price task.
        return any(not _NEGATED.search(question[:m.start()]) and not _OPTIONAL.match(question[m.end():])
                   for m in matches)
    return classification == "unadjusted" and bool(_BASIS_CUE.search(question))


def _available_at_cutoff(price, cutoff):
    basis = price.get("availability_basis")
    if price.get("stale") or not isinstance(basis, str) or not basis.strip() or "point-in-time source bytes unverified" in basis:
        return False
    try:
        if not available_by(price.get("published_at"), cutoff):
            return False
        vintage = (price.get("adjustment") or {}).get("as_of")
        return not vintage or available_by(vintage, cutoff)
    except DataError:
        return False


def check_coverage(context, rows):
    """Qualify reviewed coverage using only the endpoint records linked to that part."""
    parts = context.get("request_parts", {})
    claims = {claim["id"]: claim for claim in context.get("claims", []) if not claim.get("stale")}
    evidence = {row["id"]: row for row in context.get("evidence", [])}
    calculations = {row["id"]: row for row in context.get("calculations", [])}
    checked = []
    for row in rows:
        question = parts.get(row.get("part"), "")
        if row.get("status") == "answered" and _requires_unadjusted(question, row.get("requested_price_basis")):
            linked = [row.get("claim_id")]
            for field in ("supporting_claim_ids", "joint_claim_ids"):
                if isinstance(row.get(field), list):
                    linked.extend(row[field])
            price_ids = set()
            for cid in linked:
                claim = claims.get(cid, {}) if isinstance(cid, str) else {}
                price_ids.update(ref for ref in claim.get("evidence_ids", [])
                                 if evidence.get(ref, {}).get("kind") == "market_price")
                for ref in claim.get("calculation_ids", []):
                    calc = calculations.get(ref, {})
                    if calc.get("operation") == "price_return":
                        price_ids.update(calc.get("operand_ids", []))
            prices = [evidence.get(ref, {}) for ref in price_ids]
            limits = []
            if not prices:
                limits.append("No cited price observation establishes the requested unadjusted price basis.")
            if any(p.get("price_type") != "close" or
                   any((p.get("adjustment") or {}).get(k) != "none" for k in ("splits", "dividends"))
                   for p in prices):
                limits.append("The cited observations do not establish that every closing price is unadjusted for splits and dividends.")
            cutoff = context.get("scope", {}).get("cutoff")
            if cutoff and any(not _available_at_cutoff(p, cutoff) for p in prices):
                limits.append("The cited records do not establish the price series as available at the requested cutoff.")
            if limits:
                row = {**row, "status": "partial", "missing_support": " ".join(limits)}
        checked.append(row)
    return checked
