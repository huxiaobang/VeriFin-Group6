"""Trace the parts of a compound user turn to actual sentences in the reviewed answer."""
import re
from decimal import Decimal

from .metric_language import NAME, NAMES, metric_mentions, plain_amount_metric
from .display_numbers import bare_stated_metric, _quantities
from .skill_calculations import leading_quarter, quarter_label, quarter_mentions

# Split a list governed by an explicit enumeration request. An incidental "and"
# in an ordinary question (e.g. a comparison, company name or ratio) is not a list.
_ENUMERATION = re.compile(
    r"(?:分别|逐项)(?:列出|说明|给出|报告|列示|披露|核对|回答)|"
    r"(?:列出|给出|列示)(?=[^。！？]*[、和与])|"
    r"\b(?:report|list|give|provide|state|show)\s+(?:both|each of|separately)\b", re.I)
_POSTFIX_ENUMERATION = re.compile(r"(?:分别|各自)(?:是|为)?多少[?？。.!！]*$")
_LIST_JOIN = re.compile(r"、|以及|和|与|與|\s+(?:and|as well as)\s+|,\s+(?:and\s+)?", re.I)
_COMPOUND_NAME = re.compile(
    r"现金(?:和|与|及)现金等价物|"
    r"\bcash and cash equivalents\b|\bproperty,? plant(?:,)? and equipment\b|"
    r"\bselling, general(?:,)? and administrative\b|\bresearch and development\b", re.I)
_RELATIONSHIP = re.compile(r"的(?:比率|比值|差额|差异|关系|区别)|\b(?:ratio of|difference between)\b", re.I)
_PARAPHRASE = re.compile(r"^(?:换句话说|换言之|也就是说|in other words\b|put another way\b)[\s，,:：]*", re.I)
_DRIVER_RESTATEMENT = re.compile(
    r"(?:(?:那么?|所以|这|两者|它们|具体|主要|到底|大头|究竟|差异|变动)[，,\s]*)*"
    r"(?:(?:差在|出在)(?:哪[儿里]?|什么地方)|(?:主要)?原因(?:是什么|在哪[儿里]?)|"
    r"是(?:哪几项|哪些项目|什么原因)(?:造成的|导致的)?|(?:是)?怎么回事|为什么|哪些因素|"
    r"哪些是拖累|由哪些因素造成)(?:呢|了|吗|啊)?|"
    r"(?:so )?(?:why|what (?:mainly )?(?:drove|caused|explains) (?:that|this|the (?:change|difference|gap))"
    r"|what (?:are|were) the main drivers)", re.I)
_AMOUNT_LEAD = re.compile(r"^(?:what|how much)\s+(?:were|are|was|is)\s+", re.I)
_AMOUNT_END = re.compile(r"(?:是|为)?多少$")
_PERIOD_END = re.compile(r"\s+(?:for|in|during)\s+(?:(?:the|this|last)\s+(?:quarter|year|period)|(?:FY\s*\d{4}|Q[1-4]))$", re.I)


def is_driver_restatement(text):
    """A follow-on asks for drivers of the preceding subject without naming a new one."""
    return bool(_DRIVER_RESTATEMENT.fullmatch(text.strip().rstrip("。？?！!.").strip()))


def _listed_metric(item):
    """Recognize a financial label with only simple question, owner or time qualifiers."""
    item = item.strip().rstrip("。.?？!！").strip()
    item = _AMOUNT_LEAD.sub("", item)
    item = _AMOUNT_END.sub("", item)
    item = _PERIOD_END.sub("", item).strip()
    item = re.sub(r"^(?:[A-Z][\w.-]*(?: [A-Z][\w.-]*)*['’]s\s+|(?:the|its|quarterly|annual)\s+)", "", item)
    item = re.sub(r"^(?:[\u4e00-\u9fffA-Z]{1,12}的|本季度|本季|本期|本年)", "", item)
    match = NAME.fullmatch(item)
    return NAMES[match.group().casefold()] if match else None


def _separate_amounts(sentence):
    """Measures in a sentence made entirely of independently labelled numeric levels."""
    clauses = [s for s in re.split(r"\s*(?:[;；，、]|,(?!\d)|\band\b|而|和)\s*", sentence, flags=re.I) if s]
    if len(clauses) < 2:
        return set()
    measures = {bare_stated_metric(clause) for clause in clauses}
    return measures if None not in measures else set()


def same_research_question(first, second):
    """Recognize literal repeats and conservative, explicitly signalled restatements."""
    def wording(text):
        return " ".join(_PARAPHRASE.sub("", text.strip()).casefold().split()).rstrip("？?。.")

    old, new = wording(first), wording(second)
    if old == new:
        return True
    if not _PARAPHRASE.match(second):
        return False
    def subject_and_focus(text):
        text = re.sub(r"这个季度|这一季|本季度", "本季", text)
        text = re.sub(r"营业收入", "营收", text)
        return re.sub(r"(?:这家|该|本)?公司|是多少|为多少|的", "", text)
    old_focus, new_focus = subject_and_focus(old), subject_and_focus(new)
    if old_focus == new_focus:
        return True
    # A generic company reference can repeat the named subject of a full,
    # matching time-qualified question. Keep all metric and period modifiers.
    if "公司" in new and new_focus.startswith(("本季", "上季", "本年", "去年")) and old_focus.endswith(new_focus):
        subject = old_focus[:-len(new_focus)]
        return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z. ]{1,24}",subject)
                    and not re.search(r"地区|分部|部门|集团|业务|同比|环比|季度|本季|上季|全年|剔除|并购|有机|"
                                      r"调整|预测|预期|预计|估计|实际|口径|汇率|不含|合并|单体|假设|以及|两家", subject))
    return False


def enumerated_items(text):
    compound = list(_COMPOUND_NAME.finditer(text))
    # Parenthetical definitions and quoted labels belong to the surrounding item.
    closers = {"(":")", "（":"）", "[":"]", "【":"】", "“":"”", "‘":"’", '"':'"'}
    tokens = re.finditer(r'[()（）\[\]【】“”‘’"]|' + _LIST_JOIN.pattern, text, re.I)
    stack, boundaries = [], []
    for token in tokens:
        word = token.group()
        if stack and word == stack[-1]:
            stack.pop()
        elif word in closers:
            stack.append(closers[word])
        elif not stack and _LIST_JOIN.fullmatch(word) and not any(
                span.start() <= token.start() < span.end() for span in compound):
            boundaries.append(token)
    # "A, the ratio of B and C" contains two deliverables. Preserve the
    # conjunction inside the comparison while allowing top-level list separators.
    if _RELATIONSHIP.search(text):
        boundaries = [t for t in boundaries if t.group().strip().lower() in {"、", "以及", ",", "as well as"}]
    items, start = [], 0
    for token in boundaries:
        items.append(text[start:token.start()].strip())
        start = token.end()
    return [s for s in [*items, text[start:].strip()] if s]


