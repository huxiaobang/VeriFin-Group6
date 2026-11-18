import json
import unittest

from tests.test_answer_sections import operating_report
from verifin.model import ModelClient
from verifin.reporting import report_view, render_markdown
from verifin.tools import TOOL_SPECS


def disclosure_report():
    report = operating_report()
    report.update(episode_id="why", scope_version=2)
    report["scope"]["cutoff"] = "2025-09-01"
    bridge = report["income_bridge"]
    quote = "The change was primarily due to the mix of overseas earnings."
    row = next(r for r in bridge["rows"] if r["metric"] == "tax")
    target = {k: row[k] for k in ("metric", "label", "contribution_id")}
    record = {"id": "discussion", "kind": "document", "entity": "NEW",
              "document_report_date": "2025-07-31", "published_at": "2025-08-30",
              "start": 317, "end": 317+len(quote), "text": quote, "snapshot_hash": "hash",
              "locator": "management discussion, chars 317+", "source_url": "https://example.com/release"}
    report["evidence"].append(record)
    report["income_explanation"] = {
        "episode_id": "why", "scope_version": 2, "targets": [target], "gaps": [],
        "current_period": bridge["current_period"], "prior_period": bridge["prior_period"],
        "findings": [{**target, "evidence_id": "discussion", "quote": quote,
                      "quote_start": record["start"], "quote_end": record["end"]}]}
    return report


class IncomeReasonDeliveryTests(unittest.TestCase):
    def test_original_attribution_visible_once_with_its_own_source(self):
        report = disclosure_report()
        note = report["income_explanation"]["findings"][0]
        view = report_view(report)
        first, second = view["question_answers"]
        self.assertIn(note["quote"], first["answer_sentence"])
        self.assertEqual(first["status"], second["status"])
        section, = view["answer_sections"]
        self.assertEqual(section["attribution_sources"][0]["evidence_id"], note["evidence_id"])
        output = render_markdown(report).split("## Answers to your questions")[1].split("## ")[0]
        self.assertEqual(output.count(note["quote"]), 1)
        self.assertIn("需要公司另行量化披露", output)
        self.assertIn("https://example.com/release", output)
        self.assertIn("discussion", output)

    def test_absent_or_unusable_cause_preserves_independent_numbers(self):
        cases = [
            lambda r: r["income_explanation"]["findings"].clear(),
            lambda r: r["evidence"][-1].update(stale=True),
            lambda r: r["evidence"][-1].update(published_at="2026-01-01"),
            lambda r: r["evidence"][-1].update(document_report_date="2025-04-30"),
            lambda r: r["evidence"][-1].update(entity="OTHER"),
            lambda r: r["income_explanation"]["findings"][0].update(quote="This is forged."),
            lambda r: r["evidence"][-1].update(text="The original disclosure has been replaced."),
        ]
        for i, change in enumerate(cases):
            with self.subTest(case=i):
                report = disclosure_report()
                change(report)
                view = report_view(report)
                section, = view["answer_sections"]
                self.assertEqual(section["status"], "partial")
                self.assertIn("本季tax同比变化原因", section["missing_support"])
                self.assertEqual(section["source_table"]["rows"][-1][-1], "-1")
                self.assertNotIn("discussion", section["evidence_ids"])
                self.assertFalse(section.get("attribution_sources"))

    def test_a_component_explanation_can_remain_visible_without_covering_the_whole_line(self):
        report = disclosure_report()
        note = report["income_explanation"]["findings"][0]
        note.update(support_kind="component_context", subject_label="海外盈利构成")
        section, = report_view(report)["answer_sections"]
        self.assertEqual(section["status"], "partial")
        self.assertIn("关于与tax相关的海外盈利构成", section["answer_sentence"])
        self.assertIn(note["quote"], section["answer_sentence"])
        self.assertIn("仍缺少管理层对本季tax整体同比变化的直接解释", section["missing_support"])
        self.assertIn("discussion", section["evidence_ids"])

    def test_compact_planner_and_cached_drafting_review_payloads(self):
        report = disclosure_report()
        receipt = report["income_explanation"]
        ctx = {"income_explanation": receipt, "scope": report["scope"], "tools": TOOL_SPECS,
               "episode": {"request": "Explain the change."}, "evidence": report["evidence"]}
        captured = []

        class PayloadCaptured(Exception):
            pass

        def capture(request, timeout):
            captured.append(json.loads(request.data)["input"])
            raise PayloadCaptured

        client = ModelClient("offline", "fixture", "https://example.invalid", transport=capture,
                             api_mode="responses", cache_shared_tools=True)
        client.planner_view = "compact"
        for role in ("planner", "researcher", "critic"):
            with self.subTest(role=role), self.assertRaises(PayloadCaptured):
                client.complete(role, ctx)
            wire = captured[-1]
            working = next(m for m in wire if isinstance(m["content"], str)
                           and m["content"].startswith("CONTEXT_DATA_JSON"))
            self.assertEqual(json.loads(working["content"].split("\n", 1)[1])["income_explanation"], receipt)
            source = receipt["findings"][0]["quote"]
            self.assertTrue(any(source in str(m["content"]) for m in wire if m["role"] == "user"))
            self.assertFalse(any(source in str(m["content"]) for m in wire if m["role"] == "developer"))
