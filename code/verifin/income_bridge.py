"""Build a source-addressed year-over-year bridge from operating income to net income."""
from __future__ import annotations

from decimal import Decimal
from itertools import product
from pathlib import Path

from .data import available_by, stable_id
from .statement_analysis import Findings


def _signs(label, cells, role):
    from .income_bridge_source import semantic_sign
    if role in {"operating","subtotal","net"}:
        return [1]
    tagged = {c.tag for c in cells if c and c.tag}
    if len(tagged)>1:
        return []
    signs=set()
    for c in cells:
        if c and c.tag and c.number:
            normal=semantic_sign(c.tag,label,role)
            if normal is None:
                return []
            signs.add(normal * c.ix_sign * (1 if c.number>0 else -1))
    if signs:
        return list(signs) if len(signs)==1 else []
    normal=semantic_sign(next(iter(tagged),None),label,role)
    if normal is None:
        return []
    if normal==-1 and any(c and c.number<0 for c in cells):
        # Untagged statements may print expenses as deductions in parentheses,
        # or show a benefit as a negative expense. Require a unique sign that
        # balances the printed levels; do not fill a missing amount by residual.
        return [-1,1]
    return [normal]


def _balanced_columns(rows, signs):
    """Compare printed intermediate levels and net income, for each period."""
    tested, errors = 0,0
    for side in range(2):
        level=None
        for row,sign in zip(rows,signs):
            cell=row["cells"][side]
            val=None if cell is None else cell.number
            if row["role"]=="operating":
                level=val
            elif row["role"] in {"subtotal","net"}:
                if level is not None and val is not None:
                    tested+=1
                    errors+=level!=val
                level=val
            else:
                level=None if level is None or val is None or not sign else level+sign*val
    return tested,errors


def _rows(source):
    from .income_bridge_source import role_for
    rows=[]
    for label,cells,node in source["physical"]:
        role,metric,display=role_for(label)
        rows.append(dict(label=label,role=role,metric=metric,display_label=display,cells=cells,node=node,
                         signs=_signs(label,cells,role)))
    alternatives=[r["signs"] or [None] for r in rows]
    ambiguous=[i for i,v in enumerate(alternatives) if len(v)>1]
    if ambiguous:
        valid=[]
        if len(ambiguous)<=4:
            for candidate in product(*alternatives):
                tested,errors=_balanced_columns(rows,candidate)
                if tested and not errors:
                    valid.append(candidate)
        for i in ambiguous:
            unique={candidate[i] for candidate in valid}
            rows[i]["signs"]=list(unique) if len(unique)==1 else []
    return rows


