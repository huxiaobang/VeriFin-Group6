"""Resolve and enforce the user's source boundaries for retrieval and evidence."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


_EN_DOC = (r"(?:press release|earnings release|news release|release|filing|10-[qk]|8-k|"
           r"transcript|earnings call|report|document|announcement|statement|source|passage|table|page)s?\b")
_ZH_DOC = r"(?:新闻稿|新聞稿|[业業][绩績]公告|业绩分析页|公告|財報|财报|年报|季报|报告|文件|资料|來源|来源|电话会|逐字稿|原文|表格|页面)"
_EN_REF = r"(?:this|that|the (?:above|previous|same|attached|provided))\s+"
_ZH_REF = r"(?:这|這|那|该|該|上述|上面|前面|同一)(?:一)?(?:份|篇|[个個]|[张張]|段)?"
_DEICTIC = re.compile(r"\b" + _EN_REF + _EN_DOC + "|" + _ZH_REF + _ZH_DOC, re.I)
_SEC = r"(?<![A-Za-z0-9])(?:SEC|EDGAR)(?![A-Za-z0-9])"
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
    r"[^，。；！？,;.!?\n]{0,45}?(?:" + _EN_DOC + "|" + _ZH_DOC + "|" + _SEC + ")", re.I)
_EXCLUDE_SEC = re.compile(
    rf"(?:\b(?:do not|don't|never)\s+(?:search|check|use|cite|consult|query|look (?:at|up))\s+(?:the\s+)?"
    rf"|(?:不要|不得|不必|不可|別|别|禁止|排除)(?:(?:再)?(?:[查搜看用]|使用|依[据據]|引[用證]|"
    rf"查[询詢阅閱]|[检檢]索))?\s*){_SEC}", re.I)
_EXCLUSIVE = re.compile(
    r"\b(?:(?:only|solely|exclusively)\s+(?:(?:use|consult|read|cite|check|from|rely on)\s+)?"
    r"|(?:use|consult|read|cite|from)\s+(?:only|solely|exclusively)\s+)"
    r"|(?:只|[仅僅])(?:限[于於]?|(?:能|可)?(?:使用|用|看|查|搜|参考|參考|依[据據]|根[据據]|从|從|採用))\s*", re.I)
_EXCLUSIVE_TAIL = re.compile(r"(?:作[为為]|[为為])(?:本次|本輪|本轮)?唯一[来來]源"
                             r"|\b(?:use|read|consult|cite)\b.+\b(?:only|alone|exclusively)\s*\.?\s*$",re.I)
_ISSUER_SITE = re.compile(r"investor[- ]relations|(?:company|issuer)(?:'s)? (?:official )?(?:site|website)|"
                          r"投[资資](?:人|者)(?:关系|關係)|官[网網]|官方(?:网站|網站)|"
                          r"公司(?:网站|網站)", re.I)
_RELEASE = re.compile(r"\b(?:earnings|press|news)[-_/ ]+releases?\b|\bfinancial[-_/ ]+results\b|"
                      r"[业業][绩績]公告|[财財][报報]公告|新[闻聞]稿", re.I)
LATEST_COVERAGE_GAP = "当前允许使用的资料中没有覆盖截止日的发行方业绩公告目录，无法核实最新已公布财季。历史公告只能证明其所载财季。"


@dataclass(frozen=True)
class SourceConstraint:
    publisher: str | None = None
    release_only: bool = False
    reference: str = ""
    limited: bool = False

    @property
    def excludes_sec(self):
        return self.publisher in {"issuer", "non_sec"}

    @property
    def permits_auto_sec(self):
        return not self.reference and (self.publisher == "sec" or not self.limited)


def source_constraint(request):
    text = _NEGATED.sub("", request.replace("’", "'"))
    reference = _deictic_lock(text)
    excluded = bool(_EXCLUDE_SEC.search(text))
    clauses = re.split(r"[，,。！？!?;；\n]",text)
    selected = [clause[match.end():] for clause in clauses
                if (match := _EXCLUSIVE.search(clause))]
    selected.extend(clause for clause in clauses if _EXCLUSIVE_TAIL.search(clause))
    issuer = any(_ISSUER_SITE.search(clause) or (_RELEASE.search(clause) and not re.search(_SEC,clause,re.I))
                 for clause in selected) and not reference
    sec = any(re.search(_SEC,clause,re.I) for clause in selected)
    publisher = "issuer" if issuer else "non_sec" if excluded else "sec" if sec else None
    release = issuer and any(_RELEASE.search(clause) for clause in selected)
    return SourceConstraint(publisher,release,reference,bool(publisher or reference or _LIMIT.search(text)))


def source_limited(request: str) -> bool:
    """Conservative opt-out of automatic expansion for a user-specified source."""
    return source_constraint(request).limited


def _deictic_lock(text):
    # Bind only when the same clause contains both the instruction and a
    # reference. Separate clauses can refer to distinct, unrelated documents.
    return next((match.group() for clause in re.split(r"[.!?;。！？；\n]", text)
                 if (_LIMIT.search(clause) or _EXCLUSIVE.search(clause)) and (match := _DEICTIC.search(clause))), "")


def _document_kinds(text):
    return {kind for kind, pattern in (
        ("release", r"\brelease\b|press[-_]release|news[-_]release|earnings[-_]release|新闻稿|新聞稿"),
        ("quarterly filing", r"\b10-?q\b|\bquarterly report\b|季报"),
        ("annual filing", r"\b10-?k\b|\bannual report\b|年报"),
        ("transcript", r"transcript|逐字稿|电话会"),
    ) if re.search(pattern, text, re.I)}


def _sec_url(url):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "sec.gov" or host.endswith(".sec.gov")


def _issuer_url(row, companies):
    """Match a bundled publisher to known issuer names without inferring a web search."""
    host = (urlsplit(row.get("source_url") or "").hostname or "").lower().rstrip(".").split(".")
    labels = {re.sub(r"[^a-z0-9]","",name.casefold())
              for company in companies if company["ticker"] == row.get("entity")
              for value in (company["ticker"],company.get("name"),*company.get("aliases",[])) if value
              for name in [re.sub(r"\s+(?:incorporated|inc\.?|corp\.?|corporation|limited|ltd\.?)$", "",value,flags=re.I)]}
    # Unknown suffix structures are not evidence of issuer ownership.
    return len(host) > 1 and bool(host[-2]) and host[-2] in labels


def _publisher_matches(policy, row, companies):
    url = row.get("source_url") or ""
    location = urlsplit(url)
    if location.scheme not in {"http","https"} or not location.hostname or location.username is not None:
        return False
    if policy.publisher == "sec":
        return _sec_url(url)
    if policy.publisher == "non_sec":
        return not _sec_url(url)
    return (not _sec_url(url) and _issuer_url(row,companies)
            and (not policy.release_only or bool(_RELEASE.search(" ".join(
                str(row.get(k) or "") for k in ("form","title","source_url"))))))


def bound_context(context, *, policy=None, catalog=(), companies=()):
    """Project a named document or publisher onto current citable records."""
    request = context.get("episode", {}).get("request", "")
    policy = policy or source_constraint(request)
    reference = policy.reference
    if not reference and not policy.publisher:
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
    if reference:
        docs = named if links or identifiers or named else {r["document_id"] for r in rows if r["id"] in cites}
        doc = next(iter(docs)) if len(docs) == 1 else None
        evidence = [r for r in rows if r["document_id"] == doc]
        expected = _document_kinds(reference)
        metadata = " ".join(str(r.get(k) or "") for r in evidence for k in ("form", "source_url", "title"))
        known = _document_kinds(metadata)
        if expected and known and expected.isdisjoint(known):
            doc, evidence = None, []
        if policy.publisher and not any(_publisher_matches(policy,r,companies) for r in evidence):
            doc, evidence = None, []
        documents = []
        doc_ids = [doc] if doc else []
    else:
        documents = [r for r in catalog if _publisher_matches(policy,r,companies)]
        evidence = [r for r in context.get("evidence",[]) if not r.get("stale") and _publisher_matches(policy,r,companies)]
        doc_ids = list(dict.fromkeys(r["document_id"] for r in [*documents,*evidence] if r.get("document_id")))
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
    lock = {"document_ids":doc_ids, "instruction":"Use only these sources. Clarify an unavailable or ambiguous source."}
    if policy.publisher and not reference:
        lock.update(publisher=policy.publisher, available_documents=documents)
    return current_tool_receipts({**context, "reports": reports, "source_document_scope": lock,
            "evidence": evidence, "calculations": [r for r in calculations if r["id"] in allowed],
            "tools": [t for t in context.get("tools", []) if tool_allowed(lock.get("publisher"),t["name"])]})


_FILE_TOOLS = {"search_documents", "read_document", "find_percentage_cells", "calculate"}
_SEC_TOOLS = {"financial_facts", "discover_filings", "read_filing_history", "read_filing"}


def tool_allowed(publisher, name):
    return (name in _FILE_TOOLS or (publisher and name == "discover_company")
            or (publisher == "sec" and name in _SEC_TOOLS))


def bound_decision(context, decision):
    """Constrain the actual tool call, even when the Planner omits its document filter."""
    lock = context.get("source_document_scope")
    if lock is None or decision.get("action") == "clarify":
        return decision
    publisher = lock.get("publisher")
    doc_ids = lock["document_ids"]
    if not doc_ids and not publisher:
        return {"action": "clarify", "question": "你指的是哪一份文件？请提供标题或链接，以便只在该文件中查找。"}
    if decision.get("action") != "tool":
        return decision
    tool = decision.get("tool")
    args = decision.get("arguments", {})
    if publisher == "sec" and tool in _SEC_TOOLS | {"discover_company"}:
        return decision
    if publisher and tool == "discover_company":
        limited = {**args, "source":"replay", "view":"identity"}
        return {**decision, "arguments":limited} if limited != args else decision
    if tool == "calculate":
        allowed = {r["id"] for r in context["evidence"] + context["calculations"] if not r.get("stale")}
        if args.get("operand_ids") and set(args["operand_ids"]) <= allowed:
            return decision
    elif tool == "search_documents":
        restricted = [ref for ref in args.get("document_ids",[]) if ref in doc_ids] or doc_ids[:50]
        if restricted and restricted == args.get("document_ids"):
            return decision
        if restricted:
            return {**decision, "arguments": {**args, "document_ids": restricted}}
    elif tool in {"read_document", "find_percentage_cells"} and args.get("document_id") in doc_ids:
        return decision
    if not doc_ids:
        return {"action":"clarify", "question":"现有资料没有符合来源限定的原件。请提供允许使用的原始公告或更具体的来源。"}
    # An offset from a different file cannot identify a passage in this one.
    # Re-rank passages inside the locked document rather than carrying that offset.
    search = {"query": args.get("query") or context["episode"]["request"], "document_ids": doc_ids[:50]}
    for key in ("entity", "cutoff"):
        if key in args:
            search[key] = args[key]
    return {**decision, "action": "tool", "tool": "search_documents", "arguments": search}
