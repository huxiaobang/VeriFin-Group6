import unittest

from verifin.data import stable_id
from verifin.display_numbers import display_check
from verifin.record_context import compatible_records


class StatementBasisTests(unittest.TestCase):
    def test_annotated_arithmetic_requires_reported_inputs_from_one_table(self):
        periods = ("2024-09-30", "2023-09-30")
        cells = [dict(id=f"leaf_{i}", basis="US-GAAP", entity="XYZ", business_scope="consolidated",
                      period_start=end[:4]+"-07-01", period_end=end, cell_type="statement_amount_cell",
                      snapshot_hash="digest", table_id="income", locator=f"row5/col{i}", version=1)
                 for i,end in enumerate(periods)]
        ids = [r["id"] for r in cells]
        calc = dict(id=stable_id("calc_", ["profit_change","difference",ids,"USD"]),
                    metric="profit_change", operation="difference", operand_ids=ids, operand_versions=dict.fromkeys(ids,1),
                    basis="US-GAAP", entity="XYZ", business_scope="consolidated", unit="USD")
        records = {r["id"]:r for r in [calc,*cells]}
        wanted = {"basis":"US-GAAP；算术分解","period_end":periods[0]}
        self.assertTrue(compatible_records([calc],wanted,records))
        for basis in ("non-GAAP；算术分解","US-GAAP；剔除股份支付","US-GAAP；按原表金额计算比率"):
            with self.subTest(basis=basis):
                self.assertFalse(compatible_records([calc],{**wanted,"basis":basis},records))
        self.assertFalse(compatible_records([calc],{**wanted,"period_end":"2024-12-31"},records))
        for field, value in (("basis","adjusted"),("table_id","another table"),("version",2),("stale",True)):
            with self.subTest(field=field):
                changed={**records, ids[1]:{**cells[1],field:value}}
                self.assertFalse(compatible_records([calc],wanted,changed))

    def test_postfix_contribution_sign_is_bound_to_its_own_amount(self):
        row = {"id":"delta","kind":"financial_fact","unit":"USD","value":-270000}
        def check(text):
            return display_check({"text":text,"asserted_value":row["value"],"asserted_unit":"USD"}, [row])["method"]
        self.assertEqual(check("费用造成27万美元的负贡献。"), "matched_records")
        self.assertEqual(check("费用造成27万美元的正贡献。"), "mismatch")
        self.assertEqual(check("费用造成28万美元的负贡献。"), "mismatch")
        self.assertEqual(check("费用造成+27万美元的负贡献。"), "mismatch")
        self.assertEqual(check("费用造成负27万美元的正贡献。"), "mismatch")
