import unittest

from verifin.display_numbers import display_check
from verifin.model import validate_response


class DisplayedCalculationRateTests(unittest.TestCase):
    # Values and sentence structure from a real closing-price comparison.
    text = ("The raw closing price return for MSFT from the regular trading session close "
            "on 2025-07-30 ($513.24) to the regular trading session close on 2025-07-31 "
            "($533.50) was +3.95%.")
    prices = [{"id": "p1", "kind": "market_price", "entity": "MSFT", "metric": "provider_close",
               "period_end": "2025-07-30", "unit": "USD/share", "value": 513.239990234375},
              {"id": "p2", "kind": "market_price", "entity": "MSFT", "metric": "provider_close",
               "period_end": "2025-07-31", "unit": "USD/share", "value": 533.5}]
    calculation = {"id": "return", "operation": "price_return", "metric": "price_return",
                   "operand_ids": ["p2", "p1"], "entity": "MSFT", "period_start": "2025-07-30",
                   "period_end": "2025-07-31", "unit": "%", "value": 3.9474729465981615}

    def accepted(self, text, asserted=3.95, calculations=None, **dims):
        calculations = [self.calculation] if calculations is None else calculations
        scope = {"entity": "MSFT", "unit": "%", "metric": "price_return", **dims}
        claim = {"id": "c", "text": text, "requirement_ids": ["r"], "asserted_value": asserted,
                 "asserted_unit": scope["unit"], "constraints": scope, "evidence_ids": ["p1", "p2"],
                 "calculation_ids": [row["id"] for row in calculations]}
        context = {"requirements": [{"id": "r", "text": "Return?"}], "calculations": calculations,
                   "evidence": self.prices}
        draft = {"claims": [claim], "answered_requirement_ids": ["r"], "unanswered_requirement_ids": [],
                 "summary": ""}
        return validate_response("researcher", draft, context)["claims"][0]

    def test_dates_bind_parenthesized_prices_and_rate_retains_exact_result(self):
        claim = self.accepted(self.text)
        self.assertEqual(claim["asserted_value"], self.calculation["value"])
        checked = display_check(claim, [*self.prices, self.calculation])
        self.assertEqual([r["record_ids"] for r in checked["matched"]], [["p1"], ["p2"], ["return"]])
        for old, new in [("$513.24", "$533.50"), ("$513.24", "$513.25"), ("$513.24", "EUR 513.24"),
                         ("$513.24", "-$513.24"), ("2025-07-30", "2025-07-29")]:
            with self.subTest(replacement=new):
                wrong = self.accepted(self.text.replace(old, new))
                self.assertEqual(display_check(wrong, [*self.prices, self.calculation])["method"], "mismatch")

    def test_exact_field_is_not_inferred_from_different_or_ambiguous_records(self):
        for rate in (3.96, -3.95):
            claim = self.accepted(self.text.replace("+3.95%", f"{rate}%"), rate)
            self.assertEqual(claim["asserted_value"], rate)
            self.assertEqual(display_check(claim, [*self.prices, self.calculation])["method"], "mismatch")
        for rows, dims in (([self.calculation, {**self.calculation, "id": "other"}], {}),
                           ([{**self.calculation, "stale": True}], {}),
                           ([self.calculation], {"period_end": "2025-08-01"}),
                           ([self.calculation], {"currency": "EUR"}),
                           ([self.calculation], {"unit": "percentage_points"})):
            with self.subTest(rows=rows, dims=dims):
                self.assertEqual(self.accepted(self.text, calculations=rows, **dims)["asserted_value"], 3.95)
        self.assertEqual(self.accepted(self.text, asserted=3.951)["asserted_value"], 3.951)
