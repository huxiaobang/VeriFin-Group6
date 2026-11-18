"""Reconcile an issuer's two quarterly operating margins from retrieved statement cells."""
from collections import defaultdict
from datetime import date
import math

from .statement_analysis import Findings


ANCHORS = {"revenue": "营业收入", "gross_profit": "毛利", "operating_income": "营业利润"}
SIDES = ("current", "prior")
SIDE_NAMES = {"current": "本期", "prior": "上年同期"}


def quarter_side(cell, period_end, period_start=None):
    try:
        start, end, target = (date.fromisoformat(v) for v in (cell["period_start"], cell["period_end"], period_end))
    except (KeyError, TypeError, ValueError):
        return None
    if not 77 <= (end - start).days + 1 <= 105:
        return None
    if end == target and (not period_start or cell["period_start"] == period_start):
        return 0
    if 360 <= (target - end).days <= 372:
        return 1
    return None


def _table(evidence, entity, end, start):
    tables = defaultdict(lambda: defaultdict(dict))
    observed = defaultdict(set)
    for c in evidence:
        if (c.get("entity") != entity or c.get("stale") or c.get("cell_type") != "statement_amount_cell"
                or c.get("business_scope") != "consolidated" or c.get("basis") not in {"US-GAAP", "GAAP"}
                or c.get("scale") != 1 or not c.get("currency") or c.get("unit") != c["currency"]
                or not all(c.get(k) for k in ("id", "document_id", "table_id", "snapshot_hash"))
                or type(c.get("value")) not in (int, float) or not math.isfinite(c["value"])):
            continue
        metric = c.get("metric")
        if metric not in ANCHORS and c.get("expense_role") not in {"component", "income", "total"}:
            continue
        side = quarter_side(c, end, start)
        if side is None:
            continue
        group = tuple(c[k] for k in ("document_id", "table_id", "snapshot_hash", "basis", "currency"))
        tables[group][SIDES[side]][c["id"]] = c
        observed[side, metric].add((c["value"], c["period_start"], c["period_end"], c["currency"], c["basis"]))
    # A conflicting restatement or duplicate period needs source selection, not a cross-table join.
    if any(len(values) > 1 for values in observed.values()):
        return None
    candidates = []
    for group, table in tables.items():
        if not any(c["metric"] in ANCHORS for c in table["current"].values()):
            continue
        score = []
        for side in SIDES:
            cells = table[side].values()
            anchors = [c["metric"] for c in cells if c["metric"] in ANCHORS]
            if (len(anchors) != len(set(anchors)) or len({(c["period_start"], c["period_end"]) for c in cells}) > 1):
                break
            score.append(len(anchors))
        else:
            sections = sum(bool(c.get("expense_section")) for cells in table.values() for c in cells.values())
            size = sum(len(cells) for cells in table.values())
            candidates.append(((min(score), sum(score), sections, size, group), table))
    return max(candidates, key=lambda option: option[0])[1] if candidates else None


def _residual(ledger, gross, expenses, operating, denominator, incomes=()):
    """Numerator remains in reported money units until the final division."""
    gross_less = gross["id"]
    if expenses:
        total = ledger.calc("observed_expense_total", "sum", expenses, gross["unit"])
        gross_less = ledger.calc("gross_less_observed_expenses", "difference", [gross_less, total], gross["unit"])
    if incomes:
        gross_less = ledger.calc("gross_less_expenses_plus_other_income", "sum", [gross_less, *incomes], gross["unit"])
    residual = ledger.calc("unreconciled_expense_amount", "difference", [gross_less, operating["id"]], gross["unit"])
    rate = (ledger.calc("unreconciled_expenses_to_revenue_percent", "margin", [residual, denominator], "%")
            if denominator else None)
    return residual, rate


