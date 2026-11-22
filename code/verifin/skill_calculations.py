"""Bounded financial operations for unambiguous, in-scope follow-ups, using the public tools."""
from collections import defaultdict
from datetime import date, timedelta
import re


_QUARTER = re.compile(r"(20\d{2})\s*(?:年|财年|FY)?\s*(?:第\s*)?([一二三四1-4])\s*(?:财?季(?:度)?)|(?:FY\s*)?(20\d{2})\s*[- ]?Q([1-4])|Q([1-4])\s*(?:FY\s*)?(20\d{2})", re.I)
_ORDINAL = re.compile(r"(?<![上一前那这同另下])(?:第\s*)?([一二三四1-4])\s*(?:财季|季度)|\bQ([1-4])\b", re.I)
_YEAR_WINDOW = re.compile(r"20\d\d\s*(?:财年|年度|年\s*全年)|\bFY\s*20\d\d\b|"
                          r"(?:改看|改为|只看|改查)\s*(?:全年|本财年)|\bfull (?:fiscal )?year\b", re.I)
_PRIOR_CHINESE = r"(?:上(?:一[个個]?|[个個])?|前一[个個]?)(?:[财財]季(?:度)?|季(?:度)?)"
_PRIOR_QUARTER = re.compile(_PRIOR_CHINESE + r"|\b(?:last|previous|prior|preceding) (?:fiscal )?quarter\b", re.I)
_BASELINE = re.compile(r"(?:[环環]比|[较較]|比|相[对對](?:[于於])?|(?:相|[对對])比|[从從与與]|和|versus|vs\.?|from|compared? (?:with|to))"
                       r"\s*(?:the\s+)?$", re.I)
_OTHER_WINDOW = re.compile(r"(?:[12]\d{3}[-/]\d{1,2}[-/]\d{1,2}|(?:FY\s*20\d\d|20\d\d\s*(?:财年|年))"
                           r"|上半年|下半年|上年度|上一年|明年|下(?:一|个)?(?:财季|季度)|"
                           r"\b(?:next (?:fiscal )?quarter|(?:last|next) fiscal year)\b)", re.I)


_WORD_QUARTER = re.compile(
    r"\b(first|second|third|fourth)\s+(?:fiscal\s+)?quarter\s+(?:of\s+)?(?:fiscal\s+(?:year\s+)?|FY\s*)?(20\d{2})\b", re.I)
_SHARED_YEAR_JOIN = re.compile(r"\s*(?:[,，、和及与與]|\band\b)\s*(?=Q[1-4]\b)", re.I)
_BARE_QUARTER = re.compile(r"Q([1-4])\b", re.I)
_REFERENCE_BEFORE = re.compile(
    r"(?:相对于|相比于|对比|较|比|从|\bvs\.?|\bversus|\bfrom|\bcompared? (?:with|to))\s*(?:the\s+)?$", re.I)
_REFERENCE_AFTER = re.compile(r"\s*(?:相比|比较而言|为基[期准])")


def quarter_mentions(text):
    for match in _QUARTER.finditer(text):
        year = match[1] or match[3] or match[6]
        q = match[2] or match[4] or match[5]
        yield match, year + "Q" + (str("一二三四".index(q)+1) if q in "一二三四" else q)
        end = match.end()
        while join := _SHARED_YEAR_JOIN.match(text, end):
            other = _BARE_QUARTER.match(text, join.end())
            # A separate year after the quarter number is already parsed above.
            if re.match(r"\s*(?:FY\s*)?20\d{2}\b", text[other.end():], re.I):
                break
            yield other, year + "Q" + other[1]
            end = other.end()
    for match in _WORD_QUARTER.finditer(text):
        ordinal = ("first", "second", "third", "fourth").index(match[1].lower()) + 1
        yield match, f"{match[2]}Q{ordinal}"


def is_comparison_period(text, match):
    return bool(_REFERENCE_BEFORE.search(text[:match.start()]) or _REFERENCE_AFTER.match(text[match.end():]))


def target_labels(text):
    return {label for match, label in quarter_mentions(text) if not is_comparison_period(text, match)}


def quarter_label(text):
    labels = {label for _, label in quarter_mentions(text)}
    return next(iter(labels)) if len(labels) == 1 else None


