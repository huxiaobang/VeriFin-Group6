"""Blank forms and explicit annotation fixtures; no real human labels created."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.test_outcome_scoring import completed_annotation, packet_for
from verifin.annotation_form import (build_annotation_form, render_annotation_form, validate_completed_form,
                                     validate_reviewer_material)
from verifin.evaluation import EvaluationError
from verifin.outcome_scoring import make_annotation_template, validate_annotation
from verifin.study import write_annotation_bundle


def materials():
    packet = packet_for()
    visible = {"packet_sha256": packet["packet_sha256"], "status": "pending_independent_human_assessment",
               "baseline": packet["baseline"], "candidate": packet["candidate"],
               "candidate_availability": packet["availability"]}
    return packet, visible, make_annotation_template(packet, "reviewer-a")


class AnnotationFormTests(unittest.TestCase):
    def test_blank_form_preserves_null_judgments_and_excludes_condition_binding(self):
        packet, visible, draft = materials()
        html = render_annotation_form(visible, draft)
        embedded = re.search(r'<script id="annotation" type="application/json">(.*?)</script>', html).group(1)
        self.assertEqual(json.loads(embedded), draft)
        self.assertNotIn('"generic_review"', html)
        self.assertNotIn('"condition"', html)
        self.assertNotIn('"revision_run_sha256"', html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn('id="attest" type="checkbox"', html)
        with self.assertRaises(EvaluationError):
            validate_completed_form(visible, draft)

    def test_administrative_packet_or_mismatched_reviewer_binding_is_rejected(self):
        packet, visible, draft = materials()
        with self.assertRaisesRegex(EvaluationError, "reviewer-N"):
            render_annotation_form(packet, draft)
        wrong = copy.deepcopy(draft)
        wrong["packet_sha256"] = "f" * 64
        with self.assertRaises(EvaluationError):
            render_annotation_form(visible, wrong)
        badged = copy.deepcopy(visible)
        badged["candidate"]["claims"][0]["status"] = "Supported"
        with self.assertRaisesRegex(EvaluationError, "badges"):
            render_annotation_form(badged, draft)

    def test_quoted_source_markup_cannot_escape_inert_json(self):
        _, visible, draft = materials()
        malicious = '</script><script>alert("source injection")</script><img src="https://example.com/tracker">'
        visible["baseline"]["claims"][0]["text"] = malicious
        draft["assessment"]["baseline"]["assertions"][0]["text"] = malicious
        html = render_annotation_form(visible, draft)
        self.assertNotIn(malicious, html)
        self.assertIn("\\u003c/script\\u003e", html)
        data = json.loads(re.search(r'<script id="material" type="application/json">(.*?)</script>', html).group(1))
        self.assertEqual(data["baseline"]["claims"][0]["text"], malicious)

    def test_completed_fixture_compatible_with_authoritative_validator(self):
        packet, visible, _ = materials()
        fixture = completed_annotation(packet)
        self.assertEqual(validate_completed_form(visible, fixture), validate_annotation(packet, fixture))
        fixture["assessment"]["candidate"]["assertions"][0]["verdict"] = "unsupported"
        with self.assertRaisesRegex(EvaluationError, "fulfilled"):
            validate_completed_form(visible, fixture)

    def test_bundle_builds_one_blinded_local_file_and_never_overwrites(self):
        packet, _, _ = materials()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_annotation_bundle(packet, folder / "bundle", ["reviewer-a", "reviewer-b"])
            output = folder / "reviewer-a.html"
            result = build_annotation_form(folder / "bundle" / "reviewer-1", output)
            self.assertFalse(result["truth_scored"])
            self.assertEqual(result["completed_annotations"], 0)
            content = output.read_bytes()
            with self.assertRaises(FileExistsError):
                build_annotation_form(folder / "bundle" / "reviewer-1", output)
            self.assertEqual(content, output.read_bytes())

    def test_browser_completeness_rules_execute_and_match_explicit_fixtures(self):
        from verifin.annotation_form import _SCRIPT
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if node is None:
            self.skipTest("Node unavailable; browser validation execution check skipped")
        packet, visible, blank = materials()
        completed = completed_annotation(packet)
        broken = copy.deepcopy(completed)
        broken["assessment"]["candidate"]["assertions"][0]["verdict"] = "unsupported"
        data = {"material": visible, "annotation": blank}
        setup = "const input=" + json.dumps(data) + ";const document={getElementById:id=>({textContent:JSON.stringify(input[id])})};\n"
        program = setup + _SCRIPT.split("document.getElementById('identity')")[0]
        program += "\nconsole.log(JSON.stringify([validateComplete(" + json.dumps(blank) + "),validateComplete(" + json.dumps(completed) + "),validateComplete(" + json.dumps(broken) + ")]));"
        result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10, check=True)
        blank_errors, complete_errors, broken_errors = json.loads(result.stdout)
        self.assertTrue(blank_errors)
        self.assertEqual(complete_errors, [])
        self.assertTrue(broken_errors)
        subprocess.run([node, "--check"], input=_SCRIPT, text=True, capture_output=True, timeout=10, check=True)


if __name__ == "__main__":
    unittest.main()