def request_parts(request):
    chunks = [s.strip() for s in re.findall(r"[^?？]+[?？]?", request) if s.strip()]
    if (len(chunks) > 2 and chunks[-1][-1] not in "?？;；" and chunks[-2][-1] in "?？"
            and not _ALSO.match(chunks[-1])):
        chunks[-2:] = [" ".join(chunks[-2:])]
    parts = []
    for chunk in chunks:
        postfix = _POSTFIX_ENUMERATION.search(chunk)
        if postfix:
            items = enumerated_items(chunk[:postfix.start()])
            if len(items) > 1:
                parts.extend([*items[:-1],items[-1] + postfix.group()])
                continue
        directive = _ENUMERATION.search(chunk)
        if not directive:
            items = enumerated_items(chunk)
            direct = _AMOUNT_LEAD.match(chunk) or _AMOUNT_END.search(chunk.rstrip("。.?？!！"))
            labels = [_listed_metric(item) for item in items] if direct and len(items) > 1 else []
            if labels and all(labels) and len(set(labels)) == len(labels):
                parts.extend(items)
            else:
                parts.append(chunk)
            continue
        # The full request stays alongside its parts so later items inherit the
        # same company, period, or basis qualifier as the first.
        # Formula and citation are separate deliverables even when appended to
        # the final fiscal label in a Chinese enumeration.
        listed = re.split(r"[，,]\s*(?=(?:並|并)提供(?:可核[實实]|可核[驗验]|可核查)?(?:來源|来源))",
                          chunk[directive.end():])
        items = [piece for clause in listed for item in enumerated_items(clause)
                 for piece in re.split(r"(?<!以)及(?=[計计]算公式)", item) if piece]
        if len(items) > 1:
            start = chunk[:directive.end()]
            if start[-1].isascii() and start[-1].isalnum():
                start += " "
            parts.extend([start + items[0], *items[1:]])
        else:
            parts.append(chunk)
    return {f"part-{i}": s for i, s in enumerate(parts, 1)} if len(parts) > 1 else {}


_ALSO = re.compile(r"^(?:请\s*)?(?:另|此外|也|还|再|同时)|^(?:please\s+)?(?:also|additionally|in addition)\b", re.I)
_INSTEAD = re.compile(r"^(?:请\s*)?(?:只回答|仅回答|只需回答|改问|改为回答)|^(?:please\s+)?(?:only answer|instead|replace)\b", re.I)
_NEXT_CLAUSE = re.compile(r"[；;]\s*|[，,。]\s*(?=(?:并|另|此外|同时|也|还|再|请|and\b|also\b))", re.I)


def clarify_request(previous, reply, previous_parts=None):
    """Separate explicit additional requests from parameter answers, retaining the full wording."""
    effective = previous + "\n" + reply
    prior = list((request_parts(previous) if previous_parts is None else previous_parts).values())
    reply = reply.strip()
    added = list(request_parts(reply).values())
    if not added:
        clauses = [s.strip() for s in _NEXT_CLAUSE.split(reply) if s.strip()]
        if len(clauses)>1 or _ALSO.match(reply) or _INSTEAD.match(reply) or re.search(r"[?？]",reply):
            added = clauses
    if added:
        if _INSTEAD.match(reply):
            prior = []
        elif not prior:
            if _ALSO.match(reply) or re.search(r"[?？]", previous):
                prior = [previous]
            else:
                # Keep an opening company/topic instruction visible without counting it
                # as another question on top of the concrete questions it qualifies.
                added[0] = previous + "\n" + added[0]
    elif prior:
        # A period or other parameter qualifies the existing work, even for repeated clarifications.
        prior[-1] += "\n" + reply
    rows = {f"part-{i}": part for i, part in enumerate(prior + added, 1)}
    return effective, rows, bool(added)


def _direct_metric(question):
    """Gate deterministic checks to an explicit request for one named quantity."""
    listed = _listed_metric(question)
    if listed:
        return listed
    if not re.search(r"多少|\bhow (?:much|many)\b|\bwhat (?:was|is|were|are)\b|"
                     r"^(?:请)?(?:逐项|分别)?(?:列出|给出|列示)|^(?:list|give|report|show)\b", question, re.I):
        return None
    if re.search(r"解释|为什么|原因|说明|影响|贡献|导致|驱动|区别|如何|怎样|换算|同比|环比|增长|减少|下降|增加|变化|"
                 r"\b(?:explain|why|drivers?|effect|contribution|difference|due to|how did|change|changed|grow|"
                 r"grew|increase|increased|decrease|decline|year.over.year|quarter.over.quarter)\b", question, re.I):
        return None
    metrics = metric_mentions(question)
    return next(iter(metrics)) if len(metrics) == 1 else None


def _other_metric(expected, sentence, claim):
    """Reject an unambiguous different numerical answer, leaving qualitative reasoning to review."""
    if not expected or not isinstance(sentence,str) or not re.search(r"\d",sentence):
        return False
    separate = _separate_amounts(sentence)
    if separate:
        return expected not in separate
    stated = metric_mentions(sentence)
    constraints = claim.get("constraints") or {}
    declared = constraints.get("metric") if isinstance(constraints,dict) else None
    if not isinstance(declared,str) or not declared.strip():
        plain = plain_amount_metric(sentence)
        return bool(plain and plain != expected)
    field = metric_mentions(declared.replace("_"," "))
    return len(field) == 1 and stated == field and expected not in stated


_OTHER_PERIOD = re.compile(
    r"上(?:一|个)?季|下(?:一|个)?季|去年|上年|明年|全年|累计|预测|指引|展望|发布.*(?:以后|之前)|"
    r"\b(?:previous|last|next|prior|forecast|guidance|outlook|annual|full.year|year.to.date)\b", re.I)


def _claim_period(claim):
    dims = claim.get("constraints") or {}
    if isinstance(dims, dict):
        fields = tuple(dims.get(k) for k in ("entity", "period_start", "period_end"))
        if all(isinstance(v, str) and v for v in fields):
            return fields
    return None


