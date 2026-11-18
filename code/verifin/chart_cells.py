"""Locate and validate printed quarterly values inside native PDF chart frames."""
from decimal import Decimal
import hashlib
import json
import re
from statistics import median



FORMAT = "native-quarter-panel-v1"
NUMBER = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
QUARTER = re.compile(r"(?:Q([1-4])|([1-4])Q)\s*(?:(20\d{2})|['’‘](\d{2}))", re.I)
UNIT = re.compile(r"\((?:(USD|dollars|units)\s+in\s+)?(thousands|millions|billions)(?:\s+of\s+(dollars|units))?\)\s*(\d+)?", re.I)
SCALE = {"thousands":1000,"millions":1000000,"billions":1000000000}


class Unsupported(ValueError):
    """The visible chart cannot establish a unique, auditable operand set."""


def require(value, reason):
    if not value:
        raise Unsupported(reason)


def digest(data):
    if not isinstance(data,bytes):
        data = json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def box(words):
    return [round(min(w["x0"] for w in words),3), round(min(w["top"] for w in words),3),
            round(max(w["x1"] for w in words),3), round(max(w["bottom"] for w in words),3)]


def mid(w):
    return (w["x0"]+w["x1"])/2


def lines(words):
    grouped = []
    for w in sorted(words,key=lambda x:(x["top"],x["x0"])):
        if not grouped or abs(grouped[-1][0]["top"]-w["top"])>2.0:
            grouped.append([])
        grouped[-1].append(w)
    return [sorted(line,key=lambda w:w["x0"]) for line in grouped]


def words_text(words):
    return " ".join(w["text"] for w in words)


def periods(row):
    labels = []
    i = 0
    while i < len(row):
        match = None
        for width in (1,2):
            part = row[i:i+width]
            if len(part) != width:
                continue
            if width==2 and part[1]["x0"]-part[0]["x1"] > max(12,part[0]["height"]):
                continue
            match = QUARTER.fullmatch(words_text(part))
            if match:
                break
        if match is None:
            return []
        q = int(match.group(1) or match.group(2))
        year = int(match.group(3)) if match.group(3) else 2000 + int(match.group(4))
        b = box(part)
        labels.append({"printed":words_text(part),"period":f"{year}Q{q}","bbox":b,"x":(b[0]+b[2])/2})
        i += width
    return labels


def footnote(all_words, number, chart_bottom):
    result = []
    for row in lines([w for w in all_words if w["top"]>=chart_bottom]):
        if len(row)>=3 and row[0]["text"]==number and row[0]["height"]<row[1]["height"]*.85:
            result.append({"printed_line":words_text(row),"bbox":box(row)})
    require(len(result)==1,"referenced_scale_footnote_missing_or_ambiguous")
    return result[0]


