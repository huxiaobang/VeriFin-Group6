"""Compare known source dimensions and identify the parts requiring source review."""
import re

from .data import parse_day, stable_id
from .financial import (COMMON_INCOME, TAX_SCENARIO, TAX_SCENARIO_BASIS,
                        VALUATION_ALLOWANCE_EXPENSE, check_tax_scenario_comparison, residual_quarter)


COMPARISON_DATES = ("comparison_period_start", "comparison_period_end")
DIMENSIONS = ("entity", "metric", "period_start", "period_end", "unit", "basis", "business_scope",
              "currency", *COMPARISON_DATES)


def statement_result(record):
    # Findings.calc uses a descriptive label, including in older snapshots.
    if not record.get("operation") or not isinstance(record.get("operand_ids"), list):
        return False
    return record.get("id") == stable_id("calc_", [
        record.get("metric"), record["operation"], record["operand_ids"], record.get("unit")])


def shared_dimensions(inputs, unit):
    if not inputs:
        return {}
    # A reporting period is a pair. Two different YTD periods do not
    # describe a result starting at their common fiscal year opening.
    fields = ["business_scope", "basis"]
    if all((row.get("period_start"), row.get("period_end")) ==
           (inputs[0].get("period_start"), inputs[0].get("period_end")) for row in inputs):
        fields += ["period_start", "period_end"]
    if all(row.get("unit") == unit for row in inputs):
        fields.append("currency")
    return {k: inputs[0][k] for k in fields if inputs[0].get(k) is not None
            and all(row.get(k) == inputs[0][k] for row in inputs)}


def statement_dimensions(operation, inputs, unit):
    common = shared_dimensions(inputs, unit)
    if operation == "statement_quarter_residual":
        if (len(inputs) != 2 or not inputs[0].get("cash_flow_series") or
                any(not inputs[0].get(k) or inputs[0][k] != inputs[1].get(k)
                    for k in ("cash_flow_series", "entity", "unit")) or unit != inputs[0]["unit"]):
            raise ValueError("A quarter residual requires two amounts from the same additive cash-flow series and unit.")
        window = residual_quarter(*inputs, allow_same_end=True)
        common.update(period_start=window["period_start"], period_end=window["period_end"],
                      cash_flow_series=inputs[0]["cash_flow_series"])
    return common


def _words(label):
    return " ".join(re.sub(r"[-_]", " ", label.casefold()).split()) if isinstance(label, str) else ""


def _fact_metric(row, wanted):
    """Only accept an entire label with the same meaning as the tagged monetary fact."""
    if row.get("kind") != "financial_fact" or not row.get("concept"):
        return False
    label = _words(wanted)
    if label and label == _words(row.get("concept_label")):
        return True
    if (row.get("metric") != row["concept"] or row.get("basis") != "US-GAAP"
            or not row.get("currency") or row.get("unit") != row["currency"]):
        return False
    if row["concept"] == COMMON_INCOME:
        return bool(re.fullmatch(r"(?:basic )?net (?:income|earnings) available to common "
                                 r"(?:stockholders|shareholders)(?:,? basic)?", label))
    if row["concept"] == VALUATION_ALLOWANCE_EXPENSE:
        return bool(re.fullmatch(r"signed (?:deferred tax assets? )?valuation allowance change "
                                 r"(?:in|as) (?:income )?tax expense", label))
    return False


def _allocation_statement(row, citations):
    """A source annotation must refer to allocation rows in a directly cited filing."""
    if row.get("concept") != COMMON_INCOME or not row.get("accession"):
        return False
    for doc in citations:
        if (doc.get("kind") != "document" or doc.get("stale") or doc.get("entity") != row.get("entity")
                or doc.get("document_report_date") != row.get("period_end")
                or doc.get("document_period_start") not in (None, row.get("period_start"))
                or not (doc.get("accession") == row["accession"] or
                        row["accession"].replace("-", "") in str(doc.get("source_url", "")).split("/"))):
            continue
        lines = {line.strip().casefold() for line in doc.get("text", "").splitlines()}
        if ("net income" in lines and "net income available to common stockholders" in lines
                and lines & {"dividends on preferred shares", "preferred stock dividends", "preferred dividends"}):
            return True
    return False


def _comparison_inputs(row, records):
    ids = row.get("operand_ids")
    if (row.get("operation") in ("growth", "difference", "price_return") and
            isinstance(ids, list) and len(ids) == 2 and all(isinstance(i, str) and i in records for i in ids)):
        return records[ids[0]], records[ids[1]]
    return {}, {}


_ARITHMETIC_NOTE = re.compile(
    r"(?:算术分解(?:[，,]\s*(?:不是|不代表)经营原因分配)?|(?:表内)?算术核对(?:[，,]\s*不代表经营原因已全部解释)?|"
    r"(?:由|按)原表(?:金额|数值)计算|arithmetic (?:decomposition|reconciliation))", re.I)
_RATIO_NOTE = re.compile(r"(?:按|由)原表(?:(千|百万|亿)?(美元))?金额计算比率(?:[，,]\s*再四舍五入)?")
_PRINTED_SCALES = {"千": 1000, "百万": 1000000, "亿": 100000000, None: 1}


