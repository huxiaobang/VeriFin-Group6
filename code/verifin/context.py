"""Bounded extractive history retrieval; no generated summary or truth judgment."""
from __future__ import annotations

from collections import Counter
import json
import math
import re


def as_of_context(context):
    """Project stored disclosures to the resolved cutoff without changing the archive."""
    from .data import available_by, cutoff_time, DataError

    cutoff = context.get("scope", {}).get("cutoff")
    if not cutoff:
        return context
    boundary = cutoff_time(cutoff)

    def outside_cutoff(record, *, allow_derived=False):
        stamp = record.get("published_at")
        if not stamp:
            # Undated calculations inherit visibility through all recorded operands.
            return not (allow_derived and record.get("operand_ids"))
        try:
            return not available_by(stamp, cutoff)
        except DataError:
            return True

    excluded = {e["id"] for e in context["evidence"] if outside_cutoff(e)}
    calculations = context["calculations"]
    present = {r["id"] for r in [*context["evidence"], *calculations]}
    excluded.update(c["id"] for c in calculations if outside_cutoff(c, allow_derived=True)
                    or not present.issuperset(c.get("operand_ids", [])))
    # Dependencies are not guaranteed to be enumerated in topological order.
    while True:
        derived = {c["id"] for c in calculations if excluded.intersection(c.get("operand_ids", ()))}
        if derived <= excluded:
            break
        excluded.update(derived)
    def blocked_claim(c):
        return bool(excluded.intersection(c.get("evidence_ids", ())) or excluded.intersection(c.get("calculation_ids", ())))

    reports = context.get("reports", [])
    def later_report(r):
        stamp = (r.get("scope") or {}).get("cutoff")
        return ((stamp is not None and cutoff_time(stamp) > boundary)
                or any(outside_cutoff(row) for row in r.get("evidence", ()))
                or any(blocked_claim(c) for c in r.get("claims", [])))

    visible_reports = [r for r in reports if not later_report(r)]
    messages = context.get("messages", [])
    visible_messages = [m for m in messages if m.get("role") != "assistant" or
                        (m.get("research_cutoff") and cutoff_time(m["research_cutoff"]) <= boundary)]
    if not excluded and len(visible_reports) == len(reports) and len(visible_messages) == len(messages):
        return context
    result = dict(context)
    result["evidence"] = [r for r in context["evidence"] if r["id"] not in excluded]
    result["calculations"] = [c for c in calculations if c["id"] not in excluded]
    result["claims"] = [c for c in context["claims"] if not blocked_claim(c)]
    result["reports"] = visible_reports
    if len(visible_messages) != len(messages):
        result["messages"] = visible_messages
    result["as_of_filtered"] = {
        "evidence": len(context["evidence"]) - len(result["evidence"]),
        "calculations": len(calculations) - len(result["calculations"]),
        "reports": len(reports) - len(visible_reports),
    }
    return result


def _issuer_key(value):
    key = str(value).strip().casefold()
    cik = re.fullmatch(r"(?:cik)?(\d{1,10})", key)
    return "cik"+cik[1].zfill(10) if cik else key


def scope_issuer_keys(entities, companies=()):
    if entities is None:
        return None
    requested = {_issuer_key(value) for value in entities}
    allowed = set(requested)
    identities = [{_issuer_key(v) for v in (c.get("ticker"), c.get("name"), c.get("cik"), *c.get("aliases", [])) if v}
                  for c in companies]
    for key in requested:
        exact = [identity for identity in identities if key in identity]
        if len(exact) == 1:
            allowed.update(exact[0])
    return allowed


def outside_issuer_scope(evidence, calculations, allowed):
    """Identify old or derived operands outside the selected issuer(s). None means an unbound session."""
    if allowed is None:
        return set()
    excluded = {r["id"] for r in [*evidence, *calculations] if r.get("entity")
                and _issuer_key(r["entity"]) not in allowed}
    while True:
        derived = {r["id"] for r in calculations if excluded.intersection(r.get("operand_ids", ()))}
        if derived <= excluded:
            return excluded
        excluded.update(derived)