def _answer_mismatch(question, sentence, claim, *, shared_quarter=None, period_labels=None):
    requested = quarter_label(question) or shared_quarter
    if requested and not _OTHER_PERIOD.search(question):
        # Other claims with the identical issuer and exact dates can establish
        # the draft's own fiscal label. Do not infer one from calendar months.
        labels = (period_labels or {}).get(_claim_period(claim), set())
        entity = (claim.get("constraints") or {}).get("entity")
        stated = leading_quarter(sentence, question=question, entity=entity) or (
            next(iter(labels)) if len(labels) == 1 else None)
        if stated and stated != requested:
            return f"该答句对应 {stated}，本问要求 {requested}。"
    if _other_metric(_direct_metric(question),sentence,claim):
        return "该答句给出的财务指标与此问明确要求的指标不同。"
    if re.search(r"为什么|为何|原因|\b(?:why|what drove|what explains)\b",question,re.I):
        metrics = metric_mentions(question)
        related = metrics | {m.removesuffix("_margin").removesuffix("_growth") for m in metrics}
        if len(metrics) == 1 and bare_stated_metric(sentence) in related:
            return "该答句仅列出金额或比率，没有解释所问原因。"
    return None


def single_question_mismatch(request, claims):
    reasons = [_answer_mismatch(request,c["text"],c) for c in claims]
    if reasons and all(reasons):
        return " ".join(dict.fromkeys(reasons))
    return None


_DAILY_SALES = re.compile(r"(?:日均|每日|每天)(?:净?销售额?|营收)?|daily (?:net )?sales|sales per (?:business|selling) day", re.I)
_TOTAL_SALES = re.compile(r"总(?:净?销售|额|营收)|净销售额|(?:total|net|reported|aggregate) sales|总量|"
                          r"\btotal\s+(?=and|versus|vs\.)", re.I)
_GAP_LANGUAGE = re.compile(r"差[值额距别异]|增速差|相差|高出|低于|高于|比.{1,16}[快慢高低]|不同|"
                           r"\b(?:gap|spread|differ\w*|diverg\w*|faster|slower|higher|lower)\b", re.I)
_DIRECTION_ONLY = re.compile(r"(?:仅|只)(?:需|要|想)?(?:定性|.{0,6}方向)|(?:无需|不用|不必)(?:量化|.{0,4}数字)|"
                             r"qualitatively only|only (?:the )?direction|without (?:numbers|quantif\w+)", re.I)
_WHY = re.compile(r"为何|为什么|原因|解释|哪来|哪里来|如何|影响|贡献|\b(?:why|explain\w*|drove|account for)\b", re.I)
_BUSINESS_DAYS = re.compile(r"营业日|工作日|销售日|交易日|天数|日数|日历|(?:business|selling|working|trading)[ -]days?|calendar", re.I)
_GAP_CLAUSE = re.compile(r"差|高|低|贡献|拉动|推升|抬升|抬高|拖累|压低|带来|解释|影响|减(?:去)?(?:日均|总额|总销售)|"
                         r"\b(?:gap|spread|difference|added|contribut\w*|lift\w*|reduc\w*|drove|above|below|"
                         r"explain\w*|exceed\w*|higher|lower|faster|slower|minus|account\w*)\b", re.I)
_EXACT_POINT = re.compile(r"(?:精确(?:贡献|影响|差额|差值)?|恰好|刚好)(?:[为是了至]\s*)?$|"
                          r"\b(?:exactly|precisely)\s*$", re.I)
_UNRELATED_POINT = re.compile(r"利润率|毛利率|利率|占比|市场份额|\b(?:margin|market share|interest rate)\b", re.I)
_SMALLER = re.compile(r"低于|比.{1,23}(?:低|慢|少)|(?:拖累|压低|拉低)|\b(?:lower|slower|below)\b", re.I)
_LARGER = re.compile(r"高于|比.{1,23}(?:高|快|多)|(?:抬高|推高|拉高)|\b(?:higher|faster|above)\b", re.I)


def _sales_gap_question(question):
    return (_DAILY_SALES.search(question) and _TOTAL_SALES.search(_DAILY_SALES.sub("", question))
            and _GAP_LANGUAGE.search(question) and not _DIRECTION_ONLY.search(question))


def _displays_gap(text, expected):
    """Check a labelled gap at the precision actually displayed, including basis points."""
    for clause in re.split(r"[，,；;。！？!?\n]|(?<=\.)\s+", text):
        if not _GAP_CLAUSE.search(clause) or _UNRELATED_POINT.search(clause):
            continue
        daily = _DAILY_SALES.search(clause)
        total = next((m for m in _TOTAL_SALES.finditer(clause)
                      if not daily or not daily.start() <= m.start() < daily.end()), None)
        ordered = 0
        if total and daily:
            first, second = sorted((total, daily), key=lambda m:m.start())
            if re.search(r"减(?:去)?|\bminus\b", clause[first.end():second.start()], re.I):
                ordered = 1 if total.start() < daily.start() else -1
            direction = -1 if _SMALLER.search(clause) else 1 if _LARGER.search(clause) else 0
            if daily.start() < total.start():
                direction = -direction
            if direction and expected * direction <= 0:
                continue
        for literal, unit, value, tolerance in _quantities(clause):
            if unit != "percentage_points":
                continue
            prefix = clause[:clause.find(literal)].rstrip()
            if _EXACT_POINT.search(prefix) or re.search(r"不是|并非|not\s+(?:about\s+)?$", prefix, re.I):
                continue
            # "About 160 bp" may express a nearest-ten estimate, like "about
            # 1.6 pp". Keep explicit decimal places (1.60 pp) at their stated precision.
            rounded_bp = re.fullmatch(r"[-+−]?(\d*?[1-9])(0+)\s*(?:个?基点|bps?|basis points?)", literal, re.I)
            if rounded_bp and re.search(r"约(?:为)?|大约|近|\b(?:about|around|roughly|approximately)\s*$", prefix, re.I):
                tolerance *= 10 ** len(rounded_bp[2])
            # An unsigned spread can express magnitude in either direction.
            # Signed quantities describe total growth less daily growth.
            explicit_sign = literal.startswith(("-", "−", "负", "+"))
            if ordered or explicit_sign:
                distance = abs(value - expected * (ordered or 1))
            else:
                distance = abs(abs(value) - abs(expected))
            if distance <= tolerance:
                return True
    return False


