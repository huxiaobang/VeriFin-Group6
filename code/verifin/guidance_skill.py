"""Read dated issuer guidance and reconcile it with reported, additive period flows."""
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
import json
import re


MONEY = "USD millions"
EPS = "GAAP diluted EPS"
MARGIN = "GAAP operating margin"
REVENUE = "total revenue"
EBIT = "EBIT-adjusted"
INCOME = "operating income"
SHARES = "weighted-average diluted shares"
UNITS = {REVENUE: MONEY, EBIT: MONEY, EPS: "USD/share", INCOME: MONEY,
         SHARES: "million shares", MARGIN: "%"}
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
ENDS = ("下限", "中点", "上限")


def number(text):
    text = str(text).replace(",", "").strip()
    if re.fullmatch(r"\(\d+(?:\.\d+)?\)", text):
        text = "-" + text[1:-1]
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        raise ValueError(f"无法确定数值 {text!r}")
    return Decimal(text)


def fiscal_period(title):
    quarter = re.search(r"\b(first|second|third|fourth)[ -]quarter\b", title, re.I)
    year = re.search(r"\bfiscal(?: year)?\s+(20\d\d)\b", title, re.I)
    if not year and quarter:
        year = re.search(r"\b(20\d\d)\s+(?:first|second|third|fourth)[ -]quarter|"
                         r"(?:first|second|third|fourth)[ -]quarter\s+(20\d\d)\b", title, re.I)
    if not quarter or not year:
        raise ValueError("公告标题未识别出报告财年和季度")
    return int(next(x for x in year.groups() if x)), ORDINAL[quarter[1].lower()]


@dataclass
class Cell:
    value: Decimal
    locator: str


@dataclass
class Release:
    doc: dict
    year: int
    quarter: int
    cells: dict = field(default_factory=dict)

    def put(self, metric, period, side, value, locator):
        key = (metric, period, side)
        if key in self.cells and self.cells[key].value != value:
            raise ValueError(f"同一 {metric} {period} 有互相冲突的数值")
        self.cells.setdefault(key, Cell(value, locator))

    def guide(self, metric, target, values, locator, scale=1):
        low, high = [number(v) * scale for v in values]
        if low > high:
            raise ValueError("指引端点倒置")
        if scale != 1:
            locator += f" / 源数值 × {scale}，折为 {UNITS[metric]}"
        self.put(metric, target, "下限", low, locator + " / 下限")
        self.put(metric, target, "上限", high, locator + " / 上限")

    def fact(self, result, metric, period, side="实际"):
        cell = self.cells.get((metric, period, side))
        if cell is None:
            return None
        label = f"FY{self.year} {period} {metric} {side}"
        identifier = result.fact(self.doc, cell.locator, metric, float(cell.value),
                                 UNITS[metric], f"FY{self.year} {period}", label)
        return Point(cell.value, identifier)


