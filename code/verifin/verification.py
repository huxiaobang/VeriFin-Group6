"""Conservative deterministic defect checks plus a separate semantic review.

An empty issue list is not evidence of correctness. Only the controller can
decide report status after checking semantic review and required coverage.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .answer_periods import latest_answer_issues
from .model import ModelError, _records, requirements, validated_output
from .financial import GUIDANCE_VARIANCE, quarter_from_cumulative, chart_comparison, check_operand_shape
from .financial import COMMON_INCOME, VALUATION_ALLOWANCE_EXPENSE, TAX_SCENARIO, tax_scenario_dimensions, check_tax_scenario_comparison
from .display_numbers import assertion_fields, display_check, rate_only_in_other_period
from .fiscal_followup import period_sources
from .consolidated_results import defects as consolidated_result_defects
from .filing_links import mismatched_filing_periods, unbound_sec_links
from .source_link_followup import requested as source_link_requested
from .margin_comparison import defects as margin_comparison_defects
from .margin_formula import defects as margin_formula_defects
from .request_review import comparison_rule_issues
from .record_context import DIMENSIONS, compatible_records, shared_dimensions, statement_dimensions, statement_result
from .skill_calculations import previous_quarter


def _number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _same_calculation(expected: Decimal, stored: Any) -> bool:
    # The calculator stores exact integers and serializes other results as
    # floats. A relative dollar tolerance here grows with company size.
    serialized = int(expected) if expected == expected.to_integral_value() else float(expected)
    return _number(stored) and stored == serialized


def _definition_comparison(claim, dimensions, results, claims):
    # Older drafts express a comparison as an interpretation with a composite
    # metric label. Its two inputs must have separately identified results.
    label = dimensions.get("metric")
    if (not isinstance(label, str) or "_versus_" not in label
            or not claim["constraints"].get("interpretation")
            or "asserted_value" in claim or "asserted_unit" in claim
            or re.search(r"\d", claim.get("text", "")) or len(results) != 2):
        return False
    metrics = {r.get("metric") for r in results if r.get("operation") and _number(r.get("value"))}
    if len(metrics) != 2 or None in metrics or label in metrics:
        return False
    scope = {k:v for k,v in dimensions.items() if k != "metric"}
    if not {"entity", "period_end"} <= scope.keys():
        return False
    for result in results:
        if result.get("stale") or any(result.get(k) != v for k,v in scope.items()):
            return False
        if not any(not other.get("stale") and isinstance(other.get("constraints"), dict)
                   and other["constraints"].get("metric") == result["metric"]
                   and result["id"] in (other.get("calculation_ids") or [])
                   for other in claims.values()):
            return False
    return True


def _joint_metric_scope(claim, dimensions, display, citations, records):
    """A conjunction of metrics describes several cited facts, not a new scalar."""
    if (display.get("method") != "matched_records" or "asserted_value" in claim
            or "asserted_unit" in claim or not dimensions.get("period_end") or not dimensions.get("entity")):
        return False
    rows = display.get("matched", [])
    if not rows or not all(row.get("metric") for row in rows):
        return False
    ordered = list(dict.fromkeys(row["metric"] for row in rows))
    if len(ordered) < 2 or dimensions.get("metric") != "_and_".join(ordered):
        return False
    common = {k:v for k,v in dimensions.items() if k != "metric"}
    filing = {k: claim["constraints"][k] for k in ("accession", "form") if claim["constraints"].get(k)}
    for match in rows:
        sources = [records[ref] for ref in match["record_ids"]
                   if all(records[ref].get(k) == value for k, value in filing.items())]
        if not any(not missing for _, missing in
                   compatible_records(sources, common, records, citations=citations)):
            return False
    return True


def _timestamp(value: Any, *, end_of_day: bool = False) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        is_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not is_date and parsed.tzinfo is None:
            return None
        if is_date and end_of_day:
            parsed = datetime.combine(parsed.date(), time.max)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def check_rules(context: dict, *, display_notes=None, context_notes=None, companies=()) -> list[dict]:
    """Return inspectable defects for the *current* claims and requirements.

    Root supplies active claims, not every historical report's claims. We check
    transitive dependencies, snapshot versions, cutoff and explicit numerical
    assertions. Unit-bearing displayed amounts are checked separately from the
    model's judgment about the proposition and the source passage.
    """
    claims = _records(context.get("claims", []), "claims")
    evidence = _records(context.get("evidence", []), "evidence")
    calculations = _records(context.get("calculations", []), "calculations")
    reqs = requirements(context)
    session = context.get("session", {})
    scope = context.get("scope", session.get("scope", {}))
    request = context.get("episode", {}).get("request") or context.get("request", "")
    source_link_only = source_link_requested(request)
    scope_version = session.get("scope_version", context.get("scope_version"))
    cutoff_raw = scope.get("cutoff")
    cutoff = _timestamp(cutoff_raw, end_of_day=True)
    records = {**evidence, **calculations}
    displays = {}
    issues: list[dict] = []
    seen: set[tuple] = set()

    def add(target: str, category: str, refs: list[str], gap: str, action: str) -> None:
        # Missing IDs appear in the explanation, never as fictional source refs.
        refs = list(dict.fromkeys(r for r in refs if r in records))
        key = (target, category, tuple(refs), gap)
        if key not in seen:
            seen.add(key)
            issues.append({"target_id": target, "issue_type": category,
                           "evidence_refs": refs, "missing_support": gap,
                           "proposed_action": action})

    if evidence.keys() & calculations.keys():
        raise ModelError("Evidence and calculation IDs must be distinct")
    if claims.keys() & reqs.keys():
        raise ModelError("Claim and requirement IDs must be distinct")

    # Older statement calculations stored their operands but omitted the common
    # dates. Recover only dimensions on which every operand actually agrees.
    inherited = {}
    def result_dimensions(ref, path=()):
        row = records.get(ref, {})
        if ref in inherited or not statement_result(row) or ref in path or len(path) >= 64:
            return row
        inputs = [result_dimensions(i, (*path, ref)) if isinstance(i, str) else {} for i in row["operand_ids"]]
        inherited[ref] = shared_dimensions(inputs, row.get("unit"))
        calculations[ref] = records[ref] = {**inherited[ref], **row}
        return records[ref]

    for ref in calculations:
        result_dimensions(ref)

    def check_dependency(target: str, record_id: str, path: tuple[str, ...] = ()) -> None:
        if record_id not in records:
            add(target, "missing_reference", [], f"Referenced record {record_id!r} is unavailable.",
                "Retrieve the missing evidence or remove/revise the unsupported assertion.")
            return
        record = records[record_id]
        if record_id in path or len(path) >= 64:
            add(target, "invalid_record", [record_id], "Calculation dependency cycle or excessive depth detected.",
                "Rebuild the calculation from source evidence using the calculation tool.")
            return
        if record.get("stale"):
            add(target, "stale_reference", [record_id], "A referenced record or dependency is stale.",
                "Refresh affected evidence, recompute dependent calculations and review again.")
        if record_id in evidence:
            required = ("source_url", "locator", "snapshot_hash", "entity")
            missing = [k for k in required if not isinstance(record.get(k), str) or not record[k].strip()]
            if missing:
                add(target, "missing_provenance", [record_id], "Evidence lacks " + ", ".join(missing) + ".",
                    "Acquire an original source with a locator and snapshot provenance.")
            published = _timestamp(record.get("published_at"))
            if cutoff_raw is not None:
                if cutoff is None:
                    add(target, "invalid_record", [record_id], "Analysis cutoff is not a valid ISO date/time.",
                        "Resolve the cutoff before judging historical availability.")
                elif published is None:
                    add(target, "missing_provenance", [record_id], "Publication time is missing or invalid under a cutoff.",
                        "Find publication metadata or disclose that historical availability is unverified.")
                elif published > cutoff:
                    add(target, "source_time_mismatch", [record_id], "Evidence was published after the analysis cutoff.",
                        "Replace it with evidence available by the cutoff and recompute affected results.")
                elif (len(str(record.get("published_at"))) == 10 and len(str(cutoff_raw)) > 10
                      and published.date() == cutoff.date()):
                    add(target, "missing_provenance", [record_id], "Day-only publication date cannot verify an intraday cutoff.",
                        "Obtain the publication timestamp or mark time availability uncertain.")
            if record.get("kind") in ("financial_fact", "market_price") and not _number(record.get("value")):
                add(target, "invalid_record", [record_id], "A numerical evidence record has no finite numeric value.",
                    "Refetch the original numeric record; do not substitute a guessed value.")
            return
        operands = record.get("operand_ids", [])
        if not isinstance(operands, list) or not all(isinstance(x, str) for x in operands) or not operands:
            add(target, "invalid_record", [record_id], "Calculation has no valid operand references.",
                "Rebuild the calculation from explicit source operands.")
            return
        versions = record.get("operand_versions", {})
        if not isinstance(versions, dict):
            add(target, "invalid_record", [record_id], "Calculation operand_versions must be an object.",
                "Rebuild the calculation with explicit operand versions.")
            versions = {}
        for operand in operands:
            dependency = records.get(operand)
            if dependency and operand in versions and dependency.get("version") != versions[operand]:
                add(target, "stale_reference", [record_id, operand], "Calculation operand version changed.",
                    "Recompute the calculation using current versions, then review affected claims.")
            check_dependency(target, operand, path + (record_id,))
        try:
            check_operand_shape(record.get("operation"), operands)
        except ValueError as exc:
            add(target, "invalid_record", [record_id, *operands], str(exc),
                "Rebuild using a registered operation and the required, distinct numeric inputs.")
            return
        if any(i in records and not _number(records[i].get("value")) for i in operands):
            add(target, "invalid_record", [record_id, *operands], "A calculation operand is not a numeric amount.",
                "Use a source amount or a reproducible calculation, not a text passage.")
            return
        if any(record.get(k) != v for k, v in inherited.get(record_id, {}).items()):
            add(target, "context_mismatch", [record_id, *operands],
                "Statement result metadata contradicts the common context of its operands.",
                "Check the result period and scope against the original rows.")
        if not _number(record.get("value")):
            add(target, "invalid_record", [record_id], "Calculation result is not a finite number.",
                "Recompute with validated numeric operands.")
        elif record.get("operation") == "sales_calendar_growth_gap":
            from .sales_calendar import calendar_growth_rates
            periods = (record.get("fiscal_quarter"), record.get("comparison_quarter"))
            expected_cells = [(metric, period, unit) for metric, unit in
                              (("net_sales", "reported_amount"), ("business_days", "days")) for period in periods]
            cells = [evidence.get(i, {}) for i in operands]
            shape = [(r.get("metric"), r.get("fiscal_quarter"), r.get("unit")) for r in cells]
            table = cells[0].get("table_evidence_id")
            if (not isinstance(table, str) or table not in evidence or record.get("table_evidence_id") != table
                    or shape != expected_cells
                    or not re.fullmatch(r"\d{4}Q[1-4]", str(periods[0]))
                    or previous_quarter(periods[0], year_on_year=True) != periods[1] or not record.get("entity")
                    or any(r.get("entity") != record["entity"] or r.get("table_evidence_id") != table for r in cells)
                    or record.get("metric") != "sales_calendar_growth_gap" or record.get("unit") != "percentage_points"):
                add(target, "context_mismatch", [record_id, *operands],
                    "Sales/day bridge must use the two labelled net sales cells and the two corresponding business-day cells from one issuer table.",
                    "Recover the four original table cells for the two quarters, then recompute in percentage points.")
            else:
                try:
                    total, daily, gap = calendar_growth_rates(*(r.get("value") for r in cells))
                except ValueError:
                    add(target, "invalid_record", [record_id, *operands], "Sales/day operands are invalid.",
                        "Recover valid sales amounts and positive day counts from the table.")
                else:
                    computed = {"value":gap, "total_growth_percent":total, "daily_growth_percent":daily}
                    if any(not _same_calculation(value, record[key]) for key, value in computed.items() if key in record):
                        add(target, "numerical_inconsistency", [record_id, *operands],
                            "The stored growth rates or gap do not reproduce from the four sales/day cells.",
                            "Recompute the gap from the two sales amounts and their respective day counts.")
        elif record.get("operation") in ("sum", "midpoint"):
            if all(i in records and _number(records[i].get("value")) for i in operands):
                inputs = [records[i] for i in operands]
                if any(r.get("unit") != record.get("unit") or r.get("entity") != record.get("entity")
                       for r in inputs):
                    add(target, "context_mismatch", [record_id] + operands, "Sum or midpoint mixes issuers or units.",
                        "Use operands with matching issuer and units.")
                if record["operation"] == "midpoint" and any(row.get("guidance_role") for row in inputs):
                    try:
                        expected_dims = statement_dimensions("midpoint", inputs, record.get("unit"))
                        if any(record.get(k) != v for k, v in expected_dims.items()):
                            raise ValueError("Stored midpoint carries different guidance dimensions than the source range.")
                    except ValueError as exc:
                        add(target, "context_mismatch", [record_id, *operands], str(exc),
                            "Recompute the midpoint from the original issuer guidance range.")
                total = sum(Decimal(str(row["value"])) for row in inputs)
                expected = total / 2 if record["operation"] == "midpoint" else total
                if not _same_calculation(expected, record["value"]):
                    add(target, "numerical_inconsistency", [record_id] + operands,
                        "Stored sum or midpoint does not reproduce from its recorded operands.",
                        "Recompute through the calculation tool and review the affected claim.")
        elif len(operands) == 2 and all(i in records and _number(records[i].get("value")) for i in operands):
            # Recompute supported scalar operations without interpreting formula
            # strings. The tool remains responsible for financial comparability.
            first, second = (Decimal(str(records[i]["value"])) for i in operands)
            operation = record.get("operation")
            a, b = (records[i] for i in operands)
            if operation == "ratio":
                from .platform_revenue import CELL_TYPE, validate_share
                if any(row.get("cell_type") == CELL_TYPE for row in (a, b)):
                    try:
                        validate_share(a, b)
                    except ValueError as exc:
                        add(target, "context_mismatch", [record_id, *operands], str(exc),
                            "Recalculate the ratio with both entries from the same quarterly platform table.")
            if operation == "difference" and {a.get("concept"), b.get("concept")} == {COMMON_INCOME, VALUATION_ALLOWANCE_EXPENSE}:
                add(target, "context_mismatch", [record_id, *operands],
                    "A subtraction of reported common earnings and a tax-expense-signed item cannot establish earnings excluding that item.",
                    "Use exclude_tax_expense_item with common earnings first and the signed tax-expense effect second.")
            if operation == TAX_SCENARIO or (a.get("operation") == TAX_SCENARIO and operation in {"growth", "difference"}):
                try:
                    if operation == TAX_SCENARIO:
                        dimensions = tax_scenario_dimensions(a, b)
                        if any(record.get(k) != v for k, v in dimensions.items()):
                            raise ValueError("Stored single-item scenario has inconsistent source or result dimensions.")
                    else:
                        check_tax_scenario_comparison(a, b)
                        expected_unit = "%" if operation == "growth" else a["unit"]
                        expected_metric = a["metric"] + "_vs_reported_" + operation
                        if (record.get("unit") != expected_unit or record.get("metric") != expected_metric
                                or any(record.get(k) != a.get(k) for k in
                                       ("entity", "business_scope", "basis", "period_start", "period_end"))):
                            raise ValueError("Stored comparison has inconsistent scenario, metric, period or unit.")
                except ValueError as exc:
                    add(target, "context_mismatch", [record_id, *operands], str(exc),
                        "Rebuild the single-item scenario from the matching original earnings and signed tax facts.")
            if operation == GUIDANCE_VARIANCE:
                try:
                    expected_dims = statement_dimensions(operation, [a, b], record.get("unit"))
                    if record.get("entity") != a.get("entity") or any(record.get(k) != v for k, v in expected_dims.items()):
                        raise ValueError("Stored comparison dimensions differ from the actual and guidance.")
                except ValueError as exc:
                    add(target, "context_mismatch", [record_id, *operands], str(exc),
                        "Recompute using the same quarter's actual and earlier issuer guidance midpoint.")
            if operation == "statement_quarter_residual":
                try:
                    expected_dims = statement_dimensions(operation, [a,b], record.get("unit"))
                    if any(record.get(k) != expected_dims.get(k) for k in
                           ("cash_flow_series", "period_start", "period_end")) or record.get("entity") != a.get("entity"):
                        raise ValueError("Stored quarter or flow series differs from its operands.")
                except ValueError as exc:
                    add(target, "context_mismatch", [record_id, *operands], str(exc),
                        "Rebuild the quarter from two comparable dated statement amounts.")
            elif operation == "difference" and statement_result(record):
                # Legacy statement differences sometimes copied just the common
                # end/start from two different periods. Do not certify that as
                # a dated result; it must be recomputed from the original rows.
                periods = [(r.get("period_start"), r.get("period_end")) for r in (a,b)]
                if (all(all(p) for p in periods) and periods[0] != periods[1] and
                        (record.get("period_start") or record.get("period_end")) and
                        not all((record.get("period_start"), record.get("period_end")))):
                    add(target, "context_mismatch", [record_id, *operands],
                        "This legacy difference carries only one boundary from two different reporting periods.",
                        "Recompute from the dated source rows before using it as a quarter.")
            if operation == "percentage_point_change" and (
                    a.get("unit") != "%" or b.get("unit") != "%" or record.get("unit") != "percentage_points"):
                add(target, "context_mismatch", [record_id] + operands,
                    "Percentage-point change needs percent operands and a percentage-point result.",
                    "Check the input scale and use percentage_points for the difference.")
            if any(row.get("cell_type") == "chart_inventory_cell" for row in (a, b)):
                try:
                    periods = chart_comparison(a, b, operation)
                    if periods != (record.get("from_chart_period"), record.get("to_chart_period")):
                        raise ValueError("Stored result refers to different quarters from the source labels.")
                except ValueError as exc:
                    add(target, "context_mismatch", [record_id] + operands, str(exc),
                        "Use the same inventory figure and retain the printed quarter labels.")
            if operation == "derive_quarter":
                try:
                    window = quarter_from_cumulative(a, b)
                    for field in ("entity", "unit", "basis", "business_scope"):
                        if not a.get(field) or a.get(field) != b.get(field) or record.get(field) != a.get(field):
                            raise ValueError("Quarter operands/result have missing or inconsistent " + field + ".")
                    if record.get("period_start") != window["period_start"] or record.get("period_end") != window["period_end"]:
                        raise ValueError("Derived quarter result period does not match the residual interval.")
                except (ValueError, TypeError, KeyError) as exc:
                    add(target, "context_mismatch", [record_id] + operands,
                        "Invalid cumulative-to-quarter derivation: " + str(exc),
                        "Use matching additive flow disclosures for consecutive cumulative periods and recalculate.")
            expected = None
            try:
                if operation == "ratio":
                    expected = first / second
                elif operation == "margin":
                    expected = first / second * 100
                elif operation in ("growth", "price_return", GUIDANCE_VARIANCE):
                    expected = (first / second - 1) * 100
                elif operation == TAX_SCENARIO:
                    expected = first + second
                elif operation in ("difference", "percentage_point_change", "derive_quarter", "statement_quarter_residual"):
                    expected = first - second
            except (ArithmeticError, InvalidOperation):
                add(target, "numerical_inconsistency", [record_id] + operands,
                    "Calculation is undefined for the current operands (for example, division by zero).",
                    "Correct the operands or report the result as undefined.")
            if expected is not None and not _same_calculation(expected, record["value"]):
                add(target, "numerical_inconsistency", [record_id] + operands,
                    "Stored calculation value does not reproduce from its recorded operation and operands.",
                    "Recompute through the calculation tool and review the affected claim.")

    for target, claim in claims.items():
        if claim.get("stale") or (scope_version is not None and claim.get("scope_version") is not None
                                   and claim["scope_version"] != scope_version):
            add(target, "stale_reference", [], "Claim is stale or belongs to a different scope version.",
                "Revise it for the current request and obtain a fresh review.")
        evidence_ids = claim.get("evidence_ids", [])
        calculation_ids = claim.get("calculation_ids", [])
        if (not isinstance(evidence_ids, list) or not isinstance(calculation_ids, list)
                or not all(isinstance(i, str) for i in evidence_ids + calculation_ids)):
            add(target, "invalid_record", [], "Claim references must be arrays of record IDs.",
                "Regenerate the claim with explicit evidence and calculation IDs.")
            continue
        for record_id in evidence_ids:
            if record_id not in evidence:
                add(target, "missing_reference", [], f"Evidence reference {record_id!r} is not an evidence record.",
                    "Use an available evidence ID and review the underlying source.")
        for record_id in calculation_ids:
            if record_id not in calculations:
                add(target, "missing_reference", [], f"Calculation reference {record_id!r} is not a calculation record.",
                    "Use the calculation tool and cite its returned ID.")
        references = evidence_ids + calculation_ids
        nested = claim.get("constraints", {})
        assertion = assertion_fields(claim)
        if isinstance(nested, dict) and any(k in claim and k in nested and claim[k] != nested[k] for k in assertion):
            add(target, "numerical_inconsistency", references,
                "The claim supplies conflicting exact numerical assertions in its top-level fields and constraints.",
                "Resolve the conflicting assertion against the cited numerical record.")
        claim = {**claim, **assertion}
        claims[target] = claim
        citations = [records[ref] for ref in references if ref in records]
        display = display_check(claim, citations, record_index=records)
        displays[target] = display
        fiscal_target = context.get("fiscal_target")
        if fiscal_target and not fiscal_target.get("quarter"):
            current, outside = period_sources(fiscal_target, citations)
            if current and rate_only_in_other_period(claim["text"], current, outside):
                add(target, "context_mismatch", references,
                    "The stated rate appears only in cited records from another period.",
                    "Use the original result from the user-selected date window or label the other period explicitly.")
        matched_ids = {ref for item in display.get("matched", []) for ref in item.get("record_ids", [])}
        link_sources = [records[ref] for ref in sorted(matched_ids) if ref in records] or citations
        if unbound_sec_links(claim["text"], link_sources, records, link_only=source_link_only):
            add(target, "missing_reference", [row["id"] for row in link_sources],
                "The cited evidence does not support the displayed link to the previous answer's source." if source_link_only else
                "The cited SEC evidence or calculation inputs do not support the displayed filing link.",
                "Use the original link from the cited evidence, or obtain support for the displayed source.")
        if mismatched_filing_periods(claim["text"], link_sources, records, request):
            add(target, "missing_reference", [row["id"] for row in link_sources],
                "The cited SEC filing does not match the stated quarter or its description as the original filing.",
                "Attach each period's own original filing or label the later filing as a source of comparative figures.")
        for cited_refs, detail in consolidated_result_defects(
                claim, citations, records, scope, request, companies=companies):
            add(target, "numerical_inconsistency", cited_refs, detail,
                "Use the earnings release's consolidated GAAP figures and as-reported growth for the named quarter.")
        for category, checked_refs, gap in margin_formula_defects(context, claim, citations, records):
            add(target, category, checked_refs or references, gap,
                "Recheck the requested period and margin definition, then cite the calculation and its original inputs.")
        for literal, compared in margin_comparison_defects(claim, citations, records, request):
            add(target, "numerical_inconsistency" if compared else "missing_reference", compared or references,
                f"Operating-margin change {literal} is unsupported by a cited calculation for the stated periods and direction.",
                "Calculate both margins from the original amounts, subtract their unrounded rates, then cite the result.")
        if display_notes is not None and display.get("negated_displays"):
            display_notes.append({"claim_id":target, "evidence_refs":references,
                                  "matched":display.get("matched", []), "negated_displays":display["negated_displays"]})
        if display.get("method") == "mismatch":
            gap = "Displayed amount/unit does not match the cited numerical record or assertion: " + ", ".join(display["unmatched"])
            if cell := display.get("printed_cell"):
                gap += f". {cell['group']}, {cell['year']}: the cited row prints {cell['printed']} {cell['scale']} dollars."
            for expected in display.get("expected", []):
                gap += (f" {expected['display']} is labelled as {expected['metric']} for {expected['period_end']};"
                        f" the cited record gives {expected['value']} {expected['unit']}.")
            add(target, "numerical_inconsistency", references,
                gap,
                "Correct the sentence or cite the corresponding value, keeping the unit, scale and display rounding consistent.")
        if not references:
            add(target, "unsupported_statement", [], "Claim has no source evidence or calculation references.",
                "Acquire support or qualify/remove the assertion, preserving the unanswered requirement.")
        for reference in references:
            check_dependency(target, reference)
        for field, source in (("evidence_versions", evidence), ("calculation_versions", calculations)):
            versions = claim.get(field, {})
            if isinstance(versions, dict):
                for ref, version in versions.items():
                    if ref in source and source[ref].get("version") != version:
                        add(target, "stale_reference", [ref], "Claim reference version changed after synthesis.",
                            "Resynthesize affected claims and obtain a fresh review.")

        linked_reqs = claim.get("requirement_ids", [])
        if not isinstance(linked_reqs, list) or not all(isinstance(x, str) for x in linked_reqs):
            add(target, "invalid_record", references, "Claim requirement IDs are malformed.",
                "Map the claim to the current required-answer checklist.")
        elif set(linked_reqs) - reqs.keys():
            add(target, "context_mismatch", references, "Claim targets requirements outside the current checklist.",
                "Resolve current requirements before resynthesizing.")

        constraints = claim.get("constraints", {})
        if not isinstance(constraints, dict):
            add(target, "invalid_record", references, "Claim constraints must be an object.",
                "State the asserted result's financial context explicitly.")
            constraints = {}
        # Calculations are result anchors; earlier comparison operands can
        # legitimately use other periods. Never require all operands to match.
        anchors = [calculations[i] for i in calculation_ids if i in calculations]
        if not anchors:
            anchors = [evidence[i] for i in evidence_ids if i in evidence]
        elif display.get("method") == "matched_records" and "asserted_value" in claim:
            # A statement can report a base fact and a separate derived metric.
            # Only the result's own, independently bound source may anchor its
            # exact field. An unlabelled calculation operand cannot stand in.
            bound = [r for r in display["matched"] if r.get("metric")]
            if any(all(ref in calculations for ref in r["record_ids"]) for r in bound):
                direct = {ref for r in bound if r["metric"] == constraints.get("metric")
                          and all(i in evidence for i in r["record_ids"]) for ref in r["record_ids"]}
                anchors.extend(evidence[i] for i in evidence_ids if i in direct and i in evidence)
        dimensions = {k: constraints[k] for k in DIMENSIONS if constraints.get(k) is not None}
        if "asserted_unit" in claim:
            if constraints.get("unit") is not None and constraints["unit"] != claim["asserted_unit"]:
                add(target, "context_mismatch", references, "Claim asserted_unit conflicts with its unit constraint.",
                    "Use the source/calculation's exact unit consistently; do not silently rescale.")
            dimensions.setdefault("unit", claim["asserted_unit"])
        numeric = "asserted_value" in claim
        numeric_anchors = [a for a in anchors if _number(a.get("value"))]
        document_anchors = [a for a in anchors if a.get("kind") == "document"]
        candidates = numeric_anchors if numeric and numeric_anchors else anchors
        aligned = compatible_records(candidates, dimensions, records, citations=citations)
        document_only_uncertainty = not numeric_anchors and bool(
            compatible_records(document_anchors, dimensions, records, citations=citations))
        if dimensions and candidates and not aligned and _definition_comparison(claim, dimensions, candidates, claims):
            if context_notes is not None:
                context_notes.append({"claim_id": target, "comparison_inputs": {
                    row["id"]: row["metric"] for row in candidates},
                    "check_against_source": "Review whether the prose correctly explains both formulas."})
        elif (dimensions and candidates and not aligned
              and not _joint_metric_scope(claim, dimensions, display, citations, records)):
            add(target, "context_mismatch", [a["id"] for a in candidates],
                "Every cited result conflicts with at least one stated source dimension.",
                "Check the company, metric, dates, unit and reporting basis against the source.")
        if numeric:
            value = claim["asserted_value"]
            if not _number(value) or not isinstance(claim.get("asserted_unit"), str):
                add(target, "invalid_record", references, "Numerical assertion needs a finite value and explicit unit.",
                    "Record the exact result value/unit separately from display rounding.")
            else:
                matching = [(a, unknown) for a, unknown in aligned if a.get("value") == value]
                if (not document_only_uncertainty and not matching
                        and (aligned or not any(a.get("value") == value for a in numeric_anchors))):
                    observed = [f"{a['value']} {a.get('unit', '')}".strip() for a, _ in aligned if _number(a.get("value"))]
                    gap = (f"Exact assertion {value} {claim['asserted_unit']}; cited result(s): "
                           + ", ".join(dict.fromkeys(observed)) + ".") if observed else (
                           "Asserted value has no matching numeric source or calculation in the asserted context.")
                    add(target, "numerical_inconsistency", [a["id"] for a in numeric_anchors],
                        gap,
                        "Retrieve or calculate the result and cite its exact value/unit; do not hide the requirement.")
                if not document_only_uncertainty:
                    aligned = matching
        if context_notes is not None and aligned and any(unknown for _, unknown in aligned):
            context_notes.append({"claim_id": target, "check_against_source": {
                a["id"]: {k: dimensions[k] for k in unknown} for a, unknown in aligned if unknown}})

    for target in reqs:
        if not any(isinstance(c.get("requirement_ids"), list) and target in c["requirement_ids"] for c in claims.values()):
            add(target, "omission", [], "No candidate claim addresses this required-answer item.",
                "Research the missing item or explicitly report it as unanswered with the evidence gap.")
    # A claim can faithfully quote a real cell and still answer the wrong fiscal period.
    # Only a task with an explicitly structured exact-period fact supplies this constraint.
    task = scope.get("planning_task") or {}
    for item in task.get("items", []) if isinstance(task, dict) else []:
        if not isinstance(item, dict) or item.get("kind") != "fact" or item.get("id") not in reqs:
            continue
        periods = item.get("periods", [])
        if len(periods) != 1 or not periods[0].get("end"):
            continue
        period = periods[0]
        cited = {}
        for claim in claims.values():
            if item["id"] not in (claim.get("requirement_ids") or []) or not _number(claim.get("asserted_value")):
                continue
            for ref in claim.get("evidence_ids", []) + claim.get("calculation_ids", []):
                row = records.get(ref, {})
                if item.get("metric") in (row.get("metric"), row.get("concept")) and row.get("value") == claim["asserted_value"]:
                    cited[ref] = row
        if cited and not any(row.get("period_start") == period.get("start") and row.get("period_end") == period["end"]
                             for row in cited.values()):
            add(item["id"], "context_mismatch", sorted(cited),
                "The cited numeric answer does not cover the exact period requested for this fact.",
                "Locate the requested period or report that it is missing.")
    return issues + list(latest_answer_issues(context, reqs, claims, records, displays)) + comparison_rule_issues(context)


class Verifier:
    def __init__(self, model):
        self.model = model

    def review(self, context: dict) -> dict:
        """Exactly one counted semantic review; no fallback to rule-only success."""
        return validated_output("critic", self.model.complete("critic", context), context)
