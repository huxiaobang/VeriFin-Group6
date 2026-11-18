import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from verifin.reporting import export_report, question_answers, render_markdown, report_view


class DeliveryViewTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "request_parts": {"amount": "How much?", "cause": "Why?"},
            "scope": {"requirements": [{"id": "r1", "text": "How much?"}]},
            "status": "partial", "verification_available": False,
            "evidence": [{"id": "prior"}, {"id": "current"}],
            "calculations": [{"id": "calculation"}],
            "episode_material_ids": {"evidence": ["current"], "calculations": ["calculation"]},
        }
        self.draft = {"id": "draft", "text": "Revenue was $40.", "requirement_ids": ["r1"],
                      "evidence_ids": ["current"], "calculation_ids": ["calculation"]}

    def test_retrieval_halt_reports_no_draft_and_keeps_current_and_prior_material_distinct(self):
        report = {**self.report, "delivery_stage": "no_draft",
                  "stop_reason": "repeated_retrieval_without_new_evidence"}
        rows = question_answers(report)
        self.assertEqual(["no_draft", "no_draft"], [r["status"] for r in rows])
        self.assertTrue(all("answer_sentence" not in r and "evidence_ids" not in r for r in rows))
        markdown = render_markdown(report)
        self.assertIn("重复检索未取得新证据", markdown)
        self.assertIn("本轮留存 1 条证据、1 项计算", markdown)
        self.assertIn("**current**（本轮留存）", markdown)
        self.assertIn("**calculation**（本轮留存）", markdown)
        self.assertIn("**prior**", markdown)
        self.assertNotIn("**prior**（本轮留存）", markdown)
        self.assertNotIn("重复检索", render_markdown({**report, "stop_reason": "cancelled"}))

    def test_draft_and_rejected_receipt_do_not_promote_draft_or_transfer_it_to_another_question(self):
        for stage, reason, detail in [
                ("draft_unverified", "deterministic_check_requires_inspection", "交给 Critic 前停止"),
                ("critic_receipt_invalid", "execution_error", "回执中的问题类别无法识别")]:
            with self.subTest(stage=stage):
                report = {**self.report, "delivery_stage": stage, "stop_reason": reason,
                          "critic_receipt_error": "unknown_issue_category", "claims": [self.draft]}
                view = report_view(report)
                amount, cause = view["question_answers"]
                self.assertEqual([stage, stage], [r["status"] for r in (amount, cause)])
                self.assertEqual(["draft"], amount["draft_claim_ids"])
                self.assertNotIn("draft_claim_ids", cause)
                self.assertFalse(any("answer_sentence" in r or "claim_id" in r for r in (amount, cause)))
                markdown = render_markdown(view)
                self.assertIn(detail, markdown)
                self.assertIn("### draft — 未核验草稿", markdown)
                self.assertIn("Revenue was $40.", markdown)

    def test_single_question_and_ambiguous_archives_do_not_infer_a_critic_call(self):
        report = {"request": "What drove costs?", "verification_available": False,
                  "stop_reason": "execution_error", "usage": {"model_calls": 4}, "evidence": [{"id": "historical"}]}
        view = report_view(report)
        row, = view["question_answers"]
        self.assertEqual(("pending", "unknown", report["request"]), (row["status"], row["delivery_stage"], row["question"]))
        with TemporaryDirectory() as directory:
            display = {**report, "delivery_stage": "critic_receipt_invalid", "critic_receipt_error": "invalid_critic_json"}
            export_report(report, directory, display_report=display)
            markdown = (Path(directory) / "report.md").read_text()
            self.assertIn("Critic 已返回核验回执", markdown)
            self.assertIn("回执文本无法解析为有效的 JSON。", markdown)
            self.assertEqual(report, json.loads((Path(directory) / "report.json").read_text()))

    def test_accepted_review_with_a_bad_issue_category_keeps_verified_results(self):
        issue = {"target_id": "other", "issue_type": "invalid_record", "missing_support": "A source is missing.",
                 "contract_diagnostic": {"code": "unknown_issue_category"}}
        report = {**self.report, "verification_available": True, "delivery_stage": "reviewed",
                  "issues": [issue], "claims": [{**self.draft, "status": "Supported"}],
                  "request_coverage": [{"part": "amount", "status": "answered",
                                        "claim_id": "draft", "answer_sentence": self.draft["text"]}]}
        view = report_view({**report_view(self.report), **report})
        self.assertEqual("answered", view["question_answers"][0]["status"])
        self.assertNotIn("stage_message", view)
        self.assertIn("本条核验意见的类别无法识别", render_markdown(report))
        self.assertNotIn("Critic 已返回核验回执", render_markdown(report))
        self.assertNotIn("display_type", issue)
