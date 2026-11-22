"""Check that reviewed latest-quarter claims cite the quarter established by SEC evidence."""
from collections import defaultdict
import re

from . import latest_quarter
from .data import DataError, available_by, cutoff_time, parse_day
from .financial import duration_class
from .planning import select_relative_period
from .reporting_period import explicit_quarter_end, parse_reported_date, reported_period
from .skill_calculations import latest_reported_target, previous_quarter, quarter_mentions, requested_fiscal_year, target_labels, targets_prior_quarter
from .source_scope import LATEST_COVERAGE_GAP, source_constraint
from .tools import LATEST_QUARTER_REQUEST, PERIODIC_FORMS, QUARTER_COMPARISON, ordinary_latest_quarter


CHECK = "answer_period"


def remember_latest_quarter(episode, receipt, *, accepted_filing=False):
    """Keep the quarter evidence after the generic tool receipt history rolls off."""
    request = episode["request"]
    if not (latest_quarter.requested(request) or LATEST_QUARTER_REQUEST.search(request)):
        return
    data = receipt["data"]
    if accepted_filing and receipt["tool"] == "read_filing" and receipt["ok"]:
        filing = data.get("filing")
        if not filing:
            return
        args = receipt["arguments"]
        observation = {"status": "accepted_filing", "filing": filing, "accession": filing["accession"],
                       "entity": filing["entity"], "cutoff": args.get("cutoff")}
    elif receipt["tool"] == "financial_facts" and data.get("entity") and data.get("metric"):
        result = data.get("latest_quarter_check", data)
        if not (result is not data or result.get("status") in {"filing_review_required", "submissions_coverage_required"}
                or receipt["ok"] and data.get("selection") == "latest_reported_quarter"):
            return
        fields = ("status", "cutoff", "submissions_snapshot", "latest_filing_candidate", "latest_indexed_periods",
                  "period_start", "period_end")
        observation = {key: result[key] for key in fields if key in result}
        observation.update(entity=data["entity"], metric=data["metric"])
    else:
        return
    observation.update(request=request, evidence_ids=receipt["evidence_ids"],
                       record_versions=receipt.get("record_versions", {}), ok=receipt["ok"])
    keys = ("entity", "metric", "status", "cutoff", "accession")
    previous = episode.get("latest_quarter_observations", [])
    episode["latest_quarter_observations"] = [
        row for row in previous if any(row.get(k) != observation.get(k) for k in keys)
    ] + [observation]


def _claim_rows(claim, records, display, entity):
    refs = claim.get("evidence_ids", []) + claim.get("calculation_ids", [])
    rows = [records[ref] for ref in refs if isinstance(ref, str) and ref in records
            and not records[ref].get("stale") and (entity is None or records[ref].get("entity") == entity)]
    shown = {ref for match in display.get("matched", []) for ref in match.get("record_ids", [])}
    if "asserted_value" in claim:
        exact = {r["id"] for r in rows if r.get("value") == claim["asserted_value"] and r.get("unit") == claim.get("asserted_unit")}
        shown = exact or shown
    anchors = [r for r in rows if r["id"] in shown]
    if not anchors:
        anchors = [r for r in rows if r.get("kind") in ("document", "financial_fact") or r.get("operation")]
    dimensions = claim.get("constraints") or {}
    if dimensions.get("period_end") and QUARTER_COMPARISON.search(claim["text"]):
        primary = [r for r in anchors if _period(r)[1] == dimensions["period_end"]]
        anchors = primary or anchors
    dependencies = []
    for row in anchors:
        if row.get("operation") in ("ratio", "margin"):
            operands = [records[ref] for ref in row.get("operand_ids", []) if ref in records and not records[ref].get("stale")]
            if len(operands) != 2 or any((r.get("entity"), *_period(r)) != (row.get("entity"), *_period(row)) for r in operands):
                return []
            dependencies.extend(operands)
        else:
            dependencies.append(row)
    return list({r["id"]: r for r in dependencies}.values())


def _period(row):
    return (row.get("period_start") or row.get("document_period_start"),
            row.get("period_end") or row.get("document_report_date"))