def leading_quarter(text, *, question="", entity=""):
    """Read a quarter heading, optionally preceded by the question's issuer."""
    text = text.strip()
    label = quarter_label(text)
    if not label:
        return None
    match = next(iter(quarter_mentions(text)))[0]
    if is_comparison_period(text, match):
        return None
    prefix = text[:match.start()].strip().casefold()
    if not prefix or (isinstance(entity, str) and prefix == entity.casefold()):
        return label
    asked = next(iter(quarter_mentions(question)), None)
    if asked and prefix == question[:asked[0].start()].strip().casefold():
        return label
    return None


def previous_quarter(label, year_on_year=False):
    y, q = map(int, label.split("Q"))
    if year_on_year:
        return f"{y-1}Q{q}"
    return f"{y}Q{q-1}" if q > 1 else f"{y-1}Q4"


def targets_prior_quarter(text):
    return any(not _BASELINE.search(text[:match.start()]) for match in _PRIOR_QUARTER.finditer(text))


_LATEST_CHINESE = (r"(?:最新|最近)(?:一[个個期])?(?:已|剛|刚|經|经|的|公布|公佈|披露|[发發][布佈]|[报報]告|"
                   r"[财財][务務]|[业業][绩績]|公告)*[ \t]*(?:[财財]季(?:度)?|季度|[财財][报報]|[业業][绩績]公告)")
_LATEST_ENGLISH = (r"(?:latest|most recent(?:ly)?)\s+(?:(?:publicly|reported|announced|disclosed|published|released|"
                   r"fiscal)\s+)*quarter\b")
_ANCHOR_PREDECESSOR = re.compile(
    _LATEST_CHINESE + r"\s*(?:之前的|以前的|之前|以前|的)?\s*" + _PRIOR_CHINESE
    + r"|\b(?:quarter\s+(?:immediately\s+)?(?:before|preceding|previous to|prior to)\s+(?:the\s+)?"
    + _LATEST_ENGLISH + r"|" + _LATEST_ENGLISH + r"['’]s\s+(?:previous|prior|preceding)\s+quarter\b)", re.I)
_MULTI_PERIOD = re.compile(
    r"同比|[环環]比|比[较較]|[对對]比|差[额額]|去年同期|上年同期|"
    r"(?:近|前|過去|过去|最近|最新)\s*[两兩二三四五六七八九十2-9]\s*[个個]?(?:[财財]季|季度|季)|"
    r"\b(?:vs\.?|versus|compar\w*|(?:year|quarter).over.(?:year|quarter)|yoy|qoq|"
    r"(?:last|past|latest|previous|most recent)\s+(?:two|three|four|\d+)\s+(?:fiscal\s+)?quarters?|"
    r"both\s+(?:quarters?|periods?))\b", re.I)


def latest_reported_target(text):
    """Select the requested period when the latest disclosure is the reference point."""
    anchored = _ANCHOR_PREDECESSOR.search(text)
    if _MULTI_PERIOD.search(text) or any(quarter_mentions(text)):
        return "check_source"
    if anchored:
        rest = text[:anchored.start()] + " " + text[anchored.end():]
        if not (targets_prior_quarter(rest) or re.search(_LATEST_CHINESE + "|" + _LATEST_ENGLISH, rest, re.I)):
            return "previous"
    return "check_source" if anchored or targets_prior_quarter(text) else "latest"


def requested_quarter(questions, anchor=None):
    """Resolve relative turns from a fiscal-quarter label, without guessing fiscal calendar dates."""
    prior = anchor
    for text in questions:
        if _QUARTER.search(text) or _WORD_QUARTER.search(text):
            labels = target_labels(text)
            if labels:
                prior = next(iter(labels)) if len(labels) == 1 else None
        elif targets_prior_quarter(text):
            prior = previous_quarter(prior) if prior else None
        elif _YEAR_WINDOW.search(text):
            prior = None
        elif ordinals := {m[1] or m[2] for m in _ORDINAL.finditer(text)}:
            if len(ordinals) == 1 and prior:
                n = ordinals.pop()
                prior = prior.split("Q")[0]+"Q"+(str("一二三四".index(n)+1) if n in "一二三四" else n)
            else:
                prior = None
    return prior


def requested_fiscal_year(text):
    if _QUARTER.search(text):
        return None
    years = {y for match in _YEAR_WINDOW.finditer(text) for y in re.findall(r"20\d\d",match[0])}
    return int(next(iter(years))) if len(years) == 1 else None


