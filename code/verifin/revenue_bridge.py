"""Identify reconciled, dated sales movement components in native HTML tables."""
from __future__ import annotations
import calendar
from datetime import date
from decimal import Decimal as D
from hashlib import sha256
import json
import re


MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
DATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
DURATION = re.compile(r"\b(?:(three|six|nine|twelve|3|6|9|12)\s+months?|(?:fiscal\s+)?year|quarter)\s+ended\b", re.I)
NMONTH = {"three":3, "six":6, "nine":9, "twelve":12}
AMOUNT = re.compile(r"(?:\b(USD|CAD|EUR|GBP|US dollars|U\.S\. dollars|Canadian dollars|dollars)\s+in\s+(thousands|millions|billions)\b|\b(in)\s+(thousands|millions|billions)\s+of\s+(USD|CAD|EUR|GBP|US dollars|Canadian dollars|dollars)\b)",re.I)
METRIC = re.compile(r"\b(?:changes?|increase|decrease|movements?)\s+(?:in|of)\s+([^():;]+?)(?=\s+(?:for\s+the\s+period|during|from\s+the\s+prior)|[():;]|$)",re.I)
REVENUE = re.compile(r"\b(?:revenues?|sales|turnover)\b",re.I)
OTHER = re.compile(r"\b(?:costs?|expenses?|margin|ratio|growth rate|tax|receivables?|liabilit(?:y|ies)|unearned|deferred|per share)\b",re.I)
MARKER = re.compile(r"^(?:changes?\s+from|changes?\s+(?:due|attributable)\s+to|effects?\s+of|drivers?\s+of\s+(?:the\s+)?change)\s*:?$",re.I)
SCALE = {"thousands":1000, "millions":1000000, "billions":1000000000}
CURRENCY = {"us dollars":"USD","u.s. dollars":"USD","canadian dollars":"CAD"}
NUM = re.compile(r"^(?:[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\(\d+(?:,\d{3})*(?:\.\d+)?\))$")
ALIAS_TOKEN = {"restaurants":"restaurant","stores":"store","units":"unit","locations":"location","effects":"effect",
               "revenues":"revenue","expenses":"expense","items":"item","customers":"customer","products":"product"}


def simple(text):
    return " ".join(text.split())


def words(node):
    return simple("".join(node.itertext()))


def key(text):
    return " ".join(ALIAS_TOKEN.get(w,w) for w in re.findall(r"[a-z0-9]+", text.lower()))


def fingerprint(obj):
    return sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()