def _period(side, cells, ledger, rows, missing):
    anchors = {c["metric"]: c for c in cells.values() if c["metric"] in ANCHORS}
    prefix = SIDE_NAMES[side]
    missing.extend(prefix + name for key, name in ANCHORS.items() if key not in anchors)
    ledger.evidence.update((c["id"], c) for c in anchors.values())
    revenue = anchors.get("revenue")
    if revenue and revenue["value"] <= 0:
        missing.append(prefix + "缺少可作分母的正营业收入")
    denominator = revenue["id"] if revenue and revenue["value"] > 0 else None

    def measure(c, key):
        rate = ledger.calc(key + "_to_revenue_percent", "margin", [c["id"], denominator], "%") if denominator else None
        return {"amount": c["value"], "cell_id": c["id"], "rate": ledger.value(rate) if rate else None, "rate_id": rate}

    date_cell = next(iter(cells.values()), {})
    period = {k: date_cell.get(k) for k in ("period_start", "period_end", "currency")}
    period.update({key: measure(c, key) if key != "revenue" else {"amount": c["value"], "cell_id": c["id"]}
                   for key, c in anchors.items()})
    section = next((c["expense_section"] for c in anchors.values() if c.get("expense_section")), {})
    descriptors = section.get("rows", [])
    gross, operating = (anchors.get(m) for m in ("gross_profit", "operating_income"))
    # Releases may print income as a deduction from the expense subtotal. Only
    # assign its profit sign when all individual lines close to the published endpoints.
    detail = [d for d in descriptors if d["role"] != "total"]
    income_sign_verified = False
    if (any(d["role"] == "income" for d in detail) and gross and operating and
            all(d["role"] in {"component", "income"} and d.get("status") == "present"
                and d.get("cell_id") in cells for d in detail)):
        movement = sum(cells[d["cell_id"]]["value"] * (1 if d["role"] == "income" else -1) for d in detail)
        income_sign_verified = gross["value"] + movement == operating["value"]
    expense_ids, income_ids = [], []
    absent = []
    reported_total = None
    for desc in descriptors:
        if desc["role"] == "total":
            c = cells.get(desc.get("cell_id"))
            if c and c.get("expense_role") == "total" and desc.get("status") == "present":
                reported_total = c
                ledger.evidence[c["id"]] = c
            continue
        key, label = desc["key"], desc["label"]
        row = rows.setdefault(key, {"key": key, "label": label, "role": desc["role"],
                                   "current": None, "prior": None, "contribution_pp": None})
        c = cells.get(desc.get("cell_id"))
        if desc["role"] == "income" and not income_sign_verified:
            absent.append(label + " 对营业利润的计入方向")
            continue
        if (desc["role"] not in {"component", "income"} or desc.get("status") != "present" or not c
                or c.get("expense_role") != desc["role"] or c.get("expense_key") != key):
            absent.append(label)
            continue
        ledger.evidence[c["id"]] = c
        row[side] = measure(c, key)
        if desc["role"] == "income":
            income_ids.append(c["id"])
        else:
            expense_ids.append(c["id"])
    if not any(d["role"] != "total" for d in descriptors):
        absent.append("毛利与营业利润之间的费用明细行")
    missing.extend(prefix + "缺少可核算的 " + label for label in absent)
    residual_id = residual_rate = None
    if gross and operating:
        residual_id, residual_rate = _residual(ledger, gross, expense_ids, operating, denominator, income_ids)
        if reported_total:
            total_gap, _ = _residual(ledger, gross, [reported_total["id"]], operating, None, income_ids)
            period["reported_total"] = {"amount": reported_total["value"], "cell_id": reported_total["id"],
                                        "residual_amount": ledger.value(total_gap), "residual_id": total_gap}
            if ledger.value(total_gap) != 0:
                missing.append(prefix + "列示的费用总额与毛利减营业利润不一致")
    period["expense_amounts"] = [ledger.value(i) for i in expense_ids]
    period["other_income_amounts"] = [ledger.value(i) for i in income_ids]
    period["residual_amount"] = ledger.value(residual_id) if residual_id else None
    period["residual_id"] = residual_id
    period["residual_rate"] = ledger.value(residual_rate) if residual_rate else None
    period["residual_rate_id"] = residual_rate
    period["complete"] = bool(not absent and denominator and residual_id and period["residual_amount"] == 0
                              and not period.get("reported_total", {}).get("residual_amount"))
    if residual_id and not absent and period["residual_amount"] != 0:
        missing.append(f"{prefix}尚有 {period['residual_amount']:,} {period['currency']} 未对平，现有行无法确定差额对应的项目，需补充明细或核对原数")
    return period


