from itertools import permutations
import unittest

from verifin.context import history_report_views
from verifin.reporting import question_answers, render_markdown, report_view
from verifin.request_review import coverage_issues, single_question_mismatch
from verifin.storage import _report_summary, SessionStore


class AnswerBindingTests(unittest.TestCase):
    def test_issuer_prefixed_quarter_must_answer_the_requested_period(self):
        question = "微软 2025 财年第二季度营收是多少？"
        def mismatch(text):
            return single_question_mismatch(question, [{"text":text, "constraints":{"entity":"MSFT"}}])
        self.assertTrue(mismatch("微软2025 财年第一季度营收为 655.85 亿美元。"))
        self.assertTrue(mismatch("MSFT FY2025Q1 revenue was $65.585 billion."))
        self.assertFalse(mismatch("微软2025 财年第二季度营收为 696.32 亿美元。"))
        self.assertFalse(mismatch("本季营收为 696.32 亿美元，较 2024 财年第二季度增加。"))
        self.assertFalse(mismatch("2024 财年第二季度为基期，本季营收为 696.32 亿美元。"))

    def test_permuting_question_order_and_answer_links_only_credits_correct_links(self):
        names = {"revenue":"营业收入","operating_income":"营业利润","operating_cash_flow":"经营现金流"}
        claims = {m:{"id":"claim-"+m,"text":f"{label}为 1 亿美元。","constraints":{"metric":m},
                     "status":"Supported","requirement_ids":["req-"+m]} for m,label in names.items()}
        requirements = [{"id":"req-"+m,"text":f"{label}是多少？"} for m,label in names.items()]
        for question_order in permutations(names):
            parts = {f"part-{i}":f"{names[m]}是多少？" for i,m in enumerate(question_order,1)}
            for link_order in permutations(names):
                rows = [{"part":part,"status":"answered","claim_id":claims[m]["id"],"answer_sentence":claims[m]["text"]}
                        for part,m in zip(parts,link_order)]
                expected = {part for part,asked,answered in zip(parts,question_order,link_order) if asked != answered}
                with self.subTest(question_order=question_order,link_order=link_order):
                    self.assertEqual({i["target_id"] for i in coverage_issues(parts,{"request_coverage":rows},
                        list(claims.values()),requirements=requirements)},expected)
                    report = {"request_parts":parts, "request_coverage":rows, "claims":list(reversed(claims.values())),
                              "scope":{"requirements":requirements},"verification_available":True}
                    self.assertEqual({r["part"] for r in question_answers(report) if r["status"]=="gap"},expected)
                    summary = _report_summary(report)
                    eligible = {row["claim_id"] for row in rows if row["part"] not in expected}
                    if eligible:
                        self.assertIn(summary["summary_claim_id"],eligible)
                    else:
                        self.assertNotIn("summary",summary)

    def test_unsupported_checklist_item_with_a_true_background_claim(self):
        question = "Explain the evidence for improving end demand."
        requirements = [{"id":"need-demand","text":question},{"id":"need-source","text":"Identify the source."}]
        claim = {"id":"background","text":"The company disclosed sales.","requirement_ids":["need-source"],"status":"Supported"}
        row = {"part":"part-1","status":"answered","claim_id":"background","answer_sentence":claim["text"]}
        result = coverage_issues({"part-1":question},{"request_coverage":[row]},[claim],requirements=requirements)
        self.assertEqual([r["target_id"] for r in result],["part-1"])
        report = {"scope":{"requirements":requirements},"claims":[claim],"verification_available":True,
                  "request_parts":{"part-1":question},"request_coverage":[row],"unanswered":[requirements[0]]}
        self.assertEqual(question_answers(report)[0]["status"],"gap")

    def test_direct_question_check_leaves_comparisons_and_limits_of_inference_to_review(self):
        wrong = {"id":"c","text":"Operating income was USD 42 million.","constraints":{"metric":"operating_income"}}
        self.assertTrue(single_question_mismatch("What was revenue?",[wrong]))
        self.assertFalse(single_question_mismatch("Why was revenue higher than operating income?",[wrong]))
        self.assertFalse(single_question_mismatch("What was the contribution of pricing to revenue?",[wrong]))
        self.assertFalse(single_question_mismatch("What was revenue?",[
            {"id":"c","text":"Revenue was not separately disclosed.","constraints":{}}]))
        self.assertTrue(single_question_mismatch("经营现金流是多少？",[{"text":"营业收入为69.632十亿美元。","constraints":{}}]))
        self.assertFalse(single_question_mismatch("营收同比增长了百分之多少？",[
            {"text":"本季营收增速为12.3%。","constraints":{"metric":"revenue_growth"}}]))
        self.assertTrue(single_question_mismatch("这两个期间的营业利润率为什么不同？",[wrong]))
        self.assertFalse(single_question_mismatch("这两个期间的营业利润率为什么不同？",[
            {**wrong,"text":wrong["text"]+" The company attributed the change to higher material costs."}]))

    def test_archive_keeps_original_verdict_while_new_handoffs_surface_a_mapping_gap(self):
        # A report as saved by the previous controller, which accepted swapped rows.
        q = "What was operating cash flow?"
        claim = {"id":"sales","text":"Revenue was USD 10 million.","constraints":{"metric":"revenue"},
                 "requirement_ids":["sales-q"],"status":"Supported"}
        report = {"request":q,"request_parts":{"part-1":q},"claims":[claim],"status":"complete","stop_reason":"completed",
                  "issues":[],"unanswered":[],"verification_available":True,
                  "request_coverage":[{"part":"part-1","status":"answered","claim_id":"sales","answer_sentence":claim["text"]}]}
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            original = store.save_report(sid,report)
            current = report_view(original)
            archived = store.get_report(sid,1)
            history = history_report_views({"evidence":[],"calculations":[],"reports":[archived]})[0]
            for view in (current,history,store.list_report_summaries(sid)[0]):
                self.assertEqual((view["status"],view["archived_status"]),("partial","complete"))
            self.assertEqual(current["question_answers"][0]["status"],"gap")
            self.assertEqual(history["request_coverage"][0]["status"],"gap")
            self.assertIn("**Status:** partial",render_markdown(archived))
            self.assertEqual(archived,original)