def read_excerpt(doc, raw):
    data = json.loads(raw)
    release = Release(doc, *fiscal_period(data["source_title"]))
    if data.get("disclosed_on") != str(doc["published_at"])[:10]:
        raise ValueError("摘录披露日与文档元数据不一致")
    for row in data["rows"]:
        text = row["period_metric_unit"]
        values = row["values_in_source_order"]
        loc = f"发行人原表摘录 L{row['view_line']} / {text}"
        if re.search(r"分部|地区|恒定汇率|不变汇率|有机|备考|non[- ]gaap|pro forma", text, re.I):
            continue
        metric = None
        if "EBIT-adjusted" in text and "集团" in text:
            metric = EBIT
        elif "总收入" in text or "收入指引" in text:
            metric = REVENUE
        elif "摊薄每股收益" in text:
            metric = EPS
        elif "加权摊薄股数" in text:
            metric = SHARES
        elif "GAAP 营业利润率" in text:
            metric = MARGIN
        if not metric:
            continue
        if metric in (REVENUE, EBIT) and not re.search(r"(百万|十亿)美元", text):
            continue
        scale = 1000 if "十亿美元" in text else 1
        # Combined annual/current-quarter outlook or newly updated/previous fiscal-year range.
        outlook = re.search(r"Q([1-4]) FY(\d{2,4}), FY(\d{2,4})", text)
        if outlook and int(outlook[2]) % 100 == release.year % 100 == int(outlook[3]) % 100:
            if len(values) == 4 and "指引" in text:
                release.guide(metric, f"Q{outlook[1]}", values[:2], loc, scale)
                release.guide(metric, "FY", values[2:], loc, scale)
            elif metric == MARGIN and len(values) == 2 and values[0] == "N/A":
                release.put(metric, "FY目标", "指引", number(values[1]), loc)
            continue
        if "全年" in text and "更新、此前" in text and len(values) == 4:
            release.guide(metric, "FY", values[:2], loc, scale)
            release.guide(metric, "同表旧FY", values[2:], loc, scale)
            continue
        if re.search(r"本季、去年本季、本财年九个月、去年同期九个月", text) and len(values) == 4:
            if release.quarter == 3:
                release.put(metric, "Q3", "实际", number(values[0]) * scale, loc + " / 本季")
                release.put(metric, "9M", "实际", number(values[2]) * scale, loc + " / 本财年九个月")
        elif "半年，去年同期" in text and release.quarter == 2:
            release.put(metric, "6M", "实际", number(values[0]) * scale, loc + " / 本年半年")
        elif "本季度，去年本季" in text or (
                release.quarter == 3 and "当前第三季、去年同期" in text):
            release.put(metric, f"Q{release.quarter}", "实际", number(values[0]) * scale, loc + " / 本季")
    return release


def pdf_tables(doc, raw):
    import pdfplumber
    with pdfplumber.open(BytesIO(raw)) as pdf:
        release = None
        for page in pdf.pages:
            text = page.extract_text() or ""
            if release is None:
                # The title comes before the release body and can cover more than one line.
                release = Release(doc, *fiscal_period(" ".join(text.splitlines()[:3])))
            loc = f"完整公告 PDF 第{page.page_number}页"
            header = re.search(r"Q([1-4])\s+FY(\d{2,4})\s+Full Year FY(\d{2,4})", text, re.I)
            if header and int(header[2]) % 100 == release.year % 100 == int(header[3]) % 100:
                for line in text.splitlines():
                    if not re.match(r"^Total revenues?\s+\$", line, re.I):
                        continue
                    pair = rf"\$({NUMBER})\s*[-–]\s*\$({NUMBER})\s+(billion|million)"
                    match = re.fullmatch(rf"Total revenues?\s+{pair}\s+{pair}", line, re.I)
                    if match:
                        for target, start in ((f"Q{header[1]}", 1), ("FY", 4)):
                            scale = 1000 if match[start + 2].lower() == "billion" else 1
                            release.guide(REVENUE, target, match.groups()[start-1:start+1],
                                          f"{loc} / {header[0]} / {line}", scale)
            if "consolidated statements of operations" not in text.lower():
                continue
            if re.search(r"(?:in\s+|amounts in\s+)(?:Canadian|Australian|Hong Kong|euro)|\b(?:CAD|AUD|EUR|HKD)\b", text, re.I):
                continue
            if not re.search(r"\bin millions, except per share", text, re.I):
                continue
            duration = {1: None, 2: "Six Months Ended", 3: "Nine Months Ended", 4: "Fiscal Year Ended"}[release.quarter]
            if not duration or not re.search(r"Three Months Ended[^\n]*"+duration, text, re.I):
                continue
            # This adapter handles an explicit two-period, two-year statement only.
            years = re.search(r"^(\d{4})\s+(\d{4})\s+\1\s+\2\s*$", text, re.M)
            if not years or int(years[1]) != int(years[2]) + 1:
                continue
            for label, metric in (("Total revenues", REVENUE), ("Income from operations", INCOME),
                                  ("Diluted net income per share", EPS),
                                  ("Shares used in computing diluted net income per share", SHARES)):
                row = re.search(r"^"+label+r"\s+(?:\(\d+\)\s+)?([$\d(][^\n]*)$", text, re.I | re.M)
                if not row:
                    continue
                tokens = row[1].replace("$", "").split()
                if len(tokens) != 4:
                    continue
                period = "FY" if release.quarter == 4 else f"{3 * release.quarter}M"
                for index, span in ((0, f"Q{release.quarter}"), (2, period)):
                    release.put(metric, span, "实际", number(tokens[index]),
                                f"{loc} / Consolidated Statements of Operations / {label} / {span} / "
                                f"{years[1]} / in millions except per share")
            break
    if release is None:
        raise ValueError("PDF 中没有可读的标题")
    return release