def _calendar_obligations(context):
    """Bind only a current sales-gap question to the calculation returned for that part."""
    episode = context.get("episode", {})
    parts = context.get("request_parts") or {"part-1": episode.get("request", "")}
    calculations = {r["id"]: r for r in context.get("calculations", []) if not r.get("stale")}
    target = context.get("fiscal_target") or episode.get("skill_fiscal_quarter") or {}
    result = {}
    for receipt in reversed(episode.get("recent_tool_results", [])):
        if receipt.get("tool") != "inspect_sales_comparison" or not receipt.get("ok"):
            continue
        data = receipt.get("data", {})
        for part, finding in data.get("part_findings", {}).items():
            question = parts.get(part, "")
            if part in result or not _sales_gap_question(question):
                continue
            quarter = quarter_label(question) or target.get("quarter") or data.get("fiscal_quarter")
            if quarter != data.get("fiscal_quarter"):
                continue
            # A later inspection with a gap supersedes an earlier calculation.
            result[part] = None
            if finding.get("status") != "source_excerpt":
                continue
            for ref in finding.get("calculation_ids", []):
                calc = calculations.get(ref, {})
                if (calc.get("operation") == "sales_calendar_growth_gap"
                        and calc.get("metric") == "sales_calendar_growth_gap" and calc.get("unit") == "percentage_points"
                        and calc.get("entity") == receipt.get("arguments", {}).get("entity")
                        and (not target.get("entity") or calc["entity"] == target["entity"])
                        and calc.get("fiscal_quarter") == quarter and calc.get("comparison_quarter") == data.get("comparison_quarter")
                        and type(calc.get("value")) in (int, float) and Decimal(str(calc["value"])).is_finite()):
                    result[part] = (question, calc)
                    break
    return {part: item for part, item in result.items() if item}


_DAY_NUMBER = r"(?:\d+|一|两|二|三|四|五|one|two|three|four|five)"
_DAY_UNIT = r"(?:个?\s*(?:营业日|工作日|销售日|交易日|天)|(?:business|selling|working|trading)\s+days?)"
_DAY_DELTA_ZH = re.compile(r"(多|少|增加|减少)\s*(?:了)?\s*(" + _DAY_NUMBER + r")\s*" + _DAY_UNIT, re.I)
_DAY_DELTA_EN = re.compile(r"\b(" + _DAY_NUMBER + r")\s+(more|extra|fewer|less)\s+" + _DAY_UNIT, re.I)
_DAY_PAIR = re.compile(r"(?:营业日|工作日|销售日)(?:数|天数)?\s*(?:分别)?\s*(?:为|是)\s*(\d+)\s*天?"
                       r"\s*(?:和|与|、|对)\s*(\d+)\s*(?:个营业日|天)?")
_DAY_WORDS = dict(zip(("一","两","二","三","四","五","one","two","three","four","five"), (1,2,2,3,4,5,1,2,3,4,5)))


def _wrong_selling_days(text, calc, evidence):
    ids = calc.get("operand_ids", [])
    if len(ids) != 4:
        return False
    current, prior = (evidence.get(ref, {}).get("value") for ref in ids[-2:])
    if not all(type(x) in (int, float) for x in (current, prior)):
        return False
    for sentence in re.split(r"[。！？!?;；\n]|(?<=\.)\s+", text):
        if re.search(r"假设|假如|如果|若|\b(?:if|assuming|hypothetically)\b", sentence, re.I):
            continue
        pair = _DAY_PAIR.search(sentence)
        if pair:
            # Honour an explicitly reversed comparison such as "2023Q3 和 2024Q3".
            prefix = sentence[:pair.start()]
            left, right = (prefix.rfind(calc.get(key) or "\0") for key in ("fiscal_quarter", "comparison_quarter"))
            reversed_pair = (0 <= right < left or re.search(r"(?:上年同期|去年同期)\s*(?:和|与|、)\s*(?:本期|本季)", prefix))
            expected = (prior, current) if reversed_pair else (current, prior)
            if tuple(map(int, pair.groups())) != expected:
                return True
        for pattern in (_DAY_DELTA_ZH, _DAY_DELTA_EN):
            for match in pattern.finditer(sentence):
                prefix = re.split(r"[，,]", sentence[:match.start()])[-1].strip()
                if re.search(r"(?:不是|并非|不足|不到|至少|最多|不超过|not|at least|at most)\s*$", prefix, re.I):
                    continue
                word, number = match.groups() if pattern is _DAY_DELTA_ZH else match.groups()[::-1]
                delta = int(number) if number.isdigit() else _DAY_WORDS[number.lower()]
                if word.lower() in {"少","减少","fewer","less"}:
                    delta = -delta
                preceding_first = re.search(r"(?:上年同期|去年同期|上年同季|prior.year quarter|year.ago quarter)"
                                            r"\s*(?:(?:比|较)\s*(?:本期|本季|本季度)|had|有)?\s*$", prefix, re.I)
                # In "本期比上年同期多一天" the prior period is the comparator.
                if re.search(r"(?:比|较|相对)\s*(?:上年同期|去年同期|上年同季)\s*$", prefix):
                    preceding_first = False
                current_label, prior_label = calc.get("fiscal_quarter"), calc.get("comparison_quarter")
                if current_label and prior_label and current_label in prefix and prior_label in prefix:
                    preceding_first = prefix.index(prior_label) < prefix.index(current_label)
                if delta != (prior-current if preceding_first else current-prior):
                    return True
    return False


def _comparison_checks(context):
    if not context.get("claims"):
        return
    requirements = context.get("requirements", [])
    for part, (question, calc) in _calendar_obligations(context).items():
        ids = {r["id"] for r in requirements if _question_wording(r["text"]) == _question_wording(question)}
        # A shared checklist item can also cover the separate demand question.
        # Its claims remain independently reviewable through the part mappings.
        claims = [c for c in context.get("claims", []) if not c.get("stale")
                  and (not ids or ids.intersection(c.get("requirement_ids", [])))]
        amount = Decimal(str(calc["value"]))
        magnitude = any(calc["id"] in c.get("calculation_ids", []) and _displays_gap(c["text"], amount) for c in claims)
        days = not _WHY.search(question) or any(_BUSINESS_DAYS.search(c["text"]) for c in claims)
        evidence = {r["id"]: r for r in context.get("evidence", []) if not r.get("stale")}
        wrong_days = [c["id"] for c in claims if calc["id"] in c.get("calculation_ids", [])
                      and _wrong_selling_days(c["text"], calc, evidence)]
        reason = (f"同季原表可按两期销售额和营业日数复算，总额同比减日均同比约为 {amount:.2f} 个百分点。"
                  "须在本问答句中量化差额，引用对应计算，并说明营业日的作用。")
        if wrong_days:
            reason = "答句中的营业日数字或增减方向与该计算引用的两期营业日原格不符。"
        yield part, ids, calc, magnitude and days and not wrong_days, reason, wrong_days


