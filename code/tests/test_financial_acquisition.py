"""Exercise financial acquisition through public tool calls and verified snapshots."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tests.test_tools import DATA, MemoryStore
from verifin.data import stable_id
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


CIK = "0001234567"
PRIOR_ACCESSION = CIK + "-25-000001"
RELEASE_ACCESSION = CIK + "-25-000002"
OTHER_ACCESSION = CIK + "-25-000003"
RELEASE = "release.htm"
EXHIBIT = "ex991.htm"
RETRIEVED = "2025-08-04T00:00:00Z"
PRIOR_START, PRIOR_END = "2025-01-01", "2025-03-31"
START, END = "2025-04-01", "2025-06-30"


class FinancialAcquisitionTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = MemoryStore()
        self.company = {"ticker": "TEST", "cik": CIK, "name": "Synthetic issuer",
                        "facts": "facts.json", "submissions": "submissions.json"}
        self.manifest = {"companies": [self.company], "documents": [], "files": {}}

    def snapshot(self, name, content, url, retrieved=RETRIEVED):
        raw = json.dumps(content).encode() if isinstance(content, dict) else content
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.manifest["files"][name] = {
            "url": url, "sha256": hashlib.sha256(raw).hexdigest(),
            "retrieved_at": retrieved, "bytes": len(raw),
        }
        return path

    def corpus(self, with_release=True):
        facts = {"start": PRIOR_START, "end": PRIOR_END, "val": 30, "filed": "2025-05-01",
                 "accn": PRIOR_ACCESSION, "form": "10-Q"}
        payload = {"cik": CIK, "facts": {"us-gaap": {"OperatingIncomeLoss": {"units": {"USD": [facts]}}}}}
        self.snapshot("facts.json", payload, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json")
        rows = {
            "accessionNumber": [OTHER_ACCESSION, RELEASE_ACCESSION, PRIOR_ACCESSION],
            "form": ["8-K", "8-K", "10-Q"],
            "items": ["5.02,9.01", "2.02,9.01", ""],
            "reportDate": ["2025-08-02", "2025-07-24", PRIOR_END],
            "filingDate": ["2025-08-02", "2025-07-24", "2025-05-01"],
            "acceptanceDateTime": ["2025-08-02T20:00:00Z", "2025-07-24T20:00:00Z", "2025-05-01T20:00:00Z"],
            "primaryDocument": ["corporate.htm", RELEASE, "quarter.htm"],
        }
        self.snapshot("submissions.json", {"cik": CIK, "filings": {"recent": rows}},
                      f"https://data.sec.gov/submissions/CIK{CIK}.json")
        if with_release:
            base = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{RELEASE_ACCESSION.replace('-', '')}"
            self.snapshot(RELEASE, f'<html><body><a href="{EXHIBIT}">Earnings release</a></body></html>'.encode(),
                          f"{base}/{RELEASE}")
            self.snapshot(EXHIBIT, b"""<html><body><h2>Consolidated Statements of Income</h2>
                <p>(in millions of U.S. dollars)</p><table>
                <tr><th></th><th>Three Months Ended</th></tr>
                <tr><th></th><th>June 30, 2025</th></tr>
                <tr><td>Revenue</td><td>120</td></tr>
                <tr><td>Operating income</td><td>40</td></tr>
                </table></body></html>""", f"{base}/{EXHIBIT}")

    def start(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        return ToolRegistry(self.store, self.root, cache_dir=self.root / "live")

    def call(self, tools, name, **args):
        return tools.execute(name, args, "session", {})

    def test_names_and_empty_results_distinguish_discovery_from_evidence(self):
        self.corpus()
        tools = self.start()
        common = {"entity": "TEST", "cutoff": "2025-07-23"}
        for alias in ("Operating income", "operating_profit", "营业利润", "營業利潤", "us-gaap:OperatingIncome"):
            with self.subTest(alias=alias):
                result = self.call(tools, "financial_facts", metric=alias, **common)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["data"]["status"], "period_selection_required")
                self.assertEqual(result["data"]["resolved_concepts"], ["us-gaap:OperatingIncomeLoss"])
                self.assertEqual(result["evidence"], [])
        for args, status in (
            ({"metric": "us-gaap:NotAnAvailableConcept"}, "concept_unavailable_in_snapshot"),
            ({"metric": "operating_income", "unit": "EUR"}, "unit_unavailable"),
            ({"metric": "operating_income", "period_start": START, "period_end": END}, "no_exact_period"),
            ({"metric": "operating_income", "cutoff": "2025-04-01"}, "no_facts_at_cutoff"),
        ):
            result = self.call(tools, "financial_facts", **(common | args))
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["data"]["status"], status)
            self.assertEqual(result["evidence"], [])

    def test_recent_release_blocks_stale_fact_and_exhibit_supplies_operands(self):
        self.corpus()
        tools = self.start()
        latest = {"entity": "TEST", "metric": "operating profit", "period_selection": "latest_reported_quarter"}
        before = self.call(tools, "financial_facts", **latest, cutoff="2025-07-24T19:59:59Z")
        self.assertEqual(before["evidence"][0]["period_end"], PRIOR_END)
        after = self.call(tools, "financial_facts", **latest, cutoff="2025-08-03")
        self.assertFalse(after["ok"], after)
        self.assertEqual(after["evidence"], [])
        self.assertEqual(after["data"]["status"], "filing_review_required")
        self.assertEqual(after["data"]["latest_filing_candidate"]["accession"], RELEASE_ACCESSION)
        self.assertEqual(after["data"]["network_requests"], 0)
        action = after["data"]["next_actions"][0]
        parent = self.call(tools, action["tool"], **action["arguments"])
        self.assertEqual(parent["data"]["linked_documents"], [EXHIBIT])
        exhibit = self.call(tools, "read_filing", **action["arguments"], exhibit_document=EXHIBIT)
        self.assertEqual(exhibit["data"]["numeric_operand_status"], "attached")
        self.assertEqual(exhibit["data"]["network_requests"], 0)
        cells = {cell["metric"]: cell for cell in exhibit["evidence"] if cell["kind"] == "financial_fact"}
        self.assertEqual((cells["operating_income"]["period_start"], cells["operating_income"]["period_end"]),
                         (START, END))
        self.store.save(exhibit)
        operands = [cells["operating_income"]["id"], cells["revenue"]["id"]]
        margin = self.call(tools, "calculate", operation="margin", operand_ids=operands)
        self.assertAlmostEqual(margin["calculations"][0]["value"], 100 / 3)

    def test_company_facts_link_uses_the_selected_accession_metadata(self):
        self.corpus()
        result = self.call(self.start(), "financial_facts", entity="TEST", metric="operating_income",
                           period_start=PRIOR_START, period_end=PRIOR_END, unit="USD", cutoff="2025-08-03")
        row, = result["evidence"]
        self.assertEqual((row["value"], row["unit"], row["period_start"], row["period_end"]),
                         (30, "USD", PRIOR_START, PRIOR_END))
        self.assertEqual(row["source_url"], f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json")
        self.assertEqual(row["filing_url"],
                         f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{PRIOR_ACCESSION.replace('-', '')}/quarter.htm")
        self.assertEqual(row["filing_link_provenance"]["snapshot_hash"], self.manifest["files"]["submissions.json"]["sha256"])
        self.assertEqual(row["filing_link_provenance"]["report_date"], PRIOR_END)
        self.assertEqual(result["data"]["network_requests"], 0)

    def test_active_ordinary_latest_request_uses_latest_mode_without_new_argument(self):
        self.corpus()
        tools = self.start()
        store = SessionStore(self.root / "request.sqlite")
        self.addCleanup(store.close)
        tools.store = store
        sid = store.create_session()["id"]
        question = "TEST 最新已公布季度的營業利潤率是多少？請列出收入、營業利潤和計算公式。"
        args = {"entity": "TEST", "metric": "us-gaap:OperatingIncome"}
        scope = {"cutoff": "2025-08-03"}
        for request, extra_scope, status in (
            (question, {}, "filing_review_required"),
            ("What is operating income for the most recent reported fiscal quarter?", {}, "filing_review_required"),
            (question + " 請和去年同期比較。", {}, "period_selection_required"),
            ("Use only this press release for the latest reported quarter's operating income.",
             {}, "period_selection_required"),
            ("Compare the latest reported quarter vs. 2025-03-31.", {}, "period_selection_required"),
            (question, {"planning_task": {"items": [{}]}}, "period_selection_required"),
            (question, {"period_start": PRIOR_START, "period_end": PRIOR_END}, "facts_found"),
        ):
            with self.subTest(request=request, extra_scope=extra_scope):
                store.add_event(sid, {"type": "episode_checkpoint",
                                      "episode": {"status": "running", "request": request}})
                result = tools.execute("financial_facts", args, sid, scope | extra_scope)
                self.assertEqual(result["data"]["status"], status)
                if status == "facts_found":
                    self.assertEqual(result["data"]["selection"], "latest_public_filing_at_cutoff")
                else:
                    self.assertFalse(result["evidence"])

    def test_coverage_before_cutoff_keeps_older_indexed_period_out_of_evidence(self):
        self.corpus()
        tools = self.start()
        result = self.call(tools, "financial_facts", entity="TEST", metric="operating income",
                           period_selection="latest_reported_quarter", cutoff="2025-09-01",
                           unit="EUR")
        # An eligible earnings filing is actionable even for a unit absent from Company Facts.
        self.assertEqual(result["data"]["status"], "filing_review_required")
        self.assertEqual(result["evidence"], [])
        submissions = self.root / "submissions.json"
        payload = json.loads(submissions.read_bytes())
        payload["filings"]["recent"]["items"][1] = "5.02,9.01"
        self.snapshot("submissions.json", payload, self.manifest["files"]["submissions.json"]["url"])
        tools = self.start()
        result = self.call(tools, "financial_facts", entity="TEST", metric="operating income",
                           period_selection="latest_reported_quarter", cutoff="2025-09-01")
        self.assertEqual(result["data"]["status"], "submissions_coverage_required")
        self.assertEqual(result["evidence"], [])

    def test_damaged_facts_do_not_invalidate_real_independent_release(self):
        source = DATA / "replay"
        manifest = json.loads((source / "manifest.json").read_text())
        issuer = next(c for c in manifest["companies"] if c["ticker"] == "MSFT")
        doc = next(d for d in manifest["documents"]
                   if d["entity"] == "MSFT" and d["source"].endswith("_ir.html"))
        self.manifest["companies"] = [{key: issuer[key] for key in ("ticker", "name", "cik", "facts")}]
        self.manifest["documents"] = [doc]
        for name in (doc["source"], doc["text"], issuer["facts"]):
            info = manifest["files"][name]
            payload = (source / name).read_bytes()
            path = self.snapshot(name, payload, info["url"], info["retrieved_at"])
            if name == issuer["facts"]:
                path.write_bytes(payload + b" ")
        tools = self.start()
        failed = self.call(tools, "financial_facts", entity="MSFT", metric="revenue", cutoff="2025-02-01")
        self.assertEqual(failed["data"]["status"], "company_facts_unavailable")
        self.assertIn("integrity check failed", failed["summary"])
        action = failed["data"]["next_actions"][0]
        self.assertEqual(action["tool"], "search_documents")
        opened = self.call(tools, "read_document", entity="MSFT", cutoff="2025-02-01", limit_chars=6000,
                           document_id=stable_id("source_doc_", manifest["files"][doc["source"]]["url"]))
        self.assertTrue(opened["ok"], opened)
        self.assertEqual(opened["evidence"][0]["kind"], "document")
        self.assertIn("integrity check failed", opened["data"]["currency_reference_unavailable"])
