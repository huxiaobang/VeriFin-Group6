"""CLI integration uses real replay tools and explicitly synthetic human fixtures."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verifin.cli import main
from verifin.evaluation_cli import case_record
from verifin.storage import SessionStore
from tests.test_outcome_scoring import runs, adjudication


ROOT = Path(__file__).resolve().parents[1]


class OfflineCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model_patch = patch("verifin.model.ModelClient.from_env", side_effect=AssertionError("Offline command must not construct a model"))
        self.model_patch.start()

    def tearDown(self):
        self.model_patch.stop()
        self.temp.cleanup()

    def save(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data))
        return str(path)

    def command(self, *args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--db", str(self.root / "unused.sqlite3"), "--data-dir", str(ROOT / "data/replay"), *map(str, args)])
        self.assertEqual(expected, code, stderr.getvalue())
        return json.loads(stdout.getvalue()) if stdout.getvalue().strip() else stderr.getvalue()

    def test_human_plan_remains_pending_and_existing_file_is_not_overwritten(self):
        path = self.root / "heldout.json"
        self.command("study", "skeleton", "--output", path)
        data = json.loads(path.read_text())
        self.assertEqual(12, len(data["slots"]))
        self.assertEqual(0, data["completed_annotations"])
        self.assertEqual("pending_human_case_selection", data["status"])
        self.command("study", "skeleton", "--output", path, expected=2)
        self.assertFalse((self.root / "unused.sqlite3").exists())

    def test_packet_annotation_and_scoring_roundtrip_keep_fixture_mode_and_bindings(self):
        case, detection, revisions = runs()
        case_path = self.save("case.json", case_record(case))
        detection_path = self.save("detection.json", detection)
        revision_path = self.save("revisions.json", revisions)
        packet_path = self.root / "packet.json"
        self.command("study", "packet", "--case", case_path, "--detection", detection_path,
                     "--revisions", revision_path, "--condition", "generic_review", "--output", packet_path)
        packet = json.loads(packet_path.read_text())
        self.assertEqual("scripted_fixture", packet["model_mode"])
        result = self.command("study", "annotations", "--packet", packet_path, "--annotators", "reviewer-a", "reviewer-b", "--output", self.root / "annotation-bundle")
        self.assertEqual("pending_human_annotation", result["status"])
        # Completed annotations here are named synthetic fixtures, not project gold.
        judgment_path = self.save("synthetic-adjudication.json", adjudication(packet))
        score_path = self.root / "score.json"
        self.command("study", "score", "--packet", packet_path, "--adjudication", judgment_path, "--output", score_path)
        score = json.loads(score_path.read_text())
        self.assertEqual(packet["binding"], score["binding"])
        self.assertFalse((self.root / "unused.sqlite3").exists())

    def test_fixed_preview_acquires_real_sources_but_stops_before_model_synthesis(self):
        public_case = json.loads((ROOT / "docs/planning_case.example.json").read_text())
        task = self.save("task.json", public_case["turns"][0]["task"])
        result = self.command("planning", "preview", "--task", task, "--output", self.root / "preview")
        self.assertEqual("preview_only", result["status"])
        self.assertEqual("synthesis_required", result["stop_reason"])
        self.assertEqual(0, result["model_calls"])
        self.assertFalse(result["truth_scored"])
        values = {e["metric"]: e["value"] for e in result["context"]["evidence"]}
        self.assertEqual(69632000000, values["revenue"])
        self.assertEqual([], result["context"]["reports"])
        self.assertFalse((self.root / "unused.sqlite3").exists())

    def test_saved_session_audit_and_charts_need_no_model(self):
        from scripts.demo_replay import run_demo
        demo = run_demo(self.root / "demo", ROOT / "data/replay")
        # Commands use a real reopened SQLite snapshot, no live model.
        database = self.root / "unused.sqlite3"
        import shutil
        shutil.copy2(self.root / "demo/sessions.sqlite3", database)
        audit = self.command("audit", demo["session_id"])
        self.assertEqual("passed", audit["automatic_log_checks"])
        self.assertEqual("not_assessed", audit["semantic_acceptance"])
        self.assertFalse(audit["real_model_behavior_verified"])
        result = self.command("charts", demo["session_id"], self.root / "charts", "--version", "1")
        self.assertEqual(1, result["report_version"])
        self.assertEqual(3, len(result["charts"]))
        original = self.command("scope", demo["session_id"])
        modified = {**original["scope"], "entities": ["MSFT", "NVDA"]}
        path = self.save("scope.json", modified)
        self.command("scope", demo["session_id"], "--file", path, "--expected-version", original["scope_version"])
        self.command("scope", demo["session_id"], "--file", path, "--expected-version", original["scope_version"], expected=2)
        # Old snapshots stay valid in their old scopes after explicit edits.
        self.assertEqual("passed", self.command("audit", demo["session_id"])["automatic_log_checks"])

    def test_bm25_cli_binds_the_saved_corpus_and_rejects_mutation(self):
        corpus_path = self.root / "corpus.json"
        self.command("retrieval", "corpus", "--output", corpus_path)
        corpus = json.loads(corpus_path.read_text())
        result = self.command("retrieval", "search", "--corpus", corpus_path, "--query", "cloud revenue")
        self.assertTrue(result["passages"])
        self.assertEqual("bm25", result["mode"])
        corpus["documents"][0]["text"] += " mutated"
        corpus_path.write_text(json.dumps(corpus))
        self.command("retrieval", "search", "--corpus", corpus_path, "--query", "cloud revenue", expected=2)

    def test_offline_price_preview_uses_an_explicit_generic_listing(self):
        from tests.test_market import sample
        source = self.save("market.json", sample())
        task = self.save("task-price.json", {
            "version": 1, "cutoff": "2025-02-04T20:00:00Z", "source": "replay",
            "items": [{"id": "price", "text": "Price change for the stated listing and exact dates",
                       "kind": "price", "entities": ["INDEPENDENT"],
                       "window": {"start": "2025-01-31", "end": "2025-02-03"}}]})
        result = self.command("planning", "preview", "--task", task, "--market-data", source, "--output", self.root / "market-preview")
        self.assertEqual(0, result["model_calls"])
        self.assertEqual("synthesis_required", result["stop_reason"])
        self.assertAlmostEqual(10, result["context"]["calculations"][0]["value"])

    def test_e2_annotation_commands_keep_protocol_fixture_and_pending_humans_explicit(self):
        from tests.test_planning import ProtocolModel, task
        from tests.test_planning_evaluation import case_with
        from verifin.planning_evaluation import run_planning_pair
        case = case_with(task())
        paired = run_planning_pair(case, ProtocolModel, ROOT / "data/replay", output_dir=self.root / "paired")
        case_path = self.save("planning-case.json", case.to_dict())
        run_path = self.save("planning-run.json", paired)
        packet_path = self.root / "planning-packet.json"
        self.command("planning", "packet", "--case", case_path, "--run", run_path,
                     "--condition", "adaptive", "--output", packet_path)
        packet = json.loads(packet_path.read_text())
        self.assertEqual("scripted_protocol_fixture", packet["model_mode"])
        bundle = self.command("planning", "annotations", "--packet", packet_path,
                              "--annotators", "person-a", "person-b", "--output", self.root / "planning-annotations")
        self.assertEqual("pending_human_annotation", bundle["status"])
        self.assertFalse(bundle["truth_scored"])
        summary_path = self.root / "planning-summary.json"
        self.command("planning", "summarize", "--run", run_path, "--output", summary_path)
        self.assertFalse(json.loads(summary_path.read_text())["annotation_complete"])
        self.assertFalse((self.root / "unused.sqlite3").exists())
        from verifin.reporting import render_markdown
        report = paired["conditions"]["adaptive"]["turns"][0]["report"]
        self.assertIn("SCRIPTED FIXTURE", render_markdown(report))

    def test_paired_execution_failure_returns_nonzero_while_preserving_failure_output(self):
        failure = {"conditions": {"adaptive": {"turns": [{"status": "unavailable"}]}, "fixed": {"turns": []}}}
        with patch("verifin.planning_evaluation.run_planning_pair", return_value=failure):
            result = self.command("planning", "run", "--case", ROOT / "docs/planning_case.example.json", "--output", self.root / "failed-pair", expected=2)
        self.assertEqual(failure, result)


if __name__ == "__main__":
    unittest.main()
