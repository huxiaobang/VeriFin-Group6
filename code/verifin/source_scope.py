"""Keep an issuer follow-up inside a document the user specifically named."""
import re


_EN_DOC = (r"(?:press release|earnings release|news release|release|filing|10-[qk]|8-k|"
           r"transcript|earnings call|report|document|announcement|statement|source|passage|table|page)s?\b")
_ZH_DOC = r"(?:新闻稿|新聞稿|业绩公告|业绩分析页|公告|財報|财报|年报|季报|报告|文件|资料|來源|来源|电话会|逐字稿|原文|表格|页面)"
_EN_REF = r"(?:this|that|the (?:above|previous|same|attached|provided))\s+"
_ZH_REF = r"(?:这|這|那|该|該|上述|上面|前面|同一)(?:一)?(?:份|篇|个|张|段)?"
_DEICTIC = re.compile(r"\b" + _EN_REF + _EN_DOC + "|" + _ZH_REF + _ZH_DOC, re.I)
# Match restrictions on sources, not "current-quarter numbers only" or a request to cite a passage.
_LIMIT = re.compile(
    r"\b(?:only|just|solely|exclusively)\s+(?:(?:use|read|consider|consult|cite|reference|look at|rely on|"
    r"based on|according to|from|in)\s+)?(?:[\w’-]+\s+){0,4}" + _EN_DOC
    + r"|\b(?:use|read|consult|consider|from)\s+(?:[\w’-]+\s+){0,4}" + _EN_DOC + r"\s+(?:alone|only|exclusively)\b"
    + r"|\b(?:according to|based on|limited to|restrict(?:ed)? to|stick to|within)\s+(?:[\w’-]+\s+){0,4}" + _EN_DOC
    + r"|(?:仅(?:限于|用|看|从|参考|依据|根据|按|以|限|凭)|只(?:能)?(?:用|看|从|参考|依据|根据|按|以|凭)|根据|依据|限于|按照)"
      r"[^，。；！？\n]{0,12}" + _ZH_DOC
    + r"|\b(?:do not|don't|never) (?:consult|search|use|look)(?:\s+\w+){0,2}\s+(?:outside|beyond|other sources)\b"
      r"|(?:不要|不得|别)(?:再)?(?:查|看|用|搜|引用)(?:其[他它]|别的|其他来源)", re.I)
_NEGATED = re.compile(
    r"(?:\bnot\s+(?:only|just|solely|exclusively|limited to)|\b(?:do not|don't)\s+"
    r"(?:(?:limit|restrict)\s+(?:(?:yourself|the search)\s+)?to|(?:only|just)\s+(?:use|read|look at))|"
    r"(?:不(?:要|用|必)?|别|无需)(?:仅(?:仅)?|只|局限于|限于))"
    r"[^，。；！？,;.!?\n]{0,45}?(?:" + _EN_DOC + "|" + _ZH_DOC + ")", re.I)


def source_limited(request: str) -> bool:
    """Conservative opt-out of automatic expansion for a user-specified source."""
    return bool(_LIMIT.search(_NEGATED.sub("", request.replace("’", "'"))))


def _deictic_lock(request):
    text = _NEGATED.sub("", request.replace("’", "'"))
    # Bind only when the same clause contains both the instruction and a
    # reference. Separate clauses can refer to distinct, unrelated documents.
    return next((match.group() for clause in re.split(r"[.!?;。！？；\n]", text)
                 if _LIMIT.search(clause) and (match := _DEICTIC.search(clause))), "")


def _document_kinds(text):
    return {kind for kind, pattern in (
        ("release", r"\brelease\b|press[-_]release|news[-_]release|earnings[-_]release|新闻稿|新聞稿"),
        ("quarterly filing", r"\b10-?q\b|\bquarterly report\b|季报"),
        ("annual filing", r"\b10-?k\b|\bannual report\b|年报"),
        ("transcript", r"transcript|逐字稿|电话会"),
    ) if re.search(pattern, text, re.I)}


