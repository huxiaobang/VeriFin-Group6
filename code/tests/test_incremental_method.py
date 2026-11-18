import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_statement_amount_cells
from verifin.engine import ResearchEngine
from verifin.reporting import question_answers
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


QUESTION = "AAA 这季比去年同期每多卖一块钱能多留多少营业利润？"


def make_corpus(directory, **opts):
    source, excerpt = test_statement_amount_cells.StatementAmountTests.fixture(**opts)
    doc = {"entity":"AAA", "source":"release.htm", "text":"release.txt", "form":"8-K Exhibit 99.1",
           "period_end":"2024-12-29", "published_at":"2025-02-20", "publication_precision":"date_only",
           "extraction_method":"original row text"}
    files = {}
    for key, content in (("source", source), ("text", excerpt)):
        raw = content.encode()
        (directory/doc[key]).write_bytes(raw)
        files[doc[key]] = {"sha256":hashlib.sha256(raw).hexdigest(), "url":"https://example.org/release",
                           "retrieved_at":"2025-02-21"}
    (directory/"manifest.json").write_text(json.dumps({"companies":[{"ticker":"AAA", "name":"Example", "cik":"1"}],
                                                       "documents":[doc], "files":files}))


def offline_report(corpus, question, cutoff):
    store = SessionStore(":memory:")
    engine = ResearchEngine(store, None, ToolRegistry(store, corpus, allow_network=False))
    sid = store.create_session()["id"]
    report = engine.ask(sid, question, as_of=cutoff)["report"]
    return report, [e for e in store.events(sid) if e.get("type") == "tool_call"]


@unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML statement parser is optional")
class IncrementalMarginTests(unittest.TestCase):
    def ask(self, question=QUESTION, **source_options):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_corpus(directory, **source_options)
            report, calls = offline_report(directory, question, "2025-02-21")
        self.assertEqual(report["usage"]["model_calls"], 0)
        return report

    def test_zero_and_rounded_denominator_keep_both_differences(self):
        for prior, excerpt in (("1,100", "无定义"), ("1,099", "舍入误差")):
            with self.subTest(prior=prior):
                report = self.ask(prior=prior)
                self.assertEqual([row["operation"] for row in report["calculations"]], ["difference"]*2)
                answer, = question_answers(report)
                self.assertIn(excerpt, answer["answer_sentence"])
                self.assertFalse(any(r["operation"] == "margin" for r in report["calculations"]))

    def test_contracting_revenue_can_coexist_with_rising_profit(self):
        report = self.ask(prior="1,200")
        self.assertIn("每少卖 1 加元，营业利润增加约 1.000 加元", report["skill_notes"][0]["text"])
        self.assertEqual(report["calculations"][-1]["value"], -100)
        self.assertEqual(len(report["skill_notes"][0]["evidence_ids"]), 4)

    def test_profit_missing_preserves_revenue_and_stops(self):
        report = self.ask(omit_income=True)
        self.assertEqual([row["value"] for row in report["calculations"]], [110000])
        self.assertIn("本季营业利润", report["skill_assessments"][0]["gaps"][0])
        answer, = question_answers(report)
        self.assertEqual(answer["status"], "partial")
        self.assertIn("+110,000", answer["answer_sentence"])
        self.assertIn("本季营业利润", answer["missing_support"])
        self.assertLessEqual(report["usage"]["tool_calls"], 2)

    def test_adjusted_earnings_cannot_be_replaced_by_gaap(self):
        report = self.ask(question="AAA 这季比去年同期每多卖一块钱能多留多少调整后 EBITDA？")
        self.assertFalse(report["calculations"])
        self.assertIn("利润定义", report["skill_assessments"][0]["gaps"][0])
