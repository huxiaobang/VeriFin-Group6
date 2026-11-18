"""Read an issuer's dated diluted EPS reconciliation and isolate one disclosed tax row."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re

from .data import stable_id
from .revenue_bridge import dom, layout, numeric
from .structured_documents import _hidden
from .scope import after_cutoff_prefix
from .skill_calculations import quarter_mentions

DURATION = re.compile(r"(?:(three|twelve|3|12) months?|((?:fiscal )?years?|quarters?)) ended", re.I)
DATE = re.compile(r"[A-Z][a-z]+\.? \d{1,2},? \d{4}")
TAX_ITEM = re.compile(r"valuation allowance|valuation reserve|tax (?:reform|settlement|audit|legislation|credit)|"
                      r"(?:discrete|one[- ]time|withholding|deferred|transition|repatriation) (?:income )?tax", re.I)
GAAP = re.compile(r"(?=.*\bgaap\b)(?=.*\bdiluted\b)(?=.*(?:per (?:common )?share|\beps\b))", re.I)
NON_GAAP = re.compile(r"non[\s-]*gaap|adjusted", re.I)
SCOPE = re.compile(r"预测|预期|明年|下一季|去年同期|上(?:一|个)?季度|prior year|last year|forecast|outlook", re.I)
CASH = re.compile(r"现金|实付|实缴|实际付|退税|cash|pay(?:ment|able|ing)?|paid", re.I)
NON_CASH = re.compile(r"\bnon[- ]cash (?:income tax )?(?:charge|expense|benefit|item|adjustment)",re.I)
FAMILIES = [(r"递延|备抵|估值准备|deferred|valuation allowance",r"deferred|valuation allowance|valuation reserve"),
            (r"税收抵免|tax credit",r"tax credits?"),(r"预提税|withholding",r"withholding"),
            (r"税改|tax reform",r"tax reform|tax legislation"),(r"和解|settlement",r"settlement")]


@dataclass
class Line:
    name: str
    row: list


def target_period(question, history, quarter_end, period_start):
    """Retain explicit table durations even for 52-week years or fourth-quarter releases."""
    def duration(text):
        annual=bool(re.search(r"全年|年度|财年(?!第|季度)|\bannual\b|full.year|\bFY\s*20\d\d\b",text,re.I))
        quarterly=bool(re.search(r"季度|[一二三四]季|这季|当季|本季|\bquarter\b|\bQ[1-4]\b",text,re.I))
        if annual and quarterly: return -1
        return 12 if annual else 3 if quarterly else None
    months=duration(question)
    if quarter_end and period_start:
        days=(date.fromisoformat(quarter_end)-date.fromisoformat(period_start)).days+1
        scoped_months=3 if 75<=days<=105 else 12 if 350<=days<=380 else -1
        if months is not None and months!=scoped_months:return -1,set()
        months=scoped_months
    if months is None and history: months=duration(history[-1])
    years=set(re.findall(r"\b(20\d\d)\b|(?<!\d)(20\d\d)(?=年|\s*全年|财年)",question))
    years={a or b for a,b in years}
    return months, years


def iso_date(text):
    if not DATE.fullmatch(text):return None
    for fmt in ("%B %d %Y","%b %d %Y"):
        try:return datetime.strptime(text.replace(",","").replace(".",""),fmt).date().isoformat()
        except ValueError:continue
    return None


def period_columns(row,durations):
    dates=[]
    for span,txt,_ in row:
        end=iso_date(txt)
        if not end:continue
        covers={months for group,months in durations if all(i in group for i in span)}
        if len(covers)!=1:return []
        dates.append((span,end,covers.pop()))
    return dates


def value_in(row,span):
    pieces=[(str(v).strip(),node) for sub,v,node in row if v.strip() and any(i in span for i in sub)]
    if any(node.get("rowspan","1")!="1" for _,node in pieces):return None
    raw="".join(v for v,_ in pieces).replace("$","").strip()
    value=numeric(raw)
    if value is None:return None
    numeric_node=next(node for txt,node in pieces if re.search(r"\d",txt))
    return value, numeric_node.getroottree().getpath(numeric_node),raw


def label(row):
    return next((txt for span,txt,_ in row if span.start==0), "")


def sections(tree):
    for table in tree.xpath("//table"):
        if any(_hidden(n) for n in (table,*table.iterancestors())):continue
        try:rows=layout(table)
        except (TypeError, ValueError):continue
        durations=[]; cols=[]; start=None; items=[]
        for row in rows:
            if any(_hidden(n) for _,_,cell in row for n in (cell,cell.getparent())):
                start=None; continue
            keyed=[]
            for span,txt,_ in row:
                m=DURATION.fullmatch(txt)
                if m:keyed.append((span,3 if (m[1] or m[2]).lower() in ('3','three','quarter','quarters') else 12))
            if keyed:durations=keyed;cols=[];start=None;continue
            if any(iso_date(txt) for _,txt,_ in row):
                cols=period_columns(row,durations);start=None;continue
            name=label(row)
            if GAAP.search(name) and not NON_GAAP.search(name):
                start=Line(name,row); items=[]
            elif start and NON_GAAP.search(name) and re.search(r"per (?:common )?share|\beps\b",name,re.I):
                yield start,items,Line(name,row),cols
                start=None
            elif start and name:
                items.append(Line(name,row))


def reconciled(base,items,footer,span):
    """Check that printed signed adjustment rows run from reported EPS to adjusted EPS."""
    opening=value_in(base.row,span)
    closing=value_in(footer.row,span)
    if not opening or not closing:return False
    values=[opening[0]]
    for item in items:
        value=value_in(item.row,span)
        if value:
            values.append(value[0])
            continue
        printed="".join(v.strip() for sub,v,_ in item.row if any(i in span for i in sub))
        if printed not in ("—","–","-"):return False
    # Each printed monetary number was rounded; compare within the sum of its half-units.
    tolerance=sum(Decimal(1).scaleb(v.as_tuple().exponent)/2 for v in [*values,closing[0]])
    return abs(sum(values)-closing[0])<=tolerance


def same_item(name):
    # Reconciliation notes often switch only the plurality of the row caption.
    return [w.rstrip("s") for w in re.findall(r"[a-z]+",name.lower())]


def noncash_source(tree, name, end):
    for tr in tree.xpath("//tr"):
        if any(_hidden(n) for n in (tr,*tr.iterancestors())):continue
        cells=tr.xpath("./th|./td")
        if len(cells)<2:continue
        caption=" ".join(cells[0].text_content().split())
        if same_item(caption)!=same_item(name):continue
        for cell in cells[1:]:
            if _hidden(cell):continue
            sentence=next((s for s in re.split(r"(?<=[.!?])\s+", " ".join(cell.text_content().split()))
                           if NON_CASH.search(s) and not re.search(r"\b(?:not|may|would|could|forecast|expected)\b",s,re.I)),None)
            if sentence:
                stated_years = set(re.findall(r"\b(?:19|20)\d{2}\b", sentence))
                if not stated_years or stated_years == {end[:4]}:
                    return cell.getroottree().getpath(cell), sentence
    return None


def cite_text(f,doc,location,quote):
    key=stable_id("ev_",[doc["url"],doc["snapshot_hash"],location,quote])
    f.evidence[key]={"id":key,"kind":"document","entity":doc["entity"],"locator":location,
                     "text":quote,"source_url":doc["url"],"published_at":doc["published_at"],
                     "snapshot_hash":doc["snapshot_hash"],"snapshot_path":doc["source"],
                     "source_form":doc["source_form"]}
    return key


def analyze(question, history, docs, findings, *, quarter_end=None, period_start=None):
    question=after_cutoff_prefix(question)
    months,years=target_period(question,history,quarter_end,period_start)
    if months not in (3,12) or len(years)>1 or SCOPE.search(question):
        findings.gap("需要明确已报年度或季度和具体税项，才能选择摊薄每股调节表的对应列。")
        return
    if months==3 and not quarter_end and re.search(r"第?[一二三四1234]季|\bQ[1-4]\b",question,re.I):
        targets={label for _,label in quarter_mentions(question)}
        matching_ends=set()
        for doc in docs:
            if not doc.get("period_end") or not doc["name"].lower().endswith((".html",".htm")):continue
            title=" ".join(dom(doc["raw"]).xpath("//title/text()"))
            quarter=re.search(r"\b(first|second|third|fourth)[- ]quarter\b",title,re.I)
            fiscal_years=set(re.findall(r"\b20\d\d\b",title))
            if quarter and len(fiscal_years)==1:
                ordinal=1+("first","second","third","fourth").index(quarter[1].lower())
                if f"{fiscal_years.pop()}Q{ordinal}" in targets:matching_ends.add(doc["period_end"])
        if len(matching_ends)!=1:
            findings.gap("需要定位所指财季的期末，避免把全年或另一个财季的每股调整用于本问。")
            return
        quarter_end=matching_ends.pop()
    target=[rule for alias,rule in FAMILIES if re.search(alias,question,re.I)]
    parsed=[]
    for doc in docs:
        if not doc["name"].lower().endswith((".html",".htm")):continue
        tree=dom(doc["raw"])
        for base,items,footer,cols in sections(tree):
            for span,end,window in cols:
                if window==months and (not years or end[:4] in years) and (not quarter_end or end==quarter_end):
                    parsed.append((end,doc,tree,base,items,footer,span))
    if not parsed:
        findings.gap("未找到所问期间同时列出已报摊薄 EPS 与调整后 EPS 的发行人原表。")
        return
    latest=quarter_end or max(x[0] for x in parsed)
    if not quarter_end and not years:
        latest=max([latest]+[d["period_end"] for d in docs if d.get("period_end")])
    quotes=[]
    marked=[]
    for end,doc,tree,base,items,footer,span in parsed:
        if end!=latest:continue
        if not reconciled(base,items,footer,span):continue
        for item in items:
            if not TAX_ITEM.search(item.name):continue
            if target and not all(re.search(rule,item.name,re.I) for rule in target):continue
            a,b=value_in(base.row,span),value_in(item.row,span)
            if a and b:
                quotes.append((doc,tree,base,item,a,b))
            elif a:
                cells = [(s.strip(),node) for sub,s,node in item.row if s.strip() and any(i in span for i in sub)]
                if len(cells)==1 and cells[0][0] in {"—","–","-"}:
                    printed, node=cells[0]
                    marked.append((doc,item.name,node.getroottree().getpath(node),printed))
    values={(tuple(same_item(i.name)),a[0],b[0]) for _,_,_,i,a,b in quotes}
    if not values and len({tuple(same_item(name)) for _,name,_,_ in marked})==1:
        doc,name,path,printed=marked[-1]
        excerpt=f"{name} | {months} months ended {latest} | {printed}"
        citation=cite_text(findings,doc,path,excerpt)
        detail=f"截至 {latest} 的{months}个月，发行人每股调节表中 {name} 一行列为“{printed}”，没有单列该期间的每股调整金额。"
        findings.note(detail,[citation],kind="discrete_tax_missing",period_end=latest)
        findings.gap(detail)
        return
    if len(values)!=1:
        findings.gap("该期未找到唯一、可复核的单项税费每股调节值；税费同比变化及公司整体调整后 EPS 均不能替代。")
        return
    doc,tree,base,item,a,b=quotes[-1]
    window=f"{months} months ended {latest}"
    unit="$/share" if "$" in "".join(s for _,s,_ in base.row) else "reporting currency/share"
    def fact(line,obs):
        v,xpath,printed=obs
        key=findings.fact(doc,f"{xpath} / {line.name} / {window} / {printed}",line.name,v,unit,window)
        findings.evidence[key]["period_end"]=latest
        return key
    reported,adjustment=fact(base,a),fact(item,b)
    calc=findings.calc("reported diluted EPS excluding the identified tax item","sum",[reported,adjustment],unit)
    signed=f"{b[0]:+f}"
    impact="降低" if b[0]>0 else "提高" if b[0]<0 else "未改变"
    remainder="，剔除后仍为亏损" if findings.value(calc)<0 else ""
    findings.note(f"截至 {latest} 的{'全年' if months==12 else '单季'}，仅冲回 {item.name}："
                  f"该项{impact}已报摊薄 EPS {abs(b[0])} {unit}。{a[0]} 加上调节 {signed} = {findings.value(calc)} {unit}（单项试算）{remainder}。",
                  [reported,adjustment,calc], kind="discrete_tax_eps", period_end=latest)
    if CASH.search(question):
        quote=noncash_source(tree,item.name,latest)
        if quote:
            key=cite_text(findings,doc,*quote)
            findings.note(f"发行人将 {item.name} 列为非现金项目，该笔会计调整本身没有相应的当期现金收付。",[adjustment,key], kind="discrete_tax_cash",period_end=latest)
            if re.search(r"总共|合计|全部|总额|total.{0,18}(?:tax|paid)|cash taxes paid",question,re.I):
                findings.gap("确认该笔计提为非现金；尚需发行人整期实付所得税的独立披露。")
        else:
            findings.gap("尚缺可对应到该笔税项的现金性质或付款说明；EPS 调整额不能直接视为当期缴税。")


def answer_parts(parts, notes, gaps, fiscal_target=None):
    """Expose only tax findings that address each question, with their original records."""
    fiscal_target = fiscal_target or {}
    own = {n["kind"]: n for n in notes if n.get("kind") in ("discrete_tax_cash", "discrete_tax_eps")
           and fiscal_target.get("period_end") in (None, n.get("period_end"))}
    if not own:
        marked = [n for n in notes if n.get("kind")=="discrete_tax_missing"
                  and fiscal_target.get("period_end") in (None,n.get("period_end"))]
        if marked:
            return {k: {"status":"gap","missing_support":marked[0]["text"],
                        "evidence_ids":marked[0]["evidence_ids"],"quarter":fiscal_target.get("quarter")}
                    for k,q in parts.items() if re.search(r"税|\btax|每股|\beps\b",q,re.I)}
        identified = [g for g in gaps if g.startswith(("该期未找到唯一、可复核的单项税费",
                      "需要定位所指财季的期末", "未找到所问期间同时列出已报摊薄 EPS",
                      "需要明确已报年度或季度和具体税项"))]
        if not identified:
            return {}
        return {k: {"status":"gap","missing_support":" ".join(identified),
                    "quarter":fiscal_target.get("quarter")}
                for k,q in parts.items() if re.search(r"税|\btax|每股|\beps\b",q,re.I)}
    answers = {}
    for key, question in parts.items():
        wants_cash = bool(CASH.search(question) or re.search(r"钱.{0,12}(?:付|缴|退)", question))
        wants_eps = bool(re.search(r"每股|亏损|还亏|亏多少|赚多少|eps\b|earnings per share", question, re.I))
        # A tax cash question alone cannot be answered with a corporate-wide cash-flow bridge.
        if re.search(r"经营现金|自由现金流|cash from operations|free cash flow",question,re.I):
            wants_cash = False
        selected = []
        if wants_eps and "discrete_tax_eps" in own:
            selected.append(own["discrete_tax_eps"])
        if wants_cash and "discrete_tax_cash" in own:
            selected.append(own["discrete_tax_cash"])
        missing = [g for g in gaps if g.startswith(("尚缺可对应到该笔税项", "确认该笔计提为非现金"))] if wants_cash else []
        if not selected and not missing:
            continue
        item = {"quarter": fiscal_target.get("quarter"),
                "evidence_ids": list(dict.fromkeys(ref for note in selected for ref in note["evidence_ids"])),
                "calculation_ids": list(dict.fromkeys(ref for note in selected for ref in note["calculation_ids"]))}
        if selected:
            item["answer_sentence"] = " ".join(n["text"] for n in selected)
        if missing:
            item["missing_support"] = " ".join(missing)
        item["status"] = "partial" if selected and missing else "source_excerpt" if selected else "gap"
        answers[key] = item
    return answers