def prepare(message, previous_questions, skills, scope, *, quarter_anchor=None, target_quarter=None):
    """Only perform pre-planner arithmetic when a follow-up keeps its issuer and horizon."""
    if len(scope.get("entities", [])) != 1 or not previous_questions or not scope.get("cutoff"):
        return None
    if re.search(r"另一家|换(?:成|一|到)|改看|改查|对比.{0,8}公司|上(?:一|个)?季(?:度)?(?:如何|的数据)|上一年|明年|instead|other company", message, re.I):
        return None
    # A new named-issuer or standalone request goes to the normal Planner for scope resolution.
    previous_target = targets_prior_quarter(message)
    if not previous_target and not re.search(r"这季|这季度|这一季|同一|该季|那一季|这家|它|this quarter|that quarter|same quarter", message, re.I):
        return None
    clauses = re.split(r"[。！？!?]", message.strip())
    if not previous_target and not any(re.match(r"\s*(?:听起来|所以|那么|这季|这家|这一季|同一|现在是同一|还是|那它|how much|this quarter|that quarter|same quarter|did (?:it|they))",
                        c, re.I) for c in clauses):
        return None
    if re.search(r"北美|美[国洲]|欧洲|中国|亚洲|分部|地区|国家|某款|车型|segment|region|domestic|international", message, re.I):
        return None  # Resolve a more specific population before using a group-wide figure.
    if re.search(r"[A-Z]{2,5}\b", message) and not re.search(r"\b"+re.escape(scope["entities"][0])+r"\b", message, re.I):
        return None
    prior = quarter_anchor or requested_quarter(previous_questions)
    current = quarter_label(message)
    if not current and _OTHER_WINDOW.search(message):
        return None
    if _QUARTER.search(message) and (not current or current != prior or previous_target):
        return None
    state = {"entity": scope["entities"][0], "steps": [], "operands": [], "calculations": []}
    if "dealer-inventory" in skills and re.search(r"库存.{0,18}(?:多少|变化|降|增)|(?:how (?:much|many)|change).{0,35}inventor|inventor.{0,20}change", message, re.I):
        if not prior:
            return None
        inventory_clause = " ".join(c for c in re.split(r"[，,。；;？！?!]", message) if re.search(r"库存|inventor", c, re.I))
        yoy = bool(re.search(r"同比|去年同[季期]|year.over.year|yoy", inventory_clause, re.I))
        if yoy and re.search(r"环比|上季末|sequential|quarter.over.quarter", message, re.I):
            return None
        target = target_quarter or (previous_quarter(prior) if previous_target else prior)
        return {**state, "method": "dealer-inventory", "quarter": target,
                "prior_quarter": previous_quarter(target, yoy)}
    if "store-cohorts" in skills and re.search(r"开业|开店|按年份|cohort|open(?:ed|ing)s?", message, re.I):
        if previous_target or not scope.get("period_end") or not scope.get("period_start"):
            return None
        return {**state, "method": "store-cohorts", "end": scope["period_end"], "start": scope["period_start"]}
    return None


def tool(name, **args):
    return {"action": "tool", "tool": name, "arguments": args, "reason": "Resolve the requested comparison from original disclosures."}


FORMULA_QUERY = re.compile(r"(?:你)?刚才(?:提到|算出|给出|说|算)?的?\s*(?:这个数|那个数|结果)?\s*"
                           r"(?P<number>[+−-]?(?:\d[\d,]*(?:\.\d+)?))?\s*"
                           r"(?:是)?怎么(?:算|计算)(?:出来)?的?[？?。\s]*")


def saved_formula(store, sid, match):
    """Resolve only a unique calculation actually cited to the user, at the same scope and record version."""
    from .context import as_of_context
    from decimal import Decimal
    ctx = store.get_context(sid, report_mode="none")
    if not ctx["report_count"]:
        return [], "当前会话还没有可回看的计算。"
    ctx["scope"] = ctx["session"]["scope"]
    previous = store.get_report(sid, ctx["report_count"])
    if previous["scope_version"] != ctx["session"]["scope_version"]:
        return [], "研究范围已经改变，需要重新核对上一答的证据和算式。"
    ctx = as_of_context(ctx)
    ids = {i for n in previous.get("skill_notes", []) + previous.get("claims", [])
           if not n.get("stale") for i in n.get("calculation_ids", [])}
    old = {c["id"]:c for c in previous["calculations"]}
    current = {r["id"]:r for r in ctx["calculations"] + ctx["evidence"] if not r.get("stale")}
    literal = match.group("number")
    target = Decimal(literal.replace(",", "").replace("−", "-")) if literal else None
    candidates = [current[i] for i in ids if i in old and i in current and
                  old[i]["version"] == current[i]["version"] and
                  (target is None or Decimal(str(current[i]["value"])) == target)]
    if len(candidates) != 1:
        return [], "上一答中未能唯一确定这项计算。请指出具体数值或指标。"
    calc = candidates[0]
    if any(i not in current or current[i]["version"] != calc["operand_versions"].get(i) for i in calc["operand_ids"]):
        return [], "算式的输入已变化或在当前时点不可用，需要重新核实。"
    inputs = [current[i] for i in calc["operand_ids"]]
    display = {"difference":"减法","percentage_point_change":"相减（百分点）","derive_quarter":"累计金额相减",
               "sum":"求和","midpoint":"两端平均","growth":"后值除以前值减一，转为百分比",
               "ratio":"相除","margin":"相除后转为百分比"}
    amounts = "，".join(f"{r.get('metric') or r['id']} {r['value']:,} {r['unit']}" for r in inputs)
    return [{"text":f"{calc['metric']}用{display.get(calc['operation'],calc['operation'])}计算。按顺序的输入为{amounts}，"
                    f"结果 {calc['value']:,} {calc['unit']}。", "calculation_ids":[calc["id"]],"evidence_ids":[]}], None