def _statement_arithmetic_basis(row, wanted, records, constraints):
    # A method note can describe a calculation on reported amounts. Do not strip
    # arbitrary qualifiers such as adjusted, same-store or constant-currency.
    basis, sep, note = wanted.replace("；", ";").partition(";")
    if not sep or basis.strip().casefold() != "us-gaap" or row.get("basis") != "US-GAAP" or not statement_result(row):
        return False
    ratio = _RATIO_NOTE.fullmatch(note.strip())
    if ratio:
        if row.get("unit") not in {"%", "percentage_points"}:
            return False
    elif not _ARITHMETIC_NOTE.fullmatch(note.strip()):
        return False

    # Follow the pinned arithmetic all the way to the source cells. Every leaf
    # must belong to the same reported statement, issuer and accounting basis.
    table = None
    periods = set()
    stack = [(row, frozenset())]
    checked = set()
    while stack:
        item, ancestors = stack.pop()
        ref = item.get("id")
        if (not ref or ref in ancestors or len(ancestors) >= 64 or item.get("stale")
                or item.get("entity") != row.get("entity") or item.get("basis") != row["basis"]
                or item.get("business_scope") != row.get("business_scope")):
            return False
        if ref in checked:
            continue
        checked.add(ref)
        if item.get("operation"):
            if (item["operation"] not in {"sum","difference","margin","percentage_point_change"}
                    or not statement_result(item) or not item["operand_ids"]):
                return False
            versions = item.get("operand_versions") or {}
            for operand in item["operand_ids"]:
                if not isinstance(operand, str):
                    return False
                child = records.get(operand, {})
                if operand not in versions or child.get("version") != versions[operand]:
                    return False
                stack.append((child, ancestors | {ref}))
        else:
            source = (item.get("snapshot_hash"), item.get("table_id"))
            if (item.get("cell_type") != "statement_amount_cell" or not all(source)
                    or not item.get("locator") or not item.get("period_start") or not item.get("period_end")
                    or (table is not None and source != table)):
                return False
            table = source
            periods.add((item["period_start"], item["period_end"]))
            if ratio and ratio[2] and (item.get("currency") != "USD"
                    or item.get("printed_scale") != _PRINTED_SCALES[ratio[1]]):
                return False
    if table is None:
        return False
    for start, end in (("period_start", "period_end"), COMPARISON_DATES):
        target = (constraints.get(start), constraints.get(end))
        if any(target) and not any(all(not w or w == p for w,p in zip(target, period)) for period in periods):
            return False
    return True


def _basis_matches(row, wanted, inputs, citations, records, constraints):
    label = _words(wanted)
    if row.get("kind") == "financial_fact" and row.get("basis") == "US-GAAP":
        basis, sep, annotation = label.partition(";")
        if not re.fullmatch(r"(?:reported )?us gaap(?: reported)?", basis.strip()):
            return False
        return not sep or (bool(re.fullmatch(r"allocation rows? (?:read |taken )?from (?:the )?income statement",
                                            annotation.strip())) and _allocation_statement(row, citations))
    if isinstance(wanted, str) and _statement_arithmetic_basis(row, wanted, records, constraints):
        return True
    if row.get("basis") != TAX_SCENARIO_BASIS:
        return False
    if row.get("operation") == TAX_SCENARIO:
        return bool(re.fullmatch(r"single item (?:sensitivity|scenario)(?: only)?", label))
    current, prior = inputs
    if (row.get("operation") not in ("growth", "difference") or current.get("operation") != TAX_SCENARIO or
            not re.fullmatch(r"current single item (?:scenario|sensitivity) (?:versus|vs\.?) "
                             r"prior year reported common (?:earnings|income)", label)):
        return False
    try:
        check_tax_scenario_comparison(current, prior)
        # The calculator permits sequential comparisons too. A prior-year
        # description additionally needs corresponding period boundaries.
        return all(358 <= (parse_day(current[k]) - parse_day(prior[k])).days <= 372
                   for k in ("period_start", "period_end"))
    except ValueError:
        return False


def compatible_records(rows, constraints, records=None, *, citations=()):
    """Never join a value from one record to the known dimensions of another."""
    records = records if records is not None else {r["id"]: r for r in rows if "id" in r}
    exact, possible = [], []
    for row in rows:
        inputs = _comparison_inputs(row, records)
        unknown = []
        for key in DIMENSIONS:
            wanted = constraints.get(key)
            actual = row.get(key)
            if wanted is None:
                continue
            if key in COMPARISON_DATES and inputs[1]:
                operand_date = inputs[1].get(key.removeprefix("comparison_"))
                if actual is not None and actual != operand_date:
                    break
                actual = operand_date
            if actual == wanted or (key == "metric" and _fact_metric(row, wanted)):
                continue
            if key == "basis" and _basis_matches(row, wanted, inputs, citations, records, constraints):
                continue
            if actual is None or (key == "metric" and statement_result(row)):
                unknown.append(key)
            else:
                break
        else:
            (possible if unknown else exact).append((row, unknown))
    return exact or possible
