import copy
import unittest

from verifin.request_review import check_comparison_coverage
from verifin.sales_calendar import calendar_growth_rates
from verifin.verification import check_rules


SALES = "2025Q1，总销售额与日均销售额的同比增速为什么不同？"
DEMAND = "排掉营业日影响，客户需求普遍回暖了吗？"
DIRECTION = "多一个营业日使总销售额同比增速高于日均销售额同比增速。"
DEMAND_ANSWER = "两组客户的日均销售增速分化，无法判断客户需求普遍回暖。"


def review_fixture(text=DIRECTION, cite=False):
    a, b = "2025Q1", "2024Q1"
    values = [1910.2, 1845.9, 64, 63]
    gap = calendar_growth_rates(*values)[-1]
    table = {"id":"table", "kind":"document", "entity":"EX", "source_url":"https://example.test",
             "locator":"lines 3–7", "snapshot_hash":"snapshot", "published_at":"2025-05-01"}
    cells = [{**table, "id":ref, "metric":metric, "unit":unit, "fiscal_quarter":period,
              "value":value, "table_evidence_id":"table"}
             for (ref, metric, unit, period), value in zip(
                 [("s1", "net_sales", "reported_amount", a), ("s0", "net_sales", "reported_amount", b),
                  ("d1", "business_days", "days", a), ("d0", "business_days", "days", b)], values)]
    metric = "sales_calendar_growth_gap"
    calculation = {"id":"bridge", "operation":metric, "metric":metric, "value":float(gap),
                   "fiscal_quarter":a, "comparison_quarter":b, "unit":"percentage_points",
                   "entity":"EX", "operand_ids":[r["id"] for r in cells], "table_evidence_id":"table"}
    questions = {"part-1":SALES, "part-2":DEMAND}
    receipt = {"tool":"inspect_sales_comparison", "ok":True, "arguments":{"entity":"EX"},
               "data":{"fiscal_quarter":a, "comparison_quarter":b, "part_findings":{
                   "part-1":{"status":"source_excerpt", "calculation_ids":["bridge"]},
                   "part-2":{"status":"source_excerpt"}}}}
    ctx = {"scope":{"entities":["EX"]}, "request_parts":questions, "calculations":[calculation],
           "episode":{"request":"上一季呢？", "recent_tool_results":[receipt]},
           "evidence":[table, *cells], "requirements":[{"id":k, "text":v} for k, v in (("r1", SALES), ("r2", DEMAND))],
           "claims":[{"id":"c1", "text":text, "evidence_ids":["table"], "calculation_ids":["bridge"] if cite else [],
                      "requirement_ids":["r1"]},
                     {"id":"c2", "text":DEMAND_ANSWER, "evidence_ids":["table"], "calculation_ids":[],
                      "requirement_ids":["r2"]}]}
    rows = [{"part":"part-1","status":"answered","claim_id":"c1","answer_sentence":text},
            {"part":"part-2","status":"answered","claim_id":"c2","answer_sentence":DEMAND_ANSWER}]
    return ctx, rows


