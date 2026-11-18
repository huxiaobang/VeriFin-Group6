import unittest

from verifin.offline_attribution import findings, initial_search


class OfflineAttributionTests(unittest.TestCase):
    def setUp(self):
        self.prior = {"scope":{"entities":["ACME"], "period_end":"2025-05-31"}, "claims":[{
            "status":"Supported", "constraints":{"period_end":"2025-05-31"},
            "text":"These are results for fiscal 2025 third quarter.",
            "evidence_ids":["cell"], "evidence_versions":{"cell":1}}]}
        self.ctx = {"scope":{"entities":["ACME"], "cutoff":"2025-07-01"},
                    "episode":{"request":"Why did revenue rise in FY2025 Q3?"},
                    "evidence":[{"id":"cell","kind":"document","entity":"ACME","version":1,
                                 "document_id":"release","document_report_date":"2025-05-31"}]}

    def test_search_anchored_in_supported_current_source(self):
        self.assertEqual(initial_search(self.ctx, self.prior)["document_ids"], ["release"])
        self.ctx["episode"]["request"] = "Why did revenue rise in FY2025 Q2?"
        self.assertIsNone(initial_search(self.ctx, self.prior))
        self.ctx["episode"]["request"] = "Why did revenue rise in FY2025 Q3?"
        self.ctx["evidence"][0]["version"] = 2
        self.assertIsNone(initial_search(self.ctx, self.prior))

    def test_extract_requires_visible_verbatim_quote(self):
        row = self.ctx["evidence"][0]
        row.update(text="Revenue rose because volumes increased.", source_url="https://example.org/release")
        self.ctx["issuer_attribution"] = {"metric":"revenue","candidates":[{"quote":row["text"],"evidence_id":"cell"}]}
        self.assertEqual(findings(self.ctx)["part-1"]["status"], "source_excerpt")
        row["text"] = "Revenue rose."
        self.assertEqual(findings(self.ctx)["part-1"]["status"], "gap")
