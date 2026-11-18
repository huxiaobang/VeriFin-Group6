import unittest

from verifin.model import OutputContractError, validated_output


class PrintedAssertionUnitsTest(unittest.TestCase):
    def setUp(self):
        dims = {"entity": "EXAMPLE", "metric": "revenue", "period_start": "2025-01-01",
                "period_end": "2025-03-31", "unit": "CAD"}
        self.cell = {**dims, "id": "amount", "cell_type": "statement_amount_cell", "currency": "CAD",
                     "value": 12_500_000, "scale": 1, "printed_scale": 1_000_000}
        self.claim = {"id": "revenue", "text": "Revenue was CAD 12.5 million.",
                      "constraints": dims, "asserted_value": 12.5, "asserted_unit": "CAD millions",
                      "evidence_ids": ["amount"], "calculation_ids": [], "requirement_ids": ["r"]}

    def read(self, cell, claim):
        return validated_output("researcher", {"claims": [claim], "summary": "",
                                "answered_requirement_ids": ["r"], "unanswered_requirement_ids": []},
                                {"evidence": [cell], "requirements": [{"id": "r"}]})["claims"][0]

    def test_printed_and_base_units(self):
        for unit in ("CAD millions", "CAD_millions"):
            result = self.read(self.cell, {**self.claim, "asserted_unit": unit})
            self.assertEqual((result["asserted_value"], result["asserted_unit"]), (12_500_000, "CAD"))
            self.assertEqual(result["text"], self.claim["text"])

    def test_requires_exact_matching_cited_cell(self):
        for wrong in ({"period_start": "2024-12-01"}, {"period_end": "2024-03-31"}, {"currency": "USD"},
                      {"printed_scale": 1000}, {"value": 12_500_100}, {"metric": "operating_income"},
                      {"stale": True}, {"cell_type": "other"}, {"scale": 1_000_000}):
            with self.subTest(wrong=wrong), self.assertRaises(OutputContractError):
                self.read({**self.cell, **wrong}, self.claim)