def comparison_rule_issues(context):
    issues = []
    for _, ids, calc, complete, reason, wrong_days in _comparison_checks(context):
        if not complete:
            issues.extend({"target_id": req, "issue_type":"omission", "scope":"question_coverage",
                           "evidence_refs":[calc["id"]], "missing_support":reason,
                           "proposed_action":"按当前问题及来源补全数量解释，并核对显示精度。"} for req in sorted(ids))
        issues.extend({"target_id": cid, "issue_type":"numerical_inconsistency",
                       "evidence_refs":calc["operand_ids"][-2:], "missing_support":reason,
                       "proposed_action":"逐一对照两个季度的营业日，再修正答句。"} for cid in wrong_days)
    return issues


def check_comparison_coverage(context, rows):
    """A semantic pass on a directional sentence cannot hide an available quantitative answer."""
    claims = {c["id"]: c for c in context.get("claims", [])}
    checked = list(rows) if isinstance(rows, list) else []
    for part, _, calc, complete, reason, _ in _comparison_checks(context):
        if not context.get("request_parts"):
            return rows, None if complete else reason
        for i, row in enumerate(checked):
            if not isinstance(row, dict) or row.get("part") != part or row.get("status") != "answered":
                continue
            linked = claims.get(row.get("claim_id"), {}) if isinstance(row.get("claim_id"), str) else {}
            quote = row.get("answer_sentence")
            if (not complete or calc["id"] not in linked.get("calculation_ids", [])
                    or not isinstance(quote, str) or not _displays_gap(quote, Decimal(str(calc["value"])))):
                checked[i] = {"part":part, "status":"gap", "missing_support":reason}
    return checked, None


def _question_wording(text):
    return " ".join(text.split()).casefold().rstrip(".。?？")


def unresolved_checklist(requirements, unanswered, parts, coverage):
    """Include an explicit checklist question when every mapping for its wording is a gap."""
    states = {}
    for row in coverage:
        states.setdefault(_question_wording(parts[row["part"]]),set()).add(row["status"])
    prior = {r["id"] for r in unanswered}
    return [r for r in requirements if r["id"] in prior or
            (states.get(_question_wording(r["text"]), set()) & {"partial", "gap"}
             and "answered" not in states[_question_wording(r["text"])])]


_QUARTER_IDENTITY = re.compile(
    r"(?:查询|确定|指出|识别|是哪|哪一).{0,100}?(?:最近|最新)(?:已)?(?:发布|公布|披露|报告)?的?财季(?:[。？！?]|$)"
    r"|(?:which|identify|name)\b[^?.!]{0,100}(?:latest|most recent) fiscal quarter\b", re.I)
_FILING_LINK = re.compile(r"(?:申报|披露|报告|文件)\s*(?:的)?链接|filing (?:link|URL)",re.I)
_DATE_RANGE = re.compile(r"(?:起止|起讫)日期|start and end dates?",re.I)
_URL = re.compile(r"https?://[^\s<>，。]+",re.I)


_DATE_TEXT = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\d{4}年\s*\d{1,2}月\s*\d{1,2}日|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.? \d{1,2},? \d{4}\b",re.I)


# A quoted caption can carry the sentence's period inside the closing quote.
_REVIEW_SENTENCE_BREAK = re.compile(r"""(?<=[。！？!?])|(?<=\.)\s+|(?<=\.[”’"'])\s+(?=[A-Z]|\[)""")


def _grounded_clause(clause, source, question):
    clause = clause.strip().rstrip(".。")
    for sentence in _REVIEW_SENTENCE_BREAK.split(source):
        sentence = sentence.strip().rstrip(".。")
        if clause == sentence:
            return True
        # A repeated period or a longer filing caption can be elided in a joined
        # quote. Keep the actual source sentence (including the qualification).
        if sentence.endswith(clause) and clause:
            prefix = sentence[:-len(clause)]
            if re.fullmatch(r"For the (?:three months|quarter|year) ended " + "(?:" + _DATE_TEXT.pattern + "), ",prefix,re.I):
                return True
        if _FILING_LINK.search(question):
            caption,sep,url = clause.rpartition(" is ")
            if (sep and _URL.fullmatch(url) and sentence.endswith(sep+url)
                    and re.fullmatch(r"(?:the )?(?:[a-z]+ )?filing link(?: for the [a-z0-9-]+ report)?",caption,re.I)
                    and sentence.casefold().startswith(caption.casefold()+" covering ")):
                return True
    return False


_MARGIN_RESULT = re.compile(
    r"(?:operating (?:profit )?margin|营业利润率)\s*(?:(?:of|was|is|为|是)\s*)?"
    r"(?:(?:approximately|about|约为|约)\s*)?([-+−]?\d+(?:\.\d+)?\s*%)", re.I)
_FILING_URL = re.compile(r"https://www\.sec\.gov/Archives/edgar/data/\d+/\d{18}/[\w.-]+\.html?\b", re.I)
_MAPPING_GAP = "本版未能把该项要求对应到单独核验过的回答。"


def _consistent_claims(left, right):
    a, b = left.get("constraints") or {}, right.get("constraints") or {}
    return all(not a.get(k) or not b.get(k) or a[k] == b[k]
               for k in ("entity", "period_start", "period_end"))


def _quarter_heading(claim, question):
    text = claim["text"]
    label = leading_quarter(text, entity=(claim.get("constraints") or {}).get("entity"))
    if label:
        return label
    # The request may give the issuer's name followed by its ticker, while
    # the claim's entity field uses the ticker or CIK.
    first = next(quarter_mentions(text), None)
    if first:
        name = text[:first[0].start()].strip()
        if name and re.search(r"(?<![a-zA-Z0-9])" + re.escape(name)
                              + r"\s*[（(][A-Z][A-Z0-9.-]*[）)]", question, re.I):
            return quarter_label(text)
    return None


def _printed_dates(text):
    """Compare the same printed day across numeric, Chinese and English spellings."""
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    days = set()
    for literal in _DATE_TEXT.findall(text):
        numbers = list(map(int, re.findall(r"\d+", literal)))
        if literal[0].isdigit():
            year, month, day = numbers
        else:
            day, year = numbers
            month = months.index(literal[:3].lower()) + 1
        days.add((year, month, day))
    return days