def html_tables(doc, raw):
    from lxml import html
    tree = html.fromstring(raw)
    release = Release(doc, *fiscal_period(tree.xpath("string(//title)")))
    for table in tree.xpath("//table"):
        rows = []
        for tr in table.xpath("./tr|./thead/tr|./tbody/tr"):
            rows.append([" ".join(cell.text_content().split()) for cell in tr.xpath("./td|./th")])
        if len(rows) < 3 or len(rows[1]) < 3 or "$M" not in rows[1][0]:
            continue
        duration = " ".join(rows[0]).strip().lower()
        periods = {"three months ended": f"Q{release.quarter}", "years ended": "FY",
                   "six months ended": "6M", "nine months ended": "9M"}
        period = periods.get(duration)
        if not period or (period == "FY" and release.quarter != 4):
            continue
        dates = [re.fullmatch(r"[A-Za-z]+ \d{1,2}, (\d{4})", t) for t in rows[1][1:3]]
        if not all(dates) or int(dates[0][1]) != int(dates[1][1]) + 1:
            continue
        for pos, row in enumerate(rows[2:], 3):
            if len(row) < 3 or row[0].lower() not in ("ebit-adjusted", "revenue", "total revenues"):
                continue
            metric = EBIT if row[0].lower() == "ebit-adjusted" else REVENUE
            table_path = tree.getroottree().getpath(table)
            release.put(metric, period, "实际", number(row[1].replace("$", "")),
                        f"完整公告 HTML {table_path} / 行{pos} / {row[0]} / {duration} / {rows[1][1]} / {rows[1][0]}")
    return release


@dataclass(frozen=True)
class Point:
    value: Decimal
    id: str


def calculate(result, label, operation, a, b, unit):
    identifier = result.calc(label, operation, [a.id, b.id], unit)
    return Point(Decimal(str(result.value(identifier))), identifier)


def fmt(value):
    return format(value.normalize(), ",f")


def bounds(result, rel, metric, period):
    if rel is None or (metric, period, "上限") not in rel.cells:
        return None
    low = rel.fact(result, metric, period, "下限")
    high = rel.fact(result, metric, period, "上限")
    mid = calculate(result, f"{rel.doc['published_at'][:10]} {period} {metric} 指引中点",
                    "midpoint", low, high, UNITS[metric])
    return low, mid, high


def show_range(result, title, points, unit):
    result.note(f"{title} 下限、中点、上限分别为 "
                + "、".join(fmt(p.value) for p in points) + f" {unit}。", [p.id for p in points])


def compare(result, title, actual, points, unit):
    deltas = [calculate(result, f"{title}实际减{end}", "difference", actual, point, unit)
              for end, point in zip(ENDS, points)]
    low, mid, high = (p.value for p in points)
    outcome = "高于上限" if actual.value > high else "低于下限" if actual.value < low else "位于区间内（含边界）"
    midpoint = "高于" if actual.value > mid else "低于" if actual.value < mid else "等于"
    result.note(f"{title}，实际 {fmt(actual.value)} {unit}，{outcome}，{midpoint}中点。"
                "实际减低、中、高分别为 " + "、".join(fmt(d.value) for d in deltas) + f" {unit}。",
                [actual.id, *(d.id for d in deltas)])


