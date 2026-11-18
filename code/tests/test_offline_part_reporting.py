import unittest

from verifin.reporting import question_answers, render_markdown


class OfflinePartReporting(unittest.TestCase):
    def setUp(self):
        self.report = {
            "model_mode": "offline_tools_only", "verification_available": False,
            "request_parts": {"a": "营业日呢？", "b": "客户需求呢？"},
            "evidence": [{"id": "span_1", "version": 3}, {"id": "span_2", "version": 2}],
            "record_versions": {"evidence": {"span_1": 3, "span_2": 2}},
            "offline_part_findings": {
                "a": {"status": "source_excerpt", "answer_sentence": "原文列出了两期营业日。",
                      "quarter": "2023Q1", "evidence_ids": ["span_1", "span_2"]},
                "b": {"status": "gap", "missing_support": "当前材料缺分客户数据。",
                      "quarter": "2023Q1", "evidence_ids": []}},
        }

    def test_two_answers_with_distinct_status_and_traceable_sources(self):
        answer, gap = question_answers(self.report)
        self.assertEqual((answer["status"], gap["status"]), ("source_excerpt", "gap"))
        rendered = render_markdown(self.report)
        self.assertIn("原文列出了两期营业日。 [span_1, span_2]", rendered)
        self.assertIn("当前材料缺分客户数据。", rendered)
        self.assertNotIn("Pending verification.", rendered)

    def test_never_render_stale_wrong_version_or_missing_source_as_excerpt(self):
        for records in ([], [{"id": "span_1", "version": 3, "stale": True},
                             {"id": "span_2", "version": 2}],
                        [{"id": "span_1", "version": 1}, {"id": "span_2", "version": 2}]):
            with self.subTest(records=records):
                rows = question_answers(dict(self.report, evidence=records))
                self.assertEqual([r["status"] for r in rows], ["pending", "gap"])

    def test_offline_row_is_not_a_model_decision(self):
        rows = question_answers(dict(self.report, model_mode="live", verification_available=True))
        self.assertNotIn("原文列出了两期营业日。", str(rows))

    def test_partial_keeps_both_the_sourced_answer_and_the_unmeasured_item(self):
        finding = self.report["offline_part_findings"]["a"]
        finding.update(status="partial", missing_support="缺上年同期费用金额。")
        partial = question_answers(self.report)[0]
        self.assertEqual(("partial", finding["answer_sentence"], finding["missing_support"]),
                         (partial["status"], partial["answer_sentence"], partial["missing_support"]))
        self.report["evidence"][0]["stale"] = True
        self.assertNotIn("answer_sentence", question_answers(self.report)[0])

    def test_period_and_issuer_are_the_ones_under_review(self):
        for target, expected in (({"quarter": "2022Q4"}, ["pending", "pending"]),
                                 ({"quarter": "2023Q1", "entity": "OTHER"}, ["pending", "gap"])):
            with self.subTest(target=target):
                self.assertEqual([r["status"] for r in question_answers(
                    dict(self.report, skill_fiscal_quarter=target))], expected)
