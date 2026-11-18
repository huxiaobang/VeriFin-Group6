"""Inspect issuer disclosures for a year-over-year quarterly sales/day comparison."""
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .data import DataError, available_by, stable_id


ORDINALS = ("first", "second", "third", "fourth")
ORDINAL = "|".join(ORDINALS)
YEAR = r"(?:19|20)\d\d"
QUARTER = re.compile(
    rf"(?P<ya>{YEAR})\s*(?:annual and\s+)?(?:fiscal\s+)?(?P<oa>{ORDINAL})[ -]quarter|"
    rf"(?P<ob>{ORDINAL})[ -](?:fiscal )?quarter\s+(?:of\s+)?(?:fiscal(?: year)?\s+|FY\s*)?(?P<yb>{YEAR})|"
    rf"(?:FY\s*)?(?P<yc>{YEAR})[-\s]*Q(?P<qc>[1-4])\b|"
    rf"\bQ(?P<qd>[1-4])[-\s]*(?:FY\s*)?(?P<yd>{YEAR})\b", re.I)
PERCENT = re.compile(r"(?<![\d.])([+−-]?\d+(?:\.\d+)?)\s*%")
GROUPS = re.compile(r"customer|accounts?|end markets?|manufacturing", re.I)
DAILY = re.compile(r"daily (?:net )?sales|sales per (?:business|selling) day|\bDSR\b", re.I)
DAYS = re.compile(r"\b(?:business|selling|trading|working)[ -]days?\b", re.I)
YOY = re.compile(r"year[ -]over[ -]year|\byoy\b|(?:last|prior|previous) year|year[ -]ago", re.I)
US_DAYS = re.compile(r"(?:U\.?S\.?|United States).{0,15}(?:business|selling) days?|"
                     r"(?:business|selling) days?.{0,15}(?:U\.?S\.?\b|United States)", re.I)
PROJECTION = re.compile(r"guidance|outlook|forecast|\b(?:expect|predict|project)(?:s|ed)?\b", re.I)
FORECAST_TITLE = re.compile(r"(?:guidance|outlook|forecast)(?:\s+(?:for\s+)?(?:Q[1-4]|\d{4}))*[.:]?$", re.I)
GROUP_PERCENT = re.compile(
    r"\(\s*(?P<negative>\d+(?:\.\d+)?)(?:\s*%\s*\)|\s*\)\s*%)|(?P<signed>[+−-]?\d+(?:\.\d+)?)\s*%")
CUMULATIVE = re.compile(r"year.to.date|first (?:six|nine) months|(?:six|nine|twelve).month (?:period|results)|full.year (?:sales|growth)", re.I)
PERIOD_GROUP = re.compile(r"\b(?:(?P<months>three|six|nine|twelve|3|6|9|12)[ -]months?(?:\s+period)?|"
                          r"(?P<quarter>quarter)(?:ly period)?|year[ -]to[ -]date|full[ -]year)\b", re.I)
SUMMARY_ROW = re.compile(r"^\|?\s*(?P<label>(?:net|total(?: net)?|consolidated(?: net| total)?) sales|"
                         r"(?:net )?daily (?:net )?sales|(?:U\.?S\.? )?(?:business|selling) days)\b"
                         r"(?=\s*(?:[|$—–]|[+−-]?\d))", re.I)
CELL = re.compile(r"(?P<number>[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<percent>\s*%)?|(?P<blank>[—–])")
DOLLAR_UNITS = re.compile(r"\bdollar amounts are (?:stated|presented|expressed) in "
                          r"(?P<scale>thousands|millions|billions)\b", re.I)


def _summary_columns(group_header, year_header, quarter, prior):
    """Map the flattened duration/year headers before reading any numeric row."""
    groups = list(PERIOD_GROUP.finditer(group_header))
    if not groups or PERIOD_GROUP.sub("", group_header).strip(" |\t"):
        return None
    quarterly = [bool(m["quarter"] or (m["months"] or "").lower() in {"three", "3"}) for m in groups]
    if quarterly.count(True) != 1:
        return None
    columns = year_header.casefold().replace("|", " ").split()
    width, extra = divmod(len(columns), len(groups))
    if extra or width not in (2, 3):
        return None
    expected = {quarter[:4], prior[:4]} | ({"change"} if width == 3 else set())
    result = []
    for i, is_quarter in enumerate(quarterly):
        keys = columns[i*width:(i+1)*width]
        if set(keys) != expected:
            return None
        result.extend((is_quarter, key) for key in keys)
    return result


