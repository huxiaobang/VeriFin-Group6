"""Select packaged research instructions from user turns. No requests, issuer facts or gold answers."""
from functools import lru_cache
from importlib.resources import files
import re


TOPICS = {
    "reconcile-segment-totals": (r"(?:分部|各(?:个|块|条)?业务|各业务线|业务板块).{0,36}(?:相加|加(?:起来|在一起|总)|合计|合并抵销|合并抵消|加在一[起块]|加总|加起|汇总|抵销|抵消|对.{0,4}(?:集团|公司|合并))|"
                                 r"(?:分部|部门|业务(?:线|板块)?).{0,24}(?:之和|总和|之总).{0,28}(?:合并|集团|公司整体)|"
                                 r"(?:集团|公司|合并).{0,18}(?:比|与|和|对).{0,10}(?:分部|各业务).{0,12}(?:合计|加总|之和)|"
                                 r"(?:sum|reconcil\w*|eliminat\w*).{0,35}segments?|segments?.{0,35}(?:sum|add up|consolidated|eliminations?)"),
    "incremental-profit-margin": (r"增量(?:营业|经营|毛|调整后)?利润率|增量.{0,8}EBITDA|(?:利润|盈利).{0,6}增量.{0,12}(?:营收|销售|收入)增量|"
                                  r"(?:每(?:增加|多|少)(?:卖|做|赚|出|一)?|(?:销售|营收|收入).{0,12}(?:多出来|高出来|增加的部分|增加一)).{0,32}(?:利润|毛利|留下|盈利|EBITDA)|"
                                  r"(?:新增|增加|额外|多出).{0,5}(?:销售|收入|营收).{0,22}(?:几成|转[成为化]|带来|留下).{0,15}(?:营业利润|盈利|利润|毛利)|"
                                  r"incremental (?:operating |gross |EBITDA )?margin|(?:per|each|every) (?:additional|incremental|extra) dollar.{0,45}(?:profit|income|EBITDA)|"
                                  r"(?:profit|income|EBITDA).{0,35}(?:per|each|every) (?:additional|incremental|extra) dollar"),
    "isolate-discrete-tax-item": (r"(?:一次性|单项|非经常性|特殊).{0,8}(?:税项|税收|税务|所得税|税费)|递延税.{0,9}(?:估值备抵|减值|估值准备)|"
                                  r"(?:这笔|那笔|该笔|单笔)(?:递延|预提|一次性|非经常性)(?:所得)?税|(?:这笔|该笔|那笔|该项|这项|那项).{0,5}税.{0,20}(?:每股|现金|实付)|"
                                  r"(?:单独(?:确认|计提)|专项计提|单列).{0,6}(?:税务损失|税收收益|所得税|税项)|"
                                  r"(?:one[- ]time|discrete|nonrecurring) (?:income )?tax|(?:tax|deferred tax assets?).{0,22}valuation allowance"),
    "dealer-inventory": r"(?:经销商|渠道|分销商).{0,12}(?:库存|零售|去库|压货)|终端(?:销售|销量|需求)|出货.{0,24}(?:终端|少买|零售)|dealer (?:stock|inventor|retail)|sell[- ]through|channel inventor",
    "store-cohorts": r"同店|老店|新店|开店|新开.{0,4}(?:店|门店)|才开业|开业(?:年份|批次)|same[- ]store|comparable (?:stores?|restaurants?)|new(?:ly opened)? (?:stores?|restaurants?)|opening cohort",
    "cash-flow-bridge": r"现金流|经营现金|(?:利润|赚的钱|应收|应付).{0,18}现金|cash flow|cash from operations|cash conversion|working capital",
    "implied-period-guidance": r"指引|业绩预期|超(?:过)?预期|(?:全年|年度|今年|公司当初的).{0,12}目标|(?:公司|管理层).{0,8}上次说的|(?:抬高|提高|下调|上调).{0,8}(?:上限|下限|低端|高端)|(?:两版|两次|此前|原先).{0,6}(?:预测|展望)|隐含.{0,10}(?:收入|要求|门槛)|(?:旧|原)目标.{0,10}新目标|guidance|(?:annual|quarterly|full.year) (?:target|outlook)|beat (?:the |its )?(?:forecast|outlook)|implied quarter",
    "unit-profit-reconciliation": r"(?:门店|餐厅|单店|店面).{0,12}(?:利润|赚|盈利)|(?:restaurant|store|shop)[- ]level (?:operating |adjusted )?(?:margin|profit|contribution)|(?:margin|profit).{0,16}(?:per store|per restaurant)",
}
FOLLOWUP = re.compile(r"(?:那(?:么)?\s*)?(?:具体(?:怎么(?:算|解释))?|再查一下|继续|这个数怎么(?:算|解释)|怎么(?:算|解释)|按年份(?:呢)?|break (?:it|that) down|how was that calculated)[呢吗啊吧？?。.!\s]*", re.I)


