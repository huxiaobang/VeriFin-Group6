import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from verifin.data import DataError
from verifin.retrieval import Corpus, search
from verifin.runtime_embeddings import RuntimeEmbeddings


class TestEncoder:
    # Synthetic vectors verify caching, never retrieval effectiveness.
    metadata={"model_id":"UNIT_TEST_ONLY","revision":"1","implementation":"synthetic test",
              "provenance":"not a neural model"}
    batch_size=16
    def __init__(self):self.calls=[]
    def encode(self,texts,kind):
        self.calls.append((kind,list(texts)))
        return [[1.0, float(len(t)%5+1)] for t in texts]


class RuntimeEmbeddingTests(unittest.TestCase):
    def corpus(self, extra=False, changed=False):
        docs=[{"id":"a","text":"Revenue grew during this quarter." if not changed else "Revenue declined during this quarter."}]
        if extra:docs.append({"id":"b","text":"Operating income reached a new high."})
        return Corpus(docs,max_chars=600,overlap=80)

    def test_unseen_query_encodes_only_new_query_and_new_document_only_delta(self):
        with tempfile.TemporaryDirectory() as root:
            enc=TestEncoder();r=RuntimeEmbeddings(enc,root);c=self.corpus()
            e=r.for_query(c,"revenue")
            self.assertEqual(r.last_stats["encoded_passages"],1)
            self.assertEqual(hashlib.sha256(Path(r.last_stats["artifact_path"]).read_bytes()).hexdigest(),e.artifact_hash)
            self.assertTrue(search(c,"revenue",mode="dense",embeddings=e)["passages"])
            hot = r.for_query(c,"revenue")
            self.assertEqual(hot.artifact_hash, e.artifact_hash)
            self.assertEqual(r.last_stats["encoded_passages"], 0)
            r.for_query(c,"new question")
            self.assertEqual(r.last_stats["encoded_passages"],0)
            self.assertEqual(r.last_stats["encoded_queries"],1)
            r.for_query(self.corpus(extra=True),"new question")
            self.assertEqual(r.last_stats["encoded_passages"],1)
            self.assertEqual(r.last_stats["encoded_queries"],0)
            fresh=RuntimeEmbeddings(TestEncoder(),root)
            fresh.for_query(c,"revenue")
            self.assertEqual(fresh.encoder.calls,[])

    def test_source_and_encoder_changes_invalidate_vectors(self):
        with tempfile.TemporaryDirectory() as root:
            r=RuntimeEmbeddings(TestEncoder(),root)
            a=r.for_query(self.corpus(),"q")
            b=r.for_query(self.corpus(changed=True),"q")
            self.assertNotEqual(a.artifact_hash,b.artifact_hash)
            self.assertEqual(r.last_stats["encoded_passages"],1)
            other=TestEncoder();other.metadata={**other.metadata,"revision":"2"}
            new=RuntimeEmbeddings(other,root);new.for_query(self.corpus(),"q")
            self.assertEqual(new.last_stats["encoded_queries"],1)

    def test_corrupted_cache_and_empty_query_refused(self):
        with tempfile.TemporaryDirectory() as root:
            r=RuntimeEmbeddings(TestEncoder(),root)
            with self.assertRaises(DataError):r.for_query(self.corpus(),"")
            r.for_query(self.corpus(),"q")
            for p in Path(root).glob("vector_*.json"):p.write_text(json.dumps({"key":"wrong","vector":[1,0]}))
            with self.assertRaises(DataError):r.for_query(self.corpus(),"q")

    def test_checkpoint_stops_waiting_lock_and_between_encoding_batches(self):
        with tempfile.TemporaryDirectory() as root:
            r = RuntimeEmbeddings(TestEncoder(), root)
            r.lock.acquire()
            checks = []
            def stop_wait():
                checks.append(True)
                if len(checks) >= 3:
                    raise DataError("cancelled")
            try:
                with self.assertRaisesRegex(DataError, "cancelled"):
                    r.for_query(self.corpus(), "q", checkpoint=stop_wait)
            finally:
                r.lock.release()
            self.assertEqual(r.encoder.calls, [])
            def stop_after_batch():
                if r.encoder.calls:
                    raise DataError("time budget")
            r.encoder.batch_size = 1
            with self.assertRaisesRegex(DataError, "time budget"):
                r.for_query(self.corpus(extra=True), "q", checkpoint=stop_after_batch)
            self.assertEqual(len(r.encoder.calls), 1)
            # Cancellation released the cache locks; a later turn can resume.
            self.assertIn("q", r.for_query(self.corpus(), "q").queries)
