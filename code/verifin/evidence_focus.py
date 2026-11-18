"""Request-local filing views. Source records and tools always retain the originals."""
import re


def rx(s):
    return re.compile(s, re.I)


CASH = rx(r"现金|现付|实付|款项支付|\bcash\b|\bactually paid\b")
TAX = rx(r"税|\btax(?:es|ation)?\b|valuation allowance")
DEAL = rx(r"收购|并购|整合|acqui[rs]|merger|integration|takeover")
RISK = rx(r"风险|不确定|隐患|下行|risk|uncertain|downside|could go wrong")
BROAD = rx(r"整体|全面|综合表现|overall performance|full overview")
RETIRED = rx(r"^(?:(?:先|暂时?|这次|现在)?(?:不(?:再)?(?:看|谈|讨论|考虑|关注|问)|别(?:看|谈|管))|(?:set aside|ignore|leave aside)\b)")
# If a second identifiable subject has no filing vocabulary here, an excerpt
# selected for the supported part could silently drop the other half of the question.
OTHER_SUBJECT = rx(r"养老金|存货|营业日|客户需求|同店|销售增长|营业利润率|毛利率|库存|裁员|"
                   r"pensions?|inventory|working days|customer demand|same.store|sales growth|operating margin|gross margin|layoffs?")
CASH_TEXT = rx(r"operating cash flow|cash (?:from operations|paid|payments?|outflows?|used|provided|acquired|flow statement|consideration|on hand)|cash.{0,20}from operating activities|"
               r"statements? of cash flows|payments? (?:for|of|to)|(?:paid|payable|accrued|prepaid|expensed as incurred).{0,45}(?:cost|fee|expense|liabilit)|"
               r"acquisition[s-]*related (?:cost|expense)|acquisition and integration.related|acquisition (?:costs?|price)|"
               r"(?:incur(?:red)?|accrued).{0,110}(?:fees|expenses|costs)|(?:costs|fees|expenses).{0,55}(?:paid|payment|non.cash)|"
               r"transaction (?:fees?|costs?|expenses?|was funded)|purchase price|financing fees?|debt issuance costs|net of cash acquired|"
               r"(?:fund|funded|funding|financ\w*) (?:the |this )?(?:transaction|acquisition)|收购.{0,16}(?:费用|对价)|现金流|实际支付|应计费用")
TAX_TEXT = rx(r"income tax(?:es| expense| benefit| provision)|(?:benefit|provision) for (?:income )?tax|deferred tax|valuation allowance|"
              r"tax (?:rate|returns?|effect|credits?|assets?|liabilit|benefit|expense|recognition|status)|"
              r"net (?:income|earnings) (?:available|attributable) to|dividends (?:paid )?on preferred|税项|所得税|税率|估值备抵")
RISK_TEXT = rx(r"synerg|integrat(?:e|i|ion)|increased indebtedness|acquisition (?:of|price|fees|financ|could|may)|"
               r"(?:acquisition|acquired|transaction).{0,90}(?:risk|benefit|expect|completed|closed|funded|borrowing)|"
               r"(?:risk|benefit|completed|closed|fund|funded|funding|financ\w*) (?:of |from )?(?:the |this )?(?:acquisition|transaction)|"
               r"purchase price|credit agreement|purchase accounting|收购风险|协同|整合")
TABLE = rx(r"\b(?:three|six|nine|twelve) months (?:ended|ending)|\byears? ended|\b(?:in|dollars in) (?:thousands|millions)|"
           r"\b(?:19|20)\d{2}\b[^\w]{1,12}\b(?:19|20)\d{2}\b|\b(?:consolidated statements? of|see (?:note|footnote))|"
           r"\(unaudited\)|\(\d+\)\s*[A-Za-z]|截至.{0,14}(?:季度|年度)")