def select_skills(message, previous_questions=()):
    def matches(text):
        clauses = []
        for sentence in re.split(r"[。；;！？!?]", text):
            positive = []
            for clause in re.split(r"[，,]", sentence):
                if re.match(r"\s*(?:不要|不用|不看|别看|跳过|不问|ignore\b|skip\b)", clause, re.I):
                    clauses.append("，".join(positive))
                    positive = []
                else:
                    positive.append(clause)
            clauses.append("，".join(positive))
        return [name for name, pattern in TOPICS.items() if any(re.search(pattern, s, re.I) for s in clauses)]

    direct = matches(message)
    if direct or not FOLLOWUP.fullmatch(message.strip()):
        return direct
    if previous_questions:
        return matches(previous_questions[-1])
    return []


def _issuer_patterns(company):
    if company.get("cik"):
        yield r"\bCIK\s*0*" + re.escape(str(company["cik"]).lstrip("0")) + r"(?!\d)"
    for name in (company["ticker"],company.get("name"),*company.get("aliases", [])):
        if name:
            pattern = re.escape(name)
            yield r"(?<![a-zA-Z0-9])"+pattern+r"(?![a-zA-Z0-9])" if name.isascii() else pattern


def named_issuers(text, companies):
    return [c["ticker"] for c in companies if any(re.search(p,text,re.I) for p in _issuer_patterns(c))]


_SWITCH_TARGET = re.compile(r"(?:换|改)(?:看|查|问|到|成|为)\s*|\bswitch\s+(?:from\s+.{1,55}?\s+)?to\s+", re.I)


def focus_of_switch(message):
    switch = _SWITCH_TARGET.search(message)
    if not switch or _METRIC_OR_PERIOD.match(message[switch.end():]):
        return message
    return message[switch.end():]


_EXCLUDE = re.compile(r"不(?:要|用|再|必)?(?:看|研究|分析|查|讨论|考虑|保留)|别(?:看|查|分析)|排除|剔除|"
                      r"(?:不是|并非)\s*|\b(?:exclude|omit|ignore|skip|drop)\b|\bnot\s+(?!only|just)", re.I)
_ONLY = re.compile(r"(?:只|仅)(?:需要?|要|看|分析|研究|保留|讨论|查|考虑)|"
                   r"\b(?:only|solely|exclusively|I meant)\b", re.I)


def _excluded_issuers(text, companies):
    """Identify coordinated issuer names governed by an exclusion, preserving metric qualifiers."""
    aliases = {p for company in companies for p in _issuer_patterns(company)
               if re.search(p, text, re.I)}
    if not aliases:
        return []
    issuer = "(?:" + "|".join(sorted(aliases, key=len, reverse=True)) + ")"
    join = r"\s*(?:[,，、](?:\s*and\b)?|以及|和|与|及|&|\band\b|\bor\b)\s*"
    group = rf"(?P<issuers>{issuer}(?:{join}{issuer})*)"
    after = re.compile(r"\s*(?:both\s+)?" + group +
                       r"(?=\s*(?:$|[.。;；,，!?！？]|from\b|for now\b|this time\b|了|即可))", re.I)
    before = re.compile(group + r"\s*(?:都|均|也)?\s*$", re.I)
    excluded = []
    for clause in re.split(r"[\n;；。！？!?]", text):
        for directive in _EXCLUDE.finditer(clause):
            target = after.match(clause, directive.end())
            if not target and re.fullmatch(r"\s*(?:了|即可)?\s*(?:[.,，].*)?", clause[directive.end():]):
                subject = clause[:directive.start()]
                if _ONLY.search(subject) or _EXCLUDE.search(subject):
                    subject = re.split(r"[,，]", subject)[-1]
                target = before.search(subject)
            if target:
                excluded.extend(named_issuers(target["issuers"], companies))
    return list(dict.fromkeys(excluded))


