"""Pilot integration uses explicit scripted responses, never live model calls."""
import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_engine import FixtureTools, ScriptedModel, full_script, saved_source_followup
from verifin.model import ModelError
from verifin.pilot import main, preflight, run_pilot, validate_suite
from verifin.storage import SessionStore


DATA = Path(__file__).resolve().parents[1] / "data" / "replay"


def suite():
    return {"protocol": "verifin-pilot-v1", "label": "SCRIPTED pilot integration fixture", "split": "development",
            "budget": {"max_model_calls": 8, "max_tool_calls": 4, "max_seconds": 40},
            "cases": [{"id": "revenue", "turns": ["Revenue for the December quarter?", "Show the saved source."]}]}


class PilotTests(unittest.TestCase):
    def test_dry_run_does_not_call_model_or_create_database(self):
        model = ScriptedModel([])
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pilot(suite(), Path(tmp) / "pilot", DATA, model=model)
            self.assertFalse(result["executed"])
            self.assertFalse(result["truth_scored"])
            self.assertEqual(model.calls, [])
            self.assertFalse((Path(tmp) / "pilot" / "sessions.sqlite3").exists())
            inspect = json.loads((Path(tmp) / "pilot" / "preflight.json").read_text())
            self.assertEqual(inspect["conservative_run_upper_bounds"]["model_calls"], 16)
            self.assertEqual(inspect["model_mode"], "scripted_fixture")

    def test_missing_configuration_returns_preflight_without_exposing_secrets(self):
        inspect = preflight(suite(), DATA, env={"OPENAI_API_KEY": "do-not-export-this-value"})
        self.assertFalse(inspect["ready_to_execute"])
        self.assertTrue(inspect["credential_presence"]["OPENAI_API_KEY"])
        self.assertNotIn("do-not-export", json.dumps(inspect))
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pilot(suite(), tmp, DATA, execute=True, env={})
            self.assertFalse(result["executed"])
            self.assertEqual(result["phase"], "execution")
            self.assertFalse((Path(tmp) / "sessions.sqlite3").exists())

    def test_reopen_followup_captures_each_candidate_and_retains_complete_events(self):
        followup = saved_source_followup(suite()["cases"][0]["turns"][1])
        model = ScriptedModel(full_script() + followup)
        stores = []
        def tools(store, case_id, turn):
            stores.append(store)
            return FixtureTools()
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pilot(suite(), tmp, DATA, execute=True, model=model, tools_factory=tools)
            self.assertTrue(result["executed"])
            self.assertEqual(result["model_mode"], "scripted_fixture")
            self.assertTrue(result["automatic_checks_passed"])
            self.assertEqual(result["cases"][0]["saved_report_versions"], 2)
            self.assertIsNot(stores[0], stores[1])
            first = Path(tmp) / "revenue" / "turn-01"
            second = Path(tmp) / "revenue" / "turn-02"
            self.assertTrue((first / "candidate-before-review.json").is_file())
            self.assertTrue((second / "candidate-before-review.json").is_file())
            from verifin.evaluation_cli import load_case
            self.assertTrue(load_case(second / "candidate-before-review.json").case_id)
            events = json.loads((second / "events.json").read_text())
            self.assertEqual(sum(e["type"] == "model_call" for e in events), 4)
            self.assertEqual(sum(e["type"] == "tool_call" for e in events), 0)
            self.assertTrue(any(m["content"] == suite()["cases"][0]["turns"][0] for m in model.calls[5][1]["messages"]))
            store = SessionStore(Path(tmp) / "sessions.sqlite3")
            try:
                self.assertEqual(len(store.list_reports(result["cases"][0]["session_id"])), 2)
            finally:
                store.close()
            self.assertEqual(result["semantic_acceptance"], "not_assessed")

    def test_failed_attempt_still_exports_audit_and_no_fake_candidate(self):
        value = suite()
        value["cases"][0]["turns"] = ["Try a request"]
        model = ScriptedModel([("planner", ModelError("secret-token-never-export"))])
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pilot(value, tmp, DATA, execute=True, model=model,
                               tools_factory=lambda *args: FixtureTools())
            self.assertEqual(result["execution_errors"], 1)
            turn = result["cases"][0]["turns"][0]
            self.assertFalse(turn["candidate_captured"])
            self.assertEqual(turn["status"], "partial")
            self.assertNotIn("secret-token-never-export", json.dumps(result))

    def test_explicit_budgets_unique_safe_paths_and_no_overwrite(self):
        for edit in (lambda v: v.pop("budget"), lambda v: v["cases"].append(copy.deepcopy(v["cases"][0])),
                     lambda v: v["cases"][0].update(id="../../elsewhere"),
                     lambda v: v["budget"].update(max_seconds=float("nan"))):
            value = suite()
            edit(value)
            with self.assertRaises(ValueError):
                validate_suite(value)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "keep.txt"
            marker.write_text("keep")
            with self.assertRaisesRegex(ValueError, "new or empty"):
                run_pilot(suite(), tmp, DATA, model=ScriptedModel([]))
            self.assertEqual(marker.read_text(), "keep")

    def test_cli_defaults_to_preflight_even_with_key_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            path.write_text(json.dumps(suite()))
            env = {"VERIFIN_PROVIDER": "openai", "OPENAI_API_KEY": "test-secret", "VERIFIN_MODEL": "fixture-no-network"}
            with patch.dict("os.environ", env, clear=True), patch("verifin.model.ModelClient.complete", side_effect=AssertionError("No model calls allowed")), redirect_stdout(StringIO()):
                self.assertEqual(main(["--suite", str(path), "--output", str(Path(tmp) / "dry")]), 0)
            summary = json.loads((Path(tmp) / "dry" / "summary.json").read_text())
            self.assertFalse(summary["executed"])


if __name__ == "__main__":
    unittest.main()