def _topics(request):
    if not isinstance(request, str):
        return []
    active = ' '.join(p.strip() for p in re.split(r"[。；;！？!?，,\n]", request) if not RETIRED.search(p.strip()))
    if BROAD.search(active) or OTHER_SUBJECT.search(active):
        return []
    return [pattern for yes, pattern in ((CASH.search(active), CASH_TEXT), (TAX.search(active), TAX_TEXT),
                (DEAL.search(active) and RISK.search(active), RISK_TEXT)) if yes]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _strings(item)


def focused_evidence(context, role):
    """Build a view afresh from this episode's authoritative, unprojected evidence."""
    episode = context.get("episode") or {}
    queries = _topics(episode.get("request"))
    rows = context.get("evidence")
    if (not queries or not isinstance(rows, list)
            or not any(t.get("name") == "read_document" for t in context.get("tools", []) if isinstance(t, dict))):
        return context
    if not all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rows):
        return context
    windows = [r for r in rows if r.get("kind") == "document" and not r.get("stale") and r.get("document_id")
               and r.get("entity") and type(r.get("start")) is int and type(r.get("end")) is int and r["start"] >= 0
               and isinstance(r.get("text"), str) and len(r["text"]) == r["end"] - r["start"]]
    ids = {r["id"] for r in windows}
    if len({r["id"] for r in rows}) != len(rows):
        return context
    keep = set()
    for pattern in queries:
        matches = {r["id"] for r in windows if pattern.search(r["text"])}
        if not matches:
            return context
        keep.update(matches)
    # Keep the current claims, objections, and underlying operands; prior report
    # citations do not implicitly become active claims when the user changes focus.
    for name in ("claims", "issues", "calculations", "income_bridge", "income_explanation", "followup_focus",
                 "rule_findings", "source_context_notes", "display_review_notes", "repair_feedback"):
        keep.update(ids.intersection(_strings(context.get(name))))
    for name in ("issues", "skill_notes", "statement_notes", "skill_assessments"):
        keep.update(ids.intersection(_strings(episode.get(name))))
    keep.update(ids.intersection(episode.get("opened_document_ids", [])))
    for receipt in episode.get("recent_tool_results", []):
        if receipt.get("tool") == "read_document" and receipt.get("ok"):
            keep.update(receipt.get("evidence_ids", []))
    linked = {}
    for r in windows:
        if r["id"] in keep:
            linked.setdefault((r["document_id"], r.get("text_snapshot_hash")), []).append((r["start"], r["end"]))
    projected = []
    for row in rows:
        if (row["id"] not in ids or row["id"] in keep or len(row["text"]) < 500 or
                (TABLE.search(row["text"]) and any(lo - 320 <= row["end"] and row["start"] <= hi + 320
                 for lo, hi in linked.get((row["document_id"], row.get("text_snapshot_hash")), [])))):
            projected.append(row)
        else:
            projected.append({**{k:row[k] for k in ("id","kind","entity","document_id","form","start","end","snapshot_hash",
                              "text_snapshot_hash","document_report_date") if k in row}, "deferred":True})
    if all(a is b for a,b in zip(rows, projected)):
        return context
    # A pointer in the latest search receipt must not promise a full row after
    # that row became an index entry. A nonidentical historical receipt stays intact.
    deferred = {r["id"] for r in projected if r.get("deferred")}
    last = episode.get("last_tool_result")
    if isinstance(last, dict) and isinstance(last.get("evidence"), list):
        items = [({"deferred":True, "id":item["id"], "locator_in":"evidence"}
                  if isinstance(item, dict) and item.get("same_record_in") == "evidence" and item.get("id") in deferred
                  else item) for item in last["evidence"]]
        episode = {**episode, "last_tool_result":{**last,"evidence":items}}
    return {**context, "episode":episode, "evidence":projected, "evidence_focus":{
        "deferred_count":sum(r.get("deferred") is True for r in projected),
        "access":"Deferred IDs are locators only, not evidence for or against any claim. Planner can read_document "
                 "at document_id and offset=start (continue to end). If needed, request another read before deciding. "
                 "An omitted passage is not a negative finding. Source windows, headers and notes may continue outside the retrieved range."}}
