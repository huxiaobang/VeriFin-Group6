"""Adapter contracts use labelled synthetic fixtures, not benchmark results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from verifin.public_benchmarks import (describe_cases, export_public_inputs, load_public_dataset,
                                      match_reference, retrieval_probe, score_public_predictions)


REVISION = "a" * 40


def conv_fixture():
    return {"id": "Single_SYNTHETIC/2000/page_1.pdf-1_1", "filename": "SYNTHETIC/2000/page_1.pdf",
            "table": [["Metric", "2000", "1999"], ["revenue", "120", "100"], ["staff", "10", "9"]],
            "pre_text": ["SYNTHETIC source context."], "post_text": ["Revenue figures are in millions."],
            "qa": {"answer": "FUTURE_GOLD_SECRET", "program": "FUTURE_PROGRAM_SECRET"},
            "annotation": {"cur_dial": ["What was revenue in 2000?", "And in 1999?"], "turn_ind": 1,
                           "cur_program": "100", "exe_ans": 100,
                           "gold_ind": {"table_1": "ANNOTATED_GOLD_EVIDENCE_TEXT_SECRET"},
                           "dialogue_break": ["What was revenue in 2000?", "And in 1999?", "FUTURE_QUESTION_SECRET"],
                           "exe_ans_list": [120, 100, "FUTURE_GOLD_SECRET"],
                           "turn_program": ["120", "100", "FUTURE_PROGRAM_SECRET"]}}


def tatqa_fixture():
    return {"table": {"uid": "SYNTHETIC-table", "table": [["Metric", "2000"], ["revenue", "100"]]},
            "paragraphs": [{"uid": "SYNTHETIC-p1", "order": 1, "text": "The reporting unit is million dollars."}],
            "questions": [{"uid": "SYNTHETIC-q1", "question": "Revenue in base dollars?", "answer": 100,
                           "answer_type": "arithmetic", "answer_from": "table-text", "scale": "million",
                           "derivation": "GOLD_DERIVATION_SECRET", "rel_paragraphs": ["1"]},
                          {"uid": "SYNTHETIC-q2", "question": "Which unit?", "answer": ["million dollars"],
                           "answer_type": "span", "answer_from": "text", "scale": "", "derivation": "",
                           "rel_paragraphs": ["1"]}]}


class PublicBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, dataset, rows):
        path = self.root / (dataset + ".json")
        path.write_text(json.dumps(rows), encoding="utf-8")
        return load_public_dataset(dataset, path, split="dev", source_url="https://example.invalid/SYNTHETIC", revision=REVISION)

    def test_conv_whitelist_excludes_future_turns_programs_answers_and_gold_strings(self):
        case = self.load("convfinqa", [conv_fixture()])[0]
        value = case.input_record()
        encoded = json.dumps(value)
        for secret in ("FUTURE_QUESTION_SECRET", "FUTURE_GOLD_SECRET", "FUTURE_PROGRAM_SECRET", "ANNOTATED_GOLD_EVIDENCE_TEXT_SECRET"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(value["input"]["question"], "And in 1999?")
        self.assertEqual(value["input"]["previous_user_questions"], ["What was revenue in 2000?"])
        self.assertEqual(case.dialogue_id, "Single_SYNTHETIC/2000/page_1.pdf-1")
        self.assertEqual(case.reference["answer"], 100)
        self.assertEqual(len(value["input"]["source_blocks"]), 5)  # all rows and both text sentences
        self.assertEqual(len(case.reference["source_ids"]), 1)
        value["input"]["question"] = "tampered copy"
        self.assertNotEqual(case.task_input["question"], value["input"]["question"])

    def test_tatqa_source_labels_remain_coarse_and_no_heuristic_cell_gold_created(self):
        cases = self.load("tatqa", [tatqa_fixture()])
        self.assertNotIn("GOLD_DERIVATION_SECRET", json.dumps([c.input_record() for c in cases]))
        self.assertEqual(cases[0].reference["source_locators"], ["table", "paragraph_1"])
        self.assertIn("not_cell_spans", cases[0].reference["evidence_granularity"])
        self.assertEqual(cases[1].reference["source_locators"], ["paragraph_1"])
        self.assertEqual(describe_cases(cases)["answer_references"], 2)

    def test_numeric_scale_is_explicit_and_prose_is_not_guessed(self):
        number, span = self.load("tatqa", [tatqa_fixture()])
        self.assertTrue(match_reference(number, {"answer": 100, "scale": "million"})["correct"])
        self.assertTrue(match_reference(number, {"answer": 100_000_000, "scale": ""})["correct"])
        self.assertFalse(match_reference(number, {"answer": 100, "scale": ""})["correct"])
        self.assertFalse(match_reference(number, {"answer": "Revenue was 100 million"})["correct"])
        self.assertTrue(match_reference(span, {"answer": "million   dollars"})["correct"])
        self.assertFalse(match_reference(span, {"answer": "million dollars", "scale": "million"})["correct"])
        self.assertFalse(match_reference(number, {"answer": "1e999999"})["correct"])

    def test_execution_value_percentage_does_not_allow_hundredfold_error(self):
        raw = conv_fixture()
        raw["annotation"]["exe_ans"] = 0.2
        case = self.load("convfinqa", [raw])[0]
        self.assertTrue(match_reference(case, {"answer": "20%"})["correct"])
        self.assertTrue(match_reference(case, {"answer": 20, "scale": "percent"})["correct"])
        self.assertFalse(match_reference(case, {"answer": 20})["correct"])
        self.assertFalse(match_reference(case, {"answer": True})["correct"])

    def test_missing_predictions_are_counted_and_input_hash_binding_is_checked(self):
        cases = self.load("tatqa", [tatqa_fixture()])
        pred = {"case_id": cases[0].case_id, "input_sha256": cases[0].input_record()["input_sha256"],
                "answer": 100, "scale": "million"}
        result = score_public_predictions(cases, [pred])
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["missing_predictions"], 1)
        self.assertFalse(result["official_leaderboard_metric"])
        for bad in ([pred, pred], [{**pred, "case_id": "unknown"}], [{**pred, "input_sha256": "f" * 64}]):
            with self.assertRaises(ValueError):
                score_public_predictions(cases, bad)

    def test_missing_reference_is_unscoreable_not_zero_or_invented(self):
        raw = conv_fixture()
        raw["annotation"].pop("exe_ans")
        case = self.load("convfinqa", [raw])[0]
        self.assertFalse(match_reference(case, None)["scoreable"])
        result = score_public_predictions([case], [])
        self.assertIsNone(result["accuracy"])
        self.assertEqual(result["scoreable_cases"], 0)

    def test_unmapped_gold_remains_a_gap_and_retrieval_does_not_score_it_perfect(self):
        raw = conv_fixture()
        raw["annotation"]["gold_ind"] = {"text_999": "missing"}
        case = self.load("convfinqa", [raw])[0]
        self.assertEqual(case.reference["unmapped_source_locators"], ["text_999"])
        probe = retrieval_probe([case])
        self.assertEqual(probe["excluded_cases"], 1)
        self.assertIsNone(probe["mean_recall_at_k"])

    def test_existing_bm25_runs_all_context_candidates_without_a_model(self):
        raw = conv_fixture()
        raw["annotation"]["cur_dial"][-1] = "What was revenue in 1999?"
        case = self.load("convfinqa", [raw])[0]
        result = retrieval_probe([case], top_k=2)
        self.assertEqual(result["cases"], 1)
        self.assertEqual(result["rows"][0]["candidate_source_count"], 5)
        self.assertEqual(result["mean_recall_at_k"], 1)
        self.assertFalse(result["is_full_corpus_E3"])
        self.assertEqual(result["model_calls"], 0)

    def test_export_separates_references_and_preserves_official_ids_split_provenance(self):
        cases = self.load("convfinqa", [conv_fixture()])
        result = export_public_inputs(cases, self.root / "export")
        inputs = json.loads(Path(result["inputs"]).read_text())
        references = json.loads(Path(result["references"]).read_text())
        self.assertEqual(inputs[0]["original_id"], conv_fixture()["id"])
        self.assertEqual(inputs[0]["split"], "dev")
        self.assertEqual(inputs[0]["source"]["revision"], REVISION)
        self.assertNotIn("reference", inputs[0])
        self.assertEqual(references[0]["source"], "published_benchmark_reference")
        self.assertEqual(references[0]["input_sha256"], inputs[0]["input_sha256"])
        with self.assertRaises(ValueError):
            export_public_inputs(cases, self.root / "export")

    def test_turn_shape_duplicate_case_and_unpinned_revision_rejected(self):
        value = conv_fixture()
        value["annotation"]["turn_ind"] = 2
        with self.assertRaises(ValueError):
            self.load("convfinqa", [value])
        with self.assertRaises(ValueError):
            self.load("convfinqa", [conv_fixture(), conv_fixture()])
        path = self.root / "raw.json"
        path.write_text(json.dumps([conv_fixture()]))
        with self.assertRaisesRegex(ValueError, "Pin"):
            load_public_dataset("convfinqa", path, split="dev", source_url="https://example.invalid", revision="main")


if __name__ == "__main__":
    unittest.main()
