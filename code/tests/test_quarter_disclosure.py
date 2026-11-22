"""Offline SEC exhibit and coverage regressions for a latest-quarter question."""
from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.test_tools import DATA, HTTPBody, MemoryStore
from verifin.data import SnapshotData
from verifin.request_review import grounded_review_rows, report_coverage, request_parts
from verifin.tools import ToolRegistry
from verifin.storage import SessionStore

QUESTION = ("请通过 SEC EDGAR 查询 Adobe（ADBE）截至 2026 年 11 月 19 日最近已披露的财季。"
            "该季度的营收、营业利润和营业利润率分别是多少？请注明财季起止日期，给出 SEC 申报链接和计算公式。")
ACCESSION = "0000796343-26-000147"
PARENT = "adbe-20260910.htm"
EXHIBIT = "adbeex991q326.htm"
QUARTER_QUOTE = "third quarter FY2026 ended Aug. 28, 2026"
FILING = "https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm"


def reviewed_rows(claims, rows, summary):
    parts = request_parts(QUESTION)
    verdicts = {c["id"]: c for c in claims}
    grounded = grounded_review_rows(parts, rows, claims, verdicts, summary)
    return report_coverage(parts, grounded, claims, reviewed=True)


def release_evidence(cache):
    """Serve short source excerpts through the SEC adapter; no external I/O."""
    tools = ToolRegistry(MemoryStore(), DATA, True, "Offline tests tests@example.org", cache_dir=cache)
    base = dict(entity="796343", cutoff="2026-11-19")
    parent = dict(base, accession=ACCESSION, primary_document=PARENT)
    submissions = {"cik": "0000796343", "filings": {"recent": {
        "accessionNumber": [ACCESSION], "form": ["8-K"], "reportDate": ["2026-09-10"],
        "filingDate": ["2026-09-10"], "primaryDocument": [PARENT]}}}
    page = f'<html><a href="{EXHIBIT}">Press release</a><a href="../elsewhere.htm">Other</a></html>'
    exhibit = f'<html><p>Exhibit 99.1</p><p>Adobe reported financial results for its {QUARTER_QUOTE}.</p></html>'
    replies = [
        HTTPBody(json.dumps(submissions).encode(), "application/json"),
        HTTPBody(page.encode(), "text/html"), HTTPBody(exhibit.encode(), "text/html")]
    with patch("verifin.data.build_opener") as opener, patch.object(SnapshotData, "_last_sec_request_at", 0):
        opener.return_value.open.side_effect = replies
        def call(tool, args):
            SnapshotData._last_sec_request_at = 0
            return tools.execute(tool, args, "test", {})
        discovery = call("discover_filings", dict(base, forms=["8-K"]))
        assert discovery["ok"], discovery
        parent_result = call("read_filing", parent)
        assert parent_result["data"]["linked_documents"] == [EXHIBIT], parent_result
        for invalid in (dict(exhibit_document="elsewhere.htm"), dict(exhibit_document=EXHIBIT, cutoff="2026-09-09")):
            rejected = call("read_filing", dict(parent, **invalid))
            assert not rejected["ok"] and rejected["data"]["network_requests"] == 0
        args = dict(parent, exhibit_document=EXHIBIT)
        result = call("read_filing", args)
        assert result["ok"], result
        replay = call("read_filing", dict(args, source="replay"))
        assert opener.return_value.open.call_count == 3
        assert replay["ok"] and replay["data"]["network_requests"] == 0
        return replay["evidence"][0], replay["data"]["disclosed_quarters"][0]


class QuarterDisclosureTests(TestCase):
    def test_exhibit_provenance_and_exact_quote(self):
        with TemporaryDirectory() as cache:
            evidence, period = release_evidence(cache)
            self.assertEqual(evidence["source_url"].split("/")[-2:], [ACCESSION.replace("-", ""), EXHIBIT])
            self.assertEqual(period["evidence_id"], evidence["id"])
            self.assertEqual(period["quarter"], "2026Q3")
            start, end = map(int, re.findall(r"\d+", period["locator"]))
            text = Path(evidence["text_snapshot_path"]).read_text()
            self.assertEqual(text[start:end], QUARTER_QUOTE)
            self.assertEqual(period["quote"], QUARTER_QUOTE)
            db = Path(cache) / "session.sqlite3"
            with SessionStore(db) as store:
                sid = store.create_session()["id"]
                store.put_evidence(sid, evidence)
            with SessionStore(db) as store:
                saved = store.get_context(sid)["evidence"][0]
                self.assertEqual(saved["disclosed_quarters"], [period])
                self.assertEqual(saved["source_url"], evidence["source_url"])

    def test_review_requires_source_backed_label_and_current_links(self):
        label = "Adobe FY2026 Q3 ended Aug. 28, 2026."
        amount = "For the quarter ended August 28, 2026, Adobe reported revenue of $6,760 million and operating income of $2,354 million."
        dates = "The quarter ran from May 30, 2026 to August 28, 2026."
        formula = "Operating profit margin is operating income divided by revenue."
        result = "For the quarter ended August 28, 2026, the operating profit margin was 34.82%."
        claims = [dict(id=cid, text=text, status="Supported") for cid,text in
                  (("label",label), ("amount",amount), ("dates",dates), ("url","Form 10-Q: " + FILING),
                   ("margin", formula + " " + result))]
        rows = [
            dict(part="part-1", status="answered", claim_id="amount", answer_sentence=amount),
            dict(part="part-2", status="answered", claim_id="amount", answer_sentence=amount),
            dict(part="part-4", status="answered", claim_id="dates", answer_sentence=dates),
            dict(part="part-5", status="answered", claim_id="margin", answer_sentence=formula)]
        summary = " ".join(c["text"] for c in claims)
        complete = reviewed_rows(claims, rows, summary)
        self.assertEqual([r["status"] for r in complete], ["answered"] * 5)
        self.assertIn("FY2026 Q3", complete[0]["answer_sentence"])
        self.assertEqual(complete[2]["answer_sentence"], result)
        self.assertEqual(complete[3]["joint_claim_ids"], ["dates", "url"])
        self.assertEqual(complete[4]["answer_sentence"], formula)
        other_issuer = deepcopy(claims)
        other_issuer[0]["text"] = label.replace("Adobe", "Dobe")
        self.assertEqual(reviewed_rows(other_issuer, rows, summary)[0]["status"], "partial")
        other_period = deepcopy(claims)
        other_period[1]["constraints"] = {"period_end": "2026-08-28"}
        other_period[-1]["constraints"] = {"period_end": "2026-05-29"}
        self.assertEqual(reviewed_rows(other_period, rows, summary)[2]["status"], "gap")
        duplicate_date = deepcopy(claims)
        duplicate_date[2]["text"] = "Quarter dates: Aug. 28, 2026 and August 28, 2026."
        linked = deepcopy(rows)
        linked[2].update(answer_sentence=duplicate_date[2]["text"], supporting_claim_ids=["dates", "url"])
        self.assertNotEqual(reviewed_rows(duplicate_date, linked, summary)[3]["status"], "answered")
        invalid = deepcopy(claims)
        invalid[0]["status"] = "Unverifiable"
        invalid[3]["stale"] = True
        invalid[4]["status"] = "Unverifiable"
        self.assertEqual([r["status"] for r in reviewed_rows(invalid, rows, summary)],
                         ["partial", "answered", "gap", "partial", "gap"])
