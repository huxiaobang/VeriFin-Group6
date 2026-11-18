"""Compose each answer from the exact claim versions reviewed for that report."""
from decimal import Decimal
import re

from .display_numbers import assertion_fields


def _wording(text):
    return " ".join(text.split()).casefold().rstrip("?.。？")


def _number(value):
    if type(value) not in (int, float):
        return None
    decimal = Decimal(str(value))
    return decimal if decimal.is_finite() else None


def _format(value):
    return format(value, ",f").rstrip("0").rstrip(".") if value.as_tuple().exponent < 0 else format(value, ",f")


def _bridge_details(question, primary, proven, records, allowed):
    """Recover an older one-sentence mapping from a reviewed additive reconciliation.

    The two endpoints of the difference must also be tied by a zero residual:
    actual = base + component sums. Sharing an issuer or checklist is insufficient.
    """
    if not re.search(r"差额|差异|差距|拉开|这两个数|两者|\bgap\b|\bdifference\b|\bbridge\b", question, re.I):
        return []
    owners = {}
    for claim in proven.values():
        if claim["status"] != "Supported":
            continue
        assertion = assertion_fields(claim)
        val = _number(assertion.get("asserted_value"))
        if val is None:
            continue
        refs = claim.get("calculation_ids", []) or claim.get("evidence_ids", [])
        candidates = [r for r in refs if r in records and val == _number(records[r].get("value"))
                      and assertion.get("asserted_unit") == records[r].get("unit")]
        if len(candidates) > 1:
            metric = (claim.get("constraints") or {}).get("metric")
            candidates = [r for r in candidates if metric and records[r].get("metric") == metric]
        if len(candidates) == 1:
            owners.setdefault(candidates[0], claim)

    anchors = [r for r in primary.get("calculation_ids", []) if owners.get(r) == primary and records[r].get("operation") == "sum"]
    if len(anchors) != 1:
        return []
    anchor = records[anchors[0]]
    def good(ref):
        row = records.get(ref,{})
        return (row.get("entity") == anchor.get("entity") and row.get("unit") == anchor.get("unit")
                and _number(row.get("value")) is not None)

    def operands(ref, operation):
        row = records.get(ref,{})
        ids = row.get("operand_ids",[])
        return ids if good(ref) and row.get("operation") == operation and ids and all(good(r) for r in ids) else []

    matches = []
    for check in owners:
        endpoints = operands(check, "difference")
        if len(endpoints) != 2 or records[check]["value"] != 0:
            continue
        actual, total = endpoints
        terms = operands(total, "sum")
        if anchors[0] not in terms:
            continue
        for net in owners:
            diff = operands(net, "difference")
            if len(diff) != 2 or diff[0] != actual or diff[1] not in terms:
                continue
            base = diff[1]
            groups = [r for r in terms if r != base]
            if len(groups) < 2 or not all(r in owners and allowed(owners[r]) and operands(r,"sum") for r in groups):
                continue
            leaves = [r for group in groups for r in operands(group,"sum")]
            period = (records[actual].get("period_start"),records[actual].get("period_end"))
            if (not all(period) or len(set(leaves)) != len(leaves) or len(set(terms)) != len(terms)
                    or any((records[r].get("period_start"),records[r].get("period_end")) != period
                           for r in [base,*leaves])):
                continue
            def value(ref):
                return _number(records[ref]["value"])
            if (any(sum(value(r) for r in operands(g,"sum")) != value(g) for g in groups)
                    or sum(value(g) for g in groups) != value(net)
                    or value(actual) - value(base) != value(net) or value(total) != value(actual)):
                continue
            matches.append((groups,net,check))
    if len(matches) != 1:
        return []
    groups, net, check = matches[0]
    groups = [anchors[0], *(g for g in groups if g != anchors[0])]
    pieces = []
    for g in groups:
        if g != anchors[0]:
            pieces.append(([owners[g]["id"]],owners[g]["text"],True))
        # Give the largest reviewed positive and negative contributors, when available.
        members = sorted((r for r in operands(g,"sum") if r in owners and allowed(owners[r])),
                         key=lambda r:-abs(records[r]["value"]))
        signs = set()
        for ref in members:
            sign = records[ref]["value"] > 0
            if sign in signs or not records[ref]["value"]:
                continue
            signs.add(sign)
            pieces.append(([owners[ref]["id"]],owners[ref]["text"],False))
    pieces.append(([owners[net]["id"]],owners[net]["text"],True))
    values = [_number(records[g]["value"]) for g in groups]
    equation = _format(values[0]) + "".join((" − " if v < 0 else " + ")+_format(abs(v)) for v in values[1:])
    delta = _number(records[net]["value"])
    unit = records[net]["unit"]
    result = _format(delta)
    if unit == "USD thousands":
        result += " 千美元，即 " + _format(delta/10) + " 万美元"
    else:
        result += " "+unit
    pieces.append(([*(owners[g]["id"] for g in groups),owners[net]["id"],owners[check]["id"]],
                   f"按原表未取整金额，{equation} = {result}。",False))
    return pieces