def issuer_scoped_context(context, companies=()):
    """Separate citable operands and current tool receipts from the independently labelled report history."""
    allowed = scope_issuer_keys(context["scope"].get("entities"), companies)
    if allowed is None:
        return context
    excluded = outside_issuer_scope(context["evidence"], context["calculations"], allowed)
    def project(receipt):
        if not isinstance(receipt, dict):
            return receipt
        ids = [*receipt.get("evidence_ids", []), *receipt.get("calculation_ids", [])]
        ids.extend(r.get("id") for k in ("evidence","calculations") for r in receipt.get(k, []) if isinstance(r,dict))
        company = receipt.get("arguments", {}).get("entity")
        if excluded.intersection(ids) or (company and _issuer_key(company) not in allowed):
            return {k:receipt[k] for k in ("tool","ok") if k in receipt} | {"outside_current_issuers":True}
        return receipt
    episode = dict(context["episode"])
    if "last_tool_result" in episode:
        episode["last_tool_result"] = project(episode["last_tool_result"])
    if "recent_tool_results" in episode:
        episode["recent_tool_results"] = [project(r) for r in episode["recent_tool_results"]]
    return {**context, "episode":episode,
            "evidence":[r for r in context["evidence"] if r["id"] not in excluded],
            "calculations":[r for r in context["calculations"] if r["id"] not in excluded]}


def history_report_views(context):
    """Project archived answers onto visible source versions without changing the saved report."""
    visible = {r["id"]: r for r in context["evidence"] + context["calculations"] if not r.get("stale")}
    views = []
    for report in context["reports"]:
        view = {k: report.get(k) for k in ("id", "version", "request", "status", "stop_reason", "scope",
                                          "claims", "unanswered", "issues", "verification_available")}
        archived = report.get("evidence", []) + report.get("calculations", [])
        current_ids = {r["id"] for r in archived if r.get("version") is not None and not r.get("stale")
                       and r["id"] in visible and visible[r["id"]].get("version") == r["version"]}
        claims = []
        obsolete = False
        for claim in report.get("claims", []):
            refs = claim.get("evidence_ids", []) + claim.get("calculation_ids", [])
            versions = {**claim.get("evidence_versions", {}), **claim.get("calculation_versions", {})}
            if refs and (not current_ids.issuperset(refs) or
                         any(versions.get(ref, visible[ref]["version"]) != visible[ref]["version"] for ref in refs)):
                obsolete = True
                claim = {**claim, "status":"Pending Verification", "stale":True,
                         "archived_status":claim.get("status"), "reason":"archived_source_changed_or_unavailable"}
            claims.append(claim)
        view["claims"] = claims
        if obsolete:
            view.update(archived_status=report.get("status"), status="partial",
                        verification_available=bool(view["verification_available"] and any(
                            not c.get("stale") and c.get("status") != "Pending Verification" for c in claims)))
        if "skill_notes" in report:
            # These were printed as excerpts or calculations, without a Critic verdict.
            notes = []
            for note in report["skill_notes"]:
                refs = note.get("evidence_ids", []) + note.get("calculation_ids", [])
                if refs and current_ids.issuperset(refs):
                    notes.append(note)
            view.update(skill_notes=notes, model_mode=report.get("model_mode"))
        if report.get("skill_gap") and all(r["id"] in current_ids for r in archived):
            view["skill_gap"] = report["skill_gap"]
        if report.get("request_parts"):
            from .request_review import coverage_status, report_coverage
            view["request_parts"] = report["request_parts"]
            view["request_coverage"] = report_coverage(report["request_parts"], report.get("request_coverage", []),
                                                       claims, reviewed=report.get("verification_available", False),
                                                       requirements=report.get("scope",{}).get("requirements",[]),
                                                       unanswered_ids={r["id"] for r in report.get("unanswered",[])}, findings=report.get("issues", []))
            if not obsolete:
                view.update(coverage_status(report,view["request_coverage"]))
        views.append(view)
    return views


