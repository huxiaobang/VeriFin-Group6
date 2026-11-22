"""Bind a fiscal-quarter change to the preceding answer and its dated sources."""
from datetime import date, timedelta
import re

from .data import available_by
from .display_numbers import rate_only_in_other_period
from .skill_calculations import is_comparison_period, quarter_mentions, target_labels

_PERIOD_ONLY = re.compile(
    r"(?:那(?:么)?|再)?\s*(?:再往)?(?:上(?:一|个|一个)?|前(?:一|一个))(?:个)?(?:财季|季(?:度)?)"
    r"(?:的情况|的表现)?(?:怎么样|如何|呢)?|"
    r"(?:(?:and|then)\s+)?(?:(?:how|what) about\s+)?(?:the\s+)?(?:previous|prior|last) (?:fiscal )?quarter",
    re.I)


def prior_period_only(text):
    return bool(_PERIOD_ONLY.fullmatch(text.strip().rstrip("？?。.!！").strip()))


def quarter_reference(text):
    """Return the referenced report number, or 'earlier' for an unspecified historical quarter."""
    for clause in re.split(r"[。！？!?；;，,]", text):
        if not re.search(r"(?:财季|季(?:度)?|\bquarter\b)", clause, re.I):
            continue
        if not re.search(r"回到|回看|说回|再看|重看|回顾|\b(?:back to|revisit|return to)\b", clause, re.I):
            continue
        ordinal = re.search(r"第\s*([1-9]\d*|[一二三四五六七八九十])\s*(?:轮|问)|\b(?:turn|question)\s+(\d+)\b", clause, re.I)
        if ordinal:
            number = ordinal[1] or ordinal[2]
            return int(number) if number.isascii() else "一二三四五六七八九十".index(number) + 1
        if re.search(r"(?:最开始|一开始|最早|最初|最先|第一次).{0,12}(?:谈|聊|提|讨论|分析|问|看)"
                     r"|\bquarter (?:that )?we (?:started (?:with|out with)|(?:first|originally) discussed)"
                     r"|\b(?:first|original) quarter (?:that )?we (?:discussed|looked at)"
                     r"|\bquarter.{0,30}\bat the (?:start|beginning)\b", clause, re.I):
            return 1
        if re.search(r"之前|先前|前面|当时|曾经|[说聊讲提]过|\b(?:earlier|before|discussed|previously)\b", clause, re.I):
            return "earlier"
    return None


def same_quarter_focus(question, previous, *, retry=False):
    """Carry the comparison only for an expense follow-up. A named cash metric starts its own task."""
    from .cashflow_delivery import cash_amount_question
    from .operating_margin import expense_followup

    if retry:
        return previous.get("followup_focus")
    if cash_amount_question(question):
        return {"metric":"operating_cash_flow"}
    if expense_followup(question, previous):
        return {"metric":"operating_income_margin", "comparison":"year_over_year", "aspect":"expense_contribution"}
    return None


def _duration(start, end):
    try:
        # A single regular issuer quarter, including week-based fiscal calendars.
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    except (TypeError, ValueError):
        return False
    return 77 <= days <= 105


def report_issuer(report):
    entities = report["scope"].get("entities")
    if entities is None and report.get("verification_available"):
        entities = {(c.get("constraints") or {}).get("entity") for c in report.get("claims", [])
                    if c.get("status") == "Supported" and not c.get("stale")}
    return next(iter(entities)) if entities and len(entities) == 1 else None


_COMPARISON_SUBJECT = re.compile(
    r"(?:上年|去年)(?:同[期季]|第)|(?:上一|前一)(?:财季|季)|"
    r"\b(?:prior.year|year.ago|previous (?:fiscal )?quarter|last year.s)\b", re.I)


def reviewed_quarter(report, entity, current_records, cutoff):
    """Recover the primary quarter only when a reviewed claim and an exact source period agree."""
    if not report.get("verification_available") or report_issuer(report) != entity:
        return None
    scope = report["scope"]
    target = target_labels(report["request"])
    for requirement in scope.get("requirements", []):
        target.update(target_labels(requirement["text"]))
    if len(target) > 1:
        return None
    archived = {r["id"]: r for r in report.get("evidence", []) + report.get("calculations", [])}
    candidates = {}
    for claim in report.get("claims", []):
        dims = claim.get("constraints") or {}
        start, end = dims.get("period_start"), dims.get("period_end")
        if (claim.get("status") != "Supported" or claim.get("stale") or dims.get("entity") != entity
                or not _duration(start, end) or any(scope.get(k) and scope[k] != v
                    for k, v in (("period_start", start), ("period_end", end)))):
            continue
        labels = target_labels(claim["text"])
        if (len(labels) > 1 or (target and labels and target != labels)
                or (not labels and _COMPARISON_SUBJECT.search(claim["text"]))):
            continue
        for ref in claim.get("evidence_ids", []) + claim.get("calculation_ids", []):
            row = archived.get(ref, {})
            current = current_records.get(ref, {})
            if (row.get("entity") != entity or not current or row.get("stale") or current.get("stale")
                    or current.get("version") != row.get("version")
                    or not available_by(row.get("published_at"), cutoff)):
                continue
            dates = (row.get("period_start"), row.get("period_end"))
            if row.get("kind") == "document":
                dates = (row.get("document_period_start"), row.get("document_report_date"))
            if dates == (start, end):
                entry = candidates.setdefault(dates, {"labels": set(), "refs": set()})
                entry["labels"].update(labels)
                entry["refs"].add(ref)
    if len(candidates) != 1:
        return None
    (start, end), candidate = next(iter(candidates.items()))
    labels = candidate["labels"] or target
    if len(labels) != 1:
        return None
    return {"entity": entity, "quarter": next(iter(labels)), "period_start": start, "period_end": end,
            "evidence_ids": sorted(candidate["refs"])}