def _amount(value):
    return f"{value:,}"


def _points(value):
    return f"{value:+.2f}" if abs(value) >= .005 else "0.00"


def _summary(bridge):
    periods, rows, change = bridge["periods"], bridge["rows"], bridge["changes"]
    lines = []
    if "gross_profit" in change:
        now, prior = (periods[s]["gross_profit"]["rate"] for s in SIDES)
        lines.append(f"毛利率从 {prior:.2f}% 变为 {now:.2f}%，贡献 {_points(change['gross_profit'])} 个百分点。")
    for row in rows:
        deltas = row["contribution_pp"]
        rates = {s: row[s]["rate"] for s in SIDES if row[s] and row[s]["rate"] is not None}
        if deltas is not None:
            if deltas < 0:
                verb = "抵消" if change.get("gross_profit", 0) > 0 else "拉低营业利润率"
                impact = f"{verb} {abs(deltas):.2f} 个百分点"
            else:
                impact = f"贡献 {_points(deltas)} 个百分点"
            lines.append(f"{row['label']} 占收入从 {rates['prior']:.2f}% 变为 {rates['current']:.2f}%，"
                         f"{impact}。")
        elif rates:
            known = "、".join(f"{SIDE_NAMES[s]} {v:.2f}%" for s, v in rates.items())
            lines.append(f"{row['label']} 可核实费用率为{known}，同比贡献待核。")
    if "operating_income" in change:
        lines.append(f"营业利润率合计变动 {_points(change['operating_income'])} 个百分点。")
    for s in SIDES:
        p = periods[s]
        if p["residual_amount"] is None:
            continue
        expense_terms = "".join(" − " + _amount(v) for v in p["expense_amounts"])
        expense_terms += "".join(" + " + _amount(v) for v in p["other_income_amounts"])
        gross, operating = (p[m]["amount"] for m in ("gross_profit", "operating_income"))
        residual = p["residual_amount"]
        check = (f"{_amount(gross)}{expense_terms} = {_amount(operating)}" if not residual else
                 f"{_amount(gross)}{expense_terms} − {_amount(operating)} = {_amount(residual)}")
        rate = f"（占收入 {p['residual_rate']:.2f}%）" if residual and p["residual_rate"] is not None else ""
        lines.append(f"{SIDE_NAMES[s]}（截至 {p['period_end']}，{p['currency']}）{check}，"
                     f"剩余差额为 {_amount(residual)}{rate}。")
    if "unexplained" in change:
        lines.append(f"按以上两期可比行计算，尚未分配的同比差额为 {_points(change['unexplained'])} 个百分点。")
    movements = bridge["amount_changes"]
    if movements:
        currency = periods["current"]["currency"]
        lines.append("按原表金额同比（" + currency + "）：" +
                     "，".join(f"{m['label']} {m['value']:+,}" for m in movements) + "。")
    return "".join(lines)


