import copy
import hashlib
import math
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from verifin.data import DataError
from verifin.retrieval import Corpus, FrozenEmbeddings, bm25, chunk_spans, evaluate, search, freeze_local_embeddings


def doc(identifier, text, **extra):
    return dict(id=identifier, text=text, entity="A", published_at="2024-02-01",
                document_report_date="2023-12-31", **extra)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.corpus = Corpus([doc("one", "cloud revenue revenue growth"),
                              doc("two", "gaming margin decline")])

    def embeddings(self):
        # Hand-set orthogonal vectors test cosine mechanics only: never an E1
        # result or a claim to contain genuine neural encoder output.
        artifact = {"schema_version": 1, "corpus_fingerprint": self.corpus.fingerprint,
                    "encoder": {"model_id": "UNIT_TEST_ONLY", "revision": "1",
                                "implementation": "hand constructed test vectors", "provenance": "synthetic unit test, no quality claim"},
                    "dimension": 2, "chunks": {}, "queries": {"cloud": [1, 0]}}
        for c in self.corpus.chunks:
            artifact["chunks"][c["id"]] = [1, 0] if c["document_id"] == "two" else [0, 1]
        return artifact

    def test_bm25_matches_formula_with_length_and_saturation(self):
        chunks = [{"id": "x", "text": "alpha alpha beta"}, {"id": "y", "text": "gamma"}]
        ranking = bm25(chunks, "alpha alpha")
        expected = math.log(1 + 1.5 / 1.5) * 2 * 2.2 / (2 + 1.2 * (.25 + .75 * 3 / 2))
        self.assertAlmostEqual(ranking[0][1], expected)
        self.assertEqual(len(ranking), 1)

    def test_chunk_spans_are_exact_and_cover_non_whitespace(self):
        text = ("A long sentence with words and Unicode 雲端. " * 37 + "\n\n") * 3
        spans = list(chunk_spans(text, 400, 80))
        coverage = set()
        for start, end in spans:
            self.assertLessEqual(end - start, 400)
            self.assertGreater(end, start)
            coverage.update(range(start, end))
        self.assertTrue(all(i in coverage for i, char in enumerate(text) if not char.isspace()))
        self.assertEqual(spans[-1][1], len(text))

    def test_filtered_statistics_do_not_leak_other_issuer(self):
        docs = list(self.corpus.documents) + [dict(doc("third", "cloud " * 200), entity="B")]
        larger = Corpus(docs)
        a = search(self.corpus, "cloud", {"entity": "A"})
        b = search(larger, "cloud", {"entity": "A"})
        self.assertEqual(a["passages"][0]["retrieval_score"], b["passages"][0]["retrieval_score"])
        self.assertEqual(b["eligible_chunks"], 2)

    def test_date_only_publication_conservative_and_report_filter(self):
        self.assertFalse(search(self.corpus, "cloud", {"cutoff": "2024-02-01T12:00:00Z"})["passages"])
        self.assertTrue(search(self.corpus, "cloud", {"cutoff": "2024-02-01"})["passages"])
        self.assertFalse(search(self.corpus, "cloud", {"period_end": "2024-03-31"})["passages"])
        self.assertFalse(search(self.corpus, "cloud", {"document_ids": ["two"]})["passages"])

    def test_context_budget_and_top_k_identical_interfaces(self):
        corpus = Corpus([doc(str(i), "cloud revenue " * 200) for i in range(4)])
        results = search(corpus, "cloud", limit=10, max_context_chars=1800)
        self.assertLessEqual(results["context_chars"], 1800)
        self.assertEqual(len(results["passages"]), 1)

    def test_dense_missing_is_not_lexical_fallback(self):
        for mode in ("dense", "hybrid"):
            with self.assertRaisesRegex(DataError, "genuine"):
                search(self.corpus, "cloud", mode=mode)

    def test_dense_cosine_and_rrf_are_real_calculations(self):
        frozen = FrozenEmbeddings(self.embeddings(), self.corpus)
        dense = search(self.corpus, "cloud", mode="dense", embeddings=frozen)
        self.assertEqual(dense["passages"][0]["document_id"], "two")
        self.assertAlmostEqual(dense["passages"][0]["retrieval_score"], 1.0)
        hybrid = search(self.corpus, "cloud", mode="hybrid", embeddings=frozen)
        self.assertEqual(hybrid["passages"][0]["document_id"], "one")
        self.assertAlmostEqual(hybrid["passages"][0]["retrieval_score"], 1 / 61 + 1 / 62)
        with self.assertRaisesRegex(DataError, "exact query"):
            search(self.corpus, "another query", mode="dense", embeddings=frozen)

    def test_embedding_integrity_and_dimension_refused(self):
        artifact = self.embeddings()
        with self.assertRaisesRegex(DataError, "JSON object"):
            FrozenEmbeddings([], self.corpus)
        bad_encoder = copy.deepcopy(artifact)
        bad_encoder["encoder"] = []
        with self.assertRaisesRegex(DataError, "encoder identity"):
            FrozenEmbeddings(bad_encoder, self.corpus)
        bad = copy.deepcopy(artifact)
        bad["corpus_fingerprint"] = "wrong"
        with self.assertRaisesRegex(DataError, "different corpus"):
            FrozenEmbeddings(bad, self.corpus)
        for vector in ([0, 0], [1, True], [1], [float("inf"), 1]):
            bad = copy.deepcopy(artifact)
            bad["queries"]["cloud"] = vector
            with self.assertRaises((DataError, ValueError)):
                FrozenEmbeddings(bad, self.corpus)

    def test_source_text_hash_refused(self):
        with self.assertRaisesRegex(DataError, "text hash"):
            Corpus([doc("one", "altered", text_snapshot_hash=hashlib.sha256(b"original").hexdigest())])

    def test_corpus_fingerprint_binds_provenance_and_is_order_independent(self):
        original = Corpus([doc("one", "cloud", source_url="https://example.org/a"), doc("two", "margin")])
        self.assertEqual(original.fingerprint, Corpus(list(reversed(original.documents))).fingerprint)
        changed = copy.deepcopy(original.documents)
        changed[0]["source_url"] = "https://different.example.org/a"
        self.assertNotEqual(original.fingerprint, Corpus(changed).fingerprint)

    def test_evaluation_fixed_gold_recall_and_unavailable_dense(self):
        cases = [{"id": "q1", "query": "cloud", "filters": {"entity": "A"},
                  "relevant": [{"document_id": "one", "start": 0, "end": 5}]},
                 {"id": "q2", "query": "unfindable", "relevant": []}]
        result = evaluate(self.corpus, cases, repeats=1)
        self.assertEqual(result["methods"]["bm25"]["macro_span_recall"], 1)
        self.assertEqual(result["methods"]["bm25"]["negative_empty_accuracy"], 1)
        self.assertEqual(result["methods"]["dense"]["status"], "unavailable")
        self.assertEqual(["unspecified"], result["annotation_statuses"])
        self.assertFalse(result["annotation_provenance_verified"])
        self.assertGreaterEqual(result["methods"]["bm25"]["latency_median_ms"], 0)
        cases[0]["filters"]["entity"] = "B"
        with self.assertRaisesRegex(DataError, "excluded"):
            evaluate(self.corpus, cases)

    def test_unknown_filters_refused_and_ties_deterministic(self):
        with self.assertRaisesRegex(DataError, "Unsupported"):
            search(self.corpus, "cloud", {"year": 2024})
        c = Corpus([doc("b", "cloud"), doc("a", "cloud")])
        order = [r["id"] for r in search(c, "cloud")["passages"]]
        self.assertEqual(order, sorted(order))

    def test_evaluation_rejects_empty_or_malformed_cases_without_success_metrics(self):
        for cases in ([], {}, [None], [{"id": "x", "query": "cloud"}],
                      [{"id": "x", "query": "cloud", "relevant": [{"document_id": "one", "start": False, "end": 5}]}]):
            with self.subTest(cases=cases), self.assertRaises(DataError):
                evaluate(self.corpus, cases)
        case = [{"id": "x", "query": "cloud", "relevant": [], "annotation_status": "development_example_not_independent_gold"}]
        result = evaluate(self.corpus, case, modes=["bm25"], repeats=1)
        self.assertEqual(["development_example_not_independent_gold"], result["annotation_statuses"])
        for modes in ([], ["bm25", "bm25"], [["bm25"]]):
            with self.assertRaises(DataError):
                evaluate(self.corpus, case, modes=modes)

    def test_local_encoder_configuration_and_artifact_contract(self):
        class Array:
            def __init__(self, rows):
                self.rows = rows
            def tolist(self):
                return self.rows
        class FakeModel:
            max_seq_length = 100
            tokenizer = staticmethod(lambda texts, **kwargs: {"input_ids": [[1] * len(t.split()) for t in texts]})
            def __init__(self, *args, **kwargs):
                called.update(kwargs)
            def encode(self, texts, **kwargs):
                self_test.assertEqual(kwargs["prompt"], "")
                self_test.assertTrue(kwargs["normalize_embeddings"])
                return Array([[1.0, 0.0] for _ in texts])
        self_test, called = self, {}
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "modules.json").write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": ""}]))
            (directory / "model.safetensors").write_bytes(b"UNIT TEST ONLY: not real weights")
            module = types.SimpleNamespace(SentenceTransformer=FakeModel)
            with patch.dict("sys.modules", {"sentence_transformers": module}):
                artifact = freeze_local_embeddings(self.corpus, ["cloud"], directory,
                            model_id="UNIT_TEST_ONLY", revision="test", query_prefix="query: ")
            self.assertTrue(called["local_files_only"])
            self.assertFalse(called["trust_remote_code"])
            self.assertFalse(called["token"])
            self.assertTrue(called["model_kwargs"]["use_safetensors"])
            self.assertEqual(artifact["encoder"]["query_prefix"], "query: ")
            self.assertEqual(len(FrozenEmbeddings(artifact, self.corpus).vectors), 2)

    def test_local_encoder_refuses_download_custom_code_and_pickle(self):
        with self.assertRaisesRegex(DataError, "local model directory"):
            freeze_local_embeddings(self.corpus, ["cloud"], "/nonexistent/model", model_id="x", revision="1")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            module_file = directory / "modules.json"
            module_file.write_text(json.dumps([{"type": "custom.module", "path": ""}]))
            with self.assertRaisesRegex(DataError, "custom model code"):
                freeze_local_embeddings(self.corpus, ["cloud"], directory, model_id="x", revision="1")
            module_file.write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": "../outside"}]))
            with self.assertRaisesRegex(DataError, "escape"):
                freeze_local_embeddings(self.corpus, ["cloud"], directory, model_id="x", revision="1")
            module_file.write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": ""}]))
            (directory / "pytorch_model.bin").write_bytes(b"not executed")
            with self.assertRaisesRegex(DataError, "pickle"):
                freeze_local_embeddings(self.corpus, ["cloud"], directory, model_id="x", revision="1")
            (directory / "pytorch_model.bin").unlink()
            (directory / "model.safetensors").write_bytes(b"fixture")
            with patch.dict("sys.modules", {"sentence_transformers": None}):
                with self.assertRaisesRegex(DataError, "not installed"):
                    freeze_local_embeddings(self.corpus, ["cloud"], directory, model_id="x", revision="1")

    def test_local_encoder_refuses_hidden_truncation(self):
        class FakeModel:
            max_seq_length = 2
            tokenizer = staticmethod(lambda texts, **kwargs: {"input_ids": [[1, 2, 3] for _ in texts]})
            def __init__(self, *args, **kwargs):
                pass
            def encode(self, *args, **kwargs):
                raise AssertionError("Encoding should not proceed after a truncation check fails")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "modules.json").write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": ""}]))
            (directory / "model.safetensors").write_bytes(b"fixture")
            with patch.dict("sys.modules", {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=FakeModel)}):
                with self.assertRaisesRegex(DataError, "token limit"):
                    freeze_local_embeddings(self.corpus, ["cloud"], directory, model_id="UNIT_TEST_ONLY", revision="test")


if __name__ == "__main__":
    unittest.main()
