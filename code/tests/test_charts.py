import unittest
from xml.etree import ElementTree

from verifin.charts import chart_data, render_chart_svg


def fact(rid, value=100, **extra):
    return {"id": rid, "kind": "financial_fact", "entity": "ACME", "metric": "revenue", "value": value,
            "unit": "USD", "period_start": "2024-01-01", "period_end": "2024-03-31",
            "accounting_basis": "us-gaap", "business_scope": "consolidated", **extra}


class ChartTests(unittest.TestCase):
    def test_different_units_scopes_periods_cannot_be_combined_into_one_chart(self):
        records = [fact("usd"), fact("eur", unit="EUR"), fact("segment", business_scope="Cloud"),
                   fact("annual", period_end="2024-12-31"), fact("unknown", accounting_basis="unspecified")]
        result = chart_data({"evidence": records})
        self.assertEqual(5, len(result["groups"]))

    def test_conflicts_stale_and_missing_dimensions_are_excluded_without_arbitrary_winner(self):
        result = chart_data({"evidence": [fact("a"), fact("b", 101), fact("old", stale=True),
                                          fact("missing", entity=None), {"id": "passage", "text": "100 dollars"}]})
        self.assertEqual([], result["groups"])
        self.assertEqual({"a", "b", "old", "missing"}, {r["record_id"] for r in result["excluded"]})

    def test_equal_duplicates_retain_all_references_and_comparable_companies_share_group(self):
        result = chart_data({"version": 2, "evidence": [fact("a"), fact("b"), fact("c", 120, entity="NEW")]})
        group = result["groups"][0]
        self.assertEqual(2, len(group["points"]))
        self.assertEqual(["a", "b"], group["points"][0]["record_ids"])
        self.assertEqual(2, result["report_version"])

    def test_svg_escapes_source_text_has_zero_and_handles_negative_values(self):
        group = chart_data({"evidence": [fact("a", -50, entity='<script>alert(1)</script>'), fact("b", 80, entity="B")]})["groups"][0]
        svg = render_chart_svg(group)
        root = ElementTree.fromstring(svg)
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertIn("Bars start at zero", svg)
        self.assertIn("-50", svg)
        self.assertEqual("{http://www.w3.org/2000/svg}svg", root.tag)

    def test_broad_period_labels_do_not_merge_materially_different_durations(self):
        result = chart_data({"evidence": [fact("normal"), fact("short", 20, entity="SHORT", period_end="2024-03-12")]})
        self.assertEqual(2, len(result["groups"]))

    def test_market_adjustment_objects_keep_distinct_vintages(self):
        base = {"splits": "adjusted", "dividends": "none", "as_of": "2025-01-01T00:00:00Z"}
        records = [fact("old", metric="adjusted_close", period_start=None, adjustment=base),
                   fact("same", entity="B", metric="adjusted_close", period_start=None, adjustment=dict(reversed(list(base.items())))),
                   fact("new", entity="C", metric="adjusted_close", period_start=None,
                        adjustment={**base, "as_of": "2025-02-01T00:00:00Z"})]
        result = chart_data({"evidence": records})
        self.assertEqual([1, 2], sorted(len(g["points"]) for g in result["groups"]))
        for group in result["groups"]:
            self.assertIn("as_of", group["price_basis"])
            ElementTree.fromstring(render_chart_svg(group))


if __name__ == "__main__":
    unittest.main()
