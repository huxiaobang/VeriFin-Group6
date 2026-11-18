"""Regression cases for confusing a segment subtotal or YTD cell with a group quarter."""
import unittest

from verifin.segment_skill import analyze, answer_parts
from verifin.statement_analysis import Findings


def doc(missing=False, group_profit=18):
    def row(*values):
        return '<tr>'+''.join(f'<td>{v}</td>' for v in values)+'</tr>'
    header = (row('', 'Three Months Ended', 'Nine Months Ended')
              + row('', 'Sep 30, 2025', 'Sep 30, 2025'))
    operations = '<p>Consolidated Statements of Operations</p><p>USD in millions</p><table>'
    operations += header + row('Revenue', 130, 360) + row('Operating Income',group_profit,58) + '</table>'
    parts = '<p>Segment Information</p><p>USD in millions</p><table>'
    parts += header + row('Revenue') + row('Alpha',50,150) + row('Beta',80,210) + row('Total',130,360)
    parts += row('Operating income') + row('Alpha',10,30) + row('Beta',11,33)
    parts += row('Unallocated corporate expense','' if missing else '(3)','(5)') + row('Total',18,58) + '</table>'
    return {'entity':'TST', 'filename':'exhibit.htm', 'url':'https://example.test/earnings','source':'exhibit.htm',
            'raw':('<html><body>'+operations+parts+'</body></html>').encode(),
            'published_at':'2025-10-16', 'period_start':'2025-07-01', 'period_end':'2025-09-30',
            'source_form':'10-Q','snapshot_hash':'fixture'}


class SegmentMethodTest(unittest.TestCase):
    def check_findings(self, source, question='各分部加起来和集团一样吗？', **window):
        f=Findings()
        analyze(question,['这家公司最近一季表现怎么样？'],[source],f,**window)
        return f

    def test_read_independent_statement_and_same_end_quarter(self):
        f=self.check_findings(doc())
        self.assertEqual(len(f.notes),2)
        sums={c['value'] for c in f.calculations.values() if c['operation']=='sum'}
        self.assertTrue({130,18,21,-3} <= sums)
        self.assertNotIn(58,sums)
        self.assertFalse(f.gaps)

    def test_segment_total_disagrees_with_group_do_not_certify(self):
        f=self.check_findings(doc(group_profit=19))
        self.assertEqual(len(f.notes),1)
        self.assertIn('收入',f.notes[0]['text'])
        self.assertTrue(any('营业利润' in g for g in f.gaps))

    def test_missing_cell_is_not_zero_and_other_metric_survives(self):
        f=self.check_findings(doc(missing=True))
        self.assertEqual(len(f.notes),2)
        self.assertIn('缺少原表金额：Unallocated corporate expense',f.notes[-1]['text'])
        self.assertTrue(any('-3' in g for g in f.gaps))
        self.assertFalse(any('Unallocated' in e['metric'] for e in f.evidence.values()))

    def test_conflicting_or_unresolved_quarter_stops(self):
        f=self.check_findings(doc(),quarter_end='2025-06-30')
        self.assertFalse(f.notes)
        f=self.check_findings(doc(),'2024Q1 各分部的收入合计对得上集团吗？')
        self.assertFalse(f.notes)

    def test_part_displays_computed_subsets_and_excludes_unrelated_issues(self):
        f = self.check_findings(doc(missing=True))
        parts = {'sale':'分部收入相加能对上集团吗？','profit':'分部营业利润加起来能对上集团吗？',
                 'unrelated':'公司现金流增长了多少？'}
        out = answer_parts(parts, f.notes, f.gaps)
        self.assertEqual(set(out), {'sale','profit'})
        self.assertEqual(out['sale']['status'],'source_excerpt')
        self.assertEqual(out['profit']['status'],'partial')
        self.assertIn('18',out['profit']['answer_sentence'])
        self.assertIn('-3',out['profit']['missing_support'])
        self.assertTrue(out['profit']['evidence_ids'] and out['profit']['calculation_ids'])

    def test_only_disclosed_fiscal_year_and_quarter_identify_missing_target_dates(self):
        source = doc()
        tags = '<span name="dei:DocumentFiscalYearFocus">2026</span><span name="dei:DocumentFiscalPeriodFocus">Q2</span><span name="dei:DocumentPeriodEndDate">September 30, 2025</span>'
        source['raw'] = source['raw'].replace(b'<body>',b'<body>'+tags.encode())
        f = self.check_findings(source)
        part = {'q':'各分部营业利润加起来能对上集团吗？'}
        self.assertEqual(answer_parts(part,f.notes,f.gaps,fiscal_target={'quarter':'2026Q2'})['q']['quarter'],'2026Q2')
        self.assertFalse(answer_parts(part,f.notes,f.gaps,fiscal_target={'quarter':'2025Q3'}))

    def test_caller_selected_single_division_or_ratios(self):
        for q in ['这个部门的营业利润有多少？','各分部的利润率能直接加在一起吗？']:
            self.assertFalse(self.check_findings(doc(),q).evidence)


if __name__=='__main__':
    unittest.main()