def _is_quarter(row):
    start, end = _period(row)
    return bool(end and (row.get("fiscal_quarter") or (start and select_relative_period(
        {"kind": "quarter"}, [{"period_start": start, "period_end": end}])[0])))


def _catalog_gap(observations, cutoff):
    retrieved = [o["submissions_snapshot"]["retrieved_at"] for o in observations if o.get("submissions_snapshot")]
    latest = max(retrieved, key=cutoff_time, default=None)
    if latest and cutoff and cutoff_time(latest) < cutoff_time(cutoff):
        return (f"The available SEC submissions catalog was retrieved at {latest} and does not cover "
                f"the cutoff {cutoff}. The cited quarter cannot be verified as the latest reported quarter.")
    return "No SEC filings catalog covering the cutoff has established which quarter was the latest reported."


def _checked_periods(observations, records, cutoff):
    certified = set()
    candidates = {}
    for observation in observations:
        filing = observation.get("filing") or observation.get("latest_filing_candidate")
        if filing:
            accession = filing["accession"]
            older_end = max((r["period_end"] for r in observation.get("latest_indexed_periods", [])), default="")
            previous = candidates.get(accession, ({}, "", False))
            source = observation.get("submissions_snapshot")
            eligible = observation["status"] == "accepted_filing" or (
                source and observation.get("cutoff") and cutoff_time(source["retrieved_at"]) >= cutoff_time(observation["cutoff"]))
            candidates[accession] = (filing, max(older_end, previous[1]), eligible or previous[2])
        if observation["status"] == "catalog_verified":
            certified.add((observation["period_start"], observation["period_end"]))
        if observation["ok"] and observation["status"] in ("accepted_filing", "facts_found"):
            versions = observation["record_versions"]
            for ref in observation["evidence_ids"]:
                row = records.get(ref)
                if row and not row.get("stale") and (not versions or versions.get(ref) == row.get("version")) and _is_quarter(row):
                    certified.add(_period(row))
    if candidates:
        filing, older_end, eligible_catalog = max(candidates.values(), key=lambda entry: (
            entry[0].get("published_at", ""), entry[0]["accession"]))
        if not eligible_catalog:
            return set(), None, _catalog_gap(observations, cutoff)
        filing_end = filing.get("reportDate") if filing["form"] in PERIODIC_FORMS else None
        # 8-K filing/report dates are not fiscal dates. Its dated standalone
        # quarterly rows, or a later verified Company Facts observation, supply them.
        eligible = certified | {_period(r) for r in records.values()
                               if r.get("accession") == filing["accession"] and not r.get("stale") and _is_quarter(r)}
        ends = [end for _, end in eligible if end > older_end and (not filing_end or end >= filing_end)]
        end = max(ends, default=None)
        if not end:
            return set(), None, "A newer earnings filing may establish the requested quarter. The cited records do not establish the answer for that quarter."
        return {p for p in eligible if p[1] == end}, filing["accession"], (
            "The cited records are not tied to the quarter established by the latest SEC filing.")
    if certified:
        end = max(period[1] for period in certified)
        return {period for period in certified if period[1] == end}, None, (
            f"The SEC source check establishes a latest quarter ending {end}. An answer needs evidence for that quarter.")
    return set(), None, _catalog_gap(observations, cutoff)


