"""Locate direct causal passages and broaden a Planner-imposed single-file search once."""
import re

from .metric_language import NAME, NAMES, metric_mentions
from .source_scope import source_limited


_CAUSE_QUESTION = re.compile(r"\b(?:why|reason(?:s)?|drivers?|dr[oi]ve|explain(?:ed|s)?|attribute(?:d)?|account(?:ed|s)? for)\b|为什么|为何|原因|驱动|归因|如何解释", re.I)
_CAUSE_LINK = re.compile(r"\b(?:driven by|due to|because|attributable to|reflect(?:ed|ing)|as a result of|result(?:ed|ing) from|primarily from)\b|主要由于|受.+?推动|由于", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")
_BASIS = re.compile(r"^(?:(?:GAAP|non-GAAP|adjusted|consolidated|total|reported|our)(?:\s+and\s+|\s+))*", re.I)
_CHANGES = re.compile(r"\b(?:was|were|is|are|increased?|decreased?|grew|rose|fell|declined?|improved|expanded|contracted|growth|change)\b|增长|下降|变动", re.I)
_QUARTER = re.compile(r"\b(?:Q[1-4]|quarter(?:ly)?)\b|季度", re.I)
_YEAR = re.compile(r"\b(?:annual|full[- ]year|year[- ]to[- ]date|(?:six|nine|twelve) months|fiscal\s+(?:year|20\d\d))\b|全年|年初至今", re.I)
_PROJECTED = re.compile(r"\b(?:would|may|could|might|will|forecast|guidance|expected to|not|never)\b|预计|预期|并非", re.I)
_QUARTER_NUMBER = re.compile(r"\bQ([1-4])\b|\b(first|second|third|fourth) quarter\b|第([一二三四])季度", re.I)


def _quarters(text):
    ordinal = {"first":"1", "second":"2", "third":"3", "fourth":"4", "一":"1", "二":"2", "三":"3", "四":"4"}
    return {digit or ordinal[(word or zh).lower()] for digit, word, zh in _QUARTER_NUMBER.findall(text)}


def requested_metric(question):
    clauses = [p for p in _SENTENCES.split(question) if _CAUSE_QUESTION.search(p)]
    metrics = set().union(*(metric_mentions(p) for p in clauses))
    if clauses and not metrics:
        metrics = metric_mentions(question)
    return next(iter(metrics)) if len(metrics) == 1 else None


def _candidate_quote(sentence, metric, request):
    # Text extracted from PDFs can put a heading above a sentence and insert
    # soft line breaks inside it. Preserve the exact contiguous source span.
    lines = sentence.strip().splitlines(keepends=True)
    for i in range(len(lines)):
        quote = "".join(lines[i:]).strip()
        if len(quote) > 900 or not quote.endswith((".", "。")):
            continue
        normalized = re.sub(r"\bmargins\b", "margin", quote, flags=re.I)
        normalized = normalized[_BASIS.match(normalized).end():]
        subject = NAME.match(normalized)
        if not subject or NAMES[subject[0].casefold()] != metric:
            continue
        link = _CAUSE_LINK.search(normalized, subject.end())
        if (not link or not _CHANGES.search(normalized, subject.end(), link.start())
                or _PROJECTED.search(normalized[:link.end()])):
            continue
        if metric_mentions(normalized[:link.start()]) != {metric}:
            continue
        if _QUARTER.search(request) and _YEAR.search(quote) and not _QUARTER.search(quote):
            continue
        requested, mentioned = _quarters(request), _quarters(quote)
        if requested and mentioned and requested.isdisjoint(mentioned):
            continue
        return quote
    return None


def _current_search(receipt, ctx):
    # The Planner may first register the checklist at draft time. That bumps
    # the scope version even when the actual retrieval scope is unchanged.
    return (receipt.get("tool") == "search_documents" and receipt.get("data", {}).get("filters")
            and (receipt.get("scope_version") == ctx["session"]["scope_version"]
                 or receipt.get("search_scope") == {k:v for k,v in ctx["scope"].items() if k != "requirements"}))


def evidence_context(ctx):
    episode = ctx["episode"]
    metric = requested_metric(episode["request"])
    if not metric:
        return None
    searches = [r for r in episode.get("recent_tool_results", []) if _current_search(r, ctx)]
    if not searches:
        return None
    # Read only the passages this turn actually retrieved. A filing's headline,
    # a table of segment amounts or a different metric's driver cannot establish attribution.
    last = searches[-1]["data"]
    end = last["filters"].get("period_end") or last.get("single_document_period_end")
    entity = last["filters"].get("entity")
    if not entity or not end:
        return None
    refs = {ref for receipt in searches for ref in receipt.get("evidence_ids", [])}
    candidates = []
    seen = set()
    for row in ctx["evidence"]:
        if (row["id"] not in refs or row.get("kind") != "document" or row.get("stale")
                or row.get("entity") != entity or row.get("document_report_date") != end):
            continue
        for sentence in _SENTENCES.split(row["text"]):
            quote = _candidate_quote(sentence, metric, episode["request"])
            if not quote or (row["document_id"], quote) in seen:
                continue
            seen.add((row["document_id"], quote))
            candidates.append({"evidence_id":row["id"], "quote":quote})
            if len(candidates) >= 4:
                break
        if len(candidates) >= 4:
            break
    return {"entity":entity, "period_end":end, "metric":metric, "candidates":candidates}


def next_search(ctx, decision):
    """Return an admissible same-period query; the engine records it as a normal tool call."""
    episode = ctx["episode"]
    info = ctx.get("issuer_attribution")
    if (not info or info["candidates"] or source_limited(episode["request"])
            or episode.get("attribution_search_expanded")):
        return None
    history = episode.get("recent_tool_results", [])
    last = history[-1] if history else {}
    if not _current_search(last, ctx):
        return None
    data = last.get("data", {})
    other_sources = data.get("same_period_search")
    previous = last.get("arguments", {})
    if not other_sources or not previous.get("document_ids"):
        return None
    if decision.get("action") == "research":
        args = previous
    elif decision.get("action") == "tool" and decision.get("tool") == "search_documents":
        args = decision["arguments"]
        if any(args.get(key) != previous.get(key) for key in ("entity", "cutoff", "period_end", "forms", "document_ids")):
            return None
    else:
        return None
    # Bind the expanded query to the admissible document set even if two filings
    # share their end date but cover different periods (quarter versus full year).
    return {**{k:v for k,v in args.items() if k != "document_ids"}, **other_sources}