def source_request(message, earlier):
    """A bounded, local evidence lookup when the offline entry has no semantic Planner."""
    from .scope import after_cutoff_prefix
    message = after_cutoff_prefix(message)
    if (_PRIOR_QUARTER.search(message) or _QUARTER.search(message) or _ORDINAL.search(message) or _YEAR_WINDOW.search(message)
            or re.search(r"去年|上一年|上半年|(?:19|20)\d{2}年|"
                                             r"previous quarter|last year|\bQ[1-4]\b", message, re.I)):
        return None
    if re.search(r"管理层|电话会|earnings call|management", message, re.I) and re.search(
            r"解释|怎么说|怎么看|如何|why|explain|say", message, re.I):
        return "management"
    if re.search(r"(?:最近|近来|近期).{0,8}(?:表现|业绩|经营)|how.{0,35}perform", message, re.I):
        return "overview"
    return None


def source_action(state, ctx):
    steps = state["steps"]
    if not steps:
        return {**tool("discover_company", query=state["entity"], source="replay"),
                "scope":{"entities":[state["entity"]]}}
    if len(steps) == 1:
        companies = ctx["episode"]["last_tool_result"]["data"].get("companies", [])
        docs = [d for c in companies if c["entity"] == state["entity"] for d in c["replay_documents"] if d.get("period_end")]
        latest = max((d["period_end"] for d in docs), default=None)
        docs = [d for d in docs if d["period_end"] == latest]
        wants_call = state["method"] == "management"
        forms = r"earnings call|transcript|prepared remarks" if wants_call else r"earnings release|press release|8-k"
        candidates = [d for d in docs if re.search(forms, d.get("form") or "", re.I)]
        if not candidates:
            state["gap"] = ("到该研究时点，本地没有可确认已公开的同期电话会原文，管理层归因待核。"
                            if wants_call else "截至研究时点，本地没有可定位到报告期的业绩公告；需要补充发行人来源。")
            return None
        # More than one eligible release or different call editions: don't arbitrarily choose the first.
        if len(candidates) > 1:
            state["gap"] = "当前期间有多份候选来源，需要进一步判断哪份回应了这个问题。"
            return None
        source = candidates[0]
        if wants_call:
            state["gap"] = "已定位同期电话会；摘录须核实发言人、提问上下文和归因范围后才能形成解释。"
            return tool("search_documents", entity=state["entity"], period_end=latest, document_ids=[source["document_id"]],
                        query="revenue growth operating performance drivers comparable sales quarter management", limit=2)
        state["gap"] = "已定位当期业绩公告。综合评价仍需选择比较指标和期间，并进行语义核验。"
        state["overview_document"] = source["document_id"]
        return tool("read_document", entity=state["entity"], document_id=source["document_id"], limit_chars=3000)
    if len(steps)==2 and state["method"]=="overview" and not ctx["episode"].get("statement_notes"):
        # An 8-K can put several pages of cover material before the release.
        offset = ctx["episode"]["last_tool_result"].get("data",{}).get("next_offset")
        if offset is not None:
            return tool("read_document",entity=state["entity"],document_id=state["overview_document"],
                        offset=offset,limit_chars=6000)
    return None