def margin_reconciliation(evidence, *, entity, period_end, period_start=None):
    """Use one table and its retrieved numeric cells. Missing components stay unallocated."""
    table = _table(evidence, entity, period_end, period_start)
    if table is None:
        return None
    ledger = Findings()
    rows, missing = {}, []
    periods = {s: _period(s, table[s], ledger, rows, missing) for s in SIDES}
    changes, change_ids = {}, {}
    amount_changes = []

    def amount_change(key, label, pair):
        if all(pair):
            record = ledger.calc(key + "_profit_amount_change", "difference", pair, ledger.record(pair[0])["unit"])
            amount_changes.append({"key": key, "label": label, "value": ledger.value(record), "calculation_id": record})

    amount_change("gross_profit", "毛利贡献", [periods[s].get("gross_profit", {}).get("cell_id") for s in SIDES])
    for key in ("gross_profit", "operating_income"):
        pair = [periods[s].get(key, {}).get("rate_id") for s in SIDES]
        if all(pair):
            change_ids[key] = ledger.calc(key + "_margin_change", "percentage_point_change", pair, "percentage_points")
            changes[key] = ledger.value(change_ids[key])
    contributions = [change_ids["gross_profit"]] if "gross_profit" in change_ids else []
    for row in rows.values():
        # Higher expense reduces profit; higher operating income from another line adds to it.
        order = SIDES if row["role"] == "income" else ("prior", "current")
        amount_change(row["key"], row["label"] + "贡献", [(row[s] or {}).get("cell_id") for s in order])
        pair = [(row[s] or {}).get("rate_id") for s in order]
        if all(pair):
            label = "_income_contribution" if row["role"] == "income" else "_expense_contribution"
            delta = ledger.calc(row["key"] + label, "percentage_point_change", pair, "percentage_points")
            contributions.append(delta)
            row.update(contribution_pp=ledger.value(delta), contribution_id=delta)
        else:
            for s in SIDES:
                if row[s] is None and not any(SIDE_NAMES[s] in m and row["label"] in m for m in missing):
                    missing.append(f"{SIDE_NAMES[s]}缺少可核算的 {row['label']}")
    if "operating_income" in change_ids and contributions:
        total = ledger.calc("known_margin_contributions", "sum", contributions, "percentage_points")
        if "gross_profit" in change_ids:
            # Reconcile using the same paired expense rows on both sides. A missing
            # comparator leaves both its unknown delta and any other omissions in the residual.
            residual_rates = []
            for s in ("prior", "current"):
                period = periods[s]
                matched = [r for r in rows.values() if r["contribution_pp"] is not None]
                if len(matched) == len(period["expense_amounts"]) + len(period["other_income_amounts"]):
                    rate = period["residual_rate_id"]
                else:
                    gross, operating, revenue = (period[k]["cell_id"] for k in ("gross_profit", "operating_income", "revenue"))
                    expense_ids = [r[s]["cell_id"] for r in matched if r["role"] == "component"]
                    income_ids = [r[s]["cell_id"] for r in matched if r["role"] == "income"]
                    _, rate = _residual(ledger, ledger.record(gross), expense_ids, ledger.record(operating), revenue, income_ids)
                residual_rates.append(rate)
            residual = ledger.calc("unexplained_margin_change", "percentage_point_change", residual_rates, "percentage_points")
        else:
            residual = ledger.calc("unexplained_margin_change", "difference",
                                   [change_ids["operating_income"], total], "percentage_points")
        change_ids["known"] = total
        changes["known"] = ledger.value(total)
        change_ids["unexplained"] = residual
        changes["unexplained"] = ledger.value(residual)
    elif "operating_income" in change_ids:
        changes["unexplained"] = changes["operating_income"]
        change_ids["unexplained"] = change_ids["operating_income"]
    amount_change("operating_income", "营业利润变动", [periods[s].get("operating_income", {}).get("cell_id") for s in SIDES])
    first = next(iter(table["current"].values()))
    complete = all(p["complete"] for p in periods.values()) and not missing
    result = {"entity": entity, "document_id": first["document_id"], "table_id": first["table_id"],
              "status": "complete" if complete else "partial", "periods": periods, "rows": list(rows.values()),
              "changes": changes, "change_ids": change_ids, "amount_changes": amount_changes,
              "missing": list(dict.fromkeys(missing)),
              "evidence_ids": list(ledger.evidence), "calculation_ids": list(ledger.calculations),
              "calculations": list(ledger.calculations.values())}
    result["missing_support"] = "。".join(result["missing"]) + "。" if result["missing"] else ""
    result["summary"] = _summary(result) + result["missing_support"]
    return result
