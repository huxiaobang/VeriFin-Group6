"""Reconcile quarterly sales growth using amounts and selling days from the same table."""
from decimal import Decimal, InvalidOperation

from .data import stable_id


def calendar_growth_rates(current_sales, prior_sales, current_days, prior_days):
    """Return total YoY %, sales-per-day YoY %, and their signed difference in pp."""
    try:
        sales, base, days, base_days = (Decimal(str(v)) for v in
                                        (current_sales, prior_sales, current_days, prior_days))
    except InvalidOperation as exc:
        raise ValueError("The sales and day counts must be numeric.") from exc
    if not all(v.is_finite() for v in (sales, base, days, base_days)) or sales < 0 or base <= 0:
        raise ValueError("Quarterly sales require nonnegative current sales and a positive baseline.")
    if any(day != day.to_integral_value() or not 0 < day <= 105 for day in (days, base_days)):
        raise ValueError("A quarter needs a positive, whole number of selling days.")
    total_growth = 100 * (sales / base - 1)
    daily_growth = 100 * ((sales / days) / (base / base_days) - 1)
    return total_growth, daily_growth, total_growth - daily_growth


def attach_calendar_calculation(result, entity):
    """Publish an auditable result only for sales questions with four unambiguous source cells."""
    data = result["data"]
    periods = data.get("period_operands")
    findings = [data["part_findings"][key] for key in data.get("calendar_bridge_parts", [])]
    if not periods or not findings:
        return
    current, prior = data["fiscal_quarter"], data["comparison_quarter"]
    evidence = {r["id"]: r for r in result["evidence"]}
    roles = [(period, metric) for metric in ("net_sales", "business_days") for period in (current, prior)]
    cells = []
    table_ids = set()
    for period, metric in roles:
        extracted = periods.get(period, {}).get(metric, {})
        cell = evidence.get(extracted.get("evidence_id"), {})
        table_id = extracted.get("table_evidence_id")
        table = evidence.get(table_id, {})
        if (not table or not cell or cell.get("entity") != entity or cell.get("fiscal_quarter") != period
                or cell.get("metric") != metric or cell.get("document_id") != table.get("document_id")
                or cell.get("text_snapshot_hash") != table.get("text_snapshot_hash")
                or not table["start"] <= cell["start"] < cell["end"] <= table["end"]):
            return
        table_ids.add(table_id)
        cells.append(cell)
    ids = [cell["id"] for cell in cells]
    if (len(table_ids) != 1 or len(set(ids)) != 4 or cells[0]["unit"] != cells[1]["unit"]
            or any(cell["unit"] != "days" for cell in cells[2:])):
        return
    values = [cell["value"] for cell in cells]
    try:
        total, daily, difference = calendar_growth_rates(*values)
    except ValueError:
        return
    a, b, d, e = values
    metric = "sales_calendar_growth_gap"
    calc_id = stable_id("calc_", [metric, entity, current, prior, ids])
    calc = {"id": calc_id, "operation": metric, "metric": metric, "operand_ids": ids,
            "formula": f"100 * (({a} / {b}) - (({a} / {d}) / ({b} / {e})))",
            "value": float(difference), "unit": "percentage_points", "entity": entity, "entities": [entity],
            "fiscal_quarter": current, "comparison_quarter": prior,
            "total_growth_percent": float(total), "daily_growth_percent": float(daily),
            "basis": "reported_sales_per_selling_day", "business_scope": "consolidated",
            "published_at": cells[0]["published_at"], "table_evidence_id": next(iter(table_ids))}
    if data.get("fiscal_period"):
        calc.update({k: data["fiscal_period"][k] for k in ("period_start", "period_end")})
    result.setdefault("calculations", []).append(calc)
    sales_unit = periods[current]["net_sales"].get("reported_unit") or "表内同一金额单位"
    day_change = d - e
    calendar = (f"本期比上年同季{'多' if day_change > 0 else '少'}了 {abs(day_change)} 个营业日"
                if day_change else "两期营业日数相同")
    explanation = (f"按原表，{current} 和 {prior} 的销售额分别为 {a:,} 和 {b:,}（{sales_unit}），"
                   f"营业日分别为 {d} 天和 {e} 天。{calendar}。按披露金额复算，总销售额同比 {total:.2f}%，"
                   f"日均销售额同比 {daily:.2f}%，总额增速减日均增速约为 {difference:.2f} 个百分点。")
    for item in findings:
        # Some tables supply both levels and day counts without printing growth rates.
        item.pop("missing_support", None)
        item["status"] = "source_excerpt"
        item["answer_sentence"] = explanation
        item["calculation_ids"] = [calc_id]
