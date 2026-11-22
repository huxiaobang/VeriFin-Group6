"""Locate the cited original for a short request about the previous answer."""

import re
from urllib.parse import urlsplit

from .context import as_of_context


_LINK_ONLY = re.compile(
    r"(?:(?:[请請](?:[问問])?|能(?:否)?|可(?:以|否)?|麻[烦煩]|直接|只)\s*)?"
    r"(?:[给給发發贴貼]\s*(?:我\s*)?(?:一?[个個条條]\s*)?(?:(?:原文|出处|出處|[来來]源)\s*)?"
    r"(?:链接|連結|鏈接)(?:[吗嗎么麼])?"
    r"|(?:[刚剛]才|上一[答輪轮]|上面)?(?:的)?(?:原文|出处|出處|[来來]源)(?:在)?哪(?:[里裡儿兒])?)"
    r"|(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:just\s+)?"
    r"(?:(?:give|send|share|show|provide)\s+(?:me\s+)?(?:the|a|that)\s+)?link(?:\s+(?:please|only))?"
    r"|(?:where is|where can I find) the original (?:text|source|document|filing)", re.I)

ARCHIVE_GAP = "在当前范围内，未能从上一答已核验的主张确认唯一原始来源。请指出要核对的指标或主张。"


def requested(text):
    return bool(_LINK_ONLY.fullmatch(text.strip().rstrip("？?。.!！").rstrip()))


def public_link(value):
    """Return only a web address suitable for pointing to an archived source."""
    if not isinstance(value, str) or re.search(r"[\s<>\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in ("http", "https") and parsed.hostname and "." in parsed.hostname
                and not parsed.username and not parsed.password and parsed.port in (None, 80, 443)):
            return value
    except ValueError:
        pass
    return None


def current_claim_version(claim, version):
    """An ordinary checklist change leaves the same historical reviewed claim."""
    return (claim is not None and claim.get("version") == version and claim.get("status") == "Supported"
            and (not claim.get("stale") or set(claim.get("invalidation_reasons", ())) == {"scope_changed"}))


def saved_source(report, stored, scope):
    """Return one original link backed by the prior report and current record versions."""
    from .reporting import background_claim_ids, question_answers

    if not report.get("verification_available") or len(scope.get("entities", [])) != 1:
        return None
    if {k: v for k, v in report["scope"].items() if k != "requirements"} != {
            k: v for k, v in scope.items() if k != "requirements"}:
        return None
    current = as_of_context({**stored, "scope": scope})
    current_claims = {claim["id"]: claim for claim in stored.get("claims", [])}
    archived = {r["id"]: r for group in ("evidence", "calculations") for r in report.get(group, [])}
    records = {r["id"]: r for group in ("evidence", "calculations") for r in current[group] if not r.get("stale")}
    visible = {ref for row in question_answers(report) if row["status"] in ("answered", "partial")
               for ref in (row.get("claim_id"), *row.get("supporting_claim_ids", []),
                           *(i for fragment in row.get("answer_fragments", []) for i in fragment["claim_ids"])) if ref}
    if not visible:
        visible = set(background_claim_ids(report))
    if not report.get("request_parts"):
        visible = {c["id"] for c in report.get("claims", [])}
    links, proof_claims, proof_versions = {}, {}, {}
    claim_versions = report.get("record_versions", {}).get("claim", {})
    for claim in report.get("claims", []):
        verdict = claim.get("critic_verdict") or {}
        latest = current_claims.get(claim["id"], {})
        if (claim["id"] not in visible or claim.get("stale") or claim.get("status") != "Supported"
                or claim.get("version") is None or claim_versions.get(claim["id"]) != claim["version"]
                or claim.get("scope_version") != report.get("scope_version")
                or latest.get("version") != claim["version"]
                or verdict.get("status") != "Supported" or verdict.get("claim_id") != claim["id"]):
            continue
        if not current_claim_version(latest, claim["version"]):
            return None
        cited = claim.get("evidence_ids", []) + claim.get("calculation_ids", [])
        refs = verdict.get("evidence_refs", [])
        if not refs or not set(refs).issubset(cited):
            continue
        versions = {**claim.get("evidence_versions", {}), **claim.get("calculation_versions", {})}
        pending = [(ref, versions.get(ref)) for ref in refs]
        sources, seen = [], set()
        while pending:
            ref, expected = pending.pop()
            row, saved = records.get(ref), archived.get(ref)
            if (not row or not saved or saved.get("stale") or row.get("version") != saved.get("version")
                    or expected is None or row["version"] != expected
                    or row.get("entity") not in scope["entities"]):
                return None
            if ref in seen:
                continue
            seen.add(ref)
            if row.get("operand_ids"):
                pending.extend((operand, row.get("operand_versions", {}).get(operand)) for operand in row["operand_ids"])
            elif row.get("snapshot_hash") and row.get("locator") and row.get("kind") in ("document", "financial_fact"):
                link = public_link(row.get("filing_url") if (row.get("filing_link_provenance")
                                   and row.get("filing_url")) else row.get("source_url"))
                if link:
                    sources.append((link, ref))
        if sources:
            proof_claims[claim["id"]] = claim["version"]
            proof_versions.update({ref: records[ref]["version"] for ref in seen})
            for link, ref in sources:
                links.setdefault(link, set()).add(ref)
    if len(links) == 1:
        url, refs = next(iter(links.items()))
        return {"url": url, "report_version": report["version"], "claim_ids": list(proof_claims),
                "evidence_ids": sorted(refs), "claim_versions": proof_claims, "proof_versions": proof_versions}
    return None