def implied(result, series, metric, current, index):
    annual = bounds(result, current, metric, "FY")
    if not annual:
        result.gap("这版材料缺可定位的全年同指标上下限。")
        return None
    show_range(result, f"{current.doc['published_at'][:10]} FY{current.year} 公司全年{metric}指引", annual, UNITS[metric])
    q = current.quarter
    ytd = current.fact(result, metric, f"{3*q}M")
    if not ytd and q > 1:
        previous = next((r for r in reversed(series[:index]) if r.quarter == q - 1), None)
        previous_ytd = previous.fact(result, metric, f"{3*(q-1)}M") if previous else None
        current_q = current.fact(result, metric, f"Q{q}")
        if current_q and previous_ytd:
            ytd = calculate(result, f"FY{current.year} {3*q}M 累计{metric}", "sum", previous_ytd, current_q, UNITS[metric])
    if not ytd:
        result.gap(f"找不到同口径的 FY{current.year} 前 {3*q} 个月实际，无法计算剩余期要求。")
        return None
    target = {1:"余下九个月", 2:"H2", 3:"Q4"}.get(q)
    if not target:
        return None
    result.note(f"FY{current.year} 前 {3*q} 个月实际 {fmt(ytd.value)} {UNITS[metric]}。", [ytd.id])
    remaining = [calculate(result, f"FY{current.year} {target} 隐含{end}", "difference",
                           point, ytd, UNITS[metric]) for end, point in zip(ENDS, annual)]
    show_range(result, f"FY{current.year} {target} 全年减累计的隐含{metric}", remaining, UNITS[metric])
    return remaining


def compare_remaining_midpoints(result, metric, current, previous, new_remaining, annual, prior_annual, prior_quarter):
    """Compare two implied Q4 scenarios for an additive flow using forecasts available at each date."""
    if current.quarter != 3 or not previous:
        result.gap("比较末季要求的两版变化需要同财年二季与三季披露。")
        return
    if not all((annual, prior_annual, prior_quarter, new_remaining)):
        result.gap("缺少可比版本或旧版公司对第三季的直接指引中点；只能保留来源足够的当期区间。")
        return
    old_ytd = previous.fact(result, metric, "6M")
    actual = current.fact(result, metric, "Q3")
    if not old_ytd or not actual:
        result.gap("缺少同口径半年或第三季实际，无法对齐两版末季情景。")
        return
    nine_months = current.fact(result, metric, "9M")
    if nine_months and old_ytd.value + actual.value != nine_months.value:
        result.gap("旧版半年实绩加本季度与新版累计金额不一致，需先核对重列范围或披露精度。")
        return
    unit = UNITS[metric]
    old_half = calculate(result, "旧版全年中点减当时半年实绩", "difference", prior_annual[1], old_ytd, unit)
    old_fourth = calculate(result, "旧版隐含末季中点情景", "difference", old_half, prior_quarter[1], unit)
    change = calculate(result, "新版减旧版隐含末季中点情景", "difference", new_remaining[1], old_fourth, unit)
    result.note(f"同一财年的两版隐含末季中点情景为 {fmt(old_fourth.value)} 和 {fmt(new_remaining[1].value)} "
                f"{unit}，新版减旧版为 {fmt(change.value)} {unit}。旧版使用当时公司给第三季的中点；"
                "这是按两版假设推算的收入或盈利要求，不能据此认定公司调整过直接公布的末季指引或判定经营原因。",
                [old_fourth.id, new_remaining[1].id, change.id])


