import copy
import unittest
from pathlib import Path

from verifin.data import DataError
from verifin.financial import duration_class, quarter_from_cumulative
from verifin.tools import ToolRegistry

DATA = Path(__file__).resolve().parents[1] / "data"


def fact(identifier, end, value, concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"):
    return {"id": identifier, "kind": "financial_fact", "entity": "GENERIC", "metric": "revenue",
            "concept": concept, "period_start": "2024-01-01", "period_end": end, "value": value,
            "unit": "USD", "currency": "USD", "basis": "US-GAAP", "business_scope": "consolidated",
            "published_at": "2025-02-01", "version": 1}


class Store:
    def __init__(self, rows):
        self.rows = rows

    def get_context(self, session_id, **kwargs):
        return {"evidence": copy.deepcopy(self.rows), "calculations": []}


class FinancialBoundaryTests(unittest.TestCase):
    def test_printed_and_tagged_flow_paths_assign_the_same_quarter(self):
        from verifin.record_context import statement_dimensions
        from verifin.verification import check_rules
        from verifin.statement_analysis import Findings
        a, b = (dict(fact(key,end,base,"us-gaap:NetCashProvidedByUsedInOperatingActivities"),
                     metric="operating_cash_flow") for key,end,base in
                (("long","2024-06-30",120000),("short","2024-03-31",80000)))
        f = Findings()
        for row in (a,b):
            f.evidence[row["id"]] = dict(row,cash_flow_series="operating_cash_flow",unit="USD thousands", value=row["value"]//1000,
                metric="Net cash from operating activities", source_url="issuer",locator="cash flow table",snapshot_hash="snapshot")
        key = f.calc("Operating cash in the residual quarter","statement_quarter_residual",["long","short"],"USD thousands")
        printed = f.record(key)
        generic = ToolRegistry(Store([a,b]), DATA).execute("calculate",{"operation":"derive_quarter","operand_ids":["long","short"]},"s",{})["calculations"][0]
        self.assertEqual((printed["period_start"],printed["period_end"]), (generic["period_start"],generic["period_end"]))
        self.assertEqual(printed["value"] * 1000, generic["value"])
        ctx = {"evidence":list(f.evidence.values()),"calculations":[printed],
               "claims":[{"id":"claim","text":"Operating cash.","calculation_ids":[key]}]}
        self.assertEqual(check_rules(ctx),[])
        for changes in ({"period_start":"2024-01-01"},{"period_end":None},{"value":41},{"cash_flow_series":"net_income"}):
            self.assertTrue(check_rules({**ctx,"calculations":[dict(printed,**changes)]}))
        first, last = ctx["evidence"]
        last = dict(last,period_start="2024-04-01",period_end="2024-06-30")
        dims = statement_dimensions("statement_quarter_residual", [first,last], printed["unit"])
        self.assertEqual((dims["period_start"],dims["period_end"]),("2024-01-01","2024-03-31"))
        for changed in ({"cash_flow_series":"another"},{"cash_flow_series":None},{"period_end":"2024-07-01"},
                        {"unit":"CAD thousands"},{"entity":"OTHER"}):
            with self.assertRaises(ValueError):
                statement_dimensions("statement_quarter_residual", [first,dict(last,**changed)], printed["unit"])
    def test_same_period_monetary_gap_preserves_order_and_compatible_scope(self):
        a = fact("cash", "2024-03-31", 112, "us-gaap:NetCashProvidedByUsedInOperatingActivities")
        b = fact("net", "2024-03-31", 95, "us-gaap:NetIncomeLoss")
        a["metric"], b["metric"] = "operating_cash_flow","net_income"
        def subtract(left,right):
            reg = ToolRegistry(Store([left,right]),DATA)
            return reg.execute("calculate",{"operation":"difference","operand_ids":[left["id"],right["id"]]},"s",{})
        for left,right,expected in [(a,b,17),(b,a,-17)]:
            row = subtract(left,right)["calculations"][0]
            self.assertEqual((row["value"],row["metric"],row["unit"]),
                             (expected,left["metric"]+"_minus_"+right["metric"],"USD"))
        for change in ({"entity":"OTHER"},{"period_start":"2024-03-01"},{"period_end":"2024-06-30"},
                       {"currency":"CAD"},{"basis":"adjusted"},{"business_scope":"segment"},
                       {"concept":"us-gaap:EarningsPerShareBasic"},{"stale":True}):
            with self.subTest(change=change):
                self.assertFalse(subtract(a,{**b,**change})["ok"])

    def test_q2_q3_q4_cumulative_derivation(self):
        quarters = [("2024-06-30", "2024-03-31", 91), ("2024-09-30", "2024-06-30", 92), ("2024-12-31", "2024-09-30", 92)]
        for end, prior_end, days in quarters:
            with self.subTest(end=end):
                a, b = fact("a", end, 120), fact("b", prior_end, 80)
                self.assertEqual(quarter_from_cumulative(a, b)["quarter_days"], days)
                result = ToolRegistry(Store([a, b]), DATA).execute("calculate", {"operation": "derive_quarter", "operand_ids": ["a", "b"]}, "s", {})
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["calculations"][0]["value"], 40)
                self.assertEqual(result["calculations"][0]["period_end"], end)

    def test_eps_weighted_shares_balances_and_unknown_cannot_be_subtracted(self):
        for concept in ("us-gaap:EarningsPerShareBasic", "us-gaap:EarningsPerShareDiluted", "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic", "us-gaap:Assets", "us-gaap:UnknownFlow"):
            a, b = fact("a", "2024-12-31", 10, concept), fact("b", "2024-09-30", 8, concept)
            with self.assertRaisesRegex(DataError, "additive"):
                quarter_from_cumulative(a, b)
            result = ToolRegistry(Store([a, b]), DATA).execute("calculate", {"operation": "derive_quarter", "operand_ids": ["a", "b"]}, "s", {})
            self.assertFalse(result["ok"])

    def test_mismatched_start_skipped_period_and_short_stub_refused(self):
        a, b = fact("a", "2024-12-31", 100), fact("b", "2024-09-30", 80)
        for changes in ({"period_start": "2024-02-01"}, {"period_end": "2024-06-30"}, {"period_end": "2024-12-01"}):
            with self.assertRaises(DataError):
                quarter_from_cumulative(a, dict(b, **changes))

    def test_length_labels_are_not_fiscal_period_assertions(self):
        self.assertEqual(duration_class(None, "2024-12-31"), "instant")
        self.assertEqual(duration_class("2024-01-01", "2024-06-30"), "half_year_length")
        self.assertEqual(duration_class("2024-02-01", "2024-02-28"), "other_duration")
        with self.assertRaises(DataError):
            duration_class("2024-03-01", "2024-02-28")

    def test_new_real_alias_metadata_and_exact_period(self):
        registry = ToolRegistry(None, DATA)
        for metric in ("eps_diluted", "weighted_shares_basic", "capital_expenditures", "research_and_development"):
            result = registry.execute("financial_facts", {"entity": "MSFT", "metric": metric, "period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-02-01"}, "s", {})
            # Cash flow capex may be disclosed cumulatively only. No substitution.
            if metric == "capital_expenditures" and not result["ok"]:
                self.assertIn("exact period", result["summary"])
                continue
            self.assertTrue(result["ok"], result)
            row = result["evidence"][0]
            self.assertEqual(row["period_start"], "2024-10-01")
            self.assertEqual(row["period_length_class"], "quarter_length")
            self.assertTrue(row["concept_description"])
            self.assertIn("context only", row["fiscal_period_context"]["selection_role"])


if __name__ == "__main__":
    unittest.main()