def _complete_quarter_links(parts, rows, supported, summary):
    """Recover redundant links for a single latest-quarter answer from its reviewed text."""
    if sum(bool(_QUARTER_IDENTITY.search(q)) for q in parts.values()) != 1:
        return
    primary_claims = [supported[r["claim_id"]] for r in rows
                      if _QUARTER_IDENTITY.search(parts.get(r["part"], ""))
                      and isinstance(r.get("claim_id"), str) and r["claim_id"] in supported]
    summary_urls = set(_FILING_URL.findall(summary))
    margins = set(_MARGIN_RESULT.findall(summary))
    summary_dates = _printed_dates(summary)
    # A reviewed formula row identifies the specific claim for this question.
    # Use its separate dated result sentence only if the summary agrees.
    margin_answers = []
    for row in rows:
        if not re.fullmatch(r"(?:计算)?公式[。.?？]?|(?:calculation )?formula[.?]?", parts.get(row["part"], "").strip(), re.I):
            continue
        claim = supported.get(row.get("claim_id")) if isinstance(row.get("claim_id"), str) else None
        if (not claim or row.get("status") != "answered" or len(primary_claims) != 1
                or not _consistent_claims(claim, primary_claims[0])):
            continue
        if not isinstance(claim.get("text"), str):
            continue
        sentences = [s.strip() for s in _REVIEW_SENTENCE_BREAK.split(claim["text"])]
        if row.get("answer_sentence") not in sentences:
            continue
        for sentence in sentences:
            values = _MARGIN_RESULT.findall(sentence)
            dates = _printed_dates(sentence)
            if (sentence != row["answer_sentence"] and len(values) == 1 and margins == set(values)
                    and len(dates) == 1 and dates <= summary_dates
                    and dates <= _printed_dates(primary_claims[0]["text"])):
                margin_answers.append((claim["id"], sentence))

    for part, question in parts.items():
        matches = [r for r in rows if r["part"] == part]
        if len(matches) > 1:
            continue
        row = matches[0] if matches else {}
        if (re.fullmatch(r"(?:营业利润率|operating (?:profit )?margin)(?:分别)?(?:是多少)?[？?。.]?", question.strip(), re.I)
                and len(margin_answers) == 1 and (not row or row.get("status") == "gap"
                and row.get("missing_support", _MAPPING_GAP) == _MAPPING_GAP)):
            if not row:
                rows.append(row)
            cid, sentence = margin_answers[0]
            row.clear()
            row.update(part=part, status="answered", claim_id=cid, answer_sentence=sentence)
        if (_QUARTER_IDENTITY.search(question) and row.get("status") == "partial"
                and row.get("missing_support") == "本稿未在已核实的答句和摘要中一致给出财年及季度编号。"):
            primary = supported.get(row.get("claim_id")) if isinstance(row.get("claim_id"), str) else None
            amount = row.get("answer_sentence")
            label = quarter_label(summary)
            if primary and isinstance(amount, str) and _grounded_clause(amount, primary["text"], question) and label:
                dates = _printed_dates(amount)
                labels = [cid for cid, c in supported.items() if quarter_label(c["text"]) == label
                          and _consistent_claims(c, primary) and _quarter_heading(c, question) == label
                          and dates.intersection(_printed_dates(c["text"]))
                          and not re.search(r"unconfirmed|uncertain|not (?:confirmed|explicit)|未确认|无法确认", c["text"], re.I)]
                if len(labels) == 1 and not re.search(r"unconfirmed|无法确认|未确认", summary, re.I) and metric_mentions(question).issubset(metric_mentions(amount)):
                    ids = list(dict.fromkeys([labels[0], primary["id"]]))
                    row.update(status="answered", claim_id=labels[0], answer_sentence=supported[labels[0]]["text"],
                               joint_claim_ids=ids, supporting_claim_ids=ids)
                    row.pop("missing_support", None)
        if not (_DATE_RANGE.search(question) and _FILING_LINK.search(question) and
                row.get("status") == "partial" and row.get("missing_support") == "本项未对应到已核实的申报链接。"):
            continue
        primary = supported.get(row.get("claim_id")) if isinstance(row.get("claim_id"), str) else None
        quote = row.get("answer_sentence")
        if not primary or not isinstance(quote, str) or not _grounded_clause(quote, primary["text"], question):
            continue
        # Require both endpoints in the same summary that explicitly cites this filing.
        dates = _printed_dates(quote)
        if len(dates.intersection(summary_dates)) < 2 or len(summary_urls) != 1:
            continue
        links = [cid for cid,c in supported.items() if _consistent_claims(c, primary) and re.search(r"\b10-Q\b", c["text"], re.I)
                 and _FILING_URL.findall(c["text"]) == list(summary_urls)]
        if len(links) == 1:
            ids = list(dict.fromkeys([primary["id"], links[0]]))
            row.update(status="answered", joint_claim_ids=ids, supporting_claim_ids=ids)
            row.pop("missing_support", None)


def grounded_review_rows(parts, rows, claims, verdicts, summary):
    """Keep the reviewer's explicit links, but deliver only the cited draft's own text."""
    supported = {c["id"]:c for c in claims if not c.get("stale")
                 and verdicts.get(c["id"],{}).get("status") == "Supported"}
    checked = []
    for original in rows if isinstance(rows,list) else ():
        if not isinstance(original,dict) or not isinstance(original.get("part"),str):
            continue
        row = dict(original)
        row.pop("joint_claim_ids", None)  # Derived locally from checked quotes.
        checked.append(row)
        question = parts.get(row.get("part"), "")
        if not question or row.get("status") != "answered" or not isinstance(row.get("claim_id"),str):
            continue
        primary = row["claim_id"]
        ids = [item.strip() for item in primary.split(",")]
        quote = row.get("answer_sentence")
        if not isinstance(quote,str):
            continue
        if len(ids) > 1:
            # Some reviewers put a joined ID list in the scalar field and splice
            # clauses. Require both explicit ID lists to agree, and ground each
            # clause in its own reviewed claim before replacing the synthetic quote.
            if (ids != row.get("supporting_claim_ids") or len(set(ids)) != len(ids)
                    or any(cid not in supported for cid in ids)):
                continue
            clauses = re.split(r",\s+and\s+|；|;\s+",quote,flags=re.I)
            if len(clauses) != len(ids):
                continue
            if not all(_grounded_clause(clause,supported[cid]["text"],question) for clause,cid in zip(clauses,ids)):
                continue
            row.update(claim_id=ids[0],answer_sentence=supported[ids[0]]["text"],joint_claim_ids=ids)
        if row["claim_id"] not in supported:
            continue
        refs = row.get("supporting_claim_ids",[])
        if not isinstance(refs,list):
            refs = []
        texts = [row["answer_sentence"], *(supported[cid]["text"] for cid in refs
                                          if isinstance(cid,str) and cid in supported)]
        if not all(isinstance(t,str) for t in texts):
            continue
        answer = " ".join(texts)
        missing = []
        if _QUARTER_IDENTITY.search(question):
            label = quarter_label(answer)
            if not label or quarter_label(summary or "") != label:
                missing.append("本稿未在已核实的答句和摘要中一致给出财年及季度编号。")
            measures = metric_mentions(question)
            if measures and not measures.issubset(metric_mentions(answer)):
                missing.append("答句未对应到所问的财务指标。")
        if _DATE_RANGE.search(question) and _FILING_LINK.search(question):
            if not _URL.search(answer):
                missing.append("本项未对应到已核实的申报链接。")
            if len(_printed_dates(answer)) < 2:
                missing.append("本项未对应到已核实的完整起止日期。")
        if missing:
            row.update(status="partial",missing_support=" ".join(missing))
    _complete_quarter_links(parts, checked, supported, summary or "")
    return checked


