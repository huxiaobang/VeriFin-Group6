"""Source-backed records shared by the executable financial-statement skills."""
from decimal import Decimal
from importlib import import_module
from pathlib import Path

from .data import available_by, cutoff_time, stable_id
from .financial import GUIDANCE_VARIANCE, check_operand_shape
from .record_context import statement_dimensions


ANALYZERS = {
    "cash-flow-bridge": "cashflow_skill",
    "implied-period-guidance": "guidance_skill",
    "unit-profit-reconciliation": "unit_profit_skill",
    "reconcile-segment-totals": "segment_skill",
    "incremental-profit-margin": "incremental_margin_skill",
    "isolate-discrete-tax-item": "discrete_tax_skill",
}


def scalar(operation, values):
    if not values:
        raise ValueError("Empty calculation")
    if operation == "sum":
        return sum(values)
    if len(values) != 2:
        raise ValueError("Two operands required for " + operation)
    a, b = values
    if operation in ("difference", "percentage_point_change", "statement_quarter_residual"):
        return a - b
    if operation == "midpoint":
        return (a + b) / 2
    if operation in ("ratio", "margin"):
        if not b:
            raise ValueError("Zero denominator")
        return a / b * (100 if operation == "margin" else 1)
    if operation == GUIDANCE_VARIANCE:
        return (a / b - 1) * 100
    raise ValueError("Unknown statement operation " + operation)


def number(value):
    d = Decimal(str(value))
    if not d.is_finite():
        raise ValueError("Nonfinite disclosure amount")
    return int(d) if d == d.to_integral_value() else float(d)


class Findings:
    def __init__(self):
        self.evidence = {}
        self.calculations = {}
        self.notes = []
        self.gaps = []
        self.inapplicable_reason = None

    def fact(self, doc, locator, metric, value, unit, period, label=None, **dimensions):
        if not all(isinstance(v, str) and v.strip() for v in (locator, metric, unit, period)):
            raise ValueError("Each amount needs an original locator, measure, unit and period")
        key = stable_id("cell_", [doc["entity"], doc["url"], locator, metric, unit, period])
        record = dict(id=key, kind="financial_fact", entity=doc["entity"], metric=metric,
                      unit=unit, value=number(value), source_url=doc["url"], locator=locator,
                      published_at=doc["published_at"], snapshot_hash=doc["snapshot_hash"],
                      source_form=doc["source_form"], period_label=period,
                      text=label or metric, snapshot=doc["source"], **dimensions)
        if key in self.evidence and self.evidence[key]["value"] != record["value"]:
            raise ValueError("Conflicting values at one disclosure locator")
        self.evidence[key] = record
        return key

    def record(self, key):
        return self.evidence[key] if key in self.evidence else self.calculations[key]

    def value(self, key):
        return self.record(key)["value"]

    def calc(self, label, operation, operand_ids, unit):
        ids = list(operand_ids)
        records = [self.record(i) for i in ids]
        check_operand_shape(operation, ids)
        if len({r["entity"] for r in records}) != 1 or len({r["unit"] for r in records}) != 1:
            raise ValueError("Statement operands must have matching issuers and units")
        if operation == "percentage_point_change":
            if records[0]["unit"] != "%" or unit != "percentage_points":
                raise ValueError("Percentage-point change requires percent operands and a percentage-point result")
        elif operation not in ("ratio", "margin", GUIDANCE_VARIANCE) and unit != records[0]["unit"]:
            raise ValueError("Unexpected output unit for additive amounts")
        dimensions = statement_dimensions(operation, records, unit)
        answer = scalar(operation, [Decimal(str(r["value"])) for r in records])
        formula = (f"({ids[0]} / {ids[1]} - 1) × 100" if operation == GUIDANCE_VARIANCE
                   else f"{operation}({', '.join(ids)})")
        key = stable_id("calc_", [label, operation, ids, unit])
        self.calculations[key] = dict(id=key, entity=records[0]["entity"], metric=label,
                                     operation=operation, operand_ids=ids, unit=unit, value=number(answer),
                                     formula=formula, **dimensions)
        return key

    def note(self, text, ids, **metadata):
        ids = list(dict.fromkeys(ids))
        if not ids or any(i not in self.evidence and i not in self.calculations for i in ids):
            raise ValueError("A finding must cite its disclosure or calculation")
        self.notes.append(dict(text=text, evidence_ids=[i for i in ids if i in self.evidence],
                               calculation_ids=[i for i in ids if i in self.calculations], **metadata))

    def skip(self, reason):
        """The chosen method cannot answer the requested comparison."""
        self.inapplicable_reason = reason

    def gap(self, text):
        if text and text not in self.gaps:
            self.gaps.append(text)