def _summary_values(line, label, columns, *, days):
    matches = list(CELL.finditer(line, label.end()))
    body = line[label.end():]
    if re.fullmatch(r"[\s|$]*", CELL.sub("", body)) is None:
        return None
    if any(a.end() == b.start() for a, b in zip(matches, matches[1:])):
        return None
    if "|" in body:
        fields = body.strip().removeprefix("|").removesuffix("|").split("|")
        if any(not f.strip() for f in fields):
            if len(fields) != len(columns) or any(len(CELL.findall(f)) > 1 for f in fields):
                return None
            cells = iter(matches)
            matches = [next(cells) if CELL.search(f) else None for f in fields]
    # Visible-text snapshots omit the empty Change cells in business-day rows.
    year_columns = [c for c in columns if c[1] != "change"]
    if days and len(matches) == len(year_columns):
        columns = year_columns
    if len(matches) != len(columns):
        return None
    selected = {}
    for (is_quarter, key), cell in zip(columns, matches):
        if cell is None:
            if key != "change":
                return None
            continue
        if key == "change":
            if not (days or cell["percent"] or cell["blank"]):
                return None
        elif not cell["number"] or cell["percent"] or (days and not cell["number"].isdigit()):
            return None
        if is_quarter and cell["number"]:
            selected[key] = cell
    return selected


def _decimal_cell(cell):
    return Decimal(cell["number"].replace(",", "").replace("−", "-"))


def _part_topic(question):
    if re.search(r"(?:客户|需求|客群|市场).{0,20}(?:普遍|回暖|好转|分化|改善|分组)|"
                 r"普遍.{0,12}(?:需求|回暖|改善)|"
                 r"\bdemand\b.{0,35}\b(?:broad|recover|improv|across|widespread)|"
                 r"\b(?:broad.based|broadly|widespread)\b.{0,35}\bdemand\b|"
                 r"\bcustomer groups?\b.{0,35}\b(?:daily sales|demand)\b", question, re.I):
        return "demand"
    if re.search(r"(?:日均|每日|平均每日)(?:净)?(?:销售|营收)|daily (?:net )?sales|sales per (?:business|selling) day", question, re.I):
        return "sales"
    return None


def supports_parts(parts):
    return bool(parts) and all(_part_topic(q) for q in parts.values())


def _labels(text):
    labels = []
    for m in QUARTER.finditer(text):
        year = m["ya"] or m["yb"] or m["yc"] or m["yd"]
        ordinal = m["oa"] or m["ob"]
        q = ORDINALS.index(ordinal.lower()) + 1 if ordinal else m["qc"] or m["qd"]
        labels.append(f"{year}Q{q}")
    return labels


def _source(doc, start, end):
    return {**doc, "id": stable_id("span_", [doc["id"], doc["text_snapshot_hash"], start, end]),
            "document_id": doc["id"], "text": doc["text"][start:end], "kind": "document",
            "start": start, "end": end, "period_start": None, "period_end": None,
            "locator": f"{doc['text_snapshot_path']}:characters[{start}:{end}] (zero-based Unicode offsets)"}


def _growth(sentence, subject):
    """Accept a single disclosed change immediately attached to the named measure."""
    m = re.search(subject + r"\s+(?P<verb>rose|grew|increased|gained|fell|declined|decreased|dropped|"
                  r"growth (?:was|of)|change (?:was|of))\s+(?:by\s+)?"
                  r"(?:\$[\d,.]+(?:\s+(?:million|billion))?,?\s+or\s+)?"
                  r"(?P<rate>[+−-]?\d+(?:\.\d+)?)\s*%", sentence, re.I)
    if m:
        number = Decimal(m["rate"].replace("−", "-"))
        return -abs(number) if m["verb"].lower() in {"fell", "declined", "decreased", "dropped"} else number
    return None


