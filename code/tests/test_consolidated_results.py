"""A quarter's consolidated totals and as-reported rates stay together."""
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

from tests.critic_fixture import fixture_verdicts
from tests.test_tools import DATA
from verifin.consolidated_results import defects
from verifin.data import stable_id
from verifin.engine import Budget, ResearchEngine, digest
from verifin.evaluation import FrozenCase
from verifin.model import validate_response
from verifin.reporting import report_view
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


QUESTION = ("As of 2025-02-01, for Microsoft FY2025 Q2 (quarter ended December 31, 2024), what were consolidated GAAP "
            "revenue and operating income, and what were their reported year-over-year growth rates? Cite the earnings release. Answer concisely.")
SCOPE = {"entities": ["MSFT"], "cutoff": "2025-02-01", "period_end": "2024-12-31"}
REVENUE = "Consolidated GAAP revenue was USD 69.632 billion, up 12% year over year."
INCOME = "Consolidated GAAP operating income was USD 31.653 billion, up 17% year over year."
TRUE = REVENUE + " " + INCOME


class FixtureModel:
    mode = "scripted_ordinary_finance"

    def __init__(self, tools, text):
        self.text = text
        source = next(d for d in tools.data.manifest["documents"] if d["source"].endswith("msft_fy25q2_ir.html"))
        self.url = tools.data.info(source["source"])["url"]
        self.doc_id = stable_id("source_doc_", self.url)
        release = tools.data.raw(source["text"]).decode()
        self.offsets = [release.index(marker) for marker in ("MICROSOFT CORPORATION INCOME STATEMENTS",
                        "Revenue in Intelligent Cloud was $25.5 billion", "Revenue was $69.6 billion")]

    def complete(self, role, ctx):
        if role == "planner":
            response = self.plan(ctx)
        elif role == "researcher":
            table = [r for r in ctx["evidence"] if r.get("cell_type") == "statement_amount_cell"
                     and r.get("metric") in {"revenue", "operating_income"} and r["period_start"] == "2024-10-01"]
            original = [r for r in ctx["evidence"] if r.get("kind") == "document"]
            source = "Microsoft FY2025 Q2 earnings release: " + self.url
            claims = [{"id": "figures", "text": self.text, "constraints": {}, "calculation_ids": [],
                       "requirement_ids": ["results"], "evidence_ids": [r["id"] for r in table + original]},
                      {"id": "source", "text": source, "constraints": {}, "calculation_ids": [],
                       "requirement_ids": ["results"], "evidence_ids": [original[-1]["id"]]}]
            response = {"summary": self.text + " " + source, "claims": claims, "answered_requirement_ids": ["results"],
                        "unanswered_requirement_ids": []}
        else:
            claims = {r["id"]: r for r in ctx["claims"]}
            response = {"issues": [], "reviewed_claim_ids": list(claims), "reviewed_requirement_ids": ["results"],
                        "request_coverage": [{"part": part, "status": "answered", "claim_id": cid,
                                              "answer_sentence": claims[cid]["text"]} for part, cid in zip(
                                                  ctx["request_parts"], ("figures", "source"), strict=True)]}
        return validate_response(role, fixture_verdicts(role, response, ctx), ctx)

    def plan(self, ctx):
        results = ctx["episode"].get("recent_tool_results", [])
        if ctx["episode"].get("reviewed"):
            return {"action": "finish"}
        if not results:
            return {"action": "tool", "tool": "search_documents", "scope": SCOPE,
                    "arguments": {"entity": "MSFT", "query": "Microsoft FY2025 Q2 earnings release operating income",
                                  "period_end": "2025-01-30"},
                    "requirements": [{"id": "results", "text": QUESTION}]}
        position = len(results) - 1
        if position < len(self.offsets):
            return {"action": "tool", "tool": "read_document", "arguments": {
                "entity": "MSFT", "document_id": self.doc_id, "offset": self.offsets[position],
                "limit_chars": 2600 if position == 0 else 700}}
        return {"action": "research"}