def month_start(end, months):
    if end.day != calendar.monthrange(end.year,end.month)[1]:
        raise ValueError("Only calendar-month-end fiscal periods supported")
    idx=end.year*12+end.month-months
    return date(idx//12,idx%12+1,1)


def period_end(label):
    dates=list(DATE.finditer(label))
    if len(dates)!=1: return None
    m,d,y=dates[0].groups()
    return date(int(y),MONTHS[m.lower()],int(d))


def dom(data):
    first=re.search(br"<html\b.*?</html\s*>",data,re.S|re.I)
    from lxml import html
    return html.fromstring(first.group() if first else data,parser=html.HTMLParser(encoding="utf-8",no_network=True))


def nearby(table):
    """Use a caption or contiguous immediately preceding paragraphs, never cross another table."""
    captions=[(x,words(x)) for x in table.xpath("./caption") if words(x)]
    cursor=table
    while cursor.getprevious() is None and cursor.getparent() is not None and cursor.getparent().tag in ("div","section"):
        cursor=cursor.getparent()
    left=[]
    for sib in cursor.itersiblings(preceding=True):
        if sib.tag not in ("div","p","h2","h3","h4") or sib.xpath(".//table"): break
        txt=words(sib)
        if not txt: continue
        if len(txt)>360: break
        left.append((sib,txt))
        if len(left)>=2 or METRIC.search(txt):break
    return captions+left


def section(table):
    headings=table.xpath("preceding::*[self::h1 or self::h2 or self::h3 or self::h4 or "
                         "((self::p or self::div) and not(.//div or .//p or .//table) and "
                         "(.//b or .//strong or .//*[contains(@style,'font-weight:700')]))]"
                         "[not(ancestor::table)]")
    return next(((n,words(n)) for n in reversed(headings)
                 if 0<len(words(n))<=100 and not words(n).endswith(('.',':'))), (None,""))


def layout(table):
    rows=[]
    occupied={}
    for y,tr in enumerate(table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")):
        cells=[]
        x=0
        for td in tr.xpath("./td|./th"):
            while (y,x) in occupied:x+=1
            width=int(td.get("colspan",1));height=int(td.get("rowspan",1))
            if min(width,height)<1 or max(width,height)>250:raise ValueError("unsupported HTML span")
            cols=range(x,x+width)
            for yy in range(y,y+height):
                for xx in cols:
                    if (yy,xx) in occupied: raise ValueError("overlapping HTML cells")
                    occupied[yy,xx]=td
            cells.append((cols,words(td),td))
            x+=width
        rows.append(cells)
    return rows


def numeric(text):
    text=text.replace("−","-").strip()
    if not NUM.fullmatch(text):return None
    return D(text.replace(",","").replace("(","-").replace(")",""))


def field(row, span):
    hits=[(t,n,numeric(t)) for cols,t,n in row if set(cols)&set(span) and t and t not in ("$","€","£","USD","CAD","EUR","GBP")]
    if len(hits)!=1 or hits[0][2] is None: raise ValueError("ambiguous, missing or nonmonetary numeric cell")
    return hits[0]


def duration(text):
    matches=list(DURATION.finditer(text))
    if len(matches)!=1:raise ValueError("unsupported or ambiguous column duration")
    a=matches[0]
    word=(a.group(1) or "").lower()
    if not word:return 3 if "quarter" in a.group().lower() else 12
    return NMONTH[word] if word in NMONTH else int(word)


def first_label(row):
    return next(((t,n) for _,t,n in row if t),("",None))


def monetary(sources, currencies):
    found=[]
    for node,text in sources:
        for match in AMOUNT.finditer(text):
            cc,scale,_,scale2,cc2=match.groups()
            cc=(cc or cc2).lower()
            if cc == "dollars":
                if len(currencies)!=1: raise ValueError("ambiguous dollar currency")
                cc=next(iter(currencies))
                if cc not in {"USD","CAD","AUD","NZD","HKD","SGD"}:raise ValueError("dollar notation conflicts with filing currency")
            found.append((CURRENCY.get(cc,cc.upper()),(scale or scale2).lower(),node,text))
    if len({a[:2] for a in found})!=1: raise ValueError("missing or conflicting money unit/scale")
    return found[0]


def extract_bridge(doc, t, data_sha, url, currencies, table_no):
    ctxt=nearby(t)
    metric_parts=[(node,txt,METRIC.search(txt)) for node,txt in ctxt]
    relevant=[(node,txt,mt.group(1).strip()) for node,txt,mt in metric_parts if mt]
    if not relevant:return [],"no adjacent named movement caption"
    if len({key(s) for _,_,s in relevant})!=1:return [],"ambiguous movement metric"
    title_node,title,metric=relevant[0]
    if not REVENUE.search(metric) or OTHER.search(metric):return [],"movement relates to another metric"
    rows=layout(t)
    labels=[first_label(row)[0] for row in rows]
    marker=[i for i,v in enumerate(labels) if MARKER.fullmatch(v)]
    if len(marker)!=1:return [],"no unique movement section (levels/percent-only/narrative not sufficient)"
    pivot=marker[0]
    dated=[(i,period_end(lbl)) for i,lbl in enumerate(labels) if re.search(r"\b(?:period|year|months?|quarter)\s+(?:ended|ending)\b",lbl,re.I) and period_end(lbl)]
    if len(dated)!=2 or not dated[0][0]<pivot<dated[1][0]:return [],"missing dated opening/closing revenue levels"
    (start,prior),(end,cur)=dated
    if cur.year!=prior.year+1 or cur.month!=prior.month or cur!=date.fromisoformat(doc["period_end"]):
        return [],"amounts not a same-period year-over-year reconciliation"
    if any("restat" in s.lower() or "recast" in s.lower() for s in [title,labels[start],labels[end]]):
        return [],"restated or recast boundary requires manual comparison"
    tree=t.getroottree()
    where=lambda node:tree.getpath(node)
    section_node,section_text=section(t)
    leading=[(node,txt) for row in rows[:start] for _,txt,node in row if txt]
    if any(re.search(r"%|\bpercent|basis points|per share",txt,re.I) for _,txt in leading+ctxt):
        return [],"mixed percentage, per-share or amount units"
    currency,scale,u_node,u_text=monetary(leading+ctxt,currencies)
    columns=[(span,txt,node) for row in rows[:start] for span,txt,node in row if DURATION.search(txt)]
    if not columns:return [],"no duration bound to a column"
    if len({duration(txt) for _,txt,_ in columns})!=len(columns):return [],"ambiguous cumulative column headers"
    output=[]
    local_anchor=t.xpath("preceding::*[@id and (self::div or self::h2 or self::h3)][1]/@id")
    for span,head,head_node in columns:
        months=duration(head)
        dates={"current_start":str(month_start(cur,months)),"current_end":str(cur),
               "comparison_start":str(month_start(prior,months)),"comparison_end":str(prior)}
        if dates["current_start"][5:]!=dates["comparison_start"][5:]:raise ValueError("prior boundaries not aligned")
        a=field(rows[start],span); b=field(rows[end],span)
        factors=[]
        for row in rows[pivot+1:end]:
            label,lbl_node=first_label(row)
            if not label:continue
            if numeric(label) is not None or "%" in label or not re.search("[a-zA-Z]",label):
                raise ValueError("factor row lacks descriptive label")
            raw,cell,val=field(row,span)
            factors.append((label,where(lbl_node),raw,where(cell),val))
        if not factors or len({key(r[0]) for r in factors})!=len(factors):raise ValueError("missing or ambiguous change-factor rows")
        delta=b[2]-a[2]
        diff=sum(r[4] for r in factors)-delta
        ticks=[D(1).scaleb(x.as_tuple().exponent) for x in [a[2],b[2],*[r[4] for r in factors]]]
        tolerance=sum(ticks)/2
        if abs(diff)>tolerance:raise ValueError("factors fail to reconcile to opening and closing levels")
        cols={"heading":head,"heading_xpath":where(head_node),"columns_one_based":[span.start+1,span.stop],
              "header_stack":[{"text":txt,"xpath":where(node)} for row in rows[:start] for rng,txt,node in row
                              if txt and set(rng)&set(span)]}
        source={"snapshot_sha256":data_sha,"source":doc["source"],"url":url,"published_at":doc["published_at"],
                "form":doc["form"],"table_number":table_no,"table_xpath":where(t),"caption_xpath":where(title_node),
                "caption":title,"unit_xpath":where(u_node),"printed_unit":u_text,
                "change_marker":labels[pivot],"change_marker_xpath":where(first_label(rows[pivot])[1]),
                "section_url":url+("#"+local_anchor[0] if local_anchor else ""),
                "section_title":section_text,"section_title_xpath":where(section_node) if section_node is not None else None}
        def level(index,cell):
            return {"row_label":labels[index],"row_label_xpath":where(first_label(rows[index])[1]),"printed_amount":cell[0],
                    "cell_xpath":where(cell[1]),"value":str(cell[2])}
        bridge=dict(entity=doc["entity"],metric=metric,metric_key=key(metric),scope=key(doc.get("business_scope","")+" "+section_text),
                    comparison="same fiscal interval one year earlier",currency=currency,scale=scale,
                    months=months,**dates,header=cols,source=source,opening=level(start,a),closing=level(end,b),
                    net_movement=str(delta),component_rounding_residual=str(diff),rounding_bound=str(tolerance))
        bridge["id"]="b_"+fingerprint([data_sha,where(t),where(head_node)])[:20]
        bridge["factors"]=[]
        for label,label_xpath,raw,xpath,val in factors:
            fid="n_"+fingerprint([data_sha,xpath])[:20]
            bridge["factors"].append({"id":fid,"label":label,"factor_key":key(label),"row_label_xpath":label_xpath,
                                      "printed_amount":raw,"value":str(val),"cell_xpath":xpath,
                                      "display_quantum":str(D(1).scaleb(val.as_tuple().exponent)),
                                      "measure_kind":"contribution_to_year_over_year_change"})
        output.append(bridge)
    return output,"accepted"


def bridge_cells(raw, doc, info, document_id):
    from .data import stable_id

    tree = dom(raw)
    measures = tree.xpath('//*[local-name()="measure" or name()="xbrli:measure"]')
    currencies = {words(node).split(":")[-1] for node in measures
                  if re.fullmatch(r"iso4217:[A-Z]{3}", words(node))}
    cells = []
    for number, table in enumerate(tree.xpath("//table[not(.//table)]"), 1):
        try:
            bridges, _ = extract_bridge(doc, table, info["sha256"], info["url"], currencies, number)
        except (ValueError, OverflowError):
            continue
        for bridge in bridges:
            if not bridge["scope"]:
                continue
            categories = sorted(f["factor_key"] for f in bridge["factors"])
            for factor in bridge["factors"]:
                value = D(factor["value"]) * SCALE[bridge["scale"]]
                cells.append({
                    "kind": "financial_fact", "cell_type": "revenue_bridge_cell", "id": stable_id("cell_", [document_id, factor["id"]]),
                    "document_id": document_id, "entity": doc["entity"], "published_at": doc["published_at"],
                    "source_url": info["url"], "snapshot_hash": info["sha256"], "snapshot_path": doc["source"],
                    "locator": factor["cell_xpath"], "row_label": factor["label"],
                    "printed_value": factor["printed_amount"], "printed_unit": bridge["source"]["printed_unit"],
                    "column_heading": bridge["header"]["heading"], "metric": factor["factor_key"],
                    "concept": "issuer:revenue_change_contribution", "basis": bridge["metric_key"],
                    "business_scope": bridge["scope"], "factor_categories": categories,
                    "period_start": bridge["current_start"], "period_end": bridge["current_end"],
                    "comparison_start": bridge["comparison_start"], "comparison_end": bridge["comparison_end"],
                    "value": int(value) if value == value.to_integral_value() else float(value),
                    "unit": bridge["currency"], "currency": bridge["currency"], "scale": 1,
                })
    return cells


