"""Extract issuer wording for an offline follow-up anchored to a previously cited disclosure."""
import re
from . import issuer_attribution, skill_calculations
from .source_scope import source_limited


def _fiscal_labels(text):
    normalized = re.sub(r"\bfiscal (20\d\d) (first|second|third|fourth) quarter\b",
                        r"\2 quarter of fiscal \1", text, flags=re.I)
    return skill_calculations.target_labels(normalized)


def _citation_request(question):
    clauses = [s.strip() for s in re.split(r"[.!?。！？]", question) if s.strip()]
    if not clauses or not all(source_limited(s) for s in clauses[1:]):
        return False
    citation, *extra = re.split(r"\s+and\s+", clauses[0], flags=re.I)
    return (bool(re.fullmatch(r"(?:please )?(?:cite|quote|identify) (?:the |a )?(?:relevant |supporting )?"
                              r"(?:passage|source|statement)s?", citation, re.I))
            and all(re.fullmatch(r"(?:separate|distinguish) .+ (?:from|versus) .*(?:inference|interpretation)",
                                 task, re.I) for task in extra))


def initial_search(ctx, prior):
    question = ctx["episode"]["request"]
    metric = issuer_attribution.requested_metric(question)
    scope = prior.get("scope", {})
    entities = ctx["scope"].get("entities", [])
    if (not metric or len(entities) != 1 or entities != scope.get("entities") or not scope.get("period_end")
            or any(ctx["scope"].get(k) and ctx["scope"][k] != scope.get(k) for k in ("period_start","period_end"))
            or not ctx["scope"].get("cutoff") or skill_calculations.targets_prior_quarter(question)
            or skill_calculations.requested_fiscal_year(question)):
        return None
    if (re.search(r"\b(?:next quarter|full year|annual|year ended)\b|下一季|全年|上半年|下半年", question, re.I)
            or set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", question)) - {scope.get("period_start"), scope["period_end"]}):
        return None
    # An explicitly named target must occur in a supported claim about that period.
    claims = [c for c in prior.get("claims", []) if not c.get("stale") and c.get("status") == "Supported"
              and c.get("constraints", {}).get("period_end") == scope["period_end"]]
    labels = skill_calculations.target_labels(question)
    if re.search(r"\bQ[1-4]\b", question, re.I) and not labels:
        return None
    if labels and not any(_fiscal_labels(c["text"]) == labels for c in claims):
        return None
    cites = {(ref, c.get("evidence_versions", {}).get(ref)) for c in claims for ref in c.get("evidence_ids", [])}
    docs = {r["document_id"] for r in ctx["evidence"] if (r["id"], r.get("version")) in cites and r.get("kind") == "document"
            and not r.get("stale") and r.get("document_report_date") == scope["period_end"]
            and r.get("entity") == entities[0] and r.get("document_id")}
    if len(docs) != 1 or (source_limited(question) and set(ctx.get("source_document_scope", {}).get("document_ids", [])) != docs):
        return None
    return {"entity": entities[0], "period_end": scope["period_end"], "document_ids": sorted(docs),
            "query": metric.replace("_", " ") + " increased decreased change drivers reasons"}


def findings(ctx):
    info = ctx.get("issuer_attribution") or {}
    candidates = info.get("candidates", [])
    sources = {r["id"]: r for r in ctx["evidence"] if not r.get("stale")}
    rows = [row for row in candidates if row["evidence_id"] in sources
            and row["quote"] in sources[row["evidence_id"]].get("text", "")]
    base = {"quarter": ctx["episode"].get("skill_fiscal_quarter", {}).get("quarter")}
    if rows:
        quotes = list(dict.fromkeys(row["quote"] for row in rows))
        result = {**base, "status": "source_excerpt", "answer_sentence": "公司原文：" + " ".join(quotes),
                  "evidence_ids": list(dict.fromkeys(row["evidence_id"] for row in rows)), "calculation_ids": []}
    else:
        result = {**base, "status": "gap", "missing_support": "本轮检索到的段落中未定位到公司对该指标变动的直接归因。"}
    # Only attach the extract to the causal question. A separate synthesis,
    # calculation or comparison is still outstanding in tools-only mode.
    answers = {}
    for key, question in (ctx.get("request_parts") or {"part-1":ctx["episode"]["request"]}).items():
        if info.get("metric") and issuer_attribution.requested_metric(question) == info["metric"]:
            answers[key] = result
        elif _citation_request(question):
            if rows:
                refs = list(dict.fromkeys(sources[row["evidence_id"]]["source_url"] for row in rows))
                answers[key] = {**result, "answer_sentence": "上述归因来自公司披露。出处：" + "、".join(refs)}
            else:
                answers[key] = result
    return answers
