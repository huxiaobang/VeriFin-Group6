"""An independently archived filing document can survive missing parent HTML."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_tools import DATA, MemoryStore
from verifin.tools import ToolRegistry


class ArchivedFilingDocumentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tools = ToolRegistry(MemoryStore(), DATA, cache_dir=Path(tmp.name))
        self.args = {"entity": "MSFT", "accession": "0000950170-25-010484",
                     "primary_document": "msft-20250129.htm", "query": "cloud",
                     "source": "replay", "cutoff": "2025-01-31"}
        manifest = self.tools.data.manifest
        self.doc = next(d for d in manifest["documents"] if d["source"].endswith("msft_fy25q2_ir.html"))
        self.info = manifest["files"][self.doc["source"]]
        self.url = self.tools._filing_url("789019", self.args["accession"], "release.htm")
        self.info["url"] = self.url

    def read(self, **change):
        return self.tools.execute("read_filing", {**self.args, **change}, "session", {})

    def test_missing_parent_offers_runnable_same_accession_query(self):
        missing = self.read()
        self.assertFalse(missing["ok"])
        action = missing["data"]["next_actions"][0]
        self.assertEqual(action, {"tool": "read_filing", "arguments": {**self.args, "exhibit_document": "release.htm"}})
        with patch.object(self.tools.data, "get_sec_html", side_effect=AssertionError("unexpected HTTP")):
            found = self.read(**action["arguments"])
        self.assertTrue(found["ok"], found["summary"])
        self.assertEqual(found["data"]["filing"]["exhibit_identity_basis"], "dated_snapshot_in_same_sec_accession")
        self.assertEqual(found["evidence"][0]["source_url"], self.url)
        self.assertEqual(found["evidence"][0]["snapshot_hash"], self.info["sha256"])

    def test_attachment_evidence_retains_its_later_availability(self):
        for date, early, precision in (
            ("2025-01-30", "2025-01-30T23:00:00Z", "date_only"),
            ("2025-01-30T14:00:00-05:00", "2025-01-30T18:59:59Z", "timestamp"),
        ):
            with self.subTest(date=date), patch.dict(self.doc, {"published_at": date, "publication_precision": precision}):
                self.assertFalse(self.read(exhibit_document="release.htm", cutoff=early)["ok"])
                found = self.read(exhibit_document="release.htm")
                self.assertTrue(found["ok"], found["summary"])
                record = found["evidence"][0]
                self.assertEqual((record["published_at"], record["publication_precision"]), (date, precision))

    def test_missing_parent_cannot_authorize_live_other_issuer_other_accession_or_late_document(self):
        for field, new_value, obj in (
            ("entity", "AAPL", self.doc), ("published_at", "2025-02-01", self.doc),
            ("url", self.url.replace("010484", "010485"), self.info),
            ("url", self.url.replace("www.sec.gov", "other.example"), self.info),
        ):
            with self.subTest(field=field, value=new_value), patch.dict(obj, {field: new_value}):
                self.assertFalse(self.read(exhibit_document="release.htm")["ok"])
        self.assertFalse(self.read(exhibit_document="release.htm", source="live")["ok"])
        self.assertFalse(self.read(exhibit_document="../release.htm")["ok"])
        with patch.dict(self.info, {"sha256": "0" * 64}):
            self.assertFalse(self.read(exhibit_document="release.htm")["ok"])
