import unittest

from verifin.financial import COMMON_INCOME, TAX_SCENARIO, TAX_SCENARIO_BASIS, VALUATION_ALLOWANCE_EXPENSE
from verifin.record_context import compatible_records
from verifin.verification import check_rules


def reported(concept, **overrides):
    return {"id": "common", "kind": "financial_fact", "entity": "ACME", "concept": concept, "metric": concept,
            "period_start": "2025-01-01", "period_end": "2025-03-31", "basis": "US-GAAP",
            "business_scope": "consolidated", "unit": "USD", "currency": "USD", "value": 500,
            "accession": "0001234567-25-000001", "source_url": "https://example.org/facts",
            "snapshot_hash": "abc", "locator": "tagged fact", **overrides}


class RecordDimensionLanguageTests(unittest.TestCase):
    def test_signed_expense_measure_still_requires_exact_signed_value_and_dimensions(self):
        fact = reported(VALUATION_ALLOWANCE_EXPENSE, value=-300)
        fields = {"entity": "ACME", "metric": "signed deferred tax asset valuation allowance change as tax expense",
                  "period_start": fact["period_start"], "period_end": fact["period_end"],
                  "basis": "reported US GAAP", "business_scope": "consolidated", "currency": "USD", "unit": "USD"}
        claim = {"id": "item", "text": "The signed expense item was -300 USD.",
                 "constraints": fields, "asserted_value": -300, "asserted_unit": "USD",
                 "evidence_ids": ["common"], "calculation_ids": [], "requirement_ids": []}
        ctx = {"evidence": [fact], "claims": [claim], "requirements": []}
        self.assertEqual(check_rules(ctx), [])
        for key, wrong in (("entity", "OTHER"), ("metric", "signed valuation allowance change in tax benefit"),
                           ("period_start", "2024-04-01"), ("period_end", "2025-06-30"), ("currency", "CAD"),
                           ("unit", "USD/share"), ("basis", "adjusted US GAAP"), ("business_scope", "segment")):
            with self.subTest(field=key):
                claim["constraints"] = {**fields, key: wrong}
                self.assertIn("context_mismatch", {i["issue_type"] for i in check_rules(ctx)})
        claim.update(constraints=fields, asserted_value=300, text="The signed expense item was 300 USD.")
        self.assertIn("numerical_inconsistency", {i["issue_type"] for i in check_rules(ctx)})

    def test_statement_annotation_requires_cited_allocation_rows_from_same_filing(self):
        common = reported(COMMON_INCOME)
        fields = {"metric": "net income available to common shareholders", "basis": "US-GAAP reported"}
        self.assertTrue(compatible_records([common], fields))
        for metric in ("net income", "net income available to common stockholders, diluted", "basic earnings per share"):
            self.assertFalse(compatible_records([common], {**fields, "metric": metric}))
        fields["basis"] += "; allocation rows taken from the income statement"
        document = {"kind": "document", "entity": "ACME", "document_report_date": common["period_end"],
                    "accession": common["accession"],
                    "text": "Net income\n510\nPreferred dividends\n10\nNet income available to common stockholders\n500"}
        self.assertTrue(compatible_records([common], fields, citations=[document]))
        self.assertFalse(compatible_records([common], fields))
        for change in ({"text": "Management is reviewing preferred dividends."}, {"entity": "OTHER"},
                       {"accession": "another-filing"}, {"document_report_date": "2024-12-31"},
                       {"document_period_start": "2024-04-01"}, {"stale": True}):
            self.assertFalse(compatible_records([common], fields, citations=[{**document, **change}]))
        self.assertFalse(compatible_records([common], {**fields, "basis": "US-GAAP reported; excluding litigation"},
                                            citations=[document]))

    def test_comparison_basis_and_dates_follow_ordered_operands(self):
        prior = reported(COMMON_INCOME, id="prior", period_start="2024-01-01", period_end="2024-03-31")
        now = reported(COMMON_INCOME, id="scenario", operation=TAX_SCENARIO, basis=TAX_SCENARIO_BASIS)
        growth = {"id": "growth", "operation": "growth", "basis": now["basis"], "unit": "%",
                  "operand_ids": ["scenario", "prior"], "period_end": now["period_end"]}
        index = {r["id"]: r for r in (prior, now, growth)}
        fields = {"basis": "current single-item sensitivity vs prior-year reported common income",
                  "comparison_period_start": prior["period_start"], "comparison_period_end": prior["period_end"]}
        self.assertTrue(compatible_records([growth], fields, index))
        self.assertTrue(compatible_records([now], {"basis": "single item scenario only"}, index))
        self.assertFalse(compatible_records([now], {"basis": "adjusted earnings"}, index))
        for wrong in ({"basis": "current reported versus prior-year single-item scenario"},
                      {"comparison_period_end": "2024-06-30"}, {"comparison_period_start": "2024-02-01"}):
            self.assertFalse(compatible_records([growth], {**fields, **wrong}, index))
        for change in ({"basis": "adjusted"}, {"concept": "us-gaap:NetIncomeLoss"}, {"entity": "OTHER"},
                       {"period_start": "2024-10-01", "period_end": "2024-12-31"}):
            self.assertFalse(compatible_records([growth], {"basis": fields["basis"]},
                                                {**index, "prior": {**prior, **change}}))
        self.assertFalse(compatible_records([{**growth, "operand_ids": ["prior", "scenario"]}], fields, index))
        self.assertFalse(compatible_records([growth], fields, {}))