class CalendarReviewTests(unittest.TestCase):
    def test_passed_direction_only_or_rates_only_loses_only_sales_question(self):
        for text in (DIRECTION, "营业日同比多一天，总销售额增长3.5%，日均销售额增长1.9%。"):
            with self.subTest(text=text):
                ctx, rows = review_fixture(text)
                issues = check_rules(ctx)
                self.assertEqual([(i["target_id"], i["issue_type"], i["evidence_refs"]) for i in issues],
                                 [("r1", "omission", ["bridge"])])
                self.assertEqual(issues[0]["scope"], "question_coverage")
                checked, _ = check_comparison_coverage(ctx, rows)
                self.assertEqual([r["status"] for r in checked], ["gap", "answered"])
                self.assertIn("1.62", checked[0]["missing_support"])

    def test_valid_bridge_allows_rounded_points_or_equivalent_basis_points(self):
        for amount in ("约 1.6 个百分点", "1.62 个百分点", "约162个基点", "约 162 bps", "约160基点"):
            with self.subTest(amount=amount):
                ctx, rows = review_fixture("多一个营业日使两项增速相差" + amount + "。", cite=True)
                self.assertEqual(check_rules(ctx), [])
                self.assertEqual(check_comparison_coverage(ctx, rows), (rows, None))

    def test_value_unit_precision_citation_and_question_binding_matter(self):
        for text, cited in (("两种增速相差约1.62个百分点，原因是多一个营业日。", False),
                            ("多一个营业日使增速相差1.62%。", True),
                            ("多一个营业日使增速相差1.60个百分点。", True),
                            ("多一个营业日的贡献精确为1.6个百分点。", True),
                            ("多一个营业日，毛利率提高1.62个百分点。", True),
                            ("多一个营业日使总额减日均的增速差为-1.62个百分点。", True)):
            with self.subTest(text=text, cited=cited):
                ctx, rows = review_fixture(text, cite=cited)
                self.assertEqual(check_comparison_coverage(ctx, rows)[0][0]["status"], "gap")
        ctx, rows = review_fixture()
        ctx["claims"][1].update(text="营业日导致增速相差约1.62个百分点。", calculation_ids=["bridge"])
        self.assertEqual(check_comparison_coverage(ctx, rows)[0][0]["status"], "gap")

    def test_answer_sentence_must_include_the_result_even_if_a_sibling_sentence_has_it(self):
        ctx, rows = review_fixture(DIRECTION + "增速差约1.62个百分点。", cite=True)
        self.assertFalse(check_rules(ctx))
        rows[0]["answer_sentence"] = DIRECTION
        self.assertEqual(check_comparison_coverage(ctx, rows)[0][0]["status"], "gap")
        rows[0]["answer_sentence"] = "增速差约1.62个百分点。"
        self.assertEqual(check_comparison_coverage(ctx, rows), (rows, None))

    def test_explicit_higher_and_lower_must_agree_with_the_signed_gap(self):
        for sign in (1, -1):
            for words, direction in (("总额增速比日均增速低约", -1), ("日均增速比总额增速低约", 1),
                                     ("总额增速比日均增速高约", 1), ("日均增速比总额增速高约", -1)):
                with self.subTest(sign=sign, words=words):
                    ctx, rows = review_fixture(cite=True)
                    if sign < 0:
                        ctx["evidence"][-2]["value"], ctx["evidence"][-1]["value"] = 63, 64
                        ctx["calculations"][0]["value"] = float(calendar_growth_rates(1910.2,1845.9,63,64)[-1])
                    text = "营业日不同，" + words + f'{abs(ctx["calculations"][0]["value"]):.2f} 个百分点。'
                    rows[0]["answer_sentence"] = ctx["claims"][0]["text"] = text
                    expected = "answered" if sign == direction else "gap"
                    self.assertEqual(check_comparison_coverage(ctx, rows)[0][0]["status"], expected)
        for display, status in (("-1.62", "answered"), ("1.62", "gap")):
            ctx, rows = review_fixture(f"营业日差异下，日均增速减总额增速为{display}个百分点。", cite=True)
            self.assertEqual(check_comparison_coverage(ctx, rows)[0][0]["status"], status)

    def test_evidence_only_mode_has_no_draft_to_fault(self):
        ctx, _ = review_fixture()
        ctx["claims"] = []
        self.assertFalse(any(i["evidence_refs"] == ["bridge"] for i in check_rules(ctx)))

    def test_single_daily_metric_does_not_create_a_total_vs_daily_obligation(self):
        ctx, rows = review_fixture()
        ctx["request_parts"]["part-1"] = "各市场日均净销售额增速为什么不同？"
        self.assertEqual(check_comparison_coverage(ctx, rows), (rows, None))

    def test_single_question_and_evidence_boundaries(self):
        ctx, rows = review_fixture()
        ctx.pop("request_parts")
        ctx["episode"]["request"] = SALES
        self.assertIn("1.62", check_comparison_coverage(ctx, [])[1])
        for change in ("qualitative", "stale", "other quarter", "other issuer", "no calculation", "missing data"):
            with self.subTest(change=change):
                altered = copy.deepcopy(ctx)
                if change == "qualitative":
                    altered["episode"]["request"] += "只需定性解释。"
                elif change == "stale":
                    altered["calculations"][0]["stale"] = True
                elif change == "other quarter":
                    altered["episode"]["request"] = SALES.replace("2025Q1", "2024Q1")
                elif change == "other issuer":
                    altered["fiscal_target"] = {"entity":"OTHER"}
                elif change == "no calculation":
                    altered["calculations"] = []
                else:
                    altered["episode"]["recent_tool_results"][0]["data"]["part_findings"]["part-1"]["status"] = "gap"
                self.assertEqual(check_comparison_coverage(altered, []), ([], None))

    def test_calculation_is_recomputed_and_operand_order_checked(self):
        ctx, _ = review_fixture("多一个营业日使增速相差约1.62个百分点。", cite=True)
        ctx["calculations"][0]["value"] += .01
        self.assertIn("numerical_inconsistency", {i["issue_type"] for i in check_rules(ctx)})
        ctx["calculations"][0]["operand_ids"][:2] = ["s0", "s1"]
        self.assertIn("context_mismatch", {i["issue_type"] for i in check_rules(ctx)})
