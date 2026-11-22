"""Read reporting intervals and distinguish the primary date from a comparison base."""
from datetime import datetime
import re

from .display_numbers import _quantity_spans
from .skill_calculations import is_comparison_period, quarter_mentions, target_labels


ENGLISH_DAY = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
               r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}")
DATE_TEXT = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\b" + ENGLISH_DAY + r"\b", re.I)

_REPORTING_RANGE = re.compile(r"(" + DATE_TEXT.pattern + r")\s*(?:至|到|—|–|~|～|to|through)\s*("
                              + DATE_TEXT.pattern + r")", re.I)
_REPORTING_END = re.compile(
    r"(?:(?P<quarter>quarter|three months)|(?P<annual>fiscal year|year|twelve months))\s+ended\s+(?P<english>"
    + DATE_TEXT.pattern + r")|截至\s*(?P<chinese>" + DATE_TEXT.pattern
    + r")\s*的?\s*(?P<duration>单季|季度|财季|三个月|全年|财年|年度)", re.I)
_BASELINE_LANGUAGE = re.compile(r"同比|环比|相比|比较|对比|较|基期|\b(?:vs\.?|versus|compar[ei]\w*|from|baseline)\b", re.I)


def parse_reported_date(text):
    normalized = re.sub(r"\s*[年月]\s*", "-", text).removesuffix("日").strip().replace(".", "").replace(",", " ")
    normalized = " ".join(normalized.split())
    for fmt in ("%Y-%m-%d", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            continue
    return None


_QUARTER_DATE = re.compile(r"\s*[(（]\s*(?:(?:quarter\s+)?ended\s+|截至\s*)("
                           + DATE_TEXT.pattern + r")\s*[)）]", re.I)


def explicit_quarter_end(text):
    """Read a dated parenthetical attached to an unambiguous fiscal quarter."""
    labels = target_labels(text)
    if len(labels) != 1:
        return None
    endings = {parse_reported_date(date[1]) for match, label in quarter_mentions(text)
               if label in labels and not is_comparison_period(text, match)
               and (date := _QUARTER_DATE.match(text, match.end()))}
    return next(iter(endings)) if len(endings) == 1 else None


def reported_period(text, *, statement=False):
    """Read one explicit reporting interval or a duration-qualified end date."""
    if statement and _BASELINE_LANGUAGE.search(text):
        # A date attached to a later amount may identify only the comparison
        # base. When quantities are contrasted, read the primary date heading.
        quantities = list(_quantity_spans(text))
        if len(quantities) > 1:
            text = text[:quantities[0][0]]
    ranges = {tuple(map(parse_reported_date, m.groups())) for m in _REPORTING_RANGE.finditer(text)
              if not is_comparison_period(text, m) or re.search(r"\bfrom\s*$", text[:m.start()], re.I)}
    if len(ranges) == 1:
        start, end = ranges.pop()
        if start and end and start < end:
            return {"start":start, "end":end}
    if ranges:
        return None
    ends = {(parse_reported_date(m["english"] or m["chinese"]),
             "annual" if m["annual"] or m["duration"] in {"全年", "财年", "年度"} else "quarter")
            for m in _REPORTING_END.finditer(text) if not is_comparison_period(text, m)}
    if len(ends) == 1:
        end, kind = ends.pop()
        if end:
            return {"end":end, "kind":kind}
    return None


def filed_quarter_label(root, end):
    """Use an unambiguous inline-XBRL cover-page label for the same end date."""
    fields = []
    for name in ("DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus", "DocumentPeriodEndDate"):
        values = {" ".join(" ".join(node.itertext()).split())
                  for node in root.xpath("//*[@name=$name]", name="dei:" + name)}
        if len(values) != 1:
            return None
        fields.append(values.pop())
    year, quarter, printed = fields
    if re.fullmatch(r"20\d{2}", year) and re.fullmatch(r"Q[1-4]", quarter) and parse_reported_date(printed) == end:
        return year + quarter
    return None
