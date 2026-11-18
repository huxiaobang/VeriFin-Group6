import unittest
from types import SimpleNamespace

from verifin.income_explanation import inspect


class EarningsCauseTests(unittest.TestCase):
    def setUp(self):
        # The major reported change is interest, not tax. This example also
        # checks quarter parsing for May and binding statements in dollars.
        rows = [
            ("operating", "operating_income", "Operating income", "营业利润", 10),
            ("component", "interest_expense", "Interest expense", "利息费用", -13),
            ("net", "net_income", "Net earnings", "净利润", -3),
        ]
        self.bridge = {"complete": True, "total_id":"net_income", "rows":[
            {"role":role,"metric":metric,"label":label,"display_label":display,"contribution_id":metric}
            for role,metric,label,display,_ in rows],
            "current_period":{"start":"2025-03-01","end":"2025-05-31"},
            "prior_period":{"start":"2024-03-01","end":"2024-05-31"}}
        self.calcs = [{"id":metric,"value":value} for _,metric,_,_,value in rows]
        self.doc = {"id":"quarter", "entity":"ABC", "document_report_date":"2025-05-31",
                    "published_at":"2025-06-20", "form":"10-Q","snapshot_hash":"source",
                    "text_snapshot_path":"original.txt","text_snapshot_hash":"hash"}

    def inspect(self, text, existing=(), search=True):
        corpus = SimpleNamespace(documents=[{**self.doc, "text":text}]) if search else None
        out = inspect(corpus,"ABC","2025-08-01",self.bridge,self.calcs,
                      existing_evidence=existing,search=search)
        return out, out["data"]["income_explanation"]

    def test_month_is_not_modal_and_cumulative_attribution_is_excluded(self):
        text = ("For the three months ended May 31, 2025, interest expense rose compared with last year due to higher debt. "
                "For the six months ended May 31, 2025, interest expense rose compared with last year due to higher rates.")
        out, reason = self.inspect(text)
        finding, = reason["findings"]
        self.assertEqual(finding["metric"],"interest_expense")
        self.assertIn("higher debt",finding["quote"])
        self.assertNotIn("higher rates",finding["quote"])
        self.assertEqual(out["evidence"][0]["text"],text[finding["quote_start"]:finding["quote_end"]])
        self.assertEqual(reason["documents_examined"],1)
        _, reuse = self.inspect(text,out["evidence"],False)
        self.assertEqual(reuse["documents_examined"],0)
        self.assertEqual(reuse["findings"][0]["evidence_id"],out["evidence"][0]["id"])

    def test_only_checked_period_metric_and_historical_causation_qualify(self):
        sentences = [
            "For the three months ended May 31, 2024, interest expense rose compared with last year due to higher debt.",
            "For the six months ended May 31, 2025, interest expense rose compared with last year due to higher debt.",
            "For the three months ended May 31, 2025, interest expense rose compared with last year. Gross margin increased due to new contracts.",
            "For the three months ended May 31, 2025, interest expense rose compared with last year. Interest expense may rise due to financing costs.",
            "Quarter ended May 31, 2025\nSecond Quarter Segment Results\nInterest expense increased compared with last year due to higher debt.",
            "Quarter ended May 31, 2025\nFull-year forecast\nInterest expense increased compared with last year due to higher borrowing costs.",
        ]
        for text in sentences:
            with self.subTest(text=text):
                _, reason = self.inspect(text)
                self.assertFalse(reason["findings"])
                self.assertEqual([t["metric"] for t in reason["gaps"]],["interest_expense"])

    def test_only_committed_current_entity_amounts_can_nominate_a_driver(self):
        text = "For the three months ended May 31, 2025, interest expense increased compared to the prior year due to higher rates."
        with self.subTest("stale contribution"):
            self.calcs[1]["stale"] = True
            _, result = self.inspect(text)
            self.assertFalse(result["targets"])
            self.assertEqual(result["documents_examined"],0)
        self.calcs[1]["stale"] = False
        for changes in ({"entity":"XYZ"},{"published_at":"2026-01-01"},{"document_report_date":"2025-02-28"}):
            with self.subTest(changes=changes):
                historical = self.doc
                self.doc = {**self.doc,**changes}
                _, result = self.inspect(text)
                self.assertFalse(result["findings"])
                self.doc = historical
