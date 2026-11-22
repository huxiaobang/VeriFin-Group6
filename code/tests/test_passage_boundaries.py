import unittest
from unittest.mock import patch

from tests.test_tools import DATA
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry

from verifin.retrieval import Corpus, complete_passage_starts, search


class PassageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.text = ("Opening sentence. • Annual reported sales excluding currency effects "
                     "increased by 11%. • Management also reported a backlog.")
        self.corpus = Corpus([{"id": "doc", "entity": "XYZ", "text": self.text}])
        self.start = self.text.index("currency effects")
        self.end = self.text.index("backlog")
        self.hit = {"id": "ranked", "document_id": "doc", "start": self.start, "end": self.end,
                    "text": self.text[self.start:self.end]}

    def test_extent_budget_and_provenance(self):
        row = dict(self.hit)
        used = complete_passage_starts([row], self.corpus.documents, 100, 100)
        self.assertEqual(self.start - row["start"], used)
        self.assertTrue(row["text"].startswith("Annual reported sales excluding currency effects"))
        self.assertEqual(row["text"], self.text[row["start"]:row["end"]])
        self.assertNotEqual(row["id"], row["retrieved_chunk_id"])
        self.assertEqual(row["retrieved_chunk_id"], self.hit["id"])
        self.assertEqual(row["source_window"]["ranked_start"], self.start)
        self.assertTrue(row["source_window"]["more_text_after"])
        # A partial lead-in must never masquerade as a complete clause.
        for budget in (0, 4):
            row = dict(self.hit)
            self.assertEqual(complete_passage_starts([row], self.corpus.documents, 100, budget), 0)
            self.assertEqual(row["id"], "ranked")
            self.assertEqual(row["text"], self.hit["text"])
            self.assertTrue(row["source_window"]["more_text_before"])

    def test_single_complete_passage_keeps_id_and_text(self):
        args = {"query": "backlog", "mode": "bm25"}
        ranked = search(self.corpus, **args)
        presented = search(self.corpus, **args, complete_starts=True)
        self.assertEqual(ranked["corpus_fingerprint"], presented["corpus_fingerprint"])
        self.assertEqual(ranked["context_chars"], presented["context_chars"])
        before, after = ranked["passages"][0], presented["passages"][0]
        self.assertEqual((before["id"], before["text"]), (after["id"], after["text"]))
        self.assertFalse(after["source_window"]["more_text_before"])
        self.assertFalse(after["source_window"]["more_text_after"])


    def test_receipt_metadata_keeps_existing_evidence_and_claim_current(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session("source window compatibility")["id"]
            tools = ToolRegistry(store, DATA)
            corpus = Corpus([{"id": "doc", "entity": "MSFT", "text": "Revenue increased.",
                              "text_snapshot_path": "fixture.txt", "published_at": "2025-01-29",
                              "source_url": "https://example.org/report", "snapshot_hash": "fixture-source"}])
            with patch.object(tools, "document_corpus", return_value=corpus):
                result = tools.execute("search_documents", {"entity": "MSFT", "query": "Revenue"}, sid, {})
            self.assertTrue(result["ok"], result.get("summary"))
            row = result["evidence"][0]
            identifier = row["id"]
            original = {k: v for k, v in row.items() if k not in ("source_window", "retrieved_chunk_id")}
            store.put_evidence(sid, original)
            store.put_claim(sid, {"id": "c1", "text": "Revenue increased.", "status": "Supported",
                                  "evidence_ids": [identifier], "calculation_ids": [], "requirement_ids": []})
            store.record_tool_return(sid, {"type": "tool_call", "ok": True, "observation": result})
            self.assertEqual(len(store.history(sid, "evidence", identifier)), 1)
            self.assertFalse(store.get_context(sid)["claims"][0]["stale"])
            self.assertEqual(result["data"]["source_windows"][identifier]["retrieved_chunk_id"], identifier)