@skipUnless(find_spec("lxml"), "native HTML disclosure parser is optional")
class ConsolidatedResultsTest(TestCase):
    def run_question(self, text, *, candidate_callback=None):
        with TemporaryDirectory() as folder, SessionStore(Path(folder) / "session.sqlite3") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store, DATA, cache_dir=Path(folder) / "cache")
            engine = ResearchEngine(store, FixtureModel(tools, text), tools, candidate_callback=candidate_callback)
            result = engine.ask(sid, QUESTION, budget=Budget(16, 16, 240))["report"]
            receipts = [event for event in store.events(sid) if event["type"] == "tool_call"]
        self.assertEqual([(e["tool"], e["ok"]) for e in receipts], [
            ("search_documents", False), ("read_document", True), ("read_document", True), ("read_document", True)])
        self.assertTrue(all(digest(e["observation"]) == e["observation_sha256"] and
                            e["observation"]["data"]["network_requests"] == 0 for e in receipts))
        return result, report_view(result)["question_answers"]

    def test_public_frozen_question_applies_the_same_checks_as_the_live_draft(self):
        candidates = []
        self.run_question(TRUE.replace("69.632","25.544").replace("12%","19%"), candidate_callback=candidates.append)
        live = candidates[0]
        frozen = FrozenCase.from_context("consolidated", live).context()
        def defects(context):
            return {(i["issue_type"], i["target_id"], i["missing_support"]) for i in check_rules(context)}
        live_findings = defects(live)
        self.assertTrue(any(kind == "numerical_inconsistency" and target == "figures"
                            for kind, target, _ in live_findings))
        self.assertEqual(defects(frozen), live_findings)

    def test_recovered_lookup_uses_the_issuer_reported_results(self):
        with_currency = REVENUE + " " + INCOME.replace("17% year over year.", "17% year over year (16% in constant currency).")
        with_segment = TRUE.replace("12% year over year.", "12% year over year, while Intelligent Cloud grew 19%.")
        for text in (TRUE, with_currency, with_segment):
            with self.subTest(text=text):
                result, answers = self.run_question(text)
                self.assertEqual((result["status"], result["stop_reason"]), ("complete", "completed"))
                self.assertEqual([answer["status"] for answer in answers], ["answered", "answered"])
                self.assertEqual(answers[0]["answer_sentence"], text)
                self.assertFalse(result["issues"])

    def test_segment_figures_and_constant_currency_rates_do_not_answer_the_consolidated_question(self):
        for text, diagnostic in ((TRUE.replace("69.632", "25.544").replace("12%", "19%"), "USD 25.544 billion"),
                                 (TRUE.replace("12%", "19%"), "reported revenue growth of 12%"),
                                 (TRUE.replace("17%", "16%"), "reported operating income growth of 17%"),
                                 (TRUE.replace("17% year over year.", "16% year over year (16% in constant currency)."),
                                  "reported operating income growth of 17%")):
            with self.subTest(text=text):
                result, answers = self.run_question(text)
                self.assertEqual((result["status"], result["stop_reason"]), ("partial", "unresolved_items"))
                self.assertEqual([answer["status"] for answer in answers], ["gap", "answered"])
                self.assertEqual({claim["id"]: claim["status"] for claim in result["claims"]},
                                 {"figures": "Insufficient Evidence", "source": "Supported"})
                self.assertTrue(any(diagnostic in issue["missing_support"] for issue in result["issues"]))

    def test_equivalent_amount_wording_rejects_the_segment_amount_and_rate(self):
        forms = [
            "Microsoft reported consolidated GAAP revenue of USD {amount} billion, up {rate}% year over year.",
            "Consolidated GAAP total revenues: USD {amount} billion, up {rate}% year over year.",
            "Consolidated GAAP revenue reached USD {amount} billion, up {rate}% year over year.",
            "微软的合并 GAAP 营收为 {chinese} 亿美元，同比增长 {rate}%。",
        ]
        for form in forms:
            for amount, chinese, rate, status in [
                ("69.632", "696.32", 12, "answered"),
                ("25.544", "255.44", 19, "gap"),
                ("69.632", "696.32", 19, "gap"),
            ]:
                text = form.format(amount=amount, chinese=chinese, rate=rate) + " " + INCOME
                with self.subTest(text=text):
                    result, answers = self.run_question(text)
                    self.assertEqual([a["status"] for a in answers], [status, "answered"])
                    if status == "gap":
                        self.assertEqual(result["status"], "partial")
                        self.assertIn("reported revenue growth of 12%", answers[0]["missing_support"])
                        if amount == "25.544":
                            self.assertIn("USD 69,632,000,000", answers[0]["missing_support"])
                    else:
                        self.assertEqual(result["status"], "complete")
                        self.assertEqual(answers[0]["answer_sentence"], text)

    def test_the_amount_check_requires_a_single_reported_level_and_context(self):
        result, _ = self.run_question(TRUE)
        records = {row["id"]: row for row in result["evidence"]}
        companies = [{"ticker": "MSFT", "name": "Microsoft", "aliases": ["微软"]},
                     {"ticker": "NVDA", "name": "NVIDIA", "aliases": ["英伟达"]}]

        def check(text, dimensions=None, question=QUESTION, scope=SCOPE):
            claim = {"text": text, "constraints": dimensions or {}}
            return list(defects(claim, list(records.values()), records, scope, question, companies=companies))

        for text in [
            "Consolidated GAAP revenue increased by USD 7.612 billion.",
            "合并 GAAP 营收增加了 76.12 亿美元。",
            "Intelligent Cloud segment revenue of USD 25.544 billion, up 19% year over year.",
            "Forecast: consolidated GAAP revenue of USD 25.544 billion.",
            "If consolidated GAAP revenue were USD 25.544 billion, costs would be higher.",
            "Consolidated GAAP revenue is USD 25.544 billion on a constant-currency basis.",
            "Consolidated GAAP operating income reached USD 31.653 billion, up 16% year over year in constant currency.",
            "Consolidated GAAP revenue was USD 69.632 billion, up 6% quarter over quarter.",
            'The issuer wrote “Revenue in Intelligent Cloud was $25.5 billion and increased 19%”.',
            'The phrase “consolidated GAAP revenue was USD 25.544 billion” is not the historical result.',
            "NVIDIA consolidated GAAP revenue: USD 25.544 billion.",
            "FY2024 Q2 consolidated GAAP revenue was USD 62.020 billion.",
            "For the quarter ended December 31, 2023, consolidated GAAP revenue was USD 62.020 billion.",
            "Consolidated GAAP revenue and operating income totaled USD 101.285 billion.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(check(text), [])

        wrong = "Reported revenue of USD 25.544 billion."
        dimensions = {"entity": "MSFT", "metric": "revenue", "business_scope": "consolidated", "basis": "US-GAAP",
                      "period_start": "2024-10-01", "period_end": "2024-12-31"}
        self.assertIn("USD 69,632,000,000", check(wrong, dimensions)[0][1])
        for same_measure in ("Consolidated U.S. GAAP revenue came in at USD 25.544 billion.",
                             "微软合并 GAAP 营业收入同比增加至 255.44 亿美元。"):
            self.assertIn("USD 69,632,000,000", check(same_measure)[0][1])
        self.assertEqual(check(wrong), [])
        self.assertEqual(check(wrong, {**dimensions, "period_start": "2024-07-01"}), [])
        self.assertEqual(check(wrong, dimensions, QUESTION + " Include FY2024 Q2."), [])
        self.assertEqual(check(wrong, dimensions, QUESTION + " Include NVIDIA as well."), [])
        self.assertEqual(check(wrong, dimensions, scope={**SCOPE, "entities": ["MSFT", "NVDA"]}), [])