def eps_and_margin(result, rel, earlier):
    if earlier:
        eps_range = bounds(result, earlier, EPS, f"Q{rel.quarter}")
    else:
        eps_range = None
    eps = rel.fact(result, EPS, f"Q{rel.quarter}")
    if eps and eps_range:
        compare(result, f"Q{rel.quarter} 公司直接摊薄 EPS 指引", eps, eps_range, UNITS[EPS])
    elif not eps_range or not eps:
        result.gap("比较直接 EPS 指引与实绩需要同季度的两项原文；目前至少缺一项。")
    shares = [r.fact(result, SHARES, period) for r, period in (
        (rel, f"Q{rel.quarter}"), (earlier, f"{3*(rel.quarter-1)}M")) if r]
    if all(shares) and len(shares) == 2:
        result.note("每股收益采用各期加权股数。单季与此前累计的摊薄分母分别为 "
                    + "、".join(fmt(p.value) for p in shares) + " 百万股，不能直接相减。",
                    [p.id for p in shares])
    else:
        result.gap("现有资料未凑齐两期加权股数，不能用全年 EPS 减累计 EPS 推单季。")
    numerator = rel.fact(result, INCOME, f"Q{rel.quarter}")
    denominator = rel.fact(result, REVENUE, f"Q{rel.quarter}")
    if numerator and denominator and denominator.value:
        margin = calculate(result, f"Q{rel.quarter} 实际营业利润率", "margin", numerator, denominator, "%")
        result.note(f"营业利润率按单季利润除以单季收入，实际约 {margin.value:.2f}%。全年比例不能直接减累计比例。",
                    [margin.id])
    else:
        result.gap("缺可比的单季营业利润与收入，无法算出该季度的营业利润率。")


