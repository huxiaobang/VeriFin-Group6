"""Read two like-length, year-on-year quarter columns from an original income statement."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from .cashflow_skill import _dates
from .revenue_bridge import dom, first_label, layout
from .statement_amount_cells import AMOUNT, BAD_CONTEXT, CURRENCIES, CURRENCY, DOLLARS, SCALE, SCALE_NOTE, nearby, text
from .structured_documents import _hidden


TITLE = re.compile(r"(?:(?:condensed|consolidated|combined)\s+)*(?:statements? (?:of )?(?:consolidated )?(?:operations|earnings|income)(?: and comprehensive (?:income|loss))?|results of operations|income statements?)(?:\s*\(unaudited\))?", re.I)
OP = re.compile(r"(?:(?:total |consolidated )?operating (?:income|earnings|profit|loss)(?: \((?:income|loss)\))?|(?:profit|income|earnings|loss)(?: \(loss\))? from operations)",re.I)
NET = re.compile(r"(?:consolidated )?net (?:income|earnings|loss)(?: \((?:income|earnings|loss)\))?",re.I)
TAX = re.compile(r"\b(?:income taxes?|provision for (?:income )?taxes|tax (?:expense|benefit))\b",re.I)
PRETAX = re.compile(r"\bbefore\b.*\b(?:tax|taxes|taxation)\b",re.I)
BALANCE = re.compile(r"(?:income|earnings|loss) (?:from (?:consolidated operations|continuing operations)|before equity)",re.I)


def role_for(label):
    s = label.casefold().strip()
    if OP.fullmatch(s):
        return "operating", "operating_income", "营业利润"
    if NET.fullmatch(s):
        return "net", "net_income", "净利润"
    if PRETAX.search(s):
        return "subtotal","pretax_income","税前利润"
    if BALANCE.search(s) or s.startswith("total "):
        return "subtotal","subtotal_" + re.sub(r"[^a-z0-9]+","_",s).strip("_"),label
    if TAX.search(s):
        return "component","income_tax","所得税"
    if "interest" in s and ("expense" in s or "cost" in s):
        return "component","interest_expense","净利息费用" if "net" in s else "利息费用"
    if "interest" in s and "income" in s:
        return "component","interest_income","利息收入"
    if "income" in s and ("equity" in s or "unconsolidated" in s) or "equity in earnings" in s:
        return "component","equity_income","权益法收益"
    key = re.sub(r"[^a-z0-9]+","_",s).strip("_")
    return "component",key,("其他收入净额" if s in {"other income, net","other income (expense), net"} else label)


def semantic_sign(tag, label, row_role):
    """Normal XBRL concept sign relative to earnings, not the displayed sign."""
    if row_role in {"operating","net","subtotal"}:
        return 1
    if tag and tag.startswith("us-gaap:"):
        name = tag.partition(":")[2]
        if name.startswith(("InterestExpense","IncomeTaxExpenseBenefit")):
            return -1
        if name in {"OtherNonoperatingIncomeExpense","NonoperatingIncomeExpense","InvestmentIncomeInterest",
                    "InterestIncomeExpenseNonoperatingNet","IncomeLossFromEquityMethodInvestments",
                    "OtherIncome","InvestmentIncomeNet","InterestIncomeOperating","GainLossOnSaleOfBusiness"}:
            return 1
    s = label.casefold()
    if s.startswith(("benefit from income tax","income tax benefit","interest income","other income","other non-operating income",
                     "equity in earnings","income from unconsolidated","income from equity","gain on")):
        return 1
    if s.startswith(("interest expense","interest (income) expense","interest cost","income tax","provision for",
                     "other expense","loss on","loss from","pension settlement charge","loss related","net interest expense")):
        return -1
    return None


def _inline_resources(root,cik):
    contexts, units = {}, {}
    duplicate_ids=set()
    for el in root.iter():
        key=el.get("id")
        if el.tag in {"xbrli:context","xbrli:unit"} and (key in contexts or key in units):
            duplicate_ids.add(key)
            continue
        if el.tag == "xbrli:context":
            identity = list(el.iter("xbrli:identifier"))
            if (len(identity)!=1 or not cik or str(cik).lstrip("0") != text(identity[0]).lstrip("0")
                    or any(n.tag in {"xbrli:segment","xbrli:scenario"} for n in el.iter())):
                continue
            starts, ends = list(el.iter("xbrli:startdate")), list(el.iter("xbrli:enddate"))
            if len(starts)==len(ends)==1:
                try:
                    start,end=date.fromisoformat(text(starts[0])),date.fromisoformat(text(ends[0]))
                except ValueError:
                    continue
                if start<=end:
                    contexts[key] = (start.isoformat(),end.isoformat(),el)
        elif el.tag == "xbrli:unit" and len(el)==1 and el[0].tag=="xbrli:measure":
            value = text(el[0])
            if re.fullmatch(r"iso4217:[A-Z]{3}",value):
                units[key] = (value[-3:],el)
    for key in duplicate_ids:
        contexts.pop(key,None)
        units.pop(key,None)
    return contexts, units


@dataclass
class Cell:
    number: Decimal
    node: object
    printed: str
    tag: str | None = None
    context: tuple | None = None
    unit: tuple | None = None
    ix_sign: int = 1


def read_cell(row, span, context, units, scale):
    if not row or row[-1][0].stop < span.stop:
        return None
    selected = [(c,v,n) for c,v,n in row if v and c.start < span.stop and c.stop > span.start]
    if any(c.start < span.start or c.stop > span.stop or n.get("rowspan","1") != "1" for c,_,n in selected):
        return None
    printed = "".join(v for _,v,_ in selected)
    match=AMOUNT.fullmatch(printed.replace(" ",""))
    if not match:
        return None
    amount=match["amount"].replace("−","-").replace(",","").replace("(","-").replace(")","")
    number=Decimal(amount)*scale
    entries=[n for _,v,n in selected if re.search(r"\d",v)]
    if len(entries)!=1:
        return None
    node=entries[0]
    facts=list(node.iter("ix:nonfraction"))
    if not facts:
        return Cell(number,node,printed)
    if len(facts)!=1:
        return None
    fact=facts[0]
    ctx,unit=context.get(fact.get("contextref")),units.get(fact.get("unitref"))
    scale_attr=fact.get("scale","0")
    raw=text(fact).replace(",","")
    if (not ctx or not unit or not re.fullmatch(r"[0-9]",scale_attr) or 10**int(scale_attr)!=scale
            or not re.fullmatch(r"\d+(?:\.\d+)?",raw) or Decimal(raw)*scale!=abs(number)
            or fact.get("sign","") not in {"","-"}):
        return None
    return Cell(number,node,printed,fact.get("name"),ctx,unit,-1 if fact.get("sign")=="-" else 1)


def extract(doc, raw, info, cik, prior_units):
    root=dom(raw)
    tree=root.getroottree()
    path=tree.getpath
    contexts,units=_inline_resources(root,cik)
    paths={n:path(n) for n in root.iter() if n.tag in {"table","td","th","p","div","span","caption","h1","h2","h3","h4","xbrli:unit","xbrli:context"}}
    for n in list(root.iter()):
        if n is not root and isinstance(n.tag,str) and _hidden(n) and n.getparent() is not None:
            n.drop_tree()
    result=[]
    for table in root.iter("table"):
        if table.xpath(".//table"):
            continue
        rows=layout(table)
        labels=[first_label(r)[0] for r in rows]
        ops=[i for i,l in enumerate(labels) if OP.fullmatch(l)]
        nets=[i for i,l in enumerate(labels) if NET.fullmatch(l)]
        if len(ops)!=1 or len(nets)!=1 or not ops[0]<nets[0]:
            continue
        first,last=ops[0],nets[0]
        # Match unit and period headings before the body. A % Change column or
        # full-year column outside each selected quarter has no ownership here.
        header_end=next((i for i,l in enumerate(labels[:first]) if l.casefold() in
                         {"net sales","revenue","revenues","total revenues","net revenue","total net sales","cost of sales"}),first)
        head=rows[:header_end]
        notes=[(text(n),n) for n in nearby(table)]+[(v,n) for r in head for _,v,n in r if v]
        title=next(((v,n) for v,n in notes if TITLE.fullmatch(v)),None)
        if not title or any(BAD_CONTEXT.search(v) for v,_ in notes):
            continue
        scales=[(SCALE[m[1].lower()],n) for v,n in notes for m in SCALE_NOTE.finditer(v)]
        if len({n for n,_ in scales})!=1:
            continue
        scale,unit_node=scales[0]
        try:
            columns,heads=_dates(head,False)
        except ValueError:
            continue
        quarters=sorted([p for p in columns if p.months==3],key=lambda p:p.end,reverse=True)
        if len(quarters)!=2 or str(quarters[0].end)!=doc["period_end"] or not 350<=(quarters[0].end-quarters[1].end).days<=380:
            continue
        periods=[{"start":p.start.isoformat() if p.start else None,"end":p.end.isoformat()} for p in quarters]
        physical=[]
        for i in range(first,last+1):
            label=labels[i]
            if not label:
                if any(v for _,v,_ in rows[i]):
                    physical.append(("(unlabelled row)",[None,None],False))
                continue
            # A descriptive header has no cell values. A monetary row with a
            # missing pair still has to be represented as a missing item.
            heading=label.endswith(":") and all(not v for _,v,_ in rows[i][1:])
            if not heading:
                physical.append((label,[read_cell(rows[i],columns[p],contexts,units,scale) for p in quarters],first_label(rows[i])[1]))
        if not physical:
            continue
        currencies={CURRENCIES.get(s.upper(),s.upper()) for v,_ in notes for s in CURRENCY.findall(v)}
        basis="statement heading"
        for j,p in enumerate(quarters):
            dated=[c[j] for _,c,_ in physical if c[j] and c[j].context]
            window={(c.context[0],c.context[1]) for c in dated}
            currencies.update(c.unit[0] for c in dated)
            if window:
                basis="filed XBRL context and currency unit"
                if len(window)!=1 or next(iter(window))[1]!=str(p.end):
                    break
                s=next(iter(window))[0]
                if ((p.start and str(p.start)!=s) or not 70<=(p.end-date.fromisoformat(s)).days+1<=105):
                    break
                periods[j]["start"]=s
        else:
            witnesses=[]
            # Quarterly earnings releases often print an annual comparator in
            # adjacent columns. Its independently filed amount can establish the
            # currency of the table; it is never a quarterly operand.
            for p,span in columns.items():
                if p.months==3 or not p.start:
                    continue
                for index,metric in ((first,"operating_income"),(last,"net_income")):
                    cell=read_cell(rows[index],span,contexts,units,scale)
                    if cell:
                        known=prior_units.get((metric,str(p.start),str(p.end),cell.number),{})
                        currencies.update(known)
                        witnesses.extend(known.values())
            for j,p in enumerate(quarters):
                # Exact company-facts corroboration gives an untagged $ amount
                # a currency, and a 52/53-week issuer its actual quarter start.
                matches=defaultdict(dict)
                for label,cells,_ in physical:
                    m=("operating_income" if OP.fullmatch(label) else "net_income" if NET.fullmatch(label) else None)
                    if not m or not cells[j]:
                        continue
                    for (metric,s,e,val), known in prior_units.items():
                        if (m==metric and str(p.end)==e and val==cells[j].number and
                                70<=(p.end-date.fromisoformat(s)).days+1<=105):
                            matches[s].update(known)
                if len(matches)==1:
                    s,known=next(iter(matches.items()))
                    if periods[j]["start"] and periods[j]["start"]!=s:
                        break
                    periods[j]["start"]=s
                    witnesses.extend(known.values())
                    currencies.update(known)
                if not periods[j]["start"] and not j and doc.get("period_start"):
                    s=doc["period_start"]
                    if 70<=(p.end-date.fromisoformat(s)).days+1<=105:
                        periods[j]["start"]=s
            else:
                if len(currencies)!=1 or not all(p["start"] for p in periods):
                    continue
                if not any(c and c.context for _,cells,_ in physical for c in cells) and witnesses:
                    basis="currency corroborated by published comparison amounts"
                currency=currencies.pop()
                symbols = {symbol for _,cells,_ in physical for cell in cells if cell
                           for symbol in re.findall(r"[$£€]",cell.printed)}
                supported = {"$":DOLLARS, "£":{"GBP"}, "€":{"EUR"}}
                if any(currency not in supported[symbol] for symbol in symbols):
                    continue
                # A witnessed quarter must have comparable durations and the
                # document must bind group scope through its original title.
                days=[(date.fromisoformat(p["end"])-date.fromisoformat(p["start"])).days+1 for p in periods]
                if abs(days[0]-days[1])>8:
                    continue
                result.append(dict(physical=physical, periods=periods, currency=currency, scale=scale,
                    quarter_headers=[[paths.get(n,path(n)) for _,n in heads[p]] for p in quarters],
                    unit_locator=paths[unit_node], printed_unit=text(unit_node),currency_reference=witnesses,
                    currency_basis=basis, paths=paths, table_locator=paths[table], title=title[0],
                    title_locator=paths[title[1]], info=info, doc=doc))
    return result
