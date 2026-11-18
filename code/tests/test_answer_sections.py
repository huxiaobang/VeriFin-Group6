import unittest

from verifin.answer_sections import answer_sections
from verifin.reporting import render_markdown, report_view


def operating_report():
    periods = [{"start":"2025-05-01","end":"2025-07-31"},{"start":"2024-05-01","end":"2024-07-31"}]
    evidence, calculations, rows = [], [], []
    url, table = "https://example.com/filing","/html/body/table[2]"
    for metric, role, amounts, sign in (("operating_income","operating",[180000,160000],1),
                                       ("tax","component",[70000,40000],-1),
                                       ("net_income","net",[110000,120000],1)):
        cur, prior, diff = (metric+s for s in ("-this","-prior","-effect"))
        for ref,period,amount in zip((cur,prior),periods,amounts):
            evidence.append(dict(id=ref,metric=metric,entity="NEW",value=amount,source_url=url,
                                 table_xpath=table,locator=table+"/td",currency="USD",unit="USD",
                                 snapshot_hash="snapshot",version=1,period_start=period["start"],period_end=period["end"]))
        refs = [cur,prior] if sign>0 else [prior,cur]
        calculations.append(dict(id=diff,operation="difference",entity="NEW",unit="USD",version=1,
                                 operand_ids=refs,value=(amounts[0]-amounts[1])*sign))
        rows.append(dict(metric=metric,role=role,label=metric,current_id=cur,prior_id=prior,contribution_id=diff,sign=sign))
    calculations.append(dict(id="reconciled",operation="sum",entity="NEW",unit="USD",version=1,
                             operand_ids=[r["contribution_id"] for r in rows[:2]],value=-10000))
    q = {"reason":"Why did operating income and earnings diverge?", "driver":"What mainly drove that?"}
    return dict(status="partial", scope={"entities":["NEW"]}, request_parts=q, evidence=evidence, calculations=calculations,
                summary="| bogus | $900 million |",verification_available=False, model_mode="offline_tools_only",
                income_bridge=dict(current_period=periods[0],prior_period=periods[1],source_url=url,table_locator=table,
                                   rows=rows,complete=True,sum_id="reconciled",total_id=rows[-1]["contribution_id"],
                                   currency="USD",part_ids=list(q)),
                offline_part_findings={k:{"status":"source_excerpt","answer_sentence":"本期两表列数据可核对。",
                                       "evidence_ids":[r["id"] for r in evidence],"calculation_ids":[r["id"] for r in calculations]}
                                       for k in q})


class AnswerSectionsTests(unittest.TestCase):
    def test_source_backed_table_is_rendered_once_across_a_shared_followup(self):
        report = operating_report()
        view = report_view(report)
        self.assertEqual([a["status"] for a in view["question_answers"]], ["calculated","calculated"])
        section, = view["answer_sections"]
        self.assertEqual(section["parts"],["reason","driver"])
        output = render_markdown(report)
        self.assertEqual(output.count("| 项目 |"),1)
        self.assertIn("| tax | 4 | 7 | -3 |",output)
        self.assertIn("净利润同比减少 1 万美元",output)
        self.assertIn("tax变动使净利润减少 3 万美元",output)
        self.assertIn("operating_income变动使净利润增加 2 万美元",output)
        self.assertNotIn("tax-effect",output.split("原表逐项核算",1)[0])
        self.assertIn("tax-effect",output.split("逐项证据与计算编号",1)[1])
        self.assertNotIn("bogus",output)
        self.assertFalse(view["verification_available"])

    def test_no_reuse_of_stale_wrong_period_or_forged_values_in_table(self):
        def stale(r):
            r["calculations"][0]["stale"]=True
        def date(r):
            r["evidence"][1]["period_start"]=r["evidence"][0]["period_start"]
        def mistaken_total(r):
            r["calculations"][-1]["value"]+=1
        def wrong_sign(r):
            r["income_bridge"]["rows"][1]["sign"]=1
        def stale_version(r):
            r["record_versions"]={"evidence":{i["id"]:3 for i in r["evidence"]}}
        for mutate in (stale,date,mistaken_total,wrong_sign,stale_version):
            with self.subTest(mutate=mutate.__name__):
                report=operating_report()
                mutate(report)
                self.assertNotIn("| 项目 |",render_markdown(report))

    def test_partial_and_unrelated_questions_keep_their_own_answers(self):
        report=operating_report()
        report["request_parts"]["driver"]="What was cash flow?"
        self.assertEqual(len(report_view(report)["answer_sections"]),2)
        report=operating_report()
        report["offline_part_findings"]["driver"].update(status="partial",missing_support="缺应缴税费明细")
        self.assertEqual(len(report_view(report)["answer_sections"]),2)
        self.assertIn("缺应缴税费明细",render_markdown(report))

    def test_ambiguous_or_unreviewed_explanations_do_not_merge(self):
        rows=[{"part":"a","status":"answered","question":"How much?","claim_id":"amount","answer_sentence":"Four."},
              {"part":"b","status":"answered","question":"Why?","claim_id":"reason","answer_sentence":"A tax change."}]
        self.assertEqual(len(answer_sections({},rows)),2)
        rows[1]["claim_id"]="amount"
        self.assertEqual(len(answer_sections({},rows)),2)
        rows[0]["status"]="partial"
        rows[0]["missing_support"]="Unknown additional items."
        rows[1]["claim_id"]="amount"
        self.assertEqual(len(answer_sections({},rows)),2)

    def test_an_absent_period_value_is_not_a_zero_or_an_inferred_contribution(self):
        report=operating_report()
        bridge=report["income_bridge"]
        bridge.update(complete=False,sum_id=None,gaps=["原表缺少上年同期 tax。"])
        missing=bridge["rows"][1]
        prior_ref, diff_ref=missing["prior_id"], missing["contribution_id"]
        missing.update(prior_id=None,contribution_id=None)
        report["evidence"]=[r for r in report["evidence"] if r["id"]!=prior_ref]
        report["calculations"]=[r for r in report["calculations"] if r["id"] not in {diff_ref,"reconciled"}]
        for finding in report["offline_part_findings"].values():
            finding.update(status="partial",missing_support=bridge["gaps"][0],
                           evidence_ids=[r["id"] for r in report["evidence"]],
                           calculation_ids=[r["id"] for r in report["calculations"]])
        section, = report_view(report)["answer_sections"]
        self.assertEqual(section["status"],"partial")
        self.assertEqual(section["source_table"]["rows"][1],["tax","—","7","—"])
        self.assertFalse(section["source_table"]["complete"])
        self.assertIn("净利润同比减少 1 万美元",section["source_table"]["summary"])
        self.assertIn("只能解释部分变动",section["source_table"]["summary"])
