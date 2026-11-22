"""Check explicitly consolidated claims against the current earnings release."""
import re

from .context import scope_issuer_keys
from .display_numbers import SHARED_RANGE, _matches, _quantity_spans
from .financial import duration_class
from .metric_language import LINK, QUALIFIED_BASIS, metric_mentions, metric_spans
from .reporting_period import DATE_TEXT, parse_reported_date, reported_period
from .skill_calculations import requested_fiscal_year, target_labels, targets_prior_quarter
from .skill_routing import named_issuers


METRICS = {"revenue", "operating_income"}
GAAP = ("GAAP", "US-GAAP")
CONSOLIDATED = re.compile(r"(?:\b(?:consolidated|company[- ]wide|group[- ]wide)\b|合[并併]|集[团團])"
                          r"\s*(?:(?:U\.?S\.?[- ]?)?GAAP\b\s*|口[径徑]\s*|的\s*)*$", re.I)
LEVEL_PREDICATE = re.compile(
    r"\s*(?:(?:is|at|reached|came in at|(?:increased|decreased|rose|fell|grew)\s+to)\b|"
    r"[=:：]|(?:约|約)?(?:为|為|是|达|達)(?:到)?|"
    r"(?:同比|环比|環比)?(?:增[长長]|增加|上升|下降|减少|減少|下滑)(?:至|到))"
    r"\s*(?:(?:approximately|about)\s+|[约約]\s*)?", re.I)
REPORTED = re.compile(r"\breported\b|披露|公布|公佈|报告|報告", re.I)
YOY = re.compile(r"\b(?:year[- ]over[- ]year|YoY)\b|同比", re.I)
OTHER_RATE_PERIOD = re.compile(r"\b(?:(?:quarter|month)[- ]over[- ](?:quarter|month)|qoq|mom|"
                               r"sequential(?:ly)?|from (?:the )?prior quarter)\b|[环環]比|[较較]上季", re.I)
CURRENCY_ADJUSTED = re.compile(r"\s*(?:(?:year[- ]over[- ]year|YoY)\s*)?[（(]?\s*"
                               r"(?:(?:in |on a )?constant[- ]currency\b|(?:按|以|为|為)?固定[汇匯]率)", re.I)
CHANGE = re.compile(r"\s*(?:[,，]\s*|and\s+|[（(]\s*)?(?:(?:up|down|increased|decreased|rose|fell|grew)"
                    r"\s+(?:by\s+)?|同比\s*(?:(?:增長|增长|上升|下降|减少|減少|提高|下滑)\s*)?)"
                    r"[-+−]?\d+(?:\.\d+)?\s*[%％]", re.I)
SENTENCE_END = re.compile(r"(?<!\b[A-Z]\.[A-Z]\.)(?<=[.!?])\s+(?=[A-Z\u3400-\u9fff])|[；;。！？\n]", re.I)
LOCAL_CLAUSE = re.compile(r"[，,](?!\d)|\b(?:while|whereas|but)\b|而|但", re.I)
QUOTE = re.compile(r'''“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』|"[^"\n]*"|(?<!\w)'[^'\n]*'(?!\w)|`[^`\n]*`''')
OTHER_SCOPE = re.compile(r"\b(?:non[- ]?GAAP|pro forma|currency[- ]adjusted|constant[- ]currency|"
                         r"unconsolidated|subsidiary|estimated?|expect(?:s|ed)?|forecast(?:ed)?|outlook|targeted|"
                         r"if|assuming|hypothetical|could|might|would|will)\b|"
                         r"非\s*GAAP|未合[并併]|固定[汇匯]率|剔除|調整後|調節後|預測|預期|預計|估計|展望|指[引導]|"
                         r"如果|假[设設]|(?:业务|業務|板块|板塊|事业部|事業部)", re.I)
OTHER_PERIOD = re.compile(r"\b(?:prior|previous|last) (?:fiscal )?(?:year|quarter|period)\b|"
                          r"\byear[- ]to[- ]date\b|上[个個一]?(?:财|財)?季|上年(?:同期)?|去年(?:同期)?|"
                          r"累计|累計|年初至今", re.I)
HEADLINE = re.compile(r"^(?P<metric>Revenue|Operating income) was (?P<amount>.*?) "
                      r"and (?P<change>(?:increased|decreased) [-+−]?\d+(?:\.\d+)?%)(?![\d.%])", re.I)


def _source_version(record):
    return record.get("document_id"), record.get("snapshot_hash")


def _amounts(text):
    return [(start, end, amount) for start, end, amount in _quantity_spans(text) if amount[1] in {"$", "USD"}]


def _same_amount(amount, record):
    _, unit, value, tolerance = amount
    return _matches(unit, value, tolerance, record)


def _reported_rates(documents, table):
    """A release headline's base amount must agree with the consolidated table."""
    published = {}
    for doc in documents:
        for bullet in re.split(r"[·•\n]", doc.get("text", "")):
            match = HEADLINE.match(bullet.strip())
            if not match:
                continue
            metric = match["metric"].casefold().replace(" ", "_")
            source = table.get(metric)
            if not source or _source_version(doc) != _source_version(source):
                continue
            money, rates = _amounts(match["amount"]), list(_quantity_spans(match["change"]))
            if len(money) == len(rates) == 1 and _same_amount(money[0][2], source):
                published.setdefault(metric, {}).setdefault(rates[0][2][2], doc["id"])
    return {metric: next(iter(rates.items())) for metric, rates in published.items() if len(rates) == 1}


