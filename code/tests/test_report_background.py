import unittest

from verifin.reporting import background_claim_ids, render_markdown


class ReportBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.claim = {"id":"sales", "text":"Sales grew.", "status":"Supported", "evidence_ids":["sales-source"]}
        self.context = {"id":"locations", "text":"Stores opened.", "status":"Supported", "evidence_ids":["store-source"]}
        self.report = {"request_parts":{"a":"What happened to sales?", "b":"What drove growth?"},
                       "request_coverage":[{"part":"a", "status":"answered", "claim_id":"sales", "answer_sentence":"Sales grew."},
                                           {"part":"b", "status":"gap", "missing_support":"No contribution amount."}],
                       "verification_available":True, "claims":[self.context, self.claim]}

    def test_supported_context_follows_gaps_and_retains_its_refs(self):
        self.assertEqual(["locations"], background_claim_ids(self.report))
        main, context = render_markdown(self.report).split("## Supporting background")
        self.assertIn("No contribution amount.", main)
        self.assertIn("Sales grew.", main)
        self.assertNotIn("Stores opened.", main)
        self.assertIn("Stores opened.", context)
        self.assertIn("store-source", context)

    def test_legacy_single_question_or_unreviewed_report_does_not_demote_claims(self):
        for report in ({"claims":[self.claim], "verification_available":True},
                       dict(self.report, request_coverage=[]), dict(self.report, verification_available=False)):
            with self.subTest(report=report):
                self.assertEqual([], background_claim_ids(report))
                markdown = render_markdown(report)
                self.assertIn("Sales grew.", markdown)
                self.assertNotIn("## Supporting background", markdown)