def reopen_comparison_receipt(context, store):
    """Restore this turn's question-to-calculation links after the recent window rolls over."""
    from .engine import digest

    episode = context["episode"]
    recent = episode.get("recent_tool_results", [])
    tool = "inspect_sales_comparison"
    if (not episode.get("id") or not context.get("request_parts")
            or any(r.get("tool") == tool and r.get("ok") for r in recent)
            or not any(c.get("operation") == "sales_calendar_growth_gap" and not c.get("stale")
                       for c in context["calculations"])):
        return
    event = store.latest_successful_tool_result(context["session"]["id"], episode["id"], tool)
    if not event:
        return
    observation = event.get("observation")
    if not isinstance(observation, dict) or event.get("observation_sha256") != digest(observation):
        return
    data = observation["data"]
    link = {key: data[key] for key in ("fiscal_quarter", "comparison_quarter") if key in data}
    link["part_findings"] = {
        part: {k: row[k] for k in ("status", "calculation_ids") if k in row}
        for part, row in data.get("part_findings", {}).items()
    }
    receipt = {"tool":tool, "ok":True, "arguments":{"entity":event["arguments"].get("entity")},
               "event_sequence":event["event_sequence"], "data":link,
               "evidence_ids":[row["id"] for row in observation["evidence"]],
               "calculation_ids":[row["id"] for row in observation["calculations"]],
               "record_versions":event.get("record_versions", {})}
    episode["recent_tool_results"] = [receipt, *recent]


def current_tool_receipts(context):
    """Reopen a tool observation as current only while its cited record versions remain visible."""
    if not any(key in context["episode"] for key in ("last_tool_result", "recent_tool_results")):
        return context
    visible = {r["id"]: r for r in context["evidence"] + context["calculations"] if not r.get("stale")}

    def project(receipt):
        if not isinstance(receipt, dict):
            return receipt
        versions = receipt.get("record_versions", {})
        records = {r["id"]:r for key in ("evidence", "calculations") for r in receipt.get(key, [])
                   if isinstance(r, dict) and "id" in r}
        ids = set(records) | set(versions) | set(receipt.get("evidence_ids", [])) | set(receipt.get("calculation_ids", []))
        for ref in ids:
            current = visible.get(ref)
            # Older saved receipts have no bound version. Only the first
            # version can then be identified without guessing at provenance.
            if not current or versions.get(ref, 1) != current.get("version"):
                return {k:receipt[k] for k in ("tool", "ok") if k in receipt} | {
                    "record_status":"superseded_or_unavailable", "record_ids":sorted(ids)}
        return {k:v for k, v in receipt.items() if k != "record_versions"} if versions else receipt

    episode = dict(context["episode"])
    if "last_tool_result" in episode:
        episode["last_tool_result"] = project(episode["last_tool_result"])
    if "recent_tool_results" in episode:
        episode["recent_tool_results"] = [project(r) for r in episode["recent_tool_results"]]
    return {**context, "episode":episode}


def _terms(text):
    terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", str(text).casefold())
    result = []
    for term in terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", term):
            result.extend(term[i:i + 2] for i in range(max(1, len(term) - 1)))
        elif len(term) > 1:
            result.append(term)
    return result


def _rank(texts, query):
    documents = [Counter(_terms(text)) for text in texts]
    query_terms = set(_terms(query))
    df = Counter(t for document in documents for t in document)
    scores = []
    for index, document in enumerate(documents):
        score = sum((1 + math.log(document[t])) * math.log(1 + len(documents) / (1 + df[t]))
                    for t in query_terms & document.keys()) / math.sqrt(max(1, sum(document.values())))
        scores.append((score, index))
    return sorted(scores, reverse=True)


