"""Charts of saved numeric observations with explicit financial dimensions.

Charts visualize source values, not verified conclusions. They never combine
currencies, metrics, financial durations or accounting scopes silently.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
from html import escape
import json
import math


def _period(record):
    end = date.fromisoformat(record["period_end"])
    start_value = record.get("period_start")
    start = date.fromisoformat(start_value) if start_value else end
    if start > end:
        raise ValueError("reversed period")
    days = (end - start).days + 1
    if days == 1:
        duration = "point in time"
    elif 70 <= days <= 100:
        duration = "quarter"
    elif 160 <= days <= 200:
        duration = "six months"
    elif 240 <= days <= 290:
        duration = "nine months"
    elif 330 <= days <= 380:
        duration = "annual"
    else:
        duration = f"{days} days"
    return start.isoformat(), end.isoformat(), duration, days


def chart_data(report):
    grouped = defaultdict(list)
    excluded = []
    for record in [*report.get("evidence", []), *report.get("calculations", [])]:
        rid = record.get("id", "unknown")
        value = record.get("value")
        reason = None
        if record.get("stale"):
            reason = "stale observation"
        elif record.get("conflict") or record.get("conflicting"):
            reason = "conflicting observation"
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            # Original passages are useful evidence, but not chart data.
            continue
        elif record.get("scale", 1) != 1:
            reason = "non-base-unit scale"
        elif not all(isinstance(record.get(k), str) and record[k] for k in ("entity", "metric", "unit", "period_end")):
            reason = "missing chart dimensions"
        if reason:
            excluded.append({"record_id": rid, "reason": reason})
            continue
        try:
            start, end, duration, days = _period(record)
        except (ValueError, TypeError, KeyError):
            excluded.append({"record_id": rid, "reason": "invalid or missing period"})
            continue
        # Missing basis is kept distinct from an asserted accounting basis.
        basis = record.get("basis", record.get("accounting_basis")) or "unspecified"
        business = record.get("business_scope") or "unspecified"
        price = record.get("price_basis", record.get("adjustment")) or "not applicable"
        # Imported prices carry split/dividend/vintage metadata as an object.
        # Preserve all of it in grouping, including the adjustment vintage.
        if isinstance(price, (dict, list)):
            price = json.dumps(price, sort_keys=True, ensure_ascii=False, allow_nan=False)
        fields = (record["metric"], record["unit"], duration, str(basis), str(business), str(price))
        grouped[fields].append({"record_id": rid, "entity": record["entity"], "period_start": start,
                                "period_end": end, "duration_days": days, "value": value,
                                "source_url": record.get("source_url"), "locator": record.get("locator"),
                                "formula": record.get("formula"), "operand_ids": record.get("operand_ids", []),
                                "warnings": record.get("warnings", []), "snapshot_hash": record.get("snapshot_hash")})
    groups = []
    for dimensions, points in sorted(grouped.items()):
        buckets = defaultdict(list)
        for point in points:
            buckets[(point["entity"], point["period_start"], point["period_end"])].append(point)
        usable = []
        for key, values in buckets.items():
            if len({v["value"] for v in values}) > 1:
                excluded.extend({"record_id": v["record_id"], "reason": "multiple values for the same chart observation"} for v in values)
                continue
            chosen = dict(values[0])
            chosen["record_ids"] = [v["record_id"] for v in values]
            usable.append(chosen)
        if not usable:
            continue
        # Broad labels alone do not establish exact duration comparability.
        # Split out periods differing by more than a normal extra fiscal week.
        clusters = []
        for point in sorted(usable, key=lambda p: (p["duration_days"], p["period_end"], p["entity"])):
            cluster = next((c for c in clusters if point["duration_days"] - c[0]["duration_days"] <= 7), None)
            if cluster is None:
                clusters.append([point])
            else:
                cluster.append(point)
        for cluster in clusters:
            cluster.sort(key=lambda p: (p["period_end"], p["entity"], p["period_start"]))
            metric, unit, duration, basis, business, price = dimensions
            ident = hashlib.sha256(json.dumps([dimensions, cluster[0]["duration_days"]]).encode()).hexdigest()[:16]
            groups.append({"id": ident, "title": metric.replace("_", " "), "unit": unit,
                           "period_type": duration, "accounting_basis": basis, "business_scope": business,
                           "price_basis": price, "points": cluster,
                           "note": "Saved numeric observations; chart grouping does not establish semantic or accounting comparability."})
    return {"report_version": report.get("version"), "scope_version": report.get("scope_version"),
            "model_mode": report.get("model_mode"), "groups": groups, "excluded": excluded,
            "verification_available": bool(report.get("verification_available"))}


def render_chart_svg(group):
    """Horizontal bars, a visible zero baseline and exact values in titles.

    Geometry is scaled for the plotted group only. Text is escaped; no input
    markup, script, source URL or external resource is inserted into the SVG.
    """
    points = group.get("points", [])
    if not points or len(points) > 120:
        raise ValueError("Select a chart with 1–120 observations; use the data export for larger groups")
    values = [p["value"] for p in points]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Chart values must be finite numbers")
    width, left, right = 920, 265, 170
    height = 130 + len(points) * 46
    low, high = min(0, min(values)), max(0, max(values))
    if low == high:
        high = low + 1
    # Scaling before subtraction avoids overflow for very large finite values.
    magnitude = max(abs(low), abs(high), 1)
    low_scaled, high_scaled = low / magnitude, high / magnitude
    span = high_scaled - low_scaled
    x = lambda v: left + ((v / magnitude - low_scaled) / span) * (width - left - right)
    zero = x(0)
    title = f"{group.get('title', '')} · {group.get('unit', '')} · {group.get('period_type', '')}"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="chart-title chart-description">',
             f'<title id="chart-title">{escape(title)}</title>',
             '<desc id="chart-description">Bars start at zero. Each observation retains its entity and exact period. Values are saved data, not a new verification result.</desc>',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
             f'<text x="24" y="32" fill="#16354a" font-family="sans-serif" font-size="19">{escape(title)}</text>',
             f'<text x="24" y="56" fill="#617789" font-family="sans-serif" font-size="12">{escape(str(group.get("business_scope", "")))} · {escape(str(group.get("accounting_basis", "")))}</text>',
             f'<line x1="{zero:.3f}" x2="{zero:.3f}" y1="75" y2="{height - 35}" stroke="#8c9eaa"/>',
             f'<text x="{zero:.3f}" y="{height - 14}" text-anchor="middle" font-size="12" font-family="sans-serif" fill="#617789">0</text>']
    entities = sorted({p["entity"] for p in points})
    colors = ["#147d92", "#6c63af", "#bf7537", "#3f8662", "#a95072"]
    for i, point in enumerate(points):
        y = 82 + i * 46
        value = point["value"]
        label = f'{point["entity"]} · {point["period_end"]}'
        interval = point["period_start"] if point["period_start"] == point["period_end"] else f'{point["period_start"]} → {point["period_end"]}'
        exact = format(value, ",.12g")
        color = colors[entities.index(point["entity"]) % len(colors)]
        start, end = min(zero, x(value)), max(zero, x(value))
        tooltip = f'{point["entity"]}; {interval}; {value} {group.get("unit", "")}; {point.get("record_id", "")}'
        parts.extend([f'<text x="24" y="{y + 12}" font-family="sans-serif" font-size="13" fill="#16354a">{escape(label)}</text>',
                      f'<text x="24" y="{y + 28}" font-family="sans-serif" font-size="10" fill="#617789">{escape(interval)}</text>',
                      f'<rect x="{start:.3f}" y="{y}" width="{max(0.5, end - start):.3f}" height="25" rx="3" fill="{color}"><title>{escape(tooltip)}</title></rect>',
                      f'<text x="{width - right + 15}" y="{y + 18}" font-family="sans-serif" font-size="12" fill="#16354a">{escape(exact)}</text>'])
    parts.append('</svg>')
    return ''.join(parts)


def export_charts(report, destination):
    from pathlib import Path
    from .evaluation_cli import write_new_json
    folder = Path(destination)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("Chart export directory must be new or empty")
    data = chart_data(report)
    # Validate/render all figures before creating output files.
    figures = {f"chart-{group['id']}.svg": render_chart_svg(group) for group in data["groups"] if len(group["points"]) <= 120}
    folder.mkdir(parents=True, exist_ok=True)
    write_new_json(folder / "charts.json", data)
    for filename, svg in figures.items():
        with (folder / filename).open("x", encoding="utf-8") as stream:
            stream.write(svg)
    return {"directory": str(folder.resolve()), "report_version": report.get("version"),
            "charts": list(figures), "data": "charts.json", "model_calls": 0}