def coverage_issues(parts, response, claims, *, requirements=(), unanswered_ids=()):
    if not parts:
        return []
    rows = response.get("request_coverage", [])
    if not isinstance(rows, list):
        rows = []
    by_claim = {c["id"]: c for c in claims}
    period_labels = {}
    for claim in claims:
        dates = _claim_period(claim)
        label = leading_quarter(claim["text"])
        if dates and label:
            period_labels.setdefault(dates, set()).add(label)
    # A period in an opening heading (e.g. "Acme 2025Q1, ...") also
    # qualifies dependent questions in that request. Other periods remain local.
    first = next(iter(parts.values()))
    heading = re.match(r"^([^,，:：?？。]{1,80})[,，:：]", first)
    shared_quarter = (quarter_label(heading[1]) if heading and not _OTHER_PERIOD.search(heading[1])
                      and quarter_label(" ".join(parts.values())) else None)
    requirements_by_text = {}
    for item in requirements:
        requirements_by_text.setdefault(_question_wording(item["text"]),set()).add(item["id"])
    issues = []
    used = {}
    for part, text in parts.items():
        matches = [r for r in rows if isinstance(r, dict) and r.get("part") == part]
        row = matches[0] if len(matches) == 1 else {}
        quote = row.get("answer_sentence")
        claim = by_claim.get(row.get("claim_id"),{}) if isinstance(row.get("claim_id"), str) else {}
        source = claim.get("text")
        sentences = [s.strip() for s in _REVIEW_SENTENCE_BREAK.split(source or "")]
        stripped = quote.strip() if isinstance(quote, str) else ""
        prior = used.get(stripped, [])
        # One sentence can report two distinct levels. Reuse it only for the
        # separately named amounts, never for a driver, comparison or repeated
        # metric with different dates. Semantic review still checks each row.
        if prior:
            stated = _separate_amounts(stripped)
            expected = _listed_metric(text)
            reusable = all(ref == row.get("claim_id") and (
                same_research_question(parts[part], text) or
                (expected in stated and _listed_metric(parts[part]) in stated - {expected})
            ) for part, ref in prior)
        else:
            reusable = True
        matching_ids = requirements_by_text.get(_question_wording(text),set())
        reason = None
        joint = row.get("joint_claim_ids", [])
        if joint and (not isinstance(joint,list) or any(not isinstance(cid,str) or cid not in by_claim
                         or by_claim[cid].get("stale") for cid in joint)):
            reason = "该组合答复的部分主张已失效或尚未核实。"
        if row.get("status") == "answered" and matching_ids:
            if matching_ids.issubset(unanswered_ids):
                reason = "起草时这项要求仍列为未回答，引用的背景不能代替答案。"
            elif "requirement_ids" in claim and not matching_ids.intersection(claim["requirement_ids"]):
                reason = "引用的主张关联的是另一项清单要求。"
        if row.get("status") == "answered":
            reason = _answer_mismatch(text,stripped,claim,shared_quarter=shared_quarter,
                                      period_labels=period_labels) or reason
        if (row.get("status") == "answered" and isinstance(quote, str)
                and (stripped in sentences or stripped == source) and stripped and reusable
                and not reason):
            used.setdefault(stripped, []).append((part, row["claim_id"]))
            continue
        reason = reason or (row.get("missing_support") if row.get("status") in ("gap", "partial") else None)
        issues.append({"target_id":part, "issue_type":"omission", "evidence_refs":[],
                       "missing_support":text + " — " + (reason.strip() if isinstance(reason, str) and reason.strip()
                            else "本版未能把该项要求对应到单独核验过的回答。"),
                       "proposed_action":"对照用户原问，补出有出处的回答；现有资料不足时保留具体缺口。"})
    return issues


def check_period_coverage(parts, rows, claims, findings, requirements):
    """Prevent a reviewed mapping from delivering a rejected reporting period."""
    from .answer_periods import CHECK

    defects = {f["target_id"]: f["missing_support"] for f in findings if f.get("scope") == CHECK}
    if not defects:
        return rows
    amended = {}
    by_claim = {c["id"]: c for c in claims}
    for part, question in parts.items():
        matches = [r for r in rows if isinstance(r, dict) and r.get("part") == part]
        if len(matches) > 1:
            continue
        row = matches[0] if matches else {}
        req_ids = {r["id"] for r in requirements if _question_wording(r["text"]) == _question_wording(question)}
        claim_ids = [row.get("claim_id")]
        joint = row.get("joint_claim_ids")
        if isinstance(joint, list):
            claim_ids.extend(joint)
        claim_ids = [cid for cid in claim_ids if isinstance(cid, str)]
        if row.get("status") not in ("answered", "partial"):
            claim_ids.extend(c["id"] for c in claims if req_ids.intersection(c.get("requirement_ids", [])))
        for cid in claim_ids:
            req_ids.update(by_claim.get(cid, {}).get("requirement_ids", []))
        reasons = [defects[cid] for cid in [*sorted(req_ids), *claim_ids] if cid in defects]
        if reasons:
            amended[part] = {"part": part, "status":"gap", "missing_support": " ".join(dict.fromkeys(reasons))}
    retained = [r for r in rows if not isinstance(r, dict) or r.get("part") not in amended]
    return [*retained, *amended.values()]