def _reported_previous(scope, records):
    """Rebuild the period proof from records that are also available to frozen reviews."""
    cutoff = scope.get("cutoff")
    issuers = scope.get("entities", [])
    groups, catalogs = defaultdict(list), {}
    for row in records.values():
        issuer, url = row.get("entity"), row.get("source_url") or ""
        if (not issuer or (issuers and issuer not in issuers)
                or row.get("stale") or not available_by(row.get("published_at"), cutoff)):
            continue
        if (row.get("kind") == "source_metadata" and row.get("earnings_filings") and row.get("mode") == "live"
                and row.get("earnings_page_offset") == 0
                and url.startswith("https://data.sec.gov/submissions/CIK")
                and (not cutoff or row.get("coverage_cutoff") == cutoff
                     and _covers_disclosure_cutoff(row.get("retrieved_at"), cutoff))):
            previous = catalogs.get(issuer)
            if previous is None or cutoff_time(row["retrieved_at"]) >= cutoff_time(previous["retrieved_at"]):
                catalogs[issuer] = row
        if (not row.get("fiscal_quarter") or row.get("metric") not in {"revenue", "operating_income"}
                or row.get("value") is None or not row.get("accession")
                or not url.startswith("https://www.sec.gov/Archives/edgar/data/")
                or "/" + row["accession"].replace("-", "") + "/" not in url):
            continue
        form = row.get("form")
        if (form in {"8-K", "8-K/A"} and row.get("period_quote")
                or form == "10-Q" and row.get("cell_type") == "ixbrl_profit_cell"):
            key = (issuer, url, row.get("snapshot_hash"), row.get("period_end"))
            groups[key].append(row)

    pairs = [pair for group in groups.values() if (pair := latest_quarter.reported_pair(group))]
    entities = {pair["entity"] for pair in pairs if pair["entity"] in catalogs}
    if len(entities) != 1:
        return None
    entity, = entities
    pairs = [pair for pair in pairs if pair["entity"] == entity]
    most_recent = max(pair["period_end"] for pair in pairs)
    anchors = [pair for pair in pairs if pair["period_end"] == most_recent]
    catalog = catalogs[entity]["earnings_filings"]
    if (len({pair["quarter"] for pair in anchors}) != 1
            or not _catalog_confirms_anchor(catalog, anchors, records)):
        return None
    label = previous_quarter(anchors[0]["quarter"])
    candidates = [pair for pair in pairs
                  if pair["quarter"] == label and latest_quarter.full_quarter(*_period(pair))
                  and all(latest_quarter.preceding_end(pair["period_end"], anchor) for anchor in anchors)
                  and all(records[ref].get("form") == "10-Q" for ref in pair["evidence_ids"])]
    return candidates[0] if len(candidates) == 1 else None


def _catalog_confirms_anchor(catalog, anchors, records):
    end, label = anchors[0]["period_end"], anchors[0]["quarter"]
    if any((entry.get("reportDate") or "") > end for entry in catalog if entry["form"] in PERIODIC_FORMS):
        return False
    accessions = {pair["accession"] for pair in anchors}
    for entry in catalog:
        if entry["accession"] in accessions:
            return True
        if entry["form"] in PERIODIC_FORMS or (entry.get("reportDate") and entry["reportDate"] <= end):
            continue
        # An earnings 8-K can precede or accompany a same-quarter 10-Q. A later
        # candidate with an unresolved fiscal date can also supersede the anchor.
        disclosed = {(item["quarter"], parse_reported_date(item["end_date_text"]))
                     for row in records.values() if row.get("accession") == entry["accession"]
                     and row.get("entity") == anchors[0]["entity"] and not row.get("stale")
                     for item in row.get("disclosed_quarters", [])}
        if disclosed != {(label, end)}:
            return False
    return False


def _covers_disclosure_cutoff(retrieved, cutoff):
    if not retrieved:
        return False
    observed, bound = cutoff_time(retrieved), cutoff_time(cutoff)
    # A live catalog on the requested as-of day is an observation through the
    # retrieval time. A timestamp cutoff still requires coverage of that instant.
    return observed.date() >= bound.date() if len(cutoff) == 10 else observed >= bound


def _previous_issues(scope, requirements, claims, records, displays):
    target = _reported_previous(scope, records)
    entity = target["entity"] if target else None
    dates = _period(target) if target else ()
    accession = target["accession"] if target else None
    for claim in claims.values():
        if claim.get("stale") or not requirements.keys() & set(claim.get("requirement_ids", [])):
            continue
        refs = _claim_rows(claim, records, displays.get(claim["id"], {}), entity)
        labels = target_labels(claim["text"])
        printed = reported_period(claim["text"], statement=True) or {}
        if (target and refs and all(((_period(r) == dates and r.get("accession") == accession)
                    or (r.get("kind") == "document" and r.get("accession") == accession
                        and not _period(r)[1] and r.get("source_url") and r["source_url"] in claim["text"])) for r in refs)
                and (not labels or labels == {target["quarter"]})
                and all(not printed.get(key) or printed[key] == value for key, value in zip(("start", "end"), dates))):
            continue
        reason = "尚未核实所问上一季的报告期及其原表证据。"
        if target:
            reason = f"本问所指是 {target['quarter']}（截至 {dates[1]}）。答句及其来源需与该财季对应。"
        yield {"target_id": claim["id"], "issue_type": "context_mismatch", "scope": CHECK,
               "evidence_refs": [r["id"] for r in refs], "missing_support": reason,
               "proposed_action": "用已核实的上一季金额及其计算回答。证据不足时保留缺口。"}


