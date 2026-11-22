"""Bind displayed SEC links to the public evidence used for the statement."""

import re
from urllib.parse import parse_qsl, urlsplit

from .data import DataError, parse_day
from .financial import duration_class
from .skill_calculations import quarter_label, quarter_mentions


_LINK = re.compile(r"https?://[^\s<>\[\]()（）。，、；！？]+", re.I)
_ARCHIVE = re.compile(r"/Archives/edgar/data/[0-9]+/[0-9]{18}/[A-Za-z0-9_.-]+")
_SEC_DATA = re.compile(r"(?:/api/xbrl/companyfacts/|/submissions/)CIK[0-9]{10}(?:-submissions-[0-9]+)?\.json")
_CLAUSE = re.compile(r"[。；;！？!?\n]|(?<=\.)\s+")
_AS_FILED = re.compile(r"(?:原始|原[季期]|[当當]季)\s*(?:的\s*)?(?:SEC\s*)?(?:申[报報]|季[报報]|filing)|"
                       r"\b(?:original (?:SEC )?filing|as[- ]filed)\b", re.I)
_QUARTER_FILING = re.compile(r"(?:的\s*)?(?:SEC\s*)?(?:申[报報]|季[报報])|\b(?:SEC\s+)?filing\b", re.I)
_COMPARATIVE = re.compile(r"比[较較](?:數據|数据|列)|(?:上[年一]|去年)同[期季]|\bcomparative\b", re.I)
_LATER = re.compile(r"[后後]来|后续|後續|随后|隨後|次年|翌年|\b(?:later|subsequent)\b", re.I)


def _sec_target(url):
    if not url:
        return None
    try:
        location = urlsplit(url)
        host = location.hostname
        port = location.port
    except ValueError:
        return None
    if (location.scheme not in {"http", "https"} or location.username is not None
            or port not in (None, 443, 80)):
        return None
    if host == "data.sec.gov" and _SEC_DATA.fullmatch(location.path):
        return host, location.path, location.query
    if host not in {"sec.gov", "www.sec.gov"}:
        return None
    path, query = location.path, location.query
    if path in {"/ix", "/ixviewer/doc/action"}:
        fields = parse_qsl(query, keep_blank_values=True)
        if len(fields) != 1 or fields[0][0] != "doc":
            return None
        path, query = fields[0][1], ""
    if _ARCHIVE.fullmatch(path):
        return "www.sec.gov", path, query
    return None


def _cited_rows(citations, records):
    pending, seen = list(citations), set()
    while pending:
        row = pending.pop()
        if row["id"] in seen or row.get("stale"):
            continue
        seen.add(row["id"])
        yield row
        if row.get("operation"):
            pending.extend(records[ref] for ref in row.get("operand_ids", []) if ref in records)


def unbound_sec_links(text, citations, records, *, link_only=False):
    """A URL names one source file, or one associated filing supplied by SEC metadata."""
    from .source_link_followup import public_link

    def binding(url):
        return _sec_target(url) or (public_link(url) if link_only else None)

    shown = {}
    for match in _LINK.finditer(text):
        url = match.group().rstrip(".,;:!?'’”")
        if target := binding(url):
            shown[url] = target
    if not shown:
        return []
    sources = {target for row in _cited_rows(citations, records) for field in ("source_url", "filing_url")
               if (target := binding(row.get(field)))}
    return [url for url, target in shown.items() if target not in sources]


def _observed_quarter(row):
    """Identify native and one-year comparative quarterly facts from filing metadata."""
    provenance = row.get("filing_link_provenance") or {}
    if row.get("kind") != "financial_fact" or row.get("form") not in {"10-Q", "10-Q/A"} or not provenance:
        return None
    try:
        if duration_class(row.get("period_start"), row.get("period_end")) != "quarter_length":
            return None
    except DataError:
        return None
    try:
        difference = (parse_day(provenance.get("report_date")) - parse_day(row["period_end"])).days
    except DataError:
        return None, None, None
    filing_fiscal = row.get("fiscal_period_context") or {}
    year, quarter = filing_fiscal.get("fy"), filing_fiscal.get("fp")
    label = filing_label = None
    if isinstance(year, int) and quarter in {"Q1", "Q2", "Q3"}:
        filing_label = f"{year}{quarter}"
        if difference == 0:
            label = filing_label
        elif 350 <= difference <= 380:
            label = f"{year - 1}{quarter}"
    return label, filing_label, difference


def native_fact_quarter(row):
    """Use the indexed SEC report date to distinguish a current-quarter fact from a comparative column."""
    observation = _observed_quarter(row)
    if observation is None or observation[2] != 0:
        return None
    proof = row["filing_link_provenance"]
    submission = _sec_target(proof.get("source_url"))
    filing = _sec_target(row.get("filing_url"))
    accession = row.get("accession")
    if (not accession or not submission or not submission[1].startswith("/submissions/")
            or not proof.get("snapshot_hash") or not proof.get("locator", "").endswith("=" + accession)
            or not filing or f"/{accession.replace('-', '')}/" not in filing[1]):
        return None
    return observation[0]


def mismatched_filing_periods(text, citations, records, request):
    """Check explicit quarter-to-filing assignments separately from link membership."""
    links = list(_LINK.finditer(text))
    if not links:
        return []
    facts = {}
    for row in _cited_rows(citations, records):
        target = _sec_target(row.get("filing_url"))
        if target and (observation := _observed_quarter(row)):
            facts.setdefault(target, set()).add(observation)
    if not facts:
        return []
    issues = []
    previous_end = 0
    for link in links:
        cue = _CLAUSE.split(text[previous_end:link.start()])[-1]
        if len(links) == 1:
            cue += " " + _CLAUSE.split(text[link.end():], maxsplit=1)[0]
        previous_end = link.end()
        url = link.group().rstrip(".,;:!?'’”")
        observations = facts.get(_sec_target(url))
        if not observations:
            continue
        labels = {label for _, label in quarter_mentions(cue)}
        if not labels and len(links) == 1 and (requested := quarter_label(request)):
            labels.add(requested)
        comparative = _COMPARATIVE.search(cue)
        original = _AS_FILED.search(cue) or (labels and _QUARTER_FILING.search(cue) and not comparative)
        later = comparative and _LATER.search(cue)
        eligible = [(observed, filed) for observed, filed, delay in observations
                    if (not original or delay == 0) and (not later or (delay is not None and delay > 0))]
        observed = {label for label, _ in eligible}
        supported = observed | {label for _, label in eligible} if comparative and len(labels) > 1 else observed
        # A grouped label before several links does not assign one link to a
        # period. Individual labels (or a group with only one link) do.
        assigned = len(labels) == 1 or len(links) == 1 or comparative
        if not eligible or (labels and assigned and None not in observed
                            and (not labels.issubset(supported) or not labels.intersection(observed))):
            issues.append(url)
    return issues
