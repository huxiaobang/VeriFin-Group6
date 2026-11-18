"""Receipt-level financial invariants, independent of agent phrasing and routing."""
from copy import deepcopy
from hashlib import sha256
from unittest import TestCase

from verifin.income_bridge import analyze
from verifin.verification import check_rules


class FilingLibrary:
    def __init__(self, rows, *, unit="USD in thousands", title="Consolidated Statements of Income"):
        lines=["<html><body><h2>"+title+"</h2><p>"+unit+"</p><table>",
               "<tr><td></td><th colspan='2'>Three Months Ended June 30,</th>"
               "<th>% change</th><th colspan='2'>Six Months Ended June 30,</th></tr>",
               "<tr><td></td><th>2026</th><th>2025</th><td></td><th>2026</th><th>2025</th></tr>"]
        for label, current, prior in rows:
            # Cumulative columns deliberately have plausible but unrelated values.
            lines.append(f"<tr><td>{label}</td><td>{current}</td><td>{prior}</td>"
                         "<td>85.1%</td><td>900</td><td>800</td></tr>")
        self.body=("".join(lines)+"</table></body></html>").encode()
        self.manifest={"companies":[{"ticker":"EXAMPLE","cik":"000111"}],
                       "documents":[{"entity":"EXAMPLE","form":"8-K Exhibit 99","source":"filing.htm",
                                     "period_start":"2026-04-01","period_end":"2026-06-30",
                                     "published_at":"2026-07-20T12:45:00Z"}]}

    def info(self,_):
        return {"url":"https://issuer.test/filing","sha256":sha256(self.body).hexdigest()}

    def raw(self,_):
        return self.body


ROWS=[("Income from operations","42","37"),("Interest expense","5","3"),
      ("Other income, net","2","4"),("Earnings before income taxes","39","38"),
      ("Income tax expense","11","7"),("Net earnings","28","31")]


class IncomeBridgeSourceTests(TestCase):
    def replay(self,rows,**kwargs):
        return analyze(FilingLibrary(rows,**kwargs),"EXAMPLE","2026-08-01")

    def test_official_exhibit_handles_mixed_headings_sign_and_reproducible_subtotals(self):
        result=self.replay(ROWS)
        bridge=result["data"]["income_bridge"]
        values={c["id"]:c["value"] for c in result["calculations"]}
        self.assertTrue(bridge["complete"],bridge["gaps"])
        self.assertEqual(values[bridge["sum_id"]],-3000)
        self.assertEqual(values[bridge["total_id"]],-3000)
        self.assertEqual([values[row["contribution_id"]] for row in bridge["rows"]],[5000,-2000,-2000,1000,-4000,-3000])
        self.assertEqual(bridge["current_period"],{"start":"2026-04-01","end":"2026-06-30"})
        self.assertTrue(all(c["printed_scale"]==1000 and c["column_header_locators"] for c in result["evidence"]))
        self.assertEqual(check_rules({"evidence":result["evidence"],"calculations":result["calculations"],
                                     "claims":[{"id":"check","text":"Income bridge","calculation_ids":[bridge["sum_id"]]}]}),[])

    def test_empty_dash_and_unsupported_labels_retain_only_observed_amounts(self):
        for blank in ("","—","N/A"):
            changed=deepcopy(ROWS)
            changed[2]=("Other income, net","2",blank)
            result=self.replay(changed)
            bridge=result["data"]["income_bridge"]
            self.assertFalse(bridge["complete"])
            self.assertIsNone(bridge["sum_id"])
            self.assertIsNotNone(bridge["total_id"])
            row=bridge["rows"][2]
            self.assertTrue(row["current_id"])
            self.assertIsNone(row["prior_id"])
            self.assertIsNone(row["contribution_id"])
            self.assertIn("上年同期"," ".join(bridge["gaps"]))
        changed=[(label if i!=2 else "Unidentified adjustment",cur,old) for i,(label,cur,old) in enumerate(ROWS)]
        self.assertFalse(self.replay(changed)["data"]["income_bridge"]["complete"])

    def test_disagreeing_subtotals_cannot_hide_behind_equal_yoy_difference(self):
        changed=deepcopy(ROWS)
        changed[3]=("Earnings before income taxes","40","39")
        self.assertFalse(self.replay(changed)["data"]["income_bridge"]["complete"])

    def test_ambiguous_currency_adjusted_and_after_cutoff_are_unavailable(self):
        self.assertFalse(self.replay(ROWS,unit="in thousands")["evidence"])
        self.assertFalse(self.replay(ROWS,title="Adjusted Consolidated Statements of Income")["evidence"])
        self.assertFalse(analyze(FilingLibrary(ROWS),"EXAMPLE","2026-07-01")["evidence"])