def select_history(messages, reports, *, request, scope, max_messages=24, recent_messages=16, max_reports=3):
    """Keep recent turns plus relevant older turns and their adjacent reply.

    Returns indices/versions, leaving original records untouched. Current scope
    is supplied separately and is authoritative; old messages can be obsolete.
    """
    if any(type(n) is not int or n < 1 for n in (max_messages, recent_messages, max_reports)):
        raise ValueError("History limits must be positive integers")
    recent_messages = min(recent_messages, max_messages)
    recent_start = max(0, len(messages) - recent_messages)
    selected = set(range(recent_start, len(messages)))
    query = str(request) + " " + json.dumps({k: scope.get(k, []) for k in ("entities", "metrics")}, ensure_ascii=False)
    texts = [m.get("content", "") for m in messages[:recent_start]]
    for score, index in _rank(texts, query):
        if score <= 0 or len(selected) >= max_messages:
            break
        group = {index}
        # Retain the neighboring request/reply when it belongs to this turn.
        if messages[index].get("role") == "assistant" and index > 0 and messages[index - 1].get("role") == "user":
            group.add(index - 1)
        elif messages[index].get("role") == "user" and index + 1 < len(messages) and messages[index + 1].get("role") == "assistant":
            group.add(index + 1)
        if len(selected | group) <= max_messages:
            selected.update(group)
    # Fill spare places with recent history, including old turns with no match.
    for index in range(recent_start - 1, -1, -1):
        if len(selected) >= max_messages:
            break
        selected.add(index)
    report_indices = set(range(max(0, len(reports) - min(2, max_reports)), len(reports)))
    report_texts = [json.dumps({k: r.get(k) for k in ("request", "scope", "summary")}, ensure_ascii=False)
                    for r in reports]
    for score, index in _rank(report_texts, query):
        if len(report_indices) >= max_reports:
            break
        if score > 0:
            report_indices.add(index)
    for index in range(len(reports) - 1, -1, -1):
        if len(report_indices) >= max_reports:
            break
        report_indices.add(index)
    return {"message_indices": sorted(selected),
            "report_versions": [reports[i]["version"] for i in sorted(report_indices)],
            "method": "recent_plus_lexical_excerpts",
            "semantic_summary": False,
            "omitted": {"messages": len(messages) - len(selected), "reports": len(reports) - len(report_indices)}}


def reference_tool_records(context):
    """Keep one copy of a record only when all original fields are visible in this request."""
    episode = context.get("episode", {})
    latest = episode.get("last_tool_result")
    if not isinstance(latest, dict):
        return context
    result = dict(latest)
    changed = False
    for group in ("evidence", "calculations"):
        records = context.get(group)
        items = latest.get(group)
        if not isinstance(records, list) or not isinstance(items, list) or not items:
            continue
        if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in records):
            continue
        by_id = {row["id"]: row for row in records}
        if len(by_id) != len(records):
            continue
        projected = []
        for item in items:
            ref = item
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                row = by_id.get(item["id"])
                if row and not row.get("stale") and item.keys() <= row.keys():
                    original = json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    visible = {key: row[key] for key in item}
                    if json.dumps(visible, ensure_ascii=False, sort_keys=True, allow_nan=False) == original:
                        candidate = {"same_record_in":group,"id":item["id"],
                                     "omit_fields":sorted(row.keys() - item.keys())}
                        if len(json.dumps(candidate,ensure_ascii=False)) < len(original):
                            ref = candidate
                            changed = True
            projected.append(ref)
        result[group] = projected
    if not changed:
        return context
    return {**context, "episode":{**episode, "last_tool_result":result}}


_PROVENANCE_FIELDS = (
    "document_id", "snapshot_hash", "source_url", "snapshot_path", "text_snapshot_hash",
    "text_snapshot_path", "extraction_method", "published_at", "form", "table_id",
    "table_xpath", "table_title", "table_title_locator", "unit_locator", "currency_basis",
)


def _pack_passage(row, literals):
    """Reference only exact characters already sent literally in this source snapshot."""
    body, start, end = row["text"], row["start"], row["end"]
    cursor = start
    parts, new_literals = [], []
    for left, right, previous_id, previous_text in sorted(literals, key=lambda span: span[0]):
        if left >= end:
            break
        lo, hi = max(cursor, left), min(end, right)
        if hi <= lo:
            continue
        ref = {"evidence_id": previous_id, "start": lo, "end": hi}
        if hi - lo <= len(json.dumps(ref, ensure_ascii=False)) + 20:
            continue
        if body[lo-start:hi-start] != previous_text[lo-left:hi-left]:
            continue
        if lo > cursor:
            text = body[cursor-start:lo-start]
            parts.append(text)
            new_literals.append((cursor, lo, row["id"], text))
        parts.append(ref)
        cursor = hi
    if cursor == start:
        literals.append((start, end, row["id"], body))
        return None
    if cursor < end:
        parts.append(body[cursor-start:])
        new_literals.append((cursor, end, row["id"], parts[-1]))
    literals.extend(new_literals)
    return parts