def overview_notes(evidence):
    """Quote complete sentences from the retrieved release span, with the original record attached."""
    for record in evidence:
        if record.get("kind") != "document":
            continue
        body = record.get("text", "")
        # Keep original numbered lines, including period qualifiers and abbreviations such as U.S.
        sentences = (body.splitlines() if re.search(r"^L\d+:", body, re.M)
                     else re.split(r"(?<![A-Z]\.[A-Z]\.)(?<=[.!?])\s+|\n+", body))
        chosen = []
        for sentence in sentences:
            sentence = re.sub(r"^\s*L\d+:\s*", "", sentence).strip()
            if (30 <= len(sentence) <= 600 and re.search(r"\b(?:sales|revenues?|earnings|margin)\b", sentence, re.I)
                    and re.search(r"\$\s*\d|\d[\d.,]*\s*(?:%|million\b|billion\b|basis points?\b)", sentence, re.I)
                    and sentence.endswith((".", "!", "?")) and "|" not in sentence):
                chosen.append(sentence)
            if len(chosen) == 3:
                break
        if chosen:
            return [{"text":"当期公告原文节录：" + sentence, "evidence_ids":[record["id"]],
                     "calculation_ids":[]} for sentence in chosen]
    return []


def next_action(state, ctx):
    """Choose sources and pairs by document metadata, series, measure and period; abstain on competing pairs."""
    steps = state["steps"]
    if state["method"] == "operating_margin":
        from . import operating_margin
        return operating_margin.next_action(state, ctx)
    if state["method"] == "quarter_followup":
        if not steps:
            return tool("inspect_sales_comparison", entity=state["entity"], fiscal_quarter=state["quarter"])
        return None
    if state["method"] in ("overview", "management"):
        return source_action(state, ctx)
    if state["method"] == "statement":
        if not steps:
            return {**tool("discover_company", query=state["entity"], source="replay"),
                    "scope":{"entities":[state["entity"]]}}
        if len(steps) == 1 and steps[-1]["ok"]:
            return tool("analyze_disclosures", entity=state["entity"])
        return None
    if not steps:
        return tool("discover_company", query=state["entity"], source="replay")
    if not steps[-1]["ok"] and steps[-1]["tool"] != "search_documents":
        state["gap"] = "A required source or calculation was unavailable."
        return None
    if "queries" not in state:
        last = ctx["episode"]["last_tool_result"]["data"].get("companies", [])
        matches = [c for c in last if c["entity"] == state["entity"]]
        docs = matches[0]["replay_documents"] if len(matches) == 1 else []
        queries = []
        if state["method"] == "store-cohorts":
            interim_end = (date.fromisoformat(state["start"])-timedelta(days=1)).isoformat()
            for doc in docs:
                if doc.get("period_end") in (state["end"], interim_end) and doc.get("form") in ("10-K", "10-Q"):
                    queries.append(tool("search_documents", entity=state["entity"], document_ids=[doc["document_id"]],
                        period_end=doc["period_end"], query="comparable sales revenue change opened restaurants changes from", limit=5))
        else:
            # Search each eligible presentation; use both dated endpoints from one figure.
            for doc in docs:
                if "presentation" in (doc.get("form") or "").casefold() and doc.get("period_end"):
                    queries.append(tool("search_documents", entity=state["entity"], document_ids=[doc["document_id"]],
                        period_end=doc["period_end"], query="dealer inventory units end of quarter retail sales", limit=5))
        state["queries"] = queries
    nsearch = sum(s["tool"] == "search_documents" for s in steps)
    if nsearch < len(state["queries"]):
        return state["queries"][nsearch]
    if "pairs" not in state:
        ids = set(state["operands"])
        cells = [r for r in ctx["evidence"] if r["id"] in ids and not r.get("stale") and r.get("entity") == state["entity"]]
        if state["method"] == "dealer-inventory":
            series = defaultdict(lambda: defaultdict(list))
            for c in cells:
                if c.get("cell_type") == "chart_inventory_cell" and re.search(r"\bdealer\b", c["row_label"], re.I):
                    series[c["figure_id"]][c["chart_period"]].append(c["id"])
            pairs = [(p[state["quarter"]][0], p[state["prior_quarter"]][0]) for p in series.values()
                     if len(p[state["quarter"]]) == len(p[state["prior_quarter"]]) == 1]
            if len(pairs) != 1:
                pairs = []
            op = "difference"
        else:
            from .financial import quarter_from_cumulative
            interim_end = (date.fromisoformat(state["start"])-timedelta(days=1)).isoformat()
            by_metric = defaultdict(list)
            for a in cells:
                if a.get("cell_type") != "revenue_bridge_cell" or a["period_end"] != state["end"]:
                    continue
                for b in cells:
                    if b.get("cell_type") != "revenue_bridge_cell" or b["period_end"] != interim_end or b["metric"] != a["metric"]:
                        continue
                    try:
                        quarter_from_cumulative(a, b)
                    except ValueError:
                        continue
                    by_metric[a["metric"]].append((a["id"], b["id"]))
            pairs = [v[0] for v in by_metric.values() if len(v) == 1]
            expected = {tuple(c["factor_categories"]) for c in cells if c.get("cell_type") == "revenue_bridge_cell"
                        and c["period_end"] == state["end"]}
            if len(expected) != 1 or set(next(iter(expected))) != set(by_metric) or len(pairs) != len(by_metric):
                pairs = []
            op = "derive_quarter"
        state["operation"], state["pairs"] = op, pairs
        if not pairs:
            state["gap"] = (f"缺少 {state['entity']} 从 {state['prior_quarter']} 至 {state['quarter']} 的同一库存序列两端数据，无法核算变化。"
                            if state["method"] == "dealer-inventory" else "No unique comparable set of numeric cells for the requested periods.")
    done = len(state["calculations"])
    if done < len(state["pairs"]):
        return tool("calculate", operation=state["operation"], operand_ids=list(state["pairs"][done]))
    return None