def inspect_panel(all_words,panel,source_sha,page_number):
    x0,top,x1,bottom = panel
    selected = [w for w in all_words if w["x0"]>=x0 and w["x1"]<=x1 and w["top"]>=top and w["bottom"]<=bottom]
    require(selected,"no_native_text_in_panel")
    require(all(w.get("upright",True) for w in selected),"rotated_native_text_requires_review")
    rows = lines(selected)
    axes = [(row,periods(row)) for row in rows]
    axes = [(row,labels) for row,labels in axes if len(labels)>=2]
    require(len(axes)==1,"no_unique_single_line_quarter_axis")
    axis,labels = axes[0]
    require(len({l["period"] for l in labels})==len(labels),"duplicate_period_label")
    gaps = [b["x"]-a["x"] for a,b in zip(labels,labels[1:])]
    require(min(gaps)>18 and max(gaps)<=min(gaps)*1.6,"irregular_or_overlapping_period_columns")
    axis_top = min(w["top"] for w in axis)
    require(not any(w["top"]>axis_top+3 for w in selected),"content_below_period_axis_in_panel")
    units = [(i,UNIT.fullmatch(words_text(row))) for i,row in enumerate(rows) if row[0]["top"]<axis_top]
    units = [(i,m) for i,m in units if m is not None]
    require(len(units)==1,"no_unique_explicit_parenthesized_scale")
    unit_idx,unit_match = units[0]
    require(unit_idx>0,"chart_measure_title_missing")
    title = rows[unit_idx-1]
    measure = words_text(title)
    require(len(measure)>4 and not any(NUMBER.fullmatch(w["text"]) for w in title),"chart_measure_title_ambiguous")
    # Require the denomination as well as the multiplier. Neither is inferred from issuer or values.
    denoms = {(a or "").lower() for a in (unit_match.group(1),unit_match.group(3)) if a}
    denoms = {"USD" if x in ("usd","dollars") else "units" for x in denoms}
    if re.search(r"\bunits?\b",measure,re.I):
        denoms.add("units")
    require(len(denoms)==1,"base_denomination_missing_or_conflicting")
    denom, = denoms
    scale = SCALE[unit_match.group(2).lower()]
    unit_row = rows[unit_idx]
    unit_bottom = max(w["bottom"] for w in unit_row)
    plot = [w for w in selected if unit_bottom<w["top"]<axis_top-2]
    require(bool(plot),"no_native_numeric_labels_in_plot")
    require(all(NUMBER.fullmatch(w["text"]) for w in plot),"mixed_plot_labels_series_or_axis_requires_review")
    require(len(plot)==len(labels),"values_do_not_match_period_count")
    tolerance = min(12.0,median(gaps)*.12)
    aligned = []
    for label in labels:
        candidates = [w for w in plot if abs(mid(w)-label["x"])<=tolerance]
        require(len(candidates)==1,"nonunique_or_misaligned_value_for_period")
        aligned.append(candidates[0])
    require(len({tuple(box([w])) for w in aligned})==len(plot),"value_reused_or_unassigned")
    note_id = unit_match.group(4)
    note = footnote(all_words,note_id,bottom) if note_id else None
    require(re.fullmatch(r"[0-9a-f]{64}",source_sha),"source_identity_missing")
    records = []
    for label,w in zip(labels,aligned):
        display = w["text"]
        num = Decimal(display.replace(",",""))
        r = {"figure":measure,"figure_bbox":box(title),"period":label["period"],"printed_period":label["printed"],
             "period_bbox":label["bbox"],"displayed_value":display,"value_bbox":box([w]),
             "base_unit":denom,"scale_to_base":scale,"displayed_scale":words_text(unit_row),"unit_bbox":box(unit_row),
             "value_in_base_units":str(num*scale),"alignment_error_pt":round(abs(mid(w)-label["x"]),3),
             "pdf_sha256":source_sha,"physical_page":page_number,"panel_bbox":panel,"format":FORMAT,
             "linked_footnote":note}
        r["id"] = "chartcell_"+digest(r)
        records.append(r)
    return records


def framed_inventory_cells(pdf, number, doc, info, document_id):
    """Use the PDF's own plot border; never isolate a series by cropping its numeric labels."""
    from .data import stable_id

    page = pdf.pages[number - 1]
    if page.rotation:
        return []
    words = page.extract_words(x_tolerance=2, y_tolerance=2)
    cells = {}
    for rect in page.rects:
        x0, top, x1, bottom = (rect[k] for k in ("x0", "top", "x1", "bottom"))
        if x1 - x0 < 60 or bottom - top < 40:
            continue
        above = [w for w in words if x0 <= w["x0"] and w["x1"] <= x1 and top-80 <= w["top"] and w["bottom"] <= top]
        rows = lines(above)
        if len(rows) < 2 or not UNIT.fullmatch(words_text(rows[-1])):
            continue
        panel = [x0, min(w["top"] for w in rows[-2]), x1, bottom]
        try:
            operands = inspect_panel(words, panel, info["sha256"], number)
        except Unsupported:
            continue
        title = operands[0]["figure"]
        if operands[0]["base_unit"] != "units" or not re.search(r"\binventor(?:y|ies)\b", title, re.I):
            continue
        for op in operands:
            figure = stable_id("figure_", [document_id, info["sha256"], number, panel])
            value = Decimal(op["value_in_base_units"])
            cell_id = stable_id("cell_", [document_id, op["id"]])
            cells[cell_id] = {
                "id": cell_id, "kind": "financial_fact", "cell_type": "chart_inventory_cell", "document_id": document_id,
                "entity": doc["entity"], "source_url": info["url"], "snapshot_hash": info["sha256"],
                "snapshot_path": doc["source"], "published_at": doc["published_at"],
                "locator": f"PDF p.{number}, {title}, {op['printed_period']}, value box {op['value_bbox']}",
                "figure_id": figure, "row_label": title, "chart_period": op["period"],
                "printed_period": op["printed_period"], "printed_value": op["displayed_value"],
                "printed_unit": op["displayed_scale"], "footnote": op["linked_footnote"],
                "metric": title, "basis": "issuer_chart_labels", "business_scope": title,
                "period_start": None, "period_end": None, "unit": op["base_unit"],
                "currency": op["base_unit"] if op["base_unit"] == "USD" else None,
                "value": int(value) if value == value.to_integral_value() else float(value),
                "scale": 1, "precision": "Approximate, from published chart labels.",
            }
    return list(cells.values())


