"""Keep observable price coverage and model failures distinct in reports."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_engine import ScriptedModel
from tests.test_tools import DATA
from verifin.engine import Budget, ResearchEngine
from verifin.model import ModelError, ProviderOutputError
from verifin.reporting import render_markdown, report_view
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


QUESTION = ("截至 2026 年 11 月 19 日，NVDA 自 10 月 1 日以来的股价表现如何？这段期间是否持续下跌？"
            "选出跌幅最大的两个交易日，列出同期公开披露或报道，并区分已证实的事件和市场归因。")
WINDOW = {"entity": "NVDA", "start_date": "2026-10-01", "end_date": "2026-11-19", "cutoff": "2026-11-19"}


class MarketDeliveryTests(unittest.TestCase):
    def run_question(self, question, script):
        with TemporaryDirectory() as directory, SessionStore(Path(directory) / "test.sqlite") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store, DATA / "replay", cache_dir=Path(directory) / "cache")
            result = ResearchEngine(store, ScriptedModel(script), tools).ask(sid, question, budget=Budget(16, 16, 240))
            return result["report"], store.events(sid)

    def test_planner_failure_leaves_both_source_gaps_and_saved_sources_visible(self):
        script = [
            ("planner", {"action": "tool", "tool": "market_prices", "arguments": {**WINDOW, "source": "replay"},
                         "scope": {"entities": ["NVDA"]}, "requirements": [{"id": "market", "text": QUESTION}]}),
            ("planner", {"action": "tool", "tool": "search_documents", "arguments": {
                "entity": "NVDA", "query": "NVIDIA revenue demand operating earnings", "limit": 3}}),
            ("planner", {"action": "tool", "tool": "market_prices", "arguments": {**WINDOW, "source": "live"}}),
            ("planner", ModelError("raw provider response must stay private")),
        ]
        report, events = self.run_question(QUESTION, script)
        self.assertEqual((report["status"], report["stop_reason"], report["delivery_stage"]),
                         ("partial", "execution_error", "no_draft"))
        view = report_view(report)
        error = next(e for e in events if e["type"] == "model_call" and not e["ok"])
        self.assertEqual(error["event_sequence"], report["planner_error"]["event_sequence"])
        self.assertEqual(error["diagnostic_code"], "model_error_unclassified")
        self.assertEqual(error["usage"]["total_tokens"], 10)
        markdown = render_markdown(report)
        for text in (view["stage_message"], markdown):
            for visible in ("2026-10-01 至 2026-11-19", "随包行情缺少精确区间端点", "未配置实时行情来源",
                            "无法核验该区间的股价走势或按单日跌幅排序", f"Activity #{error['event_sequence']}"):
                self.assertIn(visible, text)
            self.assertNotIn("raw provider response", text)
        self.assertEqual(len(report["episode_material_ids"]["evidence"]), 3)
        self.assertTrue(all(record["source_url"] in markdown for record in report["evidence"]))
        self.assertFalse(report["calculations"])
        self.assertTrue(all(answer["status"] == "no_draft" for answer in view["question_answers"]))

    def test_successful_prices_clear_matching_previous_failure(self):
        args = {"entity": "NVDA", "start_date": "2025-02-24", "end_date": "2025-03-03"}
        question = "What were NVDA's closing prices on 2025-02-24 and 2025-03-03?"
        script = [
            ("planner", {"action": "tool", "tool": "market_prices", "arguments": {**args, "source": "live"},
                         "scope": {"entities": ["NVDA"]}, "requirements": [{"id": "prices", "text": question}]}),
            ("planner", {"action": "tool", "tool": "market_prices", "arguments": {**args, "source": "replay"}}),
            ("planner", ProviderOutputError("invalid_provider_json")),
        ]
        report, events = self.run_question(question, script)
        self.assertEqual([e["ok"] for e in events if e["type"] == "tool_call"], [False, True])
        self.assertEqual(report["planner_error"]["diagnostic_code"], "invalid_provider_json")
        self.assertNotIn("tool_fault", report)
        self.assertIn("invalid_provider_json", report_view(report)["stage_message"])

    def test_review_of_other_documents_does_not_erase_price_coverage(self):
        import json
        from verifin.data import stable_id
        manifest = json.loads((DATA / "replay" / "manifest.json").read_text())
        doc = next(row for row in manifest["documents"] if row["entity"] == "NVDA")
        document_id = stable_id("source_doc_", manifest["files"][doc["source"]]["url"])
        script = [("planner", {"action": "tool", "tool": "market_prices", "arguments": {**WINDOW, "source": "replay"},
                               "scope": {"entities": ["NVDA"]}, "requirements": [{"id": "prices", "text": QUESTION}]})]
        for page in range(9):
            script.append(("planner", {"action": "tool", "tool": "read_document", "arguments": {
                "entity": "NVDA", "document_id": document_id, "offset": page * 400, "limit_chars": 400}}))
        script.append(("planner", ModelError("simulated planner failure")))
        report, events = self.run_question(QUESTION, script)
        self.assertEqual(report["stop_reason"], "execution_error")
        self.assertEqual(sum(e["type"] == "tool_call" and e["tool"] == "market_prices" for e in events), 1)
        self.assertIn("随包行情缺少精确区间端点", report["tool_fault"]["reason"])
