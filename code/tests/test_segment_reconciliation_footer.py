"""Column-scoped segment sales followed by a separately printed group reconciliation."""
import unittest

from verifin.segment_skill import analyze
from verifin.statement_analysis import Findings


def filing(*, period="Three Months (13 Weeks) Ended", correction_column="group",
           group_total="86", adjustment_name="Elimination of intersegment sales", close=")"):
    columns = '<tr><td></td><td>Engineering</td><td>Services</td><td>Totals</td><td></td></tr>'
    quarter = f'<tr><td></td><th colspan="4">{period}</th></tr>'
    dated = '<tr><td></td><th colspan="4">May 31, 2025</th></tr>'
    amounts = ['','','(14',close] if correction_column == "group" else ['(14',close,'','']
    def row(*cells):
        return '<tr>'+''.join(f'<td>{cell}</td>' for cell in cells)+'</tr>'
    statement = ('<p>Consolidated Statements of Earnings</p><p>USD in millions</p><table>'
                 + quarter + '<tr><td></td><th>May 31, 2025</th></tr>'
                 + row("Net sales",group_total)+'</table>')
    segment = ('<p>Segment information</p><p>USD in millions</p><table>'+quarter+dated+columns
               +row('Sales')+row('Net sales to external customers',34,52,86)
               +row('Intersegment sales',7,7,14)+row('Total Sales',41,59,100)
               +row('Reconciliation of Sales')+row(adjustment_name,*amounts)
               +row('Net sales to external customers','','',86)+row('Corporate/eliminations','','',-3)+'</table>')
    return dict(entity='EX',filename='q.htm',url='https://example.test/quarter',source='q.htm',
                raw=('<html><body>'+statement+segment+'</body></html>').encode(),
                source_form='10-Q',published_at='2025-06-20',period_start='2025-03-01',period_end='2025-05-31',snapshot_hash='fixture')


class FooterTest(unittest.TestCase):
    def result(self, source, q='各业务的销售相加怎么对回集团？'):
        findings=Findings()
        analyze(q,[],[source],findings,quarter_end="2025-05-31",period_start="2025-03-01")
        return findings

    def test_gross_segment_cells_and_group_only_footer_tie_to_independent_statement(self):
        f=self.result(filing())
        self.assertEqual(len(f.notes),1)
        self.assertTrue(f.notes[0]['complete'])
        self.assertEqual([c['value'] for c in f.calculations.values()],[86,0,100,-14])
        by_name={e['text']:e['value'] for e in f.evidence.values()}
        self.assertEqual(by_name['Engineering'],41)
        self.assertEqual(by_name['Services'],59)
        self.assertEqual(by_name['Elimination of intersegment sales'],-14)
        self.assertNotIn('Corporate/eliminations',by_name)

    def test_single_segment_external_sales_does_not_trigger(self):
        f=self.result(filing(),'只看服务板块。卖给集团外客户多少？')
        self.assertTrue(f.inapplicable_reason)
        self.assertFalse(f.evidence)

    def test_footer_amount_in_a_business_column_is_never_a_group_adjustment(self):
        self.assertFalse(self.result(filing(correction_column="business")).notes)

    def test_reject_undated_period_unrelated_adjustment_and_broken_negative_sign(self):
        for args in [dict(period="Nine Months (39 Weeks) Ended"), dict(group_total="88"),
                     dict(adjustment_name="Elimination of intercompany pension costs"), dict(close="")]:
            with self.subTest(args=args):
                self.assertFalse(self.result(filing(**args)).notes)
