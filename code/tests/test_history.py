import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from email.message import Message

from verifin.data import SnapshotData
from verifin.tools import ToolRegistry

DATA = Path(__file__).resolve().parents[1] / "data"


class Body(io.BytesIO):
    def __init__(self, content, kind="application/json"):
        super().__init__(content)
        self.headers = Message()
        self.headers["Content-Type"] = kind


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache"
        self.tools = self.registry()
        self.filename = "CIK0001234567-submissions-001.json"
        self.root = {"cik": "0001234567", "name": "Generic issuer", "tickers": ["NEW"],
                     "filings": {"recent": {}, "files": [{"name": self.filename, "filingCount": 2,
                         "filingFrom": "2020-02-01", "filingTo": "2021-02-01"}]}}
        self.history = {"accessionNumber": ["0001234567-21-000001", "0001234567-20-000001"],
                        "form": ["10-K", "10-K"], "filingDate": ["2021-02-01", "2020-02-01"],
                        "reportDate": ["2020-12-31", "2019-12-31"], "primaryDocument": ["filing2020.htm", "filing2019.htm"],
                        "acceptanceDateTime": ["2021-02-01T21:10:00Z", "2020-02-01T21:00:00Z"]}

    def tearDown(self):
        self.tmp.cleanup()

    def registry(self):
        return ToolRegistry(None, DATA, allow_network=True, user_agent="Unit tests test@example.org", cache_dir=self.cache)

    def call(self, name, **args):
        SnapshotData._last_sec_request_at = 0
        return self.tools.execute(name, dict(entity="1234567", **args), "s", {})

    def discover(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = Body(json.dumps(self.root).encode())
            result = self.call("discover_filings", source="live")
        self.assertEqual(result["data"]["network_requests"], 1)
        self.assertEqual(result["data"]["history_files"][0]["filename"], self.filename)

    def read_history(self, **args):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = Body(json.dumps(self.history).encode())
            return self.call("read_filing_history", filename=self.filename, **args)

    def test_explicit_history_request_pagination_and_cutoff(self):
        self.discover()
        result = self.read_history(source="live", limit=1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["network_requests"], 1)
        self.assertEqual(result["data"]["next_offset"], 1)
        with patch("verifin.data.build_opener") as opener:
            next_page = self.call("read_filing_history", filename=self.filename, offset=1, limit=1)
            self.assertEqual(next_page["data"]["filings"][0]["reportDate"], "2019-12-31")
            self.assertEqual(next_page["data"]["network_requests"], 0)
            filtered = self.call("read_filing_history", filename=self.filename, cutoff="2021-02-01T21:09:59Z")
            self.assertEqual(len(filtered["data"]["filings"]), 1)
            self.assertEqual(filtered["data"]["filings"][0]["reportDate"], "2019-12-31")
            opener.assert_not_called()

    def test_unknown_or_foreign_history_filename_never_requested(self):
        self.discover()
        with patch("verifin.data.build_opener") as opener:
            for filename in ("../" + self.filename, "CIK0000789019-submissions-001.json", "CIK0001234567-submissions-002.json", "https://example.org/file"):
                result = self.call("read_filing_history", filename=filename, source="live")
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["data"]["network_requests"], 0)
            opener.assert_not_called()

    def test_file_entirely_after_cutoff_is_not_fetched(self):
        self.discover()
        with patch("verifin.data.build_opener") as opener:
            result = self.call("read_filing_history", filename=self.filename, source="live", cutoff="2019-12-31")
            self.assertFalse(result["ok"])
            self.assertEqual(result["data"]["network_requests"], 0)
            opener.assert_not_called()

    def test_reopen_history_read_filing_and_bm25_search_cache(self):
        self.discover()
        self.assertTrue(self.read_history(source="live")["ok"])
        self.tools = self.registry()
        html = b"<!DOCTYPE html><html><body><p>Revenue increased with strong demand for cloud computing.</p></body></html>"
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = Body(html, "text/html")
            result = self.call("read_filing", accession="0001234567-20-000001", primary_document="filing2019.htm", source="live")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["evidence"][0]["filing_report_date"], "2019-12-31")
        self.tools = self.registry()
        with patch("verifin.data.build_opener") as opener:
            found = self.tools.execute("search_documents", {"entity": "NEW", "query": "cloud computing", "period_end": "2019-12-31", "forms": ["10-K"]}, "s", {})
            self.assertTrue(found["ok"], found)
            self.assertIn("cloud computing", found["evidence"][0]["text"])
            self.assertIsNone(found["evidence"][0]["period_end"])
            self.assertEqual(found["evidence"][0]["document_report_date"], "2019-12-31")
            self.assertEqual(found["data"]["network_requests"], 0)
            opener.assert_not_called()

    def test_bad_columns_and_foreign_cik_fail_visibly(self):
        self.discover()
        self.history["form"] = ["10-K"]
        result = self.read_history(source="live")
        self.assertFalse(result["ok"])
        self.assertIn("inconsistent lengths", result["error"])
        self.history["form"] = ["10-K", "10-K"]
        self.history["cik"] = "0000789019"
        result = self.read_history(source="live")
        self.assertFalse(result["ok"])
        self.assertIn("identity", result["error"])

    def test_history_raw_cache_integrity(self):
        self.discover()
        result = self.read_history(source="live")
        Path(result["data"]["snapshot"]["snapshot"]).write_bytes(b"changed")
        with patch("verifin.data.build_opener") as opener:
            result = self.call("read_filing_history", filename=self.filename)
            self.assertFalse(result["ok"])
            self.assertIn("hash mismatch", result["error"])
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