def report_coverage(parts, rows, claims, *, reviewed, requirements=(), unanswered_ids=(), findings=()):
    """Only publish answer mappings grounded in the supported text of this report."""
    if not reviewed:
        return []
    rows = rows if isinstance(rows, list) else []
    rows = check_period_coverage(parts, rows, claims, findings, requirements)
    supported = [c for c in claims if c.get("status") == "Supported" and not c.get("stale")]
    issues = coverage_issues(parts, {"request_coverage": rows}, supported,requirements=requirements,unanswered_ids=unanswered_ids)
    invalid = {i["target_id"]:i for i in issues}
    by_part = {r["part"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("part"), str)}
    result = []
    for part in parts:
        row = by_part.get(part, {})
        if part not in invalid:
            result.append({k: row[k] for k in ("part", "status", "claim_id", "answer_sentence", "joint_claim_ids") if k in row})
        elif row.get("status") == "partial" and isinstance(row.get("missing_support"), str) and row["missing_support"].strip():
            # Validate the delivered portion against the exact reviewed text. A
            # mixed claim may contribute only the excerpt the Critic accepted.
            candidates = list(supported)
            for c in claims:
                if c.get("status") == "Partially Supported" and not c.get("stale"):
                    excerpt = c.get("critic_verdict", {}).get("supported_text")
                    if excerpt and excerpt in c["text"]:
                        candidates.append({**c, "text":excerpt})
            trial = [dict(r, status="answered") if r is row else r for r in rows]
            defects = coverage_issues(parts, {"request_coverage":trial}, candidates, requirements=requirements)
            if not any(issue["target_id"] == part for issue in defects):
                result.append({k:row[k] for k in ("part", "status", "claim_id", "answer_sentence", "missing_support", "joint_claim_ids") if k in row})
            else:
                result.append({"part":part, "status":"gap", "missing_support":row["missing_support"]})
        else:
            reason = row.get("missing_support") if row.get("status") == "gap" else None
            if row.get("status") == "answered" and any(c["id"] == row.get("claim_id")
                    and c.get("status") != "Supported" and not c.get("stale") for c in claims):
                # The mapping exists. Show why the reviewed claim could not be published.
                problems = [f["missing_support"] for f in findings if f.get("target_id") == row["claim_id"]
                            and isinstance(f.get("missing_support"), str) and f["missing_support"].strip()]
                if problems:
                    reason = "对应主张未通过核验：" + " ".join(dict.fromkeys(problems))
            result.append({"part": part, "status": "gap", "missing_support":
                           reason.strip() if isinstance(reason, str) and reason.strip()
                           else invalid[part]["missing_support"].removeprefix(parts[part] + " — ")})
    # Store references only. The displayed text is rebuilt from the reviewed
    # version of each claim, including its approved excerpt and later staleness.
    by_id = {claim["id"]: claim for claim in claims}
    usable = {cid for cid, claim in by_id.items() if not claim.get("stale") and
              claim.get("status") in ("Supported", "Partially Supported")}
    for accepted in result:
        original = by_part.get(accepted["part"], {})
        ids = original.get("supporting_claim_ids")
        if isinstance(ids, list) and accepted["status"] == original.get("status"):
            selected = list(dict.fromkeys(i for i in ids if isinstance(i,str) and i in usable))
            if selected:
                accepted["supporting_claim_ids"] = selected
            if accepted["status"] == "answered" and re.search(
                    r"同比|环比|去年|上年|上季|上期|前年|year.over.year|compared (?:with|to)|last (?:year|quarter)",
                    parts[accepted["part"]], re.I):
                # The reviewer linked this answer to a comparison with a dated
                # baseline. A surviving current-period amount cannot replace it.
                comparisons = [by_id[cid] for cid in [accepted.get("claim_id"), *ids] if isinstance(cid, str) and cid in by_id
                               and by_id[cid].get("constraints", {}).get("comparison_period_end")]
                if comparisons and all(c["id"] not in usable for c in comparisons):
                    accepted.update(status="partial", missing_support="与比较期的变动尚未通过核验。")
    return result


def reviewed_followup_limits(ctx, episode, review, rows):
    """Allow a disclosed research limit only when every user question has a verified answer."""
    open_ids = set(episode["unanswered_requirement_ids"])
    parts = ctx.get("request_parts", {})
    if not open_ids or not parts:
        return []
    planned = episode.get("planner_added_requirements", {})
    requirements = {r["id"]: r["text"] for r in ctx["requirements"]}
    if any(rid not in planned or planned[rid] != requirements.get(rid) or
           any(same_research_question(planned[rid], question) for question in parts.values())
           for rid in open_ids):
        return []
    # An omitted answer, rule finding, or objection to a claim remains actionable.
    # A malformed or unspecified issue target is never a disclosure boundary.
    if any(i["issue_type"] != "omission" or i["target_id"] not in open_ids
           or not i.get("evidence_refs") or i.get("affected_request_parts", []) != []
           for i in review["issues"]):
        return []
    verdicts = {v["claim_id"]: v for v in review.get("claim_verdicts", [])}
    claims = [{**c, "status": verdicts.get(c["id"], {}).get("status")} for c in ctx["claims"]]
    if not claims or any(c["status"] != "Supported" or c.get("stale") for c in claims):
        return []
    mapped = report_coverage(parts, rows, claims, reviewed=True, requirements=ctx["requirements"],
                             unanswered_ids=open_ids)
    if not mapped or any(row["status"] != "answered" for row in mapped):
        return []
    cited_claims = {cid for row in mapped for cid in [row["claim_id"], *row.get("supporting_claim_ids", [])]}
    limits = []
    for claim in claims:
        missing = claim.get("constraints", {}).get("missing_evidence")
        if (claim["id"] not in cited_claims or not claim.get("evidence_ids")
                or not isinstance(missing, list) or not missing
                or not all(isinstance(item, str) and item.strip() for item in missing)):
            continue
        ids = sorted(open_ids.intersection(claim.get("requirement_ids", ())))
        if ids:
            limits.append({"claim_id": claim["id"], "requirement_ids": ids})
    return limits if {rid for limit in limits for rid in limit["requirement_ids"]} == open_ids else []


def coverage_status(report, rows):
    """Retain the archive's verdict alongside a newly discovered answer-mapping gap."""
    if (report.get("status") == "complete" and report.get("request_coverage")
            and any(r.get("status") in ("gap","partial") for r in rows)):
        return {"archived_status":"complete", "archived_stop_reason":report.get("stop_reason"),
                "status":"partial", "stop_reason":"answer_mapping_needs_review"}
    return {}
