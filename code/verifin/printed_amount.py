"""Identify a single reported amount in an explicitly labelled comparison-table excerpt."""
from datetime import date
from decimal import Decimal
import re

from .table_cells import column_period, DATE


# Only direct historical statements with the named source measure qualify.
ZH_LABELS = {"net sales":"净销售额", "gross profit":"毛利润", "operating income":"营业利润", "net income":"净利润"}
SCALES = {"thousand": 1000, "million": 10**6, "billion": 10**9}
SCALE_NOTE = re.compile(r"\b(?:U\.S\. |US )?dollar amounts are (?:stated|presented|expressed) in (thousand|million|billion)s\b", re.I)
END_DATE = re.compile(r"\b(?:year and quarter|quarter and year) ended (" + DATE.pattern + ")", re.I)
GROUPS = re.compile(r"(three|twelve)[ -]month period\s+(three|twelve)[ -]month period", re.I)
YEARS = re.compile(r"(\d{4})\s+(\d{4})\s+Change\s+(\d{4})\s+(\d{4})\s+Change", re.I)
NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
TRIPLET = rf"\$\s*({NUM})\s+({NUM})\s+[-+−]?{NUM}\s*%"


def direct_statement(claim, literal, metric, end, months):
    """Require an identifiable period and measure in the user's visible sentence as well as the fields."""
    q = (end.month - 1) // 3 + 1
    year = str(end.year)
    quarters = [f"{year}年第{'一二三四'[q-1]}季度", f"{year}年{q}季度", f"q{q} {year}", f"{year} q{q}"]
    annual = [f"{year}年全年",f"{year}全年",f"{year}年度",f"fy{year}",f"fy {year}"]
    text = claim["text"].casefold().replace(literal.casefold(), "", 1).strip(" 。.")
    labels = [metric, ZH_LABELS[metric]] if metric in ZH_LABELS else [metric]
    for label in labels:
        if label not in text:
            continue
        remainder = text.replace(label, "", 1)
        for period in (quarters if months == "three" else annual):
            if period in remainder and re.fullmatch(r"\s*(?:的|为|约为|达|was|were|totaled|of|for|\s)*",
                                                    remainder.replace(period,"",1)):
                return True
    return False


def comparison_cell(claim, doc, literal):
    """Return the printed row, column and scale, or None for layouts with any ambiguous binding."""
    constraints = claim.get("constraints") or {}
    if not isinstance(constraints, dict):
        return None
    metric = str(constraints.get("metric","")).replace("_"," ").casefold()
    if not re.fullmatch(r"[a-z][a-z &-]{1,45}", metric) or not constraints.get("entity") or doc.get("entity") != constraints["entity"]:
        return None
    text = doc.get("text","")
    if (not isinstance(text,str) or doc.get("stale") or constraints.get("currency") != "USD"
            or re.search(r"\b(?:Canadian|Australian|Hong Kong|New Zealand|Singapore) dollars?\b|\b(?:CAD|AUD|HKD|NZD|SGD)\b", text, re.I)):
        return None
    dates, scales = list(END_DATE.finditer(text)), list(SCALE_NOTE.finditer(text))
    if len(dates) != 1 or len(scales) != 1:
        return None
    # A bare dollar sign alone does not identify a currency. Limit an implicit
    # denomination to domestic SEC reports with an as-reported dollar claim.
    if (not re.search(r"\bU\.?S\.? dollars?\b", scales[0].group(), re.I)
            and not re.match(r"(?:8-K|10-[QK])\b", str(doc.get("form","")),re.I)):
        return None
    end = doc.get("document_report_date")
    if not isinstance(end,str) or end != constraints.get("period_end") or not end.endswith("-12-31"):
        return None
    row_pattern = re.compile(re.escape(metric) + r"\s+" + TRIPLET + r"\s+" + TRIPLET, re.I)
    lines = text.splitlines()
    candidates = []
    header_position = -1
    for i, line in enumerate(lines[:-2]):
        groups, years = GROUPS.fullmatch(line.strip()), YEARS.fullmatch(lines[i+1].strip())
        if not groups or not years:
            continue
        header_position = text.find(line,header_position+1)
        if header_position < max(scales[0].end(),dates[0].end()):
            continue
        scale = scales[0].group(1).casefold()
        local_scales = re.findall(r"\bin (thousand|million|billion)s\b",text[scales[0].end():header_position],re.I)
        if any(s.casefold() != scale for s in local_scales):
            continue
        durations = [g.casefold() for g in groups.groups()]
        y1,y2,y3,y4 = years.groups()
        if len(set(durations)) != 2 or y1 != y3 or y2 != y4 or int(y1) != int(y2)+1:
            continue
        for j, months in enumerate(durations):
            period = column_period([f"{months} months ended {dates[0].group(1)}"])
            if period != (constraints.get("period_start"),end) or end[:4] != y1:
                continue
            if not direct_statement(claim,literal,metric,date.fromisoformat(end),months):
                continue
            # Follow consecutive table rows only. A title or a paragraph breaks scope.
            for body in lines[i+2:]:
                if not re.match(r"^.{1,64}?\s+(?:\$\s*)?[-(]?\d", body) or body.endswith("."):
                    break
                row = row_pattern.fullmatch(body.strip())
                if row:
                    printed = row.group(1 + 2*j)
                    candidates.append({"record_id":doc["id"],"row":body.strip(),"header":line.strip(),
                                       "years":lines[i+1].strip(),"group":months + "-month period","year":y1,
                                       "printed":printed,"scale":scale,"scale_note":scales[0].group(),"locator":doc.get("locator"),
                                       "base_value":Decimal(printed.replace(",",""))*SCALES[scale]})
    return candidates[0] if len(candidates)==1 else None