def analyze(question, history, documents, result, *, data=None, fiscal_quarter=None,
            period_start=None, period_end=None):
    """Caller supplies the issuer's as-of-visible documents. History only resolves the metric."""
    if fiscal_quarter:
        from .guidance_comparison import direct_comparison
        direct_comparison(fiscal_quarter, documents, result, data=data, period_start=period_start, period_end=period_end)
        return
    if re.search(r"恒定汇率|不变汇率|有机收入|分部|区域收入|北美收入|segment|organic revenue|constant currency|"
                 r"未调整.{0,8}EBIT|unadjusted EBIT|EBITDA", question, re.I):
        result.gap("当前仅能核对已支持的集团报告口径，提问指定的范围或盈利定义需要单独证据。")
        return
    ratio_question = re.search(r"每股|利润率|\bEPS\b|margin", question, re.I)
    metric = None
    for text in [question, *reversed(history)]:
        if re.search(r"\bEBIT\b", text, re.I):
            metric = EBIT
            break
        if re.search(r"收入|营收|\brevenues?\b", text, re.I):
            metric = REVENUE
            break
    if (not metric and not ratio_question) or not documents or len({d.get("entity") for d in documents}) != 1:
        result.gap("需要单一发行人、可见原件和明确的集团收入或调整后 EBIT 指标。")
        return
    releases = []
    for doc in sorted(documents, key=lambda d: d["published_at"]):
        raw = doc["raw"].lstrip()
        try:
            if raw.startswith(b"%PDF"):
                rel = pdf_tables(doc, raw)
            elif raw.startswith(b"{"):
                rel = read_excerpt(doc, raw)
            elif raw.startswith(b"<"):
                rel = html_tables(doc, raw)
            else:
                raise ValueError("文件格式未支持")
            releases.append(rel)
        except (ImportError, KeyError, IndexError, TypeError, ValueError) as exc:
            result.gap(f"{doc.get('name') or doc.get('filename')} 无法取数：{exc}")
            if doc["published_at"] == max(d["published_at"] for d in documents):
                return
    if not releases:
        return
    current = releases[-1]
    target = re.search(r"(?:FY\s*|财年\s*)(20\d{2})|(20\d{2})\s*财年", question, re.I)
    if target and current.year != int(target[1] or target[2]):
        result.gap("提问指定的财年与本轮最近披露的财年不一致，需限定目标财年的公告集。")
        return
    series = [r for r in releases if r.year == current.year]
    # Reject any ambiguity rather than choose a version by file order.
    if len({r.quarter for r in series}) != len(series):
        result.gap("同一财年同一季存在多个材料版本，当前无法唯一确定适用版本。")
        return
    i = len(series)-1
    prev = series[i-1] if i and series[i-1].quarter == current.quarter-1 else None
    if ratio_question and not re.search(r"收入|营收|\brevenues?\b|\bEBIT\b", question, re.I):
        eps_and_margin(result, current, prev)
        return
    unit = UNITS[metric]
    if current.quarter < 4:
        remaining = implied(result, series, metric, current, i)
        current_range = bounds(result, current, metric, "FY")
        previous_range = bounds(result, prev, metric, "FY")
        repeated_range = bounds(result, current, metric, "同表旧FY")
        old_range = previous_range or repeated_range
        if previous_range and repeated_range and any(a.value != b.value for a,b in zip(previous_range, repeated_range)):
            result.gap("前一期公告与本次同表旧版的范围有冲突，不能确定变化基准。")
            old_range = None
        if current_range and old_range:
            changes = [calculate(result, f"全年指引{end}变动", "difference", new, old, unit)
                       for end, new, old in zip(ENDS, current_range, old_range)]
            show_range(result, "此前公司全年指引", old_range, unit)
            result.note("全年指引变动：" + "，".join(
                f"{end}{'升' if p.value > 0 else '降'} {fmt(abs(p.value))}" if p.value else f"{end}不变"
                for end, p in zip(ENDS, changes)) + f"（{unit}）。", [p.id for p in changes])
        elif not (previous_range and repeated_range) and re.search(r"改|上调|提高|旧目标|此前的", question):
            result.gap("缺可比的旧版或新版全年指引，不能核定哪一端变动。")
        actual = current.fact(result, metric, f"Q{current.quarter}")
        former_direct = bounds(result, prev, metric, f"Q{current.quarter}")
        compare_versions = (re.search(r"两版|两次|新旧|相对(?:原先|以前)|比以前|versus (?:the )?previous", question, re.I)
                            and re.search(r"末季|最后一季|四季|\bQ4\b|fourth quarter", question, re.I))
        if compare_versions:
            compare_remaining_midpoints(result, metric, current, prev, remaining, current_range, previous_range, former_direct)
        if actual and former_direct:
            compare(result, f"Q{current.quarter} 上一版公司直接指引", actual, former_direct, unit)
        elif re.search(r"好过|超过|超指引|beat", question, re.I):
            result.gap("缺本季实际或事前公司直接季指引，不能判断本季是否超过直接区间。")
        direct = bounds(result, current, metric, f"Q{current.quarter+1}")
        if direct:
            show_range(result, f"公司直接披露 Q{current.quarter+1} {metric} 指引", direct, unit)
        return
    actual = current.fact(result, metric, "Q4")
    annual_actual = current.fact(result, metric, "FY")
    if not prev:
        result.gap("缺目标财年三季报时点的指引，无法评估该版目标的兑现。")
    else:
        direct = bounds(result, prev, metric, "Q4")
        residual = implied(result, series, metric, prev, i-1)
        for label, points in (("直接", direct), ("隐含", residual)):
            if actual and points:
                compare(result, f"Q4 {label}指引", actual, points, unit)
        annual = bounds(result, prev, metric, "FY")
        if annual and annual_actual:
            compare(result, "全年公司指引", annual_actual, annual, unit)
        if not actual or not annual_actual:
            result.gap("末季或全年缺少同指标实绩，仅能比较已取到的期间。")
    if ratio_question:
        eps_and_margin(result, current, prev)