_PRIOR_YEAR_AMOUNT = re.compile(r"去年同期|上年同期|同比基期|\b(?:prior[- ]year|year[- ]ago|last year)\b", re.I)
_PRIOR_QUARTER_AMOUNT = re.compile(r"上(?:一[个個]?)?(?:[财財]季|季度|季)|"
                                   r"\b(?:last|previous|prior) (?:fiscal )?quarter\b", re.I)
_CURRENT_QUARTER_AMOUNT = re.compile(r"本(?:[财財]季|季度|季)|这(?:一)?季|這(?:一)?季|"
                                     r"\b(?:current|this) (?:fiscal )?quarter\b", re.I)
_CUMULATIVE_WINDOW = re.compile(
    r"全年|全财年|全財年|年度总|年度總|年初至今|前(?:三|3)季|前(?:六|九|十二|6|9|12)个月|"
    r"\b(?:full.fiscal.year|full.year|annual|year.to.date|YTD|(?:six|nine|twelve|6|9|12)[ -]months?)\b", re.I)


def _cumulative_window(record):
    start, end = record.get("period_start"), record.get("period_end")
    if start and end:
        try:
            return duration_class(start, end) in {"annual_length", "nine_month_length", "half_year_length"}
        except DataError:
            return False
    return False


def _asks_for_quarter(text):
    return bool(target_labels(text) or targets_prior_quarter(text)
                or (reported_period(text) or {}).get("kind") == "quarter")


def _local_quarter(prefix):
    quarter = max((m.start() for m, _ in quarter_mentions(prefix)), default=-1)
    relative = max((m.start() for pattern in (_CURRENT_QUARTER_AMOUNT, _PRIOR_QUARTER_AMOUNT)
                    for m in pattern.finditer(prefix)), default=-1)
    cumulative = max((m.start() for m in _CUMULATIVE_WINDOW.finditer(prefix)), default=-1)
    return max(quarter, relative) > cumulative


def _cumulative_quarter_issues(request, requirements, claims, records, displays):
    """A matched annual or YTD number alone cannot support a quarterly answer."""
    for claim in claims.values():
        if claim.get("stale"):
            continue
        prompts = [requirements[ref]["text"] for ref in claim.get("requirement_ids", []) if ref in requirements]
        if not prompts or not any(_asks_for_quarter(text) for text in [request, *prompts]):
            continue
        cumulative_requested = any(_CUMULATIVE_WINDOW.search(text) or requested_fiscal_year(text)
                                   or (reported_period(text) or {}).get("kind") == "annual" for text in prompts)
        shown = displays.get(claim["id"], {}).get("matched", [])
        failures = []
        only_cumulative = bool(shown)
        cursor = 0
        for item in shown:
            literal = item["display"]
            position = claim["text"].find(literal, cursor)
            if position < 0:
                only_cumulative = False
                continue
            prefix = re.split(r"[，,。；;!?！？]|\.\s+", claim["text"][cursor:position])[-1]
            cursor = position + len(literal)
            bound = [records[ref] for ref in item["record_ids"] if ref in records and not records[ref].get("stale")]
            if not bound or not all(_cumulative_window(row) for row in bound):
                only_cumulative = False
                continue
            if _local_quarter(prefix) or (not cumulative_requested and not _CUMULATIVE_WINDOW.search(prefix)):
                failures.extend(item["record_ids"])
        if failures or (only_cumulative and not cumulative_requested):
            yield {"target_id": claim["id"], "issue_type": "context_mismatch", "scope": CHECK,
                   "evidence_refs": list(dict.fromkeys(failures or [ref for item in shown for ref in item["record_ids"]])),
                   "missing_support": "所引数值为全年或年内累计结果，无法核实所问的单季。",
                   "proposed_action": "使用对应单季的原表金额，或从可比的相邻累计期间计算单季后再核验。"}