def analyze(data, entity, cutoff, question, history, skills, *, cash_quarter_end=None, guidance_fiscal_quarter=None, period_start=None, period_end=None):
    docs = []
    for d in data.manifest["documents"]:
        name = Path(d["source"]).name
        if d["entity"] != entity or not d.get("published_at") or not available_by(d["published_at"], cutoff):
            continue
        if not name.lower().endswith((".html", ".htm", ".pdf", ".json")):
            continue
        info = data.info(d["source"])
        extra = {}
        if ("incremental-profit-margin" in skills and name.lower().endswith((".htm", ".html")) and d.get("text")):
            from .statement_amount_cells import filed_boundaries, prior_currency_evidence
            extra["source_text"] = data.raw(d["text"]).decode("utf-8")
            extra["prior_units"] = prior_currency_evidence(data, d)
            extra["filed_boundaries"] = filed_boundaries(data, d)
        docs.append({**d, **extra, "name":name, "filename":name, "raw":data.raw(d["source"]),
                     "url":info["url"], "snapshot_hash":info["sha256"],
                     "source_form":d.get("source_form") or d.get("form") or "issuer_document"})
    docs.sort(key=lambda doc: cutoff_time(doc["published_at"]))
    evidence, calculations, assessments, notes, gaps = {}, {}, [], [], []
    for skill in skills:
        if skill not in ANALYZERS:
            continue
        findings = Findings()
        if docs:
            try:
                module = import_module("." + ANALYZERS[skill], __package__)
                if skill == "cash-flow-bridge":
                    from .statement_amount_cells import prior_currency_evidence
                    for doc in docs:
                        doc["cash_currency_evidence"] = prior_currency_evidence(
                            data, doc, metrics=("net_income", "operating_cash_flow"))
                options = {"quarter_end":cash_quarter_end} if skill == "cash-flow-bridge" and cash_quarter_end else {}
                if skill in {"reconcile-segment-totals", "incremental-profit-margin", "isolate-discrete-tax-item"} and period_end:
                    options["quarter_end"] = period_end
                if skill in {"reconcile-segment-totals", "isolate-discrete-tax-item"} and period_start:
                    options["period_start"] = period_start
                if skill == "implied-period-guidance" and guidance_fiscal_quarter:
                    options.update(data=data, fiscal_quarter=guidance_fiscal_quarter,
                                   period_start=period_start, period_end=period_end)
                module.analyze(question, history, docs, findings, **options)
            except ImportError as exc:
                if exc.name not in ("lxml", "pdfplumber", "pypdf"):
                    raise
                findings.gap("本地解析缺少依赖 " + exc.name)
        if not findings.notes and not findings.gaps and not findings.inapplicable_reason:
            findings.gap("截至研究时点，没有找到支撑这项计算的同口径披露。")
        evidence.update(findings.evidence)
        calculations.update(findings.calculations)
        notes.extend({**note, "method": skill} for note in findings.notes)
        gaps.extend(findings.gaps)
        assessments.append({"skill":skill, "evidence_count":len(findings.evidence),
                            "calculation_count":len(findings.calculations),
                            "notes":len(findings.notes), "gaps":findings.gaps,
                            **({"status":"not_applicable", "reason":findings.inapplicable_reason}
                               if findings.inapplicable_reason else {})})
    return {"ok":True, "summary":f"{entity}: examined {len(docs)} dated disclosures; {len(notes)} sourced findings.",
            "evidence":list(evidence.values()), "calculations":list(calculations.values()),
            "data":{"inspected_documents":[{k:d[k] for k in ("name","url","published_at","source_form","snapshot_hash")}
                                           for d in docs],
                    "skill_assessments":assessments, "skill_notes":notes, "skill_gaps":gaps}}

def answer_statement_parts(parts, data, fiscal_target):
    """Deliver only findings that each selected method can link to an actual question."""
    from .cashflow_delivery import answer_parts as cash_answers
    answers = {}
    for assessment in data.get("skill_assessments", []):
        if assessment.get("status") == "not_applicable":
            continue
        skill = assessment["skill"]
        notes = [n for n in data.get("skill_notes", []) if n.get("method", skill) == skill]
        gaps = assessment.get("gaps", [])
        if skill == "cash-flow-bridge":
            matched = cash_answers(parts, notes, fiscal_target, gaps)
        elif skill in {"reconcile-segment-totals", "incremental-profit-margin", "isolate-discrete-tax-item"}:
            module = import_module("." + ANALYZERS[skill], __package__)
            matched = module.answer_parts(parts, notes, gaps, fiscal_target=fiscal_target)
        else:
            continue
        for key, item in matched.items():
            if key not in answers:
                answers[key] = item
                continue
            # A compound question may receive two cited results, including a partial one.
            prior = answers[key]
            for field in ("answer_sentence", "missing_support"):
                lines = dict.fromkeys(r[field] for r in (prior, item) if r.get(field))
                if lines:
                    prior[field] = " ".join(lines)
            for field in ("evidence_ids", "calculation_ids"):
                prior[field] = list(dict.fromkeys([*prior.get(field, []), *item.get(field, [])]))
            if prior.get("missing_support"):
                prior["status"] = "partial" if prior.get("answer_sentence") else "gap"
    return answers