def expand_answers(report, answers):
    if not report.get("verification_available"):
        return answers
    versions = report.get("record_versions",{})
    def current(row, kind):
        expected = versions.get(kind)
        return not row.get("stale") and (expected is None or expected.get(row["id"]) == row.get("version",1))
    records = {r["id"]:r for key,kind in (("evidence","evidence"),("calculations","calculation"))
               for r in report.get(key,[]) if current(r,kind)}
    backed_refs = set()
    def backed(ref, path=()):
        if ref in backed_refs:
            return True
        record = records.get(ref)
        if not record or ref in path or len(path) >= 64:
            return False
        versions = record.get("operand_versions",{})
        for operand in record.get("operand_ids",[]):
            if not backed(operand, (*path,ref)) or (operand in versions and
                    versions[operand] != records[operand].get("version",1)):
                return False
        backed_refs.add(ref)
        return True
    proven = {}
    for c in report.get("claims",[]):
        verdict = c.get("critic_verdict") or {}
        if (c.get("status") not in ("Supported","Partially Supported") or verdict.get("status") != c["status"]
                or verdict.get("claim_id") != c["id"] or not current(c,"claim")
                or c.get("scope_version", report.get("scope_version")) != report.get("scope_version")
                or not verdict.get("evidence_refs") or not all(backed(r) for r in verdict["evidence_refs"])):
            continue
        if any(not backed(r) or (r in c.get(version_key,{}) and
               c[version_key][r] != records[r].get("version",1))
               for refs,version_key in (("evidence_ids","evidence_versions"),("calculation_ids","calculation_versions"))
               for r in c.get(refs,[])):
            continue
        proven[c["id"]] = c
    requirements = {r["id"]:_wording(r["text"]) for r in report.get("scope",{}).get("requirements",[])}
    questions = {_wording(a["question"]) for a in answers}
    for row in answers:
        primary = proven.get(row.get("claim_id"))
        question = _wording(row["question"])
        def specific(c):
            return {requirements.get(i) for i in c.get("requirement_ids",[])} & questions
        def allowed(c):
            return not specific(c) or question in specific(c)
        parts = []
        def append(ids,text,new_paragraph=False):
            if text and all(text != p["text"] for p in parts):
                parts.append({"claim_ids":ids,"text":text})
                if new_paragraph:
                    parts[-1]["new_paragraph"] = True
        def quote(c):
            if c["status"] == "Supported":
                return c["text"]
            excerpt = c["critic_verdict"].get("supported_text")
            return excerpt if excerpt and excerpt in c["text"] else ""
        if row.get("answer_sentence") and not primary:
            continue
        if primary and row.get("answer_sentence"):
            anchor = row["answer_sentence"]
            full = quote(primary)
            # The review can link the same claim to two parts under one broad
            # checklist item. Its own explicit supporting link applies to this part.
            linked = primary["id"] in row.get("supporting_claim_ids", []) and allowed(primary)
            expand = specific(primary) == {question} or linked
            append([primary["id"]],full if expand and anchor in full else anchor)
        for cid in row.get("supporting_claim_ids",[]):
            if cid != row.get("claim_id") and (c := proven.get(cid)) and allowed(c):
                append([cid],quote(c))
        if row["status"] == "answered" and primary:
            for ids,text,start in _bridge_details(row["question"],primary,proven,records,allowed):
                append(ids,text,start)
        if row["status"] == "gap" and not parts:
            for c in proven.values():
                if specific(c) == {question} and c["status"] == "Partially Supported":
                    append([c["id"]],quote(c))
        if parts and (len(parts)>1 or parts[0]["text"] != row.get("answer_sentence")):
            row["answer_fragments"] = parts
            row["answer_text"] = ("已核实的相关事实：" if row["status"] == "gap" else "") + "".join(
                ("" if i==0 else "\n\n" if p.get("new_paragraph") else " ") + p["text"] for i,p in enumerate(parts))
    return answers