def shared_evidence_sources(context):
    """Pack rows in source order. Appending evidence never rewrites an earlier row."""
    evidence = context.get("evidence")
    if not isinstance(evidence, list):
        return context
    anchors, passages, rows, seen = {}, {}, [], set()
    for row in evidence:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen
                or "source_fields_from" in row or "text_parts" in row):
            # Ambiguous IDs or reserved fields make in-request references unsafe.
            return context
        seen.add(row["id"])
        packed = row
        source = (row.get("document_id"), row.get("snapshot_hash"))
        if not row.get("stale") and all(isinstance(v, str) and v for v in source):
            table = row.get("table_id", "")
            if isinstance(table, str):
                anchor = anchors.setdefault((*source, table), row)
                if anchor is not row:
                    fields = [k for k in _PROVENANCE_FIELDS if isinstance(row.get(k), str)
                              and row[k] == anchor.get(k)]
                    ref = {"id": anchor["id"], "fields": fields}
                    omitted = {k:row[k] for k in fields}
                    if len(json.dumps(omitted, ensure_ascii=False)) > len(json.dumps(ref, ensure_ascii=False)) + 24:
                        packed = {k:v for k,v in row.items() if k not in omitted}
                        packed["source_fields_from"] = ref
            if (row.get("kind") == "document" and isinstance(row.get("entity"), str)
                    and isinstance(row.get("text_snapshot_hash"), str) and row["text_snapshot_hash"]
                    and type(row.get("start")) is int and type(row.get("end")) is int
                    and 0 <= row["start"] < row["end"] and isinstance(row.get("text"), str)
                    and len(row["text"]) == row["end"] - row["start"]):
                literals = passages.setdefault((*source, row["entity"], row["text_snapshot_hash"]), [])
                parts = _pack_passage(row, literals)
                if parts:
                    packed = {k:v for k,v in packed.items() if k != "text"}
                    packed["text_parts"] = parts
        rows.append(packed)
    if all(row is original for row, original in zip(rows, evidence)):
        return context
    return {**context, "evidence": rows}


def planner_view(context):
    """Index reopenable passages for planning; keep the evidence store authoritative."""
    tools = {x.get("name") for x in context.get("tools", [])}
    if "read_document" not in tools:
        return context
    episode = context.get("episode", {})
    recent = episode.get("recent_tool_results", [])
    last = recent[-1] if recent else {}
    # Each retrieval, including a search with multiple hits, hands off its original windows.
    just_read = set(last.get("evidence_ids", []))
    raw = episode.get("last_tool_result", {})
    if isinstance(raw, dict):
        just_read.update(row.get("id") for row in raw.get("evidence", []) if isinstance(row, dict))
    rows, indexed = [], 0
    for row in context.get("evidence", []):
        doc, start, end, body = (row.get(k) for k in ("document_id", "start", "end", "text"))
        if (row.get("kind") != "document" or row.get("id") in just_read or row.get("stale")
                or not doc or type(start) is not int or type(end) is not int or start < 0
                or not isinstance(body, str) or len(body) != end - start or len(body) <= 640):
            rows.append(row)
            continue
        # Keep numbers in structured records intact; previews are explicitly *not*
        # continuous quotations, and are never written back into the store.
        item = {k: v for k, v in row.items() if k != "text"}
        item.update(preview_start=body[:360], preview_end=body[-140:],
                    read_document={"document_id":doc, "offset":start, "limit_chars":min(6000,max(200,end-start))})
        rows.append(item)
        indexed += 1
    return {**reference_tool_records({**context, "evidence": rows}), "planner_evidence_index":{
        "indexed_passages":indexed, "meaning":"Preview start and end are separate excerpts. To inspect a passage, call read_document with its offset, document_id, current entity/cutoff when required. Continue through end for a multi-window span. Structured cells are complete. Researcher and Critic receive original evidence."}}