def _quarter_offset(current, other):
    year, quarter = map(int, current.split("Q"))
    other_year, other_quarter = map(int, other.split("Q"))
    return (other_year - year) * 4 + other_quarter - quarter


def _display_in_period(row, wanted_end, target_label, offset):
    end = _period(row)[1]
    if not end:
        return False
    if offset == 0:
        return end == wanted_end
    try:
        delta = (parse_day(end) - parse_day(wanted_end)).days
    except DataError:
        return False
    if not delta or (delta > 0) != (offset > 0):
        return False
    labels = target_labels(row.get("fiscal_quarter") or "")
    if len(labels) == 1:
        return _quarter_offset(target_label, next(iter(labels))) == offset
    if not latest_quarter.full_quarter(*_period(row)):
        return False
    if abs(offset) == 1:
        return 70 <= abs(delta) <= 105
    if abs(offset) == 4:
        return 350 <= abs(delta) <= 380
    return False


def _dated_displays_match(text, shown, records, wanted_end, target_label):
    primary = False
    cursor = 0
    for item in shown:
        literal = item["display"]
        position = text.find(literal, cursor)
        if position < 0:
            return False
        prefix = re.split(r"[。；;!?！？]|\.\s+", text[cursor:position])[-1]
        cursor = position + len(literal)
        labels = {label for _, label in quarter_mentions(prefix)}
        if len(labels) > 1:
            return False
        label = next(iter(labels), None)
        if label:
            offset = _quarter_offset(target_label, label)
        else:
            qualifiers = [(m.start(), n) for pattern, n in ((_PRIOR_YEAR_AMOUNT, -4), (_PRIOR_QUARTER_AMOUNT, -1),
                                                              (_CURRENT_QUARTER_AMOUNT, 0)) for m in pattern.finditer(prefix)]
            offset = max(qualifiers, default=(-1, 0))[1]
        matched = [records[ref] for ref in item["record_ids"] if ref in records and not records[ref].get("stale")]
        if not any(_display_in_period(row, wanted_end, target_label, offset) for row in matched):
            return False
        primary = primary or offset == 0
    return primary


def _dated_quarter_issues(request, end, requirements, claims, records, displays):
    labels = target_labels(request)
    target_label, = labels
    for claim in claims.values():
        if claim.get("stale") or not requirements.keys() & set(claim.get("requirement_ids") or []):
            continue
        display = displays.get(claim["id"], {})
        rows = _claim_rows(claim, records, display, None)
        periods = {date for row in rows if (date := _period(row)[1])}
        stated = reported_period(claim["text"], statement=True) or {}
        printed_labels = target_labels(claim["text"])
        if shown := display.get("matched"):
            supported = _dated_displays_match(claim["text"], shown, records, end, target_label)
        else:
            supported = (not periods or end in periods) and (not printed_labels or labels == printed_labels)
        if supported and stated.get("end", end) == end:
            continue
        yield {"target_id": claim["id"], "issue_type": "context_mismatch", "scope": CHECK,
               "evidence_refs": [r["id"] for r in rows],
               "missing_support": f"所引答句或数值未对应本问指定的截至 {end} 的财季。",
               "proposed_action": "使用该财季的原表金额或经核验的单季计算。"}


