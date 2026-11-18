import unittest

from verifin.discrete_tax_skill import analyze, answer_parts
from verifin.statement_analysis import Findings


def filing(discrete='(0.40)', other='', footer='2.10', cash=True):
    rows=[
        '<tr><th></th><th>Three Months Ended</th><th>Twelve Months Ended</th></tr>',
        '<tr><th>Per share</th><th>Dec 28, 2024</th><th>Dec 28, 2024</th></tr>',
        '<tr><td>GAAP diluted earnings per share</td><td>$0.60</td><td>$2.50</td></tr>',
        f'<tr><td>One-time tax settlement</td><td>—</td><td>{discrete}</td></tr>',
        other,
        f'<tr><td>Non-GAAP diluted earnings per share</td><td>$0.60</td><td>${footer}</td></tr>']
    explanation='<table><tr><td>One-time tax settlement</td><td>A discrete non-cash benefit was recognized.</td></tr></table>' if cash else ''
    doc='<html><head><title>Fourth Quarter and Full-Year 2024 Results</title></head><body><table>'+''.join(rows)+'</table>'+explanation+'</body></html>'
    return {'name':'exhibit.htm', 'source':'exhibit.htm','raw':doc.encode('utf-8'), 'url':'https://issuer.example/release',
            'entity':'TEST','published_at':'2025-01-28','period_end':'2024-12-28','snapshot_hash':'unit','source_form':'exhibit'}


class DiscreteTaxMethodTest(unittest.TestCase):
    def run_method(self,question,doc=None,**window):
        result=Findings()
        analyze(question,[],[doc or filing()],result,**window)
        return result

    def test_gain_is_removed_and_currency_and_cash_records_are_distinct(self):
        question='2024 全年这笔税影响每股多少，现金收到了吗？'
        r=self.run_method(question)
        self.assertEqual([calc['value'] for calc in r.calculations.values()],[2.10])
        answer=answer_parts({'one':question},r.notes,r.gaps)['one']
        self.assertEqual(answer['status'],'source_excerpt')
        self.assertEqual(len(answer['evidence_ids']),3)
        self.assertIn('非现金',answer['answer_sentence'])

    def test_cash_explanation_hidden_retains_arithmetic_and_marks_only_cash_unresolved(self):
        question='2024 全年每股扣除这笔税后是多少，现金收到了吗？'
        r=self.run_method(question,filing(cash=False))
        a=answer_parts({'mixed':question,'eps':'每股呢？','cash':'现金呢？'},r.notes,r.gaps)
        self.assertEqual([a[k]['status'] for k in ('mixed','eps','cash')],['partial','source_excerpt','gap'])
        self.assertIn("2.1",a['mixed']['answer_sentence'])
        self.assertIn("现金",a['mixed']['missing_support'])

    def test_document_q4_and_annual_share_end_date_but_not_adjustment(self):
        q='2024 年第四季度，那笔税影响每股多少？'
        r=self.run_method(q)
        self.assertFalse(r.calculations)
        self.assertTrue(r.gaps)
        self.assertEqual(answer_parts({'one':q},r.notes,r.gaps)['one']['status'],'gap')
        self.assertFalse(self.run_method('2024 全年这笔税影响每股多少？',period_start='2024-09-29',
                                         quarter_end='2024-12-28').calculations)

    def test_all_adjustments_are_reconciled_before_using_signed_effect(self):
        r=self.run_method('全年这笔税影响每股多少？',filing(discrete='0.40'))
        self.assertFalse(r.calculations)
        two=filing(other='<tr><td>Transition tax charge</td><td>—</td><td>0.10</td></tr>',footer='2.20')
        self.assertFalse(self.run_method('全年这笔税影响每股多少？',two).calculations)

    def test_former_year_cash_note_cannot_be_assigned_to_a_later_tax_item(self):
        doc=filing()
        doc["raw"]=doc["raw"].replace(b"was recognized.",b"was recognized in 2023.")
        r=self.run_method('2024 全年这笔所得税每股是多少，实际付现金了吗？',doc)
        self.assertEqual(len(r.calculations),1)
        self.assertTrue(r.gaps)
        self.assertNotIn("discrete_tax_cash",{n["kind"] for n in r.notes})

    def test_single_question_has_no_split_but_offline_finding_is_visible_in_the_report(self):
        from verifin.reporting import question_answers
        report = {
            "model_mode": "offline_tools_only",
            "request": "这笔税实际收到现金了吗？", "request_parts": {},
            "evidence": [{"id":"footnote","version":1}],
            "offline_part_findings":{"part-1":{"status":"source_excerpt","answer_sentence":"原文称该项非现金。",
                                               "evidence_ids":["footnote"]}},
        }
        self.assertEqual(question_answers(report)[0]["answer_sentence"],"原文称该项非现金。")
        report["evidence"][0]["stale"]=True
        self.assertEqual(question_answers(report)[0]["status"],"pending")
