"""Reconcile a single quarter from two cumulative, same-start cash flow statements."""
from decimal import Decimal
import re

from .cashflow_delivery import cash_amount, difference_text
from .cashflow_skill import Missing, _bridge_rows, _cell, _fact, _index, _one, CFO, START
from .financial import residual_quarter


def _period_pair(tables, end):
    periods = {p for t in tables if t.kind == "bridge" for p in t.columns}
    pairs = []
    def bounds(p):
        return {"period_start":p.start.isoformat() if p.start else None,"period_end":p.end.isoformat()}
    for later in periods:
        if later.end != end or later.months not in (6, 9, 12):
            continue
        for earlier in periods:
            if earlier.months + 3 != later.months:
                continue
            try:
                window = residual_quarter(bounds(later), bounds(earlier))
            except ValueError:
                continue
            pairs.append((later, earlier, window))
    if len(pairs) != 1:
        raise Missing("没有唯一同财年起点、相隔一个季度的两份累计现金流量表。")
    later, earlier, window = pairs[0]
    latest = _one(tables, later, {"bridge"})
    prior = _one(tables, earlier, {"bridge"})
    if latest.doc["entity"] != prior.doc["entity"] or latest.unit != prior.unit:
        raise Missing("两份累计现金流量表的发行人或金额单位不同，不能相减。")
    return latest, later, prior, earlier, window


def _groups(table, period, result):
    """Represent each cumulative column in its own source taxonomy and verify its totals."""
    net, groups, cash = _bridge_rows(table)
    amounts = [[_cell(table, i, period)[0] for i in group] for group in groups]
    if not all(amounts) or _cell(table,net,period)[0] + sum(map(sum,amounts)) != _cell(table,cash,period)[0]:
        raise Missing("累计现金流量表的明细未能加回经营现金流，只交付已披露的端点差额。")
    group_ids, rows = [], {}
    for group_num, indices in enumerate(groups):
        refs = []
        for i in indices:
            label = table.labels[i]
            key = (group_num, " ".join(label.casefold().rstrip(":").split()))
            if key in rows:
                raise Missing("累计现金流量表有同组重名项目，无法按行比较。")
            ref = _fact(table,i,period,result)
            rows[key] = (ref,label)
            refs.append(ref)
        subtotal = result.calc(f"{period.label()} 第 {group_num+1} 组经营现金调节", "sum", refs,table.unit)
        result.calculations[subtotal]["cash_flow_series"] = f"cash-reconciliation-group:{group_num}"
        group_ids.append(subtotal)
    return group_ids, rows


def derive_quarter(tables, end, result):
    """Use only additive cash-statement amounts. Never treat an omitted row as zero."""
    later, full, earlier, ytd, window = _period_pair(tables, end)
    target = {k:window[k] for k in ("period_start", "period_end")}
    quarter = f"{target['period_start']} 至 {target['period_end']}"
    unit = later.unit

    def subtract(label, last_ref, prior_ref):
        return result.calc(f"{quarter} {label}", "statement_quarter_residual", [last_ref,prior_ref],unit)

    endpoints = []
    for name, pattern in [("合并净利润",START),("经营现金流",CFO)]:
        refs = [_fact(table,_index(table,pattern),period,result) for table,period in [(later,full),(earlier,ytd)]]
        endpoints.append(subtract(name,*refs))
    net,cash = endpoints
    delta = result.calc(f"{quarter} 经营现金流减净利润","difference",[cash,net],unit)
    def amt(ref):
        return Decimal(str(result.value(ref)))

    endpoints_raw = [ref for endpoint in endpoints for ref in result.record(endpoint)["operand_ids"]]
    result.note(f"{quarter}（{unit}），用同一财年截至 {full.end} 减截至 {ytd.end} 的累计金额："
                f"单季净利润 {amt(net):,}，经营现金流 {amt(cash):,}，{difference_text(amt(delta),unit)}。",
                [net,cash,delta,*endpoints_raw],kind="cash_bridge",cash_amount=cash_amount(result,cash),**target)
    try:
        current_groups, current = _groups(later,full,result)
        prior_groups, prior = _groups(earlier,ytd,result)
        # Distinguish the effects of acquisitions or divestitures excluded by an issuer's pivot.
        from .cashflow_skill import PIVOT
        exclusions = [re.sub(r"\W+"," ",table.labels[_index(table,PIVOT)].casefold()).strip()
                      for table in (later,earlier)]
        if exclusions[0] != exclusions[1]:
            raise Missing("两张表的经营资产负债分组说明不同，分组与逐项季度归因需确认同一范围。")
        group_delta = [subtract(label,c,p) for label,c,p in zip(
            ("经营资产负债变动前的调节","经营性资产负债变动"),current_groups,prior_groups)]
        if sum(amt(i) for i in group_delta) != amt(delta):
            raise Missing("两组调节之和与经营现金流减净利润不符。")
        common = sorted(current.keys() & prior.keys())
        items = sorted(common,key=lambda k: -abs(amt(current[k][0])-amt(prior[k][0])))
        leaders, amounts, descriptions = [], [], []
        for k in items[:3]:
            now,label = current[k]
            prev,_ = prior[k]
            item_delta = subtract(label,now,prev)
            leaders.append(item_delta)
            amounts += [now, prev]
            descriptions.append(f"{label} {amt(item_delta):+,}")
        boundary = "两表列示项目不完全相同，单项仅列两表均有的同名行。" if current.keys()!=prior.keys() else ""
        text=(f"{quarter}（{unit}）：经营资产负债变动前的调节 {amt(group_delta[0]):+,}，"
              f"经营性资产负债变动 {amt(group_delta[1]):+,}，两组合计 {amt(delta):+,}。")
        if descriptions:
            text+="可逐项比较的主要调节为 "+"；".join(descriptions)+"。"
        result.note(text+boundary,[*endpoints_raw,*amounts,*group_delta,delta,*leaders],kind="cash_drivers",**target)
    except Missing as exc:
        result.gap(str(exc))