def _rate(value):
    return f"{value:g}%"


def _consistent(total, daily, counts):
    """Check whether the two printed growth ranges intersect after day-count adjustment."""
    current, previous = counts
    if not (0 < current <= 100 and 0 < previous <= 100):
        return False
    ratio = Decimal(current) / previous
    adjusted = (100 + daily) * ratio - 100
    total_rounding = Decimal(1).scaleb(total.as_tuple().exponent) / 2
    daily_rounding = Decimal(1).scaleb(daily.as_tuple().exponent) / 2
    return abs(adjusted - total) <= total_rounding + daily_rounding * ratio


class Inspection:
    def __init__(self, entity, quarter):
        self.entity = entity
        self.quarter = quarter
        year, q = quarter.split("Q")
        self.prior = f"{int(year)-1}Q{q}"
        self.metrics = defaultdict(lambda: defaultdict(set))
        self.groups = defaultdict(lambda: defaultdict(set))
        self.group_sets = []
        self.incomplete_customer_tables = set()
        self.sources = {}
        self.boundary = {"sales": {}, "demand": {}}
        self.foreign_tables = {"sales": set(), "demand": set()}
        self.us_basis = set()
        self.dates = set()
        self.other_measures = set()
        self.operand_tables = []
        self.operand_conflict = False

    def evidence(self, doc, start, end):
        record = _source(doc, start, end)
        self.sources[record["id"]] = record
        return record["id"]

    def scan(self, doc):
        raw = doc["text"]
        rows = [(m.start(), m.end(), re.sub(r"^\s*L\d+:\s*", "", m[0]).strip())
                for m in re.finditer(r"[^\r\n]+", raw) if m[0].strip()]
        unit_matches = list(DOLLAR_UNITS.finditer(raw))
        sales_unit = {}
        if unit_matches and len({m["scale"].lower() for m in unit_matches}) == 1:
            match = unit_matches[0]
            sales_unit = {"reported_unit": match["scale"].lower() + " of dollars",
                          "unit_evidence_id": self.evidence(doc, match.start(), match.end())}
        heading = None
        release_seen = False
        forecast_section = False
        for i, (start, end, line) in enumerate(rows):
            labels = _labels(line)
            if len(line) < 140 and PROJECTION.search(line) and (
                    not re.search(r"[.!?]$", line) or FORECAST_TITLE.search(line)):
                heading = None
                forecast_section = True
            # A standalone period can be either a new section or one cell of
            # a multi-line header. Neither may inherit the previous quarter.
            if QUARTER.fullmatch(line.rstrip(":.")):
                next_line = rows[i+1][2] if i+1 < len(rows) else ""
                heading = ((labels[0], start, end) if not forecast_section and PERIOD_GROUP.match(next_line)
                           else None)
            # Only an explicit release or section title sets the table's fiscal quarter.
            if (len(labels) == 1 and len(line) < 170 and
                    re.search(r"\b(?:earnings|results|performance summary|sales comparison)\b", line, re.I)
                    and not PROJECTION.search(line)
                    and not PERCENT.search(line)):
                heading = (labels[0], start, end)
                forecast_section = False
                if not release_seen and re.search(r"\breports?\b", line, re.I):
                    release_seen = True
                    if heading[0] == self.quarter:
                        a, b = doc.get("document_period_start"), doc.get("document_report_date")
                        if a and b and 74 < (date.fromisoformat(b) - date.fromisoformat(a)).days < 106:
                            self.dates.add((a, b))
            if forecast_section:
                continue
            if US_DAYS.search(line) and DAILY.search(line) and re.search(r"defined|divid", line, re.I):
                self.us_basis.add(self.evidence(doc, start, end))
            if PERIOD_GROUP.match(line.lstrip("| ")):
                self.summary_table(doc, rows, i, heading, sales_unit)
            if re.fullmatch(r"(?:DSR|daily sales(?: rate)?) change", line, re.I):
                self.group_table(doc, rows, i, heading)
            if self.quarter in labels:
                # Period labels can occur elsewhere, e.g. in payroll or capital tables.
                # Save the surrounding original lines as an auditable scope check.
                if len(line) < 40:
                    extent = rows[i:min(i+16, len(rows))]
                    excerpt = self.evidence(doc, rows[max(0, i-2)][0], extent[-1][1])
                    for _, _, row in extent:
                        name = re.match(r"^\D*?\b(headcount|locations|devices)\b[^\d]*\d", row, re.I)
                        if name:
                            self.other_measures.add({"headcount":"员工人数", "locations":"网点数",
                                                     "devices":"设备数"}[name[1].lower()])
                    for topic in self.boundary:
                        self.boundary[topic].setdefault("target_mention", excerpt)
                self.narrative(doc, start, raw[start:end])
                if len(labels) >= 2 and len(set(labels)) == len(labels):
                    self.comparative_table(doc, rows, i, labels, sales_unit)

    def table_scope(self, doc, heading, topic, first, last):
        label, a, b = heading
        title = self.evidence(doc, a, b)
        table = self.evidence(doc, first, last)
        if label != self.quarter:
            self.foreign_tables[topic].add(label)
            self.boundary[topic].update(title=title, table=table)
        return {title, table}

    def summary_table(self, doc, rows, i, heading, sales_unit):
        if not heading or i+2 >= len(rows):
            return
        label = heading[0]
        prior = f"{int(label[:4])-1}{label[4:]}"
        columns = _summary_columns(rows[i][2], rows[i+1][2], label, prior)
        if columns is not None:
            self.read_amount_table(doc, rows, i, i+2, heading, columns,
                                   {label: label[:4], prior: prior[:4]}, sales_unit)

    def read_amount_table(self, doc, rows, header, first_row, heading, columns, period_keys, sales_unit):
        found = {}
        last = None
        for a, b, line in rows[first_row:]:
            # A table body ends at the next heading, note, or prose passage.
            if not re.search(r"[0-9%—–)]\s*\|?$", line) or not re.match(r"^\|?\s*[A-Za-z%]", line):
                break
            row = SUMMARY_ROW.match(line)
            if not row:
                continue
            name = row["label"]
            key = "business_days" if DAYS.search(name) else "daily" if DAILY.search(name) else "net_sales"
            values = _summary_values(line, row, columns, days=key == "business_days")
            # A repeated row label is ambiguous even if one occurrence is malformed.
            found[key] = None if key in found or values is None else (a, b, line, name, values)
            last = b
        if not found:
            return
        refs = self.table_scope(doc, heading, "sales", rows[header][0], last)
        if heading[0] != self.quarter:
            return
        table_ref = self.evidence(doc, rows[header][0], last)
        for key, metric in (("net_sales", "total"), ("daily", "daily")):
            if found.get(key) and "change" in found[key][-1]:
                value = _decimal_cell(found[key][-1]["change"])
                self.metrics[metric][value].update(refs)
        if found.get("net_sales"):
            amounts = tuple(_decimal_cell(found["net_sales"][-1][key]) for key in period_keys.values())
            self.metrics["amounts"][amounts].update(refs)
        if not found.get("business_days"):
            return
        day_row = found["business_days"]
        counts = tuple(int(day_row[-1][key]["number"]) for key in period_keys.values())
        if not all(0 < count <= 100 for count in counts):
            return
        self.metrics["counts"][counts].update(refs)
        if US_DAYS.search(day_row[3]):
            self.us_basis.update(refs)
        if not found.get("net_sales"):
            return
        operands = {}
        for period, column_key in period_keys.items():
            operands[period] = {}
            for key in ("net_sales", "business_days"):
                a, b, line, row_label, values = found[key]
                cell = values[column_key]
                # line is trimmed and can have a removed "L123:" prefix.
                origin = doc["text"].index(line, a, b)
                start, end = origin + cell.start("number"), origin + cell.end("number")
                ref = self.evidence(doc, start, end)
                value = _decimal_cell(cell)
                meta = {"metric": key, "period": period, "fiscal_quarter": period, "raw": doc["text"][start:end],
                        "row_label": row_label, "unit": "days" if key == "business_days" else "reported_amount",
                        "table_evidence_id": table_ref, "document_id": doc["id"]}
                if key == "net_sales":
                    meta.update(sales_unit)
                self.sources[ref].update(meta, value=int(value) if value == value.to_integral() else float(value))
                operands[period][key] = dict(meta, start=start, end=end, evidence_id=ref, value=str(value))
        self.operand_tables.append(operands)

    def period_operands(self):
        if not self.operand_tables:
            return {}
        periods = (self.quarter, self.prior)
        chosen = self.operand_tables[0]
        current, prior = (Decimal(chosen[p]["net_sales"]["value"]) for p in periods)
        days, prior_days = (Decimal(chosen[p]["business_days"]["value"]) for p in periods)
        units = {table[self.quarter]["net_sales"]["reported_unit"] for table in self.operand_tables
                 if "reported_unit" in table[self.quarter]["net_sales"]}
        if (len(units) > 1 or len(self.metrics["amounts"]) > 1
                or len(self.metrics["counts"]) > 1 or len(self.metrics["day_delta"]) > 1
                or (self.metrics["day_delta"] and days-prior_days not in self.metrics["day_delta"])):
            self.operand_conflict = True
            return {}
        # Compare rounding intervals for source amounts and the printed percentages.
        # Example: a displayed 31.5 represents [31.45, 31.55] at source precision.
        def half_step(x):
            return Decimal(1).scaleb(x.as_tuple().exponent) / 2
        if prior <= half_step(prior) or current < 0:
            return {}
        low = (current-half_step(current)) / (prior+half_step(prior))
        high = (current+half_step(current)) / (prior-half_step(prior))
        for key, factor in (("total", Decimal(1)), ("daily", prior_days/days)):
            rates = self.metrics[key]
            if len(rates) > 1 or any((high*factor-1)*100 < rate-half_step(rate)
                                     or (low*factor-1)*100 > rate+half_step(rate) for rate in rates):
                self.operand_conflict = True
                return {}
        return chosen

    def group_table(self, doc, rows, i, heading):
        if not heading or not i or i+5 >= len(rows):
            return
        context = rows[i-1][2]
        if not GROUPS.search(context) or not re.search(r"(?:customers in|end market standpoint|customer (?:group|categor))", context, re.I):
            return
        if not all(re.fullmatch(pattern, rows[i+n][2], re.I) for n, pattern in
                   ((1, "Three.month Period"), (2, "% of Sales"), (3, "Three.month Period"))):
            return
        year = int(heading[0][:4])
        if rows[i+4][2].split() != [str(year), str(year-1)]*2:
            return
        target_column = self.quarter in (heading[0], f"{year-1}Q{heading[0][-1]}")
        column = 0 if self.quarter == heading[0] else 1
        groups = {}
        incomplete = False
        last = i + 4
        for _, _, line in rows[i+5:]:
            first_value = re.search(r"(?<!\S)(?:[+−-]?\d|\(\s*\d|[—–]|N/A\b)", line, re.I)
            if not first_value:
                break
            name = line[:first_value.start()].strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z &/–-]+", name):
                break
            body = line[first_value.start():]
            if not re.search(r"%|\bN/A\b|[—–]", body, re.I):
                break
            last += 1
            values = list(GROUP_PERCENT.finditer(body))
            if len(values) != 4 or GROUP_PERCENT.sub("", body).strip(" |\t") or name in groups:
                incomplete = True
                continue
            cell = values[column]
            groups[name] = (-Decimal(cell["negative"]) if cell["negative"] is not None
                            else Decimal(cell["signed"].replace("−", "-")))
        if not groups and not incomplete:
            return
        refs = self.table_scope(doc, heading, "demand", rows[i-1][0], rows[last][1])
        if target_column and re.search(r"(?:prior|previous|last) year|year.over.year|\byoy\b", context, re.I):
            if incomplete or len(groups) < 2:
                if re.search(r"customers in|customer (?:group|categor)", context, re.I):
                    self.incomplete_customer_tables.update(refs)
                return
            self.group_sets.append(list(groups))
            for name, change in groups.items():
                self.groups[name][change].update(refs)

    def comparative_table(self, doc, rows, i, labels, sales_unit):
        """Read explicitly quarter-labelled comparative columns, even in another period's filing."""
        header = QUARTER.sub("", rows[i][2])
        if not re.fullmatch(r"(?:metric|period|fiscal quarter)?[\s|,]*", header.strip(), re.I):
            return
        target = labels.index(self.quarter)
        prior = labels.index(self.prior) if self.prior in labels else None
        if prior is not None:
            periods = {p: p for p in (self.quarter, self.prior)}
            columns = [(p in periods, p) for p in labels]
            self.read_amount_table(doc, rows, i, i+1, (self.quarter, rows[i][0], rows[i][1]),
                                   columns, periods, sales_unit)
        for a, b, line in rows[i+1:]:
            first = re.search(r"[+−-]?\d", line)
            if not first:
                break
            name = line[:first.start()].strip(" |")
            values = line[first.start():]
            refs = {self.evidence(doc, rows[i][0], rows[i][1]), self.evidence(doc, a, b)}
            if re.fullmatch(r"(?:(?:U\.?S\.?)\s+)?(?:business|selling) days", name, re.I):
                numbers = re.findall(r"\d+", values)
                if prior is not None and len(numbers) == len(labels) and re.fullmatch(r"[\d\s|]+", values):
                    self.metrics["counts"][(int(numbers[target]), int(numbers[prior]))].update(refs)
                    if US_DAYS.search(name):
                        self.us_basis.update(refs)
                continue
            numbers = PERCENT.findall(values)
            if (len(numbers) != len(labels) or not YOY.search(name) or
                    not re.fullmatch(r"[\s|]*", PERCENT.sub("", values))):
                continue
            value = Decimal(numbers[target].replace("−", "-"))
            group = re.match(r"(.+?\b(?:accounts?|customers?))\b", name, re.I)
            if group and DAILY.search(name):
                self.groups[group[1]][value].update(refs)
            elif re.fullmatch(r"(?:(?:net|total|consolidated) sales|(?:net )?daily (?:net )?sales)\s+"
                              r"(?:growth\s+)?(?:yoy|year.over.year)(?:\s+(?:growth|change))?", name, re.I):
                self.metrics["daily" if DAILY.search(name) else "total"][value].update(refs)

    def narrative(self, doc, start, original_line):
        for m in re.finditer(r"(?:U\.S\.|\d\.\d|[^.!?]|\.(?=\S))+[.!?]?", original_line):
            sentence = m[0].strip()
            periods = _labels(sentence)
            if (not periods or periods[0] != self.quarter or set(periods) - {self.quarter, self.prior}
                    or not (YOY.search(sentence) or self.prior in periods)
                    or PROJECTION.search(sentence) or CUMULATIVE.search(sentence)):
                continue
            before = sentence[:QUARTER.search(sentence).start()]
            if re.search(r"(?:\bthan|\bversus|\bvs\.?|relative to|compar(?:ed|able) (?:with|to))\s*(?:the )?$", before, re.I):
                continue
            ref = self.evidence(doc, start+m.start(), start+m.end())
            day_counts = re.search(r"(\d+)\s+(?:(?:U\.?S\.?)\s+)?(?:business|selling) days\s*"
                                   r"(?:compared (?:with|to)|versus|vs\.?|against)\s+(\d+)\b", sentence, re.I)
            if day_counts:
                self.metrics["counts"][tuple(map(int, day_counts.groups()))].add(ref)
                if US_DAYS.search(sentence):
                    self.us_basis.add(ref)
            if DAYS.search(sentence):
                delta = re.search(r"\b(one|\d+)\s+(more|fewer|less)\s+(?:(?:U\.?S\.?)\s+)?(?:business|selling) days?\b", sentence, re.I)
                if delta:
                    magnitude = 1 if delta[1].casefold() == "one" else int(delta[1])
                    self.metrics["day_delta"][magnitude if delta[2].lower()=="more" else -magnitude].add(ref)
            if GROUPS.search(sentence) or re.search(r"\b(?:division|segment|product|region)\b", sentence, re.I):
                # Accept named groups only when explicitly measured on a daily-sales basis.
                if DAILY.search(sentence):
                    group = r"([A-Za-z][A-Za-z -]{0,30}?\b(?:accounts?|customers?))"
                    change = r"\s+(rose|grew|increased|fell|declined|decreased)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%"
                    entries = (re.findall(r"\b(?:daily sales|DSR) (?:for|to) " + group + change, sentence, re.I)
                               + re.findall(r"\b" + group + r"['’]? (?:daily sales|DSR)" + change, sentence, re.I))
                    for name, verb, amount in entries:
                        name = re.split(r"\b(?:for|and|while|whereas|with)\s+", name, flags=re.I)[-1].strip()
                        value = Decimal(amount) * (-1 if verb.lower() in {"fell", "declined", "decreased"} else 1)
                        self.groups[name][value].add(ref)
                continue
            for key, pattern in (("total", r"(?<!daily )\b(?:net|total|consolidated) sales"),
                                 ("daily", r"\b(?:net )?daily (?:net )?sales")):
                rate = _growth(sentence, pattern)
                if rate is not None:
                    self.metrics[key][rate].add(ref)

    def result(self, topic):
        def unique(key):
            values = self.metrics[key]
            return next(iter(values)) if len(values) == 1 else None

        receipts = set()
        boundary = sorted(self.foreign_tables[topic])
        limit = (f"所查销售表对应 {'、'.join(boundary)}。" if boundary else "")
        if self.other_measures:
            limit += f"另表列有 {self.quarter} 的{'、'.join(sorted(self.other_measures))}。"
        if topic == "sales":
            total, daily, counts, delta = (unique(k) for k in ("total", "daily", "counts", "day_delta"))
            facts = []
            for key, name, value in (("total","总销售额",total),("daily","日均销售额",daily)):
                if value is not None:
                    facts.append(f"{name}同比 {_rate(value)}")
                    receipts.update(self.metrics[key][value])
            day_refs = set().union(*(list(self.metrics["counts"].values()) + list(self.metrics["day_delta"].values())))
            day_docs = {self.sources[ref]["document_id"] for ref in day_refs}
            us_refs = {ref for ref in self.us_basis if self.sources[ref]["document_id"] in day_docs}
            basis = "美国营业日" if us_refs else "营业日"
            if counts is not None:
                current, previous = counts
                facts.append(f"{self.quarter} 和 {self.prior} 分别为 {current} 和 {previous} 个{basis}")
                receipts.update(self.metrics["counts"][counts])
            if delta is not None:
                facts.append(f"{basis}较上年同季变化 {delta:+d} 天")
                receipts.update(self.metrics["day_delta"][delta])
            if counts is not None or delta is not None:
                receipts.update(us_refs)
            missing = [name for name, v in (("总销售额同比",total), ("日均销售额同比",daily),
                                             ("两期美国营业日数或公司披露的天数差", counts if counts is not None else delta))
                       if v is None]
            if self.operand_conflict:
                missing = ["相互一致的表内销售额、营业日数和同比增长率"]
                receipts.update(set().union(*self.metrics["amounts"].values()))
            if not missing:
                change = current-previous if counts else delta
                consistent = (_consistent(total, daily, counts) if counts else
                              ((total-daily)*change > 0 or change == 0 and total == daily))
                if consistent and len(self.metrics["day_delta"]) <= 1 and len(self.metrics["counts"]) <= 1 and (delta is None or delta == change):
                    reason = ("本期多出的营业日抬高总额增速" if change > 0 else
                              "本期营业日减少压低总额增速" if change < 0 else "两期营业日相同，天数没有造成增速差")
                    detail = ("。两种增速和两期天数在披露精度内吻合。" if counts else
                              "。仅有天数差，能判断方向，尚无法复算两种增速之间的准确差额。")
                    return self.row("source_excerpt", "，".join(facts) + "。" + reason + detail, receipts)
                missing = ["相互一致的增长率和营业日口径"]
                receipts.update(day_refs)
            known = ("已找到" + "，".join(facts) + "。") if facts else ""
            message = (f"就当前可用材料，{self.quarter} 与 {self.prior} 的比较中，{known}尚缺可对应到这两个季度的"
                       + "、".join(missing) + "，无法核实营业日对总额与日均增速差的影响。")
        else:
            if self.incomplete_customer_tables:
                return self.row("gap", f"{self.quarter} 的客户表中存在不完整或含歧义的分组，"
                                "无法据此判断各类客户的需求是否普遍回暖。", self.incomplete_customer_tables)
            groups = {name: next(iter(values)) for name, values in self.groups.items() if len(values) == 1}
            if len(groups) >= 2 and len(groups) == len(self.groups):
                # Prefer the actual customer table. One contrary group suffices to qualify breadth.
                preferred = next((g for g in self.group_sets if any(re.search(r"accounts?|customers?", n, re.I) for n in g)), None)
                names = preferred or list(groups)
                parts = []
                for name in names:
                    value = groups[name]
                    parts.append(f"{name} {_rate(value)}")
                    receipts.update(self.groups[name][value])
                assessment = ("有客群日均销售持平或下降，不能据此判断客户需求普遍回暖。"
                              if min(groups[name] for name in names) <= 0 else
                              "这些分组的日均销售均有增长；仅凭销售额无法区分价格、客户结构和原有客户的需求变化。")
                return self.row("source_excerpt", f"{self.quarter} 分组日均销售同比变化为 " + "、".join(parts) + "。" + assessment, receipts)
            partial = ""
            if len(groups) == 1:
                name, value = next(iter(groups.items()))
                partial = f"已找到 {name} 日均销售同比 {_rate(value)}。"
                receipts.update(self.groups[name][value])
            message = (f"就当前可用材料，{self.quarter} 与 {self.prior} 的客户需求比较中，{partial}缺少可比客户分组或终端市场的"
                       "日均销售同比表现，无法判断剔除营业日后需求是否普遍回暖。")
        receipts.update(self.boundary[topic].values())
        return self.row("gap", message + limit, receipts)

    def row(self, status, message, receipts):
        key = "answer_sentence" if status == "source_excerpt" else "missing_support"
        return {"status": status, key: message, "quarter": self.quarter, "evidence_ids": sorted(receipts)}