def _claim_amounts(text, dimensions, labels, start, end):
    """Bind only adjacent, monetary levels to a stated measure and population."""
    structured = dimensions.get("business_scope") == "consolidated"
    typed_metric = str(dimensions.get("metric") or "").replace("_", " ")
    structured_metrics = metric_mentions(typed_metric) if typed_metric else metric_mentions(text)
    for sentence in SENTENCE_END.split(text):
        if not sentence.strip() or SHARED_RANGE.search(sentence):
            continue
        stated_labels = target_labels(sentence)
        if (stated_labels - labels or targets_prior_quarter(sentence) or requested_fiscal_year(sentence)
                or any(parse_reported_date(m[0]) not in {start, end} for m in DATE_TEXT.finditer(sentence))):
            continue
        for marker, metric in metric_spans(sentence):
            if metric not in METRICS:
                continue
            prefix = LOCAL_CLAUSE.split(sentence[:marker.start()])[-1]
            if (QUALIFIED_BASIS.search(prefix) or OTHER_SCOPE.search(prefix) or OTHER_PERIOD.search(prefix)
                    or not (CONSOLIDATED.search(prefix) or structured and structured_metrics == {metric})):
                continue
            rest = sentence[marker.end():]
            amounts = _amounts(rest)
            if not amounts:
                continue
            amount_start, amount_end, amount = amounts[0]
            connector = rest[:amount_start]
            if not (LINK.fullmatch(connector) or LEVEL_PREDICATE.fullmatch(connector)):
                continue
            tail = rest[amount_end:]
            change = CHANGE.match(tail)
            qualifier = LOCAL_CLAUSE.split(tail.lstrip(",， "), maxsplit=1)[0]
            if ((not change and (QUALIFIED_BASIS.search(qualifier) or OTHER_SCOPE.search(qualifier)))
                    or OTHER_PERIOD.search(qualifier)):
                continue
            # A constant-currency growth rate can accompany a reported dollar
            # level. It must not be checked against the release's reported rate.
            rate = None
            if change:
                following = tail[change.end():]
                if not (CURRENCY_ADJUSTED.match(following)
                        or OTHER_RATE_PERIOD.search(LOCAL_CLAUSE.split(following, maxsplit=1)[0])):
                    rate = next(_quantity_spans(change[0]))[2]
            yield metric, amount, rate


def defects(claim, cited, records, scope, question, *, companies=()):
    """Use a retrieved earnings statement and refuse switches in population or currency basis."""
    entities = scope.get("entities") or []
    dimensions = claim.get("constraints", {})
    text = QUOTE.sub("\n", claim["text"])
    if (len(entities) != 1 or not isinstance(dimensions, dict)
            or not metric_mentions(text).intersection(METRICS)):
        return
    entity, = entities
    allowed = scope_issuer_keys([entity], companies)
    subjects = [dimensions.get("entity"), *(r.get("entity") for r in cited if not r.get("stale")),
                *named_issuers(question, companies), *named_issuers(text, companies)]
    if (any(subject and str(subject).strip().casefold() not in allowed for subject in subjects)
            or (dimensions.get("business_scope") or "consolidated") != "consolidated"
            or (dimensions.get("basis") or "GAAP") not in GAAP
            or (dimensions.get("currency") or "USD") != "USD"):
        return
    version_keys = {_source_version(record) for record in cited if not record.get("stale") and record.get("document_id")}
    requested = reported_period(question) or {}
    labels = target_labels(question)
    start = requested.get("start") or scope.get("period_start")
    end = requested.get("end") or scope.get("period_end")
    duration = requested.get("kind")
    if (not end or not version_keys or (not start and duration != "quarter") or len(labels) > 1
            or (not requested and len({m[0] for m in DATE_TEXT.finditer(question)}) > 1)
            or (dimensions.get("period_end") and dimensions["period_end"] != end)
            or (start and dimensions.get("period_start") and dimensions["period_start"] != start)):
        return
    candidates = {}
    for row in records.values():
        if (row.get("cell_type") != "statement_amount_cell" or row.get("entity") != entity or row.get("stale")
                or row.get("metric") not in METRICS or row.get("business_scope") != "consolidated"
                or row.get("basis") not in GAAP or row.get("unit") != "USD"
                or _source_version(row) not in version_keys or row.get("period_end") != end
                or (start and row.get("period_start") != start)
                or (duration and duration_class(row.get("period_start"), end) != f"{duration}_length")):
            continue
        candidates.setdefault(row["metric"], {})[(row["value"], row["table_id"], _source_version(row))] = row
    table = {metric: next(iter(rows.values())) for metric, rows in candidates.items() if len(rows) == 1}
    reported = {}
    if (table.keys() == METRICS and REPORTED.search(question) and YOY.search(question)
            and len({(row["table_id"], _source_version(row)) for row in table.values()}) == 1):
        docs = [row for row in records.values() if row.get("kind") == "document" and not row.get("stale")
                and row.get("document_report_date") == end]
        reported = _reported_rates(docs, table)
        if reported.keys() != table.keys():
            reported = {}
    for metric, amount, rate in _claim_amounts(text, dimensions, labels, start, end):
        source = table.get(metric)
        if not source or (dimensions.get("period_start") and dimensions["period_start"] != source.get("period_start")):
            continue
        name = metric.replace("_", " ")
        if not _same_amount(amount, source):
            yield [source["id"]], f"{amount[0]} does not match the same-period consolidated {name} of USD {source['value']:,}."
        if metric not in reported or rate is None:
            continue
        literal, _, value, precision = rate
        expected, ref = reported[metric]
        if abs(value - expected) > precision:
            yield [source["id"], ref], f"{literal} does not match the release's reported {name} growth of {expected}%."
