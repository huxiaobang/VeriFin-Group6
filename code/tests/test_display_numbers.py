import unittest

from verifin.display_numbers import display_check
from verifin.financial import VALUATION_ALLOWANCE_EXPENSE
from verifin.metric_language import metric_mentions


def fact(value, unit, record_id="value", **extra):
    return {"id":record_id,"kind":"financial_fact","value":value,"unit":unit,**extra}


def check(text, *records, **fields):
    return display_check({"text":text, **fields}, records)


class DisplayNumbersTests(unittest.TestCase):
    def test_tax_benefit_magnitude_is_bound_to_an_expense_signed_fact(self):
        expense = fact(-10_500_000, "USD", concept=VALUATION_ALLOWANCE_EXPENSE)
        asserted = dict(asserted_value=-10_500_000, asserted_unit="USD")
        release = ("转回金额为1,050万美元，全部作为2026年第三季度持续经营中的一次性所得税收益入账；"
                   "在所得税费用方向列示时，其符号为负数。")
        for text in (release, "所得税收益约为1,050万美元。", "The income tax benefit was $10.5 million."):
            with self.subTest(text=text):
                self.assertEqual(check(text, expense, **asserted)["method"], "matched_records")
        for text in (release.replace("负数", "正数"), release.replace("1,050", "1,051"), release.replace("美元", "欧元"),
                     release.replace("收益入账", "费用入账"), "所得税费用为1,050万美元。",
                     "The tax benefit was -$10.5 million."):
            with self.subTest(text=text):
                self.assertEqual(check(text, expense, **asserted)["method"], "mismatch")
        self.assertEqual(check(release, fact(-10_500_000, "USD"), **asserted)["method"], "mismatch")
        self.assertEqual(check(release, {**expense, "value":10_500_000})["method"], "mismatch")
        self.assertEqual(check(release, expense, asserted_value=10_500_000, asserted_unit="USD")["method"], "mismatch")

    def test_comparative_percentage_keeps_its_sign_and_printed_precision(self):
        growth = fact(19.929339971093626, "%")
        text = "当季普通股利润，仍比2025年第三季度已报普通股利润高约19.9%。"
        self.assertEqual(check(text, growth)["method"], "matched_records")
        for wrong in (text.replace("高约", "低约"), text.replace("19.9%", "20.0%"),
                      text.replace("19.9%", "19.9个百分点")):
            with self.subTest(text=wrong):
                self.assertEqual(check(wrong, growth)["method"], "mismatch")
        self.assertEqual(check(text.replace("高约", "低约"), {**growth, "value":-growth["value"]})["method"],
                         "matched_records")

    def test_displayed_amount_with_scaled_source_checks_digits_and_direction(self):
        source = fact(-45678, "USD thousands")
        exact = dict(asserted_value=-45678,asserted_unit="USD thousands")
        self.assertEqual(check("合计调减经营现金流45.68百万美元。",source,**exact)["method"],"matched_records")
        for sentence in ("合计调减经营现金流45.78百万美元。","合计为+45.68百万美元。"):
            self.assertEqual(check(sentence,source,**exact)["method"],"mismatch")

    def test_metric_terms_do_not_duplicate_fragments_of_longer_financial_labels(self):
        self.assertEqual(metric_mentions("营业收入和营业利润率，经营活动产生的现金流量净额"),{"revenue","operating_income_margin","operating_cash_flow"})
        self.assertEqual(metric_mentions("cost of revenue and cash flow from operations"),{"cost_of_revenue","operating_cash_flow"})
        self.assertEqual(metric_mentions("describe cash flow"),set())

    def test_wrong_metric_swaps_and_fabricated_second_amount_survive_no_citation_mix(self):
        ctx = {"entity":"XYZ","period_start":"2030-10-01","period_end":"2030-12-31","basis":"GAAP"}
        sales = fact(150,"USD","sales",metric="revenue",**ctx)
        profit = fact(80,"USD","op",metric="operating_income",**ctx)
        doc = {"id":"source","kind":"document","text":"Management commentary"}
        correct = check("Revenue was $150, and operating profit was $80.",sales,profit,doc,constraints=ctx)
        self.assertEqual([r["record_ids"] for r in correct["matched"]],[["sales"],["op"]])
        self.assertEqual(correct["method"],"matched_records")
        for draft in ("营业收入为80美元，营业利润为150美元。","营业收入为150美元，营业利润为90美元。"):
            for cites in ((sales,profit), (sales,profit,doc)):
                with self.subTest(draft=draft,doc=len(cites)):
                    self.assertEqual(check(draft,*cites,constraints=ctx,asserted_value=150,asserted_unit="USD")["method"],"mismatch")
        # A narrative about different scopes can legitimately use other figures.
        self.assertEqual(check("预计营业收入为80美元，营业利润为150美元。",sales,profit,doc,constraints=ctx)["method"],"source_review")
        self.assertEqual(check("营业收入为80美元，管理层预计下季营业利润为150美元。",sales,profit,doc,constraints=ctx)["method"],"mismatch")

    def test_explicit_current_and_comparison_period_must_remain_attached_to_their_own_amount(self):
        ctx = {"entity":"XYZ","period_start":"2030-10-01","period_end":"2030-12-31"}
        now = fact(150,"USD","now",metric="revenue",**ctx)
        old = fact(125,"USD","old",metric="revenue",entity="XYZ",period_start="2029-10-01",period_end="2029-12-31")
        for current_prefix,old_prefix in (("本季","上年同期"),("截至2030-12-31的季度","截至2029-12-31的季度")):
            text = f"{current_prefix}营业收入为125美元，{old_prefix}营业收入为150美元。"
            result = check(text,now,old,constraints=ctx)
            self.assertEqual([r["record_ids"] for r in result["expected"]],[["now"],["old"]])
        # Non-calendar-matched fiscal periods require an actual fiscal alignment.
        leap = dict(old,period_start="2029-09-28",period_end="2029-12-29")
        self.assertEqual(check("本季营业收入为150美元，上年同期营业收入为125美元。",now,leap,constraints=ctx)["method"],"source_review")

    def test_exact_contradiction_with_and_without_asserted_value(self):
        revenue = fact(69_632_000_000, "USD")
        for fields in ({}, {"asserted_value":revenue["value"],"asserted_unit":"USD"}):
            with self.subTest(fields=fields):
                result = check("季度营业收入为 79.632 十亿美元。", revenue, **fields)
                self.assertEqual(result["method"], "mismatch")
                self.assertIn("79.632 十亿美元", result["unmatched"])

    def test_equivalent_scales_display_precision_and_currency(self):
        revenue = fact(69_632_000_000, "USD")
        for text in ("USD 69.632 billion", "$69.63bn", "$69.632B", "696.32 亿美元", "69,632 百万美元",
                     "69,632,000 thousand USD", "FY2024 Q3 revenue was $69.6 billion."):
            with self.subTest(text=text):
                result = check(text, revenue)
                self.assertEqual(result["matched"][0]["record_ids"], ["value"])
                self.assertEqual(result["method"], "matched_records")
        for text in ("$69.64 billion", "696.32 亿元人民币", "69,632 thousand USD", "EUR 69.632 billion"):
            with self.subTest(text=text):
                self.assertEqual(check(text,revenue)["method"], "mismatch")

    def test_percentage_points_signed_changes_and_relative_percent(self):
        change = fact(-1.1, "percentage_points")
        for text in ("下降了约1.1个百分点", "down 110 basis points", "−110bp", "down 1.1pp", "declined by 1.1 percentage points"):
            with self.subTest(text=text):
                self.assertEqual(check(text,change)["method"], "matched_records")
        self.assertEqual(check("下降1.1%",change)["method"], "mismatch")
        self.assertEqual(check("增长1.1个百分点",change)["method"], "mismatch")
        self.assertEqual(check("Revenue grew (14.1%).",fact(14.1,"%"))["method"], "matched_records")
        self.assertEqual(check("Change (14.1%).",fact(-14.1,"%"))["method"], "source_review")
        self.assertEqual(check("Sales hit a new record ($12 million).",fact(12_000_000,"USD"))["method"], "source_review")

    def test_a_rejected_rate_is_visible_to_review_and_cannot_support_an_assertion(self):
        record = fact(1.245121274152021, "percentage_points")
        for text in ("利润率提高约1.25个百分点；用未取整的数值相减，并非增长1.25%。",
                     "并非增长1.25%，营业利润率提高1.25个百分点。",
                     "The margin rose by 1.25 percentage points, not 1.25%.",
                     "The margin rose by 125 basis points rather than an increase of 1.25%."):
            with self.subTest(text=text):
                result = check(text, record)
                self.assertEqual(result["method"], "matched_records")
                self.assertEqual(result["matched"][0]["record_ids"], ["value"])
                excluded, = result["negated_displays"]
                self.assertEqual(text[excluded["start"]:excluded["end"]], "1.25%")
        self.assertEqual(check("并非提高1.25个百分点。", record)["method"], "source_review")
        for text in ("提高1.25%，而非1.25个百分点。", "提高1.25个百分点，并非增长1.25%，实际增长了9%。",
                     "The increase was not only 1.25%.", "同比增长不少于1.25%。"):
            with self.subTest(text=text):
                self.assertEqual(check(text, record)["method"], "mismatch")

    def test_eps_losses_and_per_share_units(self):
        eps = fact(-4.38,"USD/share")
        for text in ("EPS was -$4.38.", "每股收益为−4.38美元", "EPS为负4.38美元", "每股亏损为4.38美元", "USD -4.38 per diluted share",
                     "Diluted EPS was ($4.38 per share)."):
            with self.subTest(text=text):
                self.assertEqual(check(text,eps)["method"], "matched_records")
        self.assertEqual(check("EPS was $4.38",eps)["method"], "mismatch")
        self.assertEqual(check("Net loss was USD -4.38",eps)["method"], "mismatch")

    def test_paragraph_cannot_become_a_machine_numeric_anchor(self):
        doc = {"kind":"document","id":"p","text":"Revenue was $1,824.5 million; growth was 3.7%."}
        self.assertEqual(check("2024 Q4 revenue was $1,824.5 million.",doc)["method"], "source_review")
        self.assertEqual(check("增长3.7%。",doc, asserted_value=4.1, asserted_unit="%")["method"], "mismatch")
        self.assertEqual(check("本季有62个营业日。",doc)["method"], "source_review")
        self.assertEqual(check("管理层指出客户需求疲软。",doc), {})
        mixed = check("销售额10亿美元，增长3.7%。", fact(10**9,"USD"),doc)
        self.assertEqual(mixed["method"], "source_review")
        self.assertEqual(mixed["matched"][0]["record_ids"], ["value"])

    def test_explicit_context_and_multi_amounts(self):
        current = fact(15,"USD","new",period_end="2024-12-31")
        prior = fact(10,"USD","old",period_end="2023-12-31")
        self.assertEqual(check("2024 sales were $10", current, prior,constraints={"period_end":"2024-12-31"})["method"], "mismatch")
        multi = check("Sales rose from $10 to $15.", current, prior, asserted_value=15,asserted_unit="USD")
        self.assertEqual(multi["method"], "source_review")
        self.assertEqual([m["record_ids"] for m in multi["matched"]], [["old"], ["new"]])
        self.assertEqual(check("Amounts were $10 and $10.", current, prior, asserted_value=15,
                               asserted_unit="USD")["method"], "mismatch")
        self.assertEqual(check("Sales were $15, up 25%.", current, fact(25,"%","growth"))["method"], "matched_records")

    def test_ranges_identifiers_and_periods_are_not_independent_anchors(self):
        self.assertEqual(check("公司预计增长10–12%。",fact(12,"%"))["method"], "source_review")
        self.assertEqual(check("FY2024 Q3 2024-10-01.",fact(2024,"USD")), {})
        self.assertEqual(check("FY2024 growth.",fact(2024,"USD"),asserted_value=2024,asserted_unit="USD")["method"],"source_review")

    def test_reported_table_uses_the_named_period_column_and_the_document_scale(self):
        doc = {"kind":"document","id":"issuer-doc","entity":"EXAMPLE","form":"8-K",
               "document_report_date":"2030-12-31","text":
               "Results for the year and quarter ended December 31, 2030. U.S. dollar amounts are stated in millions.\n"
               "Three-month Period Twelve-month Period\n2030 2029 Change 2030 2029 Change\n"
               "Operating income $ 23.5 20.0 17.5 % $ 110.0 100.0 10.0 %"}
        scope = {"metric":"operating_income","entity":"EXAMPLE","currency":"USD",
                 "period_start":"2030-10-01","period_end":"2030-12-31"}
        right = "2030 Q4 operating income was $23.5 million."
        wrong = "2030 Q4 operating income was $110.0 million."
        proven = check(right,doc,constraints=scope)
        self.assertEqual(proven["method"], "matched_printed_cell")
        self.assertEqual((proven["printed_cell"]["group"],proven["printed_cell"]["printed"]),("three-month period","23.5"))
        self.assertEqual(check(wrong,doc,constraints=scope)["method"],"mismatch")
        self.assertEqual(check(right.replace("23.5","23.49"),doc,constraints=scope)["method"],"source_review")
        prior_year_wrong = right.replace("23.5","20.0")
        self.assertEqual(check(prior_year_wrong,doc,constraints=scope)["method"],"mismatch")
        annual = {**scope,"period_start":"2030-01-01"}
        self.assertEqual(check("FY2030 operating income was $110 million.",doc,constraints=annual)["method"],"matched_printed_cell")
        for source in (doc["text"].replace("in millions","in unspecified units"),
                       doc["text"].replace("2030 2029 Change","2029 2030 Change"),
                       doc["text"]+"\n"+doc["text"].splitlines()[-1],
                       doc["text"].replace("U.S. dollar","Canadian dollar"),
                       doc["text"].replace("Three-month Period","another period")):
            with self.subTest(source=source):
                self.assertEqual(check(wrong,{**doc,"text":source},constraints=scope)["method"],"source_review")
        self.assertEqual(check("Management estimates 2030 Q4 operating income was $110 million.",doc,
                               constraints=scope)["method"],"source_review")