def bound_context(context):
    """Resolve 'this release' only from a unique document cited in the preceding answer."""
    request = context.get("episode", {}).get("request", "")
    reference = _deictic_lock(request)
    if not reference:
        return context
    reports = context.get("reports", [])
    prior = reports[-1] if reports else {}
    cites = {ref for claim in prior.get("claims", []) if not claim.get("stale")
             for ref in claim.get("evidence_ids", [])}
    rows = [r for r in context.get("evidence", []) if r.get("document_id") and not r.get("stale")]
    # A clarification can identify a specific visible source even when the
    # preceding answer cited several documents. An unknown link cannot do so.
    links = {link.rstrip(".,;。；，)") for link in re.findall(r"https?://[^\s<>，。；）]+", request)}
    identifiers = set(re.findall(r"\bsource_doc_[\w-]+\b", request))
    named = {r["document_id"] for r in rows if r.get("source_url") in links
             or r["document_id"] in identifiers
             or (r.get("title") and r["title"].casefold() in request.casefold())}
    docs = named if links or identifiers or named else {r["document_id"] for r in rows if r["id"] in cites}
    doc = next(iter(docs)) if len(docs) == 1 else None
    evidence = [r for r in rows if r["document_id"] == doc]
    expected = _document_kinds(reference)
    metadata = " ".join(str(r.get(k) or "") for r in evidence for k in ("form", "source_url", "title"))
    known = _document_kinds(metadata)
    if expected and known and expected.isdisjoint(known):
        doc, evidence = None, []
    allowed = {r["id"] for r in evidence if not r.get("stale")}
    # A saved derived result is usable only when every operand is itself usable.
    calculations = context.get("calculations", [])
    while True:
        previous = len(allowed)
        allowed.update(r["id"] for r in calculations if not r.get("stale") and r.get("operand_ids")
                       and all(ref in allowed for ref in r["operand_ids"]))
        if len(allowed) == previous:
            break
    # The actual store still contains the complete immutable archive. Older
    # summaries and objections are not new-turn evidence inside a source lock.
    reports = [{k: report[k] for k in ("version", "request") if k in report} for report in reports]
    from .context import current_tool_receipts
    return current_tool_receipts({**context, "reports": reports, "source_document_scope": {"document_ids": [doc] if doc else [],
            "instruction": "Use only the identified document. Clarify an ambiguous reference before retrieval."},
            "evidence": evidence, "calculations": [r for r in calculations if r["id"] in allowed],
            "tools": [t for t in context.get("tools", []) if t["name"] in _FILE_TOOLS]})


_FILE_TOOLS = {"search_documents", "read_document", "find_percentage_cells", "calculate"}


def bound_decision(context, decision):
    """Constrain the actual tool call, even when the Planner omits its document filter."""
    lock = context.get("source_document_scope")
    if lock is None or decision.get("action") == "clarify":
        return decision
    if not lock["document_ids"]:
        return {"action": "clarify", "question": "你指的是哪一份文件？请提供标题或链接，以便只在该文件中查找。"}
    if decision.get("action") != "tool":
        return decision
    doc, = lock["document_ids"]
    tool = decision.get("tool")
    args = decision.get("arguments", {})
    if tool == "calculate":
        allowed = {r["id"] for r in context["evidence"] + context["calculations"] if not r.get("stale")}
        if args.get("operand_ids") and set(args["operand_ids"]) <= allowed:
            return decision
    elif tool == "search_documents":
        if args.get("document_ids") == [doc]:
            return decision
        return {**decision, "arguments": {**args, "document_ids": [doc]}}
    elif tool in {"read_document", "find_percentage_cells"} and args.get("document_id") == doc:
        return decision
    # An offset from a different file cannot identify a passage in this one.
    # Re-rank passages inside the locked document rather than carrying that offset.
    search = {"query": args.get("query") or context["episode"]["request"], "document_ids": [doc]}
    for key in ("entity", "cutoff"):
        if key in args:
            search[key] = args[key]
    return {**decision, "action": "tool", "tool": "search_documents", "arguments": search}