def evidence_notes(episode, ctx):
    """Plain source/amount notes for offline inspection. These never acquire a Critic verdict."""
    if "statement_notes" in episode:
        valid = {r["id"] for r in ctx["evidence"] + ctx["calculations"] if not r.get("stale")}
        return [n for n in episode["statement_notes"] if valid.issuperset(n["evidence_ids"] + n["calculation_ids"])]
    chosen = set(episode.get("skill_calculations", []))
    cells = {c["id"]: c for c in ctx["evidence"] if not c.get("stale")}
    notes = []
    for calc in ctx["calculations"]:
        if calc["id"] not in chosen or calc.get("stale") or not all(i in cells for i in calc["operand_ids"]):
            continue
        # The two-period chart and revenue notes below compare one pair.
        # Multi-cell calculations are displayed with their formula and per-question answer.
        if len(calc["operand_ids"]) != 2:
            continue
        a, b = [cells[i] for i in calc["operand_ids"]]
        if a.get("cell_type") == "chart_inventory_cell":
            text = (f"{a['row_label']}，{b['chart_period']} 至 {a['chart_period']}，"
                    f"按图上舍入读数约变化 {calc['value']:,} {calc['unit']}。")
        else:
            text = (f"{a['row_label']}，{calc['period_start']} 至 {calc['period_end']} "
                    f"的同比收入增量贡献为 {calc['value']:,} {calc['unit']}。")
        notes.append({"text": text, "calculation_ids": [calc["id"]], "evidence_ids": calc["operand_ids"]})
    if notes and "store-cohorts" in episode.get("research_skills", []):
        notes.append({"text": "以上金额按原表行的开业批次和可比范围计算。以年份划分的批次不能单独给出本季才开业门店的收入。"
                      if any(re.search(r"opened in (?:19|20)\d\d", cells[i]["row_label"], re.I)
                             for n in notes for i in n["evidence_ids"]) else "请按原表的分类解释各项贡献。",
                      "evidence_ids": [i for n in notes for i in n["evidence_ids"]], "calculation_ids": []})
    if (notes and "dealer-inventory" in episode.get("research_skills", []) and ctx["reports"]
            and re.search(r"出货|零售|终端|买|wholesale|retail|consumer", episode["request"], re.I)):
        prior = ctx["reports"][-1]
        scope = ctx["scope"]
        if prior.get("verification_available") and all(prior["scope"].get(k) == scope.get(k)
                for k in ("entities", "period_start", "period_end", "cutoff")):
            for claim in prior.get("claims", []):
                refs = claim.get("evidence_ids", [])
                if (claim.get("status") == "Supported" and not claim.get("stale") and not claim.get("calculation_ids")
                        and refs and all(i in cells and cells[i]["version"] == claim.get("evidence_versions", {}).get(i) for i in refs)
                        and re.search(r"同比|year.over.year", claim["text"], re.I)
                        and re.search(r"批发出货|终端零售|wholesale shipments|retail sales", claim["text"], re.I)):
                    notes.append({"text": "前轮同口径已核验记录："+claim["text"], "evidence_ids": refs, "calculation_ids": []})
    return notes