def amended_issuers(message, scope, companies):
    """Resolve explicit selection/exclusion directives; ordinary metric negation is not an issuer change."""
    text = re.sub(r"\bnot (?:only|just)\b|不[仅只](?:是)?", "", message, flags=re.I)
    text = re.sub(r"\b(?:rather than|instead of|but not)\s+|而非|而不是", ", exclude ", text, flags=re.I)
    text = re.sub(r"而是|\bbut\s+(?!not\b)", ", ", text, flags=re.I)
    if not _ONLY.search(text) and not _EXCLUDE.search(text) and "不是" not in text and "并非" not in text:
        return None
    selected, other = [], []
    excluded = _excluded_issuers(text, companies)
    for clause in re.split(r"[\n,，;；。！？!?]", text):
        names = [n for n in named_issuers(clause, companies) if n not in excluded]
        if not names:
            continue
        if _ONLY.search(clause) or (_COMPARISON.search(clause) and selected):
            selected.extend(names)
        else:
            other.extend(names)
    if not excluded and not selected:
        return None
    names = selected or other or scope.get("entities", [])
    return list(dict.fromkeys(n for n in names if n not in excluded)), list(dict.fromkeys(excluded))

_ADDITION = re.compile(r"(?:还要|也要|再加上|也(?:查|看)|加上|另(?:加|看))|"
                       r"\b(?:also (?:include|look at|check)|add|as well as)\b", re.I)
_OPEN_PEERS = re.compile(r"同业|同行|竞争对手|其他公司|\b(?:peers?|competitors?)\b", re.I)
_PEER = re.compile(r"两家|两者|同业|同行|竞争对手|其他公司|\b(?:peers?|both companies|competitor[s]?)\b", re.I)
_METRIC_OR_PERIOD = re.compile(r"每股|利润率|按|季度|全年|本季|上(?:一|个)?季|下(?:一|个)?季|(?:第\s*)?[一二三四1-4]\s*财?季|"
                               r"(?:经营|自由|融资|投资)?现金流|库存|销售额|营收|营业收入|20\d{2}|"
                               r"(?:the\s+)?(?:Q[1-4]\b|(?:previous|prior|last|next|first|second|third|fourth) (?:fiscal )?quarter|"
                               r"(?:operating |free |financing |investing )?cash flow|revenue|inventory|(?:operating )?margin|EPS\b)", re.I)
_COMPARISON = re.compile(r"相比|对比|比较|对照|相比之下|(?:跟|和|与).{1,16}比[，,？?。]|"
                         r"\b(?:compar(?:e|ed|ison)|versus|vs|alongside)\b", re.I)
_PERIOD_COMPARISON = re.compile(r"(?:跟|和|与|相对|较|比)\s*(?:去年同期|上年同期|上(?:一|个)?(?:财季|季度)|"
                                r"去年|上年|同一季度)(?:的水平)?\s*(?:相比|比较|对照|对比)?|"
                                r"\b(?:compar(?:e|ed)(?:.{0,38}?)\s+(?:with|to)|versus|vs\.?)\s+"
                                r"(?:the )?(?:last|previous|prior|same) (?:fiscal )?(?:year|quarter)\b", re.I)
_REPORTED_SUBJECT = re.compile(r"(?:^|[。？！!?，,])\s*([\u4e00-\u9fff·]{2,16}?)(?:刚(?:刚)?|已经|最新)?"
                               r"(?:公布|发布|发完|披露)(?=.{0,24}(?:财报|业绩|季报|年报))")


