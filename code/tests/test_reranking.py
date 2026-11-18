import copy
import json
import tempfile
import unittest
from pathlib import Path

from verifin.data import DataError
from verifin.reranking import LocalQwenReranker, select_reranked


class RerankingTests(unittest.TestCase):
    def test_budget_preserves_evidence_and_deterministic_ties(self):
        passages = [{"id": "large", "text": "x" * 8, "start": 0, "end": 8, "document_id": "doc"},
                    {"id": "b", "text": "beta", "start": 10, "end": 14, "document_id": "doc"},
                    {"id": "a", "text": "alpha", "start": 20, "end": 25, "document_id": "doc"}]
        original = copy.deepcopy(passages)
        selected = select_reranked(passages, [.99, .7, .7], limit=2, max_context_chars=9)
        self.assertEqual([p["id"] for p in selected], ["large"])
        selected = select_reranked(passages, [.1, .7, .7], limit=2, max_context_chars=9)
        self.assertEqual([p["id"] for p in selected], ["a", "b"])
        self.assertEqual([(p["start"], p["end"]) for p in selected], [(20, 25), (10, 14)])
        self.assertEqual(passages, original)

    def test_invalid_scores_and_duplicate_evidence_are_rejected(self):
        passage = {"id": "one", "text": "evidence"}
        for scores in ([], [float("nan")], [float("inf")], [True], [".5"]):
            with self.subTest(scores=scores), self.assertRaises(DataError):
                select_reranked([passage], scores)
        with self.assertRaisesRegex(DataError, "unique"):
            select_reranked([passage, passage], [.5, .6])
        self.assertEqual(select_reranked([], []), [])

    def test_overlong_inputs_fail_before_any_model_forward(self):
        # Deliberate tokenizer double only exercises refusal; no model-quality claim.
        class Tokenizer:
            def __call__(self, texts, **kwargs):
                self.last = kwargs
                return {"input_ids": [[0] * 30 for _ in texts]}
        scorer = object.__new__(LocalQwenReranker)
        scorer.tokenizer = Tokenizer()
        scorer.prefix_ids, scorer.suffix_ids = [1], [2]
        scorer.instruction = "test"
        scorer.max_length = 31
        import sys
        from unittest.mock import patch
        with patch.dict(sys.modules, {"torch": object()}):
            with self.assertRaisesRegex(DataError, "truncation"):
                scorer.score("query", ["document"])
        self.assertFalse(scorer.tokenizer.last["truncation"])

    def test_incomplete_local_model_does_not_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(DataError, "local"):
                LocalQwenReranker(Path(tmp))

    def test_generator_weights_cannot_be_labelled_as_official_reranker(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "config.json").write_text(json.dumps({"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]}))
            (directory / "model.safetensors").write_bytes(b"Not the officially pinned reranker weights")
            with self.assertRaisesRegex(DataError, "official pinned"):
                LocalQwenReranker(directory)


if __name__ == "__main__":
    unittest.main()