def inspect(corpus, entity, cutoff, quarter, parts):
    if not re.fullmatch(r"(?:19|20)\d\dQ[1-4]", quarter):
        raise DataError("Use an explicit fiscal-quarter label such as YYYYQn.")
    if not cutoff:
        raise DataError("A publication cutoff is required for the sales comparison.")
    if not supports_parts(parts):
        raise DataError("The active questions must concern sales per day or customer demand breadth.")
    audit = Inspection(entity, quarter)
    docs = [doc for doc in corpus.documents if doc["entity"] == entity and available_by(doc["published_at"], cutoff)]
    for doc in docs:
        audit.scan(doc)
    sales_parts = [part for part, question in parts.items() if _part_topic(question) == "sales"]
    operands = audit.period_operands() if sales_parts else {}
    rows = {part: audit.result(_part_topic(question)) for part, question in parts.items()}
    table_refs = {cell[field] for table in audit.operand_tables for cells in table.values()
                  for cell in cells.values() for field in ("evidence_id", "table_evidence_id", "unit_evidence_id")
                  if field in cell}
    for part in sales_parts:
        rows[part]["evidence_ids"] = sorted(set(rows[part]["evidence_ids"]) | table_refs)
    kept = {key for row in rows.values() for key in row["evidence_ids"]}
    details = {"fiscal_quarter": quarter, "comparison_quarter": audit.prior, "part_findings": rows,
               "inspected_documents": [{"document_id": d["id"], "source_url": d["source_url"],
                                        "snapshot_hash": d["snapshot_hash"], "text_snapshot_hash": d["text_snapshot_hash"],
                                        "characters_examined": len(d["text"]), "published_at": d["published_at"]} for d in docs]}
    details["calendar_bridge_parts"] = sales_parts
    if operands:
        details["period_operands"] = operands
    if len(audit.dates) == 1:
        first, last = next(iter(audit.dates))
        details["fiscal_period"] = {"entity":entity, "quarter":quarter, "period_start":first, "period_end":last}
    return {"ok": True, "summary": f"Inspected {len(docs)} local issuer disclosures for {quarter} and {audit.prior}.",
            "evidence": [s for k, s in audit.sources.items() if k in kept], "data": details}