def confirmed_dates(period, known):
    return (_duration(period.get("period_start"), period.get("period_end"))
            and all(period.get(k) == v for k, v in known.items()))


def adjacent_dates(anchor, documents, cutoff):
    if not anchor.get("period_start"):
        return {}
    end = (date.fromisoformat(anchor["period_start"]) - timedelta(days=1)).isoformat()
    starts = {doc["period_start"] for doc in documents if doc.get("entity") == anchor["entity"]
              and doc.get("period_end") == end and _duration(doc.get("period_start"), end)
              and available_by(doc.get("published_at"), cutoff)}
    return {"period_end": end, **({"period_start": starts.pop()} if len(starts) == 1 else {})}


def inherited_questions(report, previous, anchor, quarter):
    original = (previous.get("quarter_followup_parts") or report.get("request_parts")
                or {"part-1": report["request"]})
    rebased = {}
    parts = {}
    for key, text in original.items():
        # Preserve the user's actual questions, replacing an explicit target label.
        # Past observations embedded in a question must be checked again this quarter.
        period_mentions = sorted(quarter_mentions(text), key=lambda pair: pair[0].start(), reverse=True)
        for match, label in period_mentions:
            if label == anchor and not is_comparison_period(text, match):
                text = text[:match.start()] + quarter + text[match.end():]
        rebased[key] = text
        parts[key] = f"{quarter}，请重新核对本季情况并回答：「{text}」"
    return rebased, parts

def bound_plan(decision, target, ctx):
    """Preserve a resolved fiscal window when the planner proposes dates or rewrites the checklist."""
    patch = dict(decision.get("scope", {}))
    known = {k: target[k] for k in ("period_start", "period_end") if target.get(k)}
    if known and ("period_start" in patch or "period_end" in patch):
        # The model can supply a previously unknown start if a retrieved duration
        # pairs it with the established quarter end. A later filing may cite it.
        start = patch.get("period_start")
        if start and "period_start" not in known:
            end = known.get("period_end")
            supported = any((not target.get("entity") or r.get("entity") == target["entity"]) and not r.get("stale") and
                ((r.get("period_start"), r.get("period_end")) == (start, end) or
                 (r.get("document_period_start"), r.get("document_report_date")) == (start, end))
                for r in ctx["evidence"] + ctx["calculations"])
            if not supported or not _duration(start, end):
                patch.pop("period_start")
        patch.update(known)
    result = dict(decision)
    if "scope" in result:
        result["scope"] = patch
    if quarter := target.get("quarter") or target.get("requested_quarter"):
        for part in (result, patch):
            if "requirements" in part:
                part["requirements"] = [r for r in part["requirements"] if
                                        not (target_labels(r["text"]) - {quarter})]
    return result


def check_answer_periods(rows, claims, target, questions, records=()):
    """A reviewed assertion about another quarter cannot answer a relative-quarter request."""
    by_id = {c["id"]: c for c in claims}
    quarter = target.get("quarter")
    requested = quarter or target.get("requested_quarter")
    disagreement = target.get("conflicting_quarter")
    indexed = {} if quarter else {r["id"]: r for r in records}
    checked = []
    for row in rows:
        claim = by_id.get(row.get("claim_id"), {})
        dims = claim.get("constraints") or {}
        question = questions.get(row.get("part"), "")
        if row.get("status") in ("answered", "partial") and disagreement:
            row = {"part": row["part"], "status": "gap", "missing_support":
                   f"已核实的日期对应 {disagreement}，本问要求 {requested}。请确认季度或调整日期。"}
        elif row.get("status") in ("answered", "partial") and not _COMPARISON_SUBJECT.search(question):
            labels = target_labels(row.get("answer_sentence", "")) or target_labels(claim.get("text", ""))
            conflict = requested and labels and requested not in labels
            conflict = conflict or any(target.get(k) and dims.get(k) and target[k] != dims[k]
                                       for k in ("entity", "period_start", "period_end"))
            if not quarter and not conflict:
                refs = claim.get("evidence_ids", []) + claim.get("calculation_ids", [])
                current, outside = period_sources(target, (indexed[ref] for ref in dict.fromkeys(refs) if ref in indexed))
                conflict = not current or any(
                    rate_only_in_other_period(text, current, outside)
                    for text in (claim.get("text", ""), row.get("answer_sentence", "")))
            if conflict:
                row = {"part": row["part"], "status": "gap", "missing_support":
                       (f"所引主张的对象或财季与本问的 {target['entity']} {quarter} 不符。" if quarter
                        else "引文未能核实该主张属于用户指定的期间。")}
        checked.append(row)
    return checked


def period_sources(target, records):
    """Use an observation's explicitly typed dates before its document's reporting dates."""
    bounds = {k: target[k] for k in ("period_start", "period_end") if target.get(k)}
    current, outside = [], []
    if not bounds:
        return current, outside
    for record in records:
        if record.get("stale") or (target.get("entity") and record.get("entity") != target["entity"]):
            continue
        dates = record if record.get("period_end") or record.get("period_start") else {
            "period_start": record.get("document_period_start"),
            "period_end": record.get("document_report_date"),
        }
        if all(dates.get(key) == value for key, value in bounds.items()):
            current.append(record)
        elif any(dates.get(key) for key in bounds):
            outside.append(record)
    return current, outside
