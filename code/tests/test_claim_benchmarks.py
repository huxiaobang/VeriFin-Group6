import json
from pathlib import Path
import tempfile
import unittest

from verifin.claim_benchmarks import digest, load_findver, retrieval_diagnostic, score_claim_predictions
from verifin.data import DataError


class ClaimBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        report = {"url": "https://example.org/filing", "context": [
            {"id": 0, "type": "table", "context": "Year 2025 revenue 100, year 2024 revenue 80"},
            {"id": 1, "type": "paragraph", "context": "Margin expansion reflected lower input costs."}]}
        (self.base / "report.json").write_text(json.dumps(report))
        self.rows = [{"example_id": "ie-val-1", "subset": "ie", "split": "testmini",
                      "statement": "Revenue in 2025 was 100.", "entailment_label": True,
                      "explanation": "SECRET_REFERENCE_SENTINEL", "relevant_context": [0], "report": "report.json"},
                     {"example_id": "ie-val-2", "subset": "ie", "split": "testmini",
                      "statement": "Revenue in 2025 was 200.", "entailment_label": False,
                      "explanation": "SECRET_REFERENCE_SENTINEL", "relevant_context": [0], "report": "report.json"}]
        self.annotations = self.base / "annotations.json"
        self.annotations.write_text(json.dumps(self.rows))
        self.cases = load_findver(self.annotations, self.base, split="testmini")

    def test_inputs_hide_gold_and_keep_full_context(self):
        model_input = self.cases[0].input_record()
        serialized = json.dumps(model_input)
        for forbidden in ("SECRET_REFERENCE_SENTINEL", "relevant_context", "entailment_label", "ie-val-1", "testmini", "subset", "provenance"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(len(model_input["input"]["source_blocks"]), 2)
        self.assertEqual(self.cases[0].reference["evidence_source_ids"], ["report.json#context-0"])
        result = retrieval_diagnostic(self.cases, limit=1)
        self.assertEqual(result["summary"]["all"]["macro_evidence_recall"], 1)
        self.assertIsNone(result["verification_accuracy"])

    def test_omissions_and_abstentions_count_in_denominator(self):
        one = {"case_id": self.cases[0].model_case_id, "input_sha256": digest(self.cases[0].task_input), "entailed": True}
        result = score_claim_predictions(self.cases, [one])
        self.assertEqual(result["accuracy"], .5)
        self.assertEqual(result["coverage"], .5)
        self.assertEqual(result["class_scores"]["false"]["fn"], 1)
        with self.assertRaises(DataError):
            score_claim_predictions(self.cases, [one, one])
        with self.assertRaises(DataError):
            score_claim_predictions(self.cases, [dict(one, entailed="true")])
        with self.assertRaisesRegex(DataError, "hash"):
            score_claim_predictions(self.cases, [dict(one, input_sha256="changed")])

    def test_invalid_gold_reference_or_report_escape_fails(self):
        self.rows[0]["relevant_context"] = [999]
        self.annotations.write_text(json.dumps(self.rows))
        with self.assertRaisesRegex(DataError, "absent"):
            load_findver(self.annotations, self.base, split="testmini")
        self.rows[0]["report"] = "../report.json"
        self.annotations.write_text(json.dumps(self.rows))
        with self.assertRaisesRegex(DataError, "local"):
            load_findver(self.annotations, self.base, split="testmini")


if __name__ == "__main__":
    unittest.main()