def _prepare(source):
    rows=_rows(source)
    fs=Findings()
    doc,info=source["doc"],source["info"]
    periods=source["periods"]
    currency=source["currency"]
    gaps=[]
    ids=[]
    seen=set()
    for r in rows:
        if r["metric"] in seen:
            gaps.append("原表含重名项目 "+r["label"]+"，无法唯一归属。")
        seen.add(r["metric"])
        refs=[]
        for j,(p,c) in enumerate(zip(periods,r["cells"])):
            if not c:
                refs.append(None)
                gaps.append(("本季" if j==0 else "上年同期")+"缺少可核验的"+r["display_label"]+"金额。")
                continue
            locator=source["paths"][c.node]
            ref=fs.fact(doc,locator,r["metric"],c.number,currency,p["end"],r["label"])
            fs.evidence[ref].update(cell_type="income_bridge_cell",mode="replay",form=doc["form"],
                document_id=stable_id("source_doc_",info["url"]),snapshot_path=doc["source"],
                basis="US-GAAP",business_scope="consolidated",currency=currency,scale=1,
                period_start=p["start"],period_end=p["end"],row_label=r["label"],
                concept=c.tag or "issuer:"+r["metric"],printed_value=c.printed,printed_unit=source["printed_unit"],
                printed_scale=source["scale"],table_xpath=source["table_locator"],table_title=source["title"],
                table_title_locator=source["title_locator"],row_label_locator=source["paths"][r["node"]],
                column_header_locators=source["quarter_headers"][j],currency_basis=source["currency_basis"],
                currency_reference=source["currency_reference"],
                unit_locator=source["unit_locator"],context_ref=c.context[2].get("id") if c.context else None,
                period_locator=source["paths"][c.context[2]] if c.context else source["quarter_headers"][j][-1],
                currency_locator=source["paths"][c.unit[1]] if c.unit else source["unit_locator"])
            refs.append(ref)
        r["current_id"],r["prior_id"]=refs
        r["sign"]=r["signs"][0] if len(r["signs"])==1 else None
        if r["sign"] is None:
            gaps.append("无法核实 "+r["label"]+" 在净利润中的加减方向。")
        r["contribution_id"]=None
        if all(refs) and r["sign"] is not None:
            operands=refs if r["sign"]==1 else list(reversed(refs))
            cid=fs.calc(r["metric"]+" 对净利润的同比变化","difference",operands,currency)
            r["contribution_id"]=cid
            if r["role"] in {"operating","component"}:
                ids.append(cid)
    _,errors=_balanced_columns(rows,[r["sign"] for r in rows])
    if errors:
        gaps.append("两期原表的中间小计或净利润未能逐列勾稽，不能判定调节完整。")
    net=next(r for r in rows if r["role"]=="net")
    total=net["contribution_id"]
    cid=None
    if not gaps and total and ids:
        if sum((Decimal(str(fs.value(k))) for k in ids),Decimal(0))==Decimal(str(fs.value(total))):
            cid=fs.calc("从营业利润到净利润的同比贡献合计","sum",ids,currency)
        else:
            gaps.append("已识别项目的同比变化之和与原表净利润变化不符。")
    bridge=dict(complete=cid is not None,rows=[{k:v for k,v in row.items() if k not in {"cells","node","signs"}}
                                                for row in rows],gaps=list(dict.fromkeys(gaps)),currency=currency,
                current_period=periods[0],prior_period=periods[1],total_id=total,sum_id=cid,
                source_url=info["url"],snapshot_hash=info["sha256"],table_locator=source["table_locator"],
                title=source["title"])
    return fs,bridge


def analyze(data, entity, cutoff, *, period_end=None, period_start=None):
    """Select an original consolidated statement and return its reproducible records."""
    from .income_bridge_source import extract
    from .statement_amount_cells import prior_currency_evidence
    docs=[d for d in data.manifest["documents"] if d["entity"]==entity and d.get("period_end") and
          available_by(d["published_at"],cutoff) and Path(d["source"]).suffix.lower() in {".htm",".html"} and
          (d.get("form") or "").startswith(("10-Q","10-K","8-K"))]
    target=period_end or max((d["period_end"] for d in docs),default=None)
    docs=[d for d in docs if d["period_end"]==target]
    company=next((co for co in data.manifest["companies"] if co["ticker"]==entity),{})
    options=[]
    for doc in docs:
        info=data.info(doc["source"])
        enriched=dict(doc,url=info["url"],snapshot_hash=info["sha256"],source_form=doc["form"])
        prior=prior_currency_evidence(data,doc,metrics=("operating_income","net_income"))
        for source in extract(enriched,data.raw(doc["source"]),info,company.get("cik"),prior):
            if period_start and source["periods"][0]["start"]!=period_start:
                continue
            fs,bridge=_prepare(source)
            priority=(bridge["complete"],doc["form"].startswith(("10-Q","10-K")),len(fs.evidence))
            options.append((priority,fs,bridge))
    if options:
        priority=max(option[0] for option in options)
        finalists=[option for option in options if option[0]==priority]
        signatures={tuple((r["metric"],fs.value(r["current_id"]) if r["current_id"] else None,
                           fs.value(r["prior_id"]) if r["prior_id"] else None,r["sign"]) for r in b["rows"])
                    for _,fs,b in finalists}
    else:
        finalists=[]
        signatures=set()
    if len(signatures)==1:
        _,fs,bridge=finalists[0]
    else:
        fs=Findings()
        gap=("同一季度的合并原表之间存在冲突，无法唯一核实。" if options else
             "未找到同时可核验本季及上年同期、金额单位与季度范围的合并利润表。")
        bridge=dict(complete=False,rows=[],gaps=[gap],current_period={"start":period_start,"end":target},
                    prior_period={},currency=None,total_id=None,sum_id=None,source_url=None,table_locator=None)
    return {"ok":True,"summary":"已核对原表两期金额及同比变化。" if bridge["complete"] else "已保留可核对的金额及原表缺口。",
            "evidence":list(fs.evidence.values()),"calculations":list(fs.calculations.values()),
            "data":{"income_bridge":bridge}}