def resolve_issuer_scope(message, previous_questions, scope, companies, *, amendment=None):
    """Return known participants for the planner and a single issuer iff automatic work is unambiguous."""
    if amendment is not None:
        selected, _ = amendment
        return selected, selected[0] if len(selected) == 1 and not _PEER.search(message) else None
    focus = focus_of_switch(message)
    named = named_issuers(focus, companies)
    previous = scope.get("entities", [])
    # Some replay manifests only contain English names. A previous single-issuer
    # filing question may directly name the issuer in the user's own language.
    if len(previous) == 1 and previous[0] not in named:
        past = same_issuer_history(previous_questions, previous[0], companies)
        subjects = {s for q in past for s in _REPORTED_SUBJECT.findall(q)
                    if not re.search(r"和|与|以及|两家|公司们", s)}
        if len(subjects) == 1:
            subject = re.escape(next(iter(subjects)))
            if re.search(subject + r"(?=的|与|和|跟|相比|对比|[，,。！？!?\s]|$)", focus):
                named.append(previous[0])
    if named and previous and focus == message and any(
            named_issuers(clause[directive.end():], companies)
            for clause in re.split(r"[,，;；。！？!?]", focus)
            for directive in _ADDITION.finditer(clause)):
        participants = list(dict.fromkeys(previous + named))
        return participants, participants[0] if len(participants) == 1 else None
    if len(named) > 1:
        return named, None
    if _PEER.search(focus):
        return named or previous, None
    if named and previous and named[0] not in previous and _COMPARISON.search(_PERIOD_COMPARISON.sub(" ", message)):
        return list(dict.fromkeys(previous + named)), None
    active = named[0] if named else (issuer_context(message, previous_questions, scope, companies) if focus == message else None)
    return ([active] if active else (previous if len(previous) > 1 else [])), active


def user_issuer_boundary(message, declared, resolved, prior):
    """Pin fully bound, explicitly requested issuers. For an open peer set, require named participants only."""
    if not declared:
        return None
    focus = focus_of_switch(message)
    adding = bool(_ADDITION.search(focus))
    open_peers = bool(_OPEN_PEERS.search(focus) or
                      (not adding and len(prior) > 1 and len(declared) == 1 and len(resolved) > 1))
    return {"entities":list(declared if open_peers and not adding else resolved), "open_peers":open_peers} if resolved else None


def issuer_decision_conflict(boundary, decision, companies):
    if not boundary:
        return None
    from .context import scope_issuer_keys
    expected = boundary["entities"]
    allowed = scope_issuer_keys(expected, companies)
    excluded = scope_issuer_keys(boundary.get("excluded", []), companies)
    proposed = decision.get("scope", {}).get("entities")
    if proposed is not None:
        actual = scope_issuer_keys(proposed, companies)
        if excluded.intersection(actual) or not all(e.casefold() in actual for e in expected) or (not boundary["open_peers"] and actual != allowed):
            return {"field":"scope.entities","proposed":proposed,"user_entities":expected}
    if decision.get("action") == "tool":
        arguments = decision.get("arguments", {})
        field = "query" if decision.get("tool") == "discover_company" else "entity"
        target = arguments.get(field)
        target_keys = scope_issuer_keys([target], companies) if target else set()
        if target and (excluded.intersection(target_keys) or
                       (not boundary["open_peers"] and not any(e.casefold() in target_keys for e in expected))):
            return {"field":f"{decision.get('tool')}.{field}","proposed":target,"user_entities":expected}
    return None


def same_issuer_history(questions, entity, companies):
    for i in range(len(questions) - 1, -1, -1):
        names = named_issuers(questions[i], companies)
        if names and names != [entity]:
            return questions[i+1:]
    return questions