def latest_answer_issues(context, requirements, claims, records, displays):
    """Reject a stale quarter even when its printed amounts and arithmetic are accurate."""
    scope = context.get("scope", {})
    episode = context.get("episode", {})
    request = context.get("request") or episode.get("request", "")
    policy = source_constraint(request)
    if scope.get("planning_task"):
        return
    yield from _cumulative_quarter_issues(request, requirements, claims, records, displays)
    if end := explicit_quarter_end(request):
        yield from _dated_quarter_issues(request, end, requirements, claims, records, displays)
    if policy.excludes_sec and latest_quarter.mentions_latest_quarter(request):
        for claim in claims.values():
            # A dated historical fact or a link to an original can stand even
            # when the allowed sources do not show which quarter is latest.
            if not claim.get("stale") and latest_quarter.mentions_latest_quarter(claim["text"]):
                yield {"target_id":claim["id"], "issue_type":"context_mismatch", "scope":CHECK,
                       "evidence_refs":[r["id"] for r in _claim_rows(claim,records,displays.get(claim["id"],{}),None)],
                       "missing_support":LATEST_COVERAGE_GAP,
                       "proposed_action":"Use a source allowed by the request that establishes latest coverage, or leave the latest quarter unresolved."}
        return
    if not policy.permits_auto_sec:
        return
    horizon = latest_reported_target(request)
    if horizon == "previous":
        yield from _previous_issues(scope, requirements, claims, records, displays)
        return
    if not episode.get("request"):
        return
    observations = [r for r in episode.get("latest_quarter_observations", [])
                    if r["request"] == request
                    and (not scope.get("cutoff") or r.get("cutoff") == scope["cutoff"])
                    and (not scope.get("entities") or r["entity"] in scope["entities"])]
    issuers = {r["entity"] for r in observations}
    ordinary = ordinary_latest_quarter(request)
    if len(issuers) != 1 and not ordinary:
        return
    entity = next(iter(issuers)) if len(issuers) == 1 else None
    periods, accession, gap = _checked_periods(observations if entity else [], records, scope.get("cutoff"))
    fiscal_labels = {label for row in records.values()
                     if row.get("entity") == entity and not row.get("stale") and _period(row) in periods
                     for label in target_labels(row.get("fiscal_quarter") or "")}
    current_requirements = (set(requirements) if ordinary else
        {rid for rid, requirement in requirements.items() if ordinary_latest_quarter(requirement["text"])}
        if horizon == "check_source" else
        {rid for rid, requirement in requirements.items()
         if LATEST_QUARTER_REQUEST.search(requirement["text"])
         or not (reported_period(requirement["text"]) or targets_prior_quarter(requirement["text"])
                 or target_labels(requirement["text"]) or QUARTER_COMPARISON.search(requirement["text"]))})
    for claim in claims.values():
        if claim.get("stale") or not current_requirements.intersection(claim.get("requirement_ids") or []):
            continue
        anchors = _claim_rows(claim, records, displays.get(claim["id"], {}), entity)
        reason = gap
        if anchors and all(_period(row) in periods or (
                accession and row.get("kind") == "document" and row.get("accession") == accession
                and not _period(row)[1] and row.get("source_url") and row["source_url"] in claim["text"]) for row in anchors):
            labels = target_labels(claim["text"])
            dates = reported_period(claim["text"], statement=True)
            if len(labels) == len(fiscal_labels) == 1 and labels != fiscal_labels:
                reason = "答句中的财年或季度编号与已核实的 SEC 业绩公告不一致。"
            elif dates and dates.get("start") and not any(_period(row) == (dates["start"], dates["end"])
                                                          for row in anchors):
                reason = "所引 SEC 证据未核实答句中的财季起止日期。"
            else:
                continue
        yield {"target_id": claim["id"], "issue_type": "context_mismatch", "scope": CHECK,
               "evidence_refs": [r["id"] for r in anchors], "missing_support": reason,
               "proposed_action": "Use the source amount for the requested period, or leave that answer unresolved."}


def restricted_latest_coverage(parts, rows):
    """Keep a distinct, explicitly dated question outside the unsupported latest question."""
    latest = False
    blocked = []
    for part, question in parts.items():
        if latest_quarter.mentions_latest_quarter(question):
            latest = True
        elif reported_period(question) or target_labels(question):
            latest = False
        if latest:
            blocked.append(part)
    if not blocked:
        return rows
    return ([row for row in rows if row.get("part") not in blocked]
            + [{"part":part,"status":"gap","missing_support":LATEST_COVERAGE_GAP} for part in blocked])