def issuer_context(message, previous_questions, scope, companies):
    """Bind an issuer only from named companies or an existing single-issuer context."""

    active = named_issuers(message, companies)
    if active:
        return active[0] if len(active) == 1 else None
    # An unresolved switch/comparison must not inherit yesterday's subject.
    if re.search(r"另一家|换(?:成|到|一家公司)|改(?:看|查)|相比.{0,10}公司|instead|other company", message, re.I):
        switch = _SWITCH_TARGET.search(message)
        dimension_change = switch and _METRIC_OR_PERIOD.match(message[switch.end():])
        if not dimension_change and re.search(r"\binstead\b", message, re.I):
            dimension_change = any(re.match(r"\s+instead\b",message[m.end():],re.I) for m in _METRIC_OR_PERIOD.finditer(message))
        if not dimension_change:
            return None
    tickers = set(re.findall(r"(?<![A-Za-z])([A-Z]{2,6})(?![A-Za-z])", message))
    if tickers.difference({"EBIT","EBITDA","EPS","GAAP","FY","CFO","USD","CNY","FCF","US","AP","AR"}):
        return None
    # An unrecognised possessor is not a reference to the previous company.
    # Bare "this quarter's dealer inventory" and "its inventory" stay eligible.
    if re.search(r"(?:这(?:一)?季(?:度)?|上(?:一)?季(?:度)?)[，,\s]*"
                 r"(?!这家公司|该公司|公司|集团|它|其|全球)[\u4e00-\u9fffA-Za-z0-9·-]{2,24}的"
                 r"(?:经销商|库存|现金流|指引|门店|餐厅|利润|销售|营业收入)", message):
        return None
    possessor = re.match(r"(?:请问|看看|再看)?([\u4e00-\u9fff·]{2,18}?)的(?:经销商|库存|现金流|指引|门店|餐厅|利润|销售|营业收入)", message)
    if possessor and not re.fullmatch(r"这(?:一)?季(?:度)?|本季(?:度)?|上(?:一|个)?季(?:度)?|这家公司|该公司|公司|集团|它们|"
                                     r"全球|全公司|经营活动|投资活动|融资活动|管理层|同店", possessor[1]):
        return None
    if re.search(r"\b(?!(?i:company|group|management|issuer)\b)[A-Z][A-Za-z.-]+['’]s\s+"
                 r"(?:dealer|inventory|(?:operating )?cash flow|guidance|sales|revenue)", message):
        return None
    if "entities" in scope:
        entities = scope["entities"]
        return entities[0] if len(entities) == 1 else None
    for question in reversed(previous_questions):
        earlier = named_issuers(question, companies)
        if earlier:
            return earlier[0] if len(earlier) == 1 else None
        if re.search(r"换一家公司|换到|改看|另一家|switch to", question, re.I):
            return None
    return None


@lru_cache(maxsize=1)
def catalog():
    """Read only trusted packaged method names and descriptions; never the research examples."""
    entries = []
    for directory in files("verifin").joinpath("skills").iterdir():
        if not directory.is_dir() or not directory.joinpath("SKILL.md").is_file():
            continue
        body = directory.joinpath("SKILL.md").read_text(encoding="utf-8")
        meta = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---", body)
        if not meta or meta[1] != directory.name:
            raise ValueError(f"Invalid packaged skill metadata: {directory.name}")
        entries.append((meta[1], meta[2]))
    return tuple(sorted(entries))


def checked_selection(value):
    known = dict(catalog())
    if not isinstance(value, list) or any(not isinstance(n, str) or n not in known for n in value):
        raise ValueError("research_skills must be an array of registered IDs")
    return list(dict.fromkeys(value))


@lru_cache(maxsize=None)
def role_sections(name):
    if name not in dict(catalog()):
        return {}
    card = files("verifin").joinpath("skills", name, "SKILL.md").read_text(encoding="utf-8")
    pieces = re.split(r"^## (Planner|Researcher|Critic)\s*$", card, flags=re.M)
    return {role.lower(): section.strip() for role, section in zip(pieces[1::2], pieces[2::2])}


def instructions(names, role):
    return "\n\n".join(f"[{name}]\n{role_sections(name)[role]}" for name in names if role in role_sections(name))
