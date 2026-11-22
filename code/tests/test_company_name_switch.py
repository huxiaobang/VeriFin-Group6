"""Verified company names use the existing issuer boundary before planning."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tests.critic_fixture import fixture_verdicts
from tests.test_tools import DATA
from verifin.engine import Budget, ResearchEngine, digest
from verifin.evaluation import FrozenCase
from verifin.model import validate_response
from verifin.reporting_period import explicit_quarter_end
from verifin.skill_calculations import target_labels
from verifin.skill_routing import focus_of_switch, named_issuers, resolve_issuer_scope
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


QUESTION = "换成 NVIDIA，看看 FY2025 Q4 的营业利润率。"
CUT = "2025-03-01"


class MarginFixture:
    mode = "scripted_company_switch"

    def __init__(self):
        self.first = None

    def complete(self, role, ctx):
        if role == "planner":
            if self.first is None:
                self.first = {"scope": ctx["scope"], "quarter": ctx["episode"]["skill_fiscal_quarter"],
                              "evidence": list(ctx["evidence"]), "calculations": list(ctx["calculations"])}
            response = self.plan(ctx)
        else:
            requirements = [r["id"] for r in ctx["requirements"]]
            if role == "researcher":
                margin = next(c for c in ctx["calculations"] if c["metric"] == "operating_income_margin")
                text = f"NVIDIA FY2025 Q4 营业利润率为 {margin['value']:.2f}%。"
                response = {"claims": [{"id": "nvda-q4", "text": text, "constraints": {},
                                        "evidence_ids": [], "calculation_ids": [margin["id"]],
                                        "requirement_ids": requirements}], "summary": text,
                            "answered_requirement_ids": requirements, "unanswered_requirement_ids": []}
            else:
                response = {"issues": [], "reviewed_claim_ids": ["nvda-q4"], "reviewed_requirement_ids": requirements}
        return validate_response(role, fixture_verdicts(role, response, ctx), ctx)

    @staticmethod
    def plan(ctx):
        if ctx["episode"].get("reviewed"):
            return {"action": "finish"}
        scope = ctx["scope"]
        evidence, calculations = ctx["evidence"], ctx["calculations"]
        for metric in ("revenue", "operating_income"):
            facts = {r["period_end"]: r["id"] for r in evidence if r.get("kind") == "financial_fact"
                     and r.get("metric") == metric and r.get("period_start") == "2024-01-29"}
            ends = ("2025-01-26", "2024-10-27")
            for end in ends:
                if end not in facts:
                    action = {"action": "tool", "tool": "financial_facts", "arguments": {
                        "entity": "NVIDIA Corporation", "source": "replay", "metric": metric,
                        "period_start": "2024-01-29", "period_end": end}}
                    if not scope.get("requirements"):
                        action["requirements"] = [{"id": "margin", "text": QUESTION}]
                    return action
            if not any(c.get("metric") == metric for c in calculations):
                return {"action": "tool", "tool": "calculate", "arguments": {
                    "operation": "derive_quarter", "operand_ids": [facts[end] for end in ends]}}
        if not any(c.get("metric") == "operating_income_margin" for c in calculations):
            ids = {c["metric"]: c["id"] for c in calculations}
            return {"action": "tool", "tool": "calculate", "arguments": {"operation": "margin",
                    "operand_ids": [ids["operating_income"], ids["revenue"]]}}
        return {"action": "research"}


class CompanyNameSwitchTests(TestCase):
    def test_localized_aliases_keep_or_change_the_actual_cik(self):
        with SessionStore(":memory:") as store:
            tools = ToolRegistry(store, DATA)
            sid = store.create_session()["id"]
            catalog = tools.data.manifest["companies"]
            scope = {"entities": ["MSFT"]}
            for name, entity in (("微软", "MSFT"), ("微軟", "MSFT"), ("英伟达", "NVDA"), ("輝達", "NVDA")):
                with self.subTest(name=name):
                    message = f"把分析期間改為{name} FY2025 Q1。"
                    self.assertEqual(resolve_issuer_scope(message, [], scope, catalog), ([entity], entity))
                    discovery = tools.execute("discover_company", {"query": name, "source": "replay"}, sid, scope)
                    self.assertEqual([row["entity"] for row in discovery["data"]["companies"]], [entity])
            self.assertEqual(resolve_issuer_scope("把分析期間改為天外食品 FY2025 Q1。", [], scope, catalog), ([], None))
            self.assertEqual(resolve_issuer_scope("把分析期間改為第四季度。", [], scope, catalog), (["MSFT"], "MSFT"))
        self.assertEqual(explicit_quarter_end("微軟 FY2025 Q1（截至 2024 年 9 月 30 日）"), "2024-09-30")
        self.assertEqual(target_labels("比較微軟 FY2025 Q1 和 Q2 的營收"), {"2025Q1", "2025Q2"})
        self.assertIsNone(explicit_quarter_end("比較微軟 FY2025 Q1 和 Q2 的營收"))

    def test_reopened_dated_quarter_requires_matching_evidence(self):
        request = "把分析期間改為微軟 FY2025 Q1（截至 2024 年 9 月 30 日），這一季的營收是多少？"
        expected = "2024-09-30"

        class QuarterlyRevenueFixture:
            def __init__(self, earlier):
                self.earlier = earlier
                self.first = None

            def complete(self, role, ctx):
                if role == "planner":
                    if self.first is None:
                        self.first = ctx
                    if ctx["episode"].get("reviewed"):
                        return {"action": "finish"}
                    if not self.earlier and not any(e.get("period_end") == expected for e in ctx["evidence"]):
                        return {"action": "tool", "tool": "financial_facts", "arguments": {
                            "entity": "微軟", "source": "replay", "metric": "revenue",
                            "period_start": "2024-07-01", "unit": "USD"}}
                    return {"action": "research"}
                requirements = [r["id"] for r in ctx["requirements"]]
                row = next(e for e in ctx["evidence"] if e.get("kind") == "financial_fact"
                           and (e.get("period_end") != expected if self.earlier else e.get("period_end") == expected))
                sentence = f"營收為 {row['value'] // 1_000_000:,} 百萬美元。"
                if role == "researcher":
                    response = {"claims": [{"id": "revenue", "text": sentence, "constraints": {},
                                            "evidence_ids": [row["id"]], "calculation_ids": [],
                                            "requirement_ids": requirements}], "summary": sentence,
                                "answered_requirement_ids": requirements, "unanswered_requirement_ids": []}
                else:
                    response = {"issues": [], "reviewed_claim_ids": ["revenue"], "reviewed_requirement_ids": requirements,
                                "request_coverage": [{"part": part, "status": "answered", "claim_id": "revenue",
                                                      "answer_sentence": sentence} for part in ctx.get("request_parts", {})]}
                return validate_response(role, fixture_verdicts(role, response, ctx), ctx)

        for wrong in (False, True):
            with self.subTest(wrong_quarter=wrong), TemporaryDirectory() as folder:
                path = Path(folder) / "session.sqlite3"
                with SessionStore(path) as store:
                    sid = store.create_session()["id"]
                    scope = {"entities": ["MSFT"], "cutoff": CUT,
                             "period_start": "2024-10-01", "period_end": "2024-12-31"}
                    store.update_scope(sid, scope)
                    old = ToolRegistry(store, DATA).execute("financial_facts", {"entity": "MSFT", "metric": "revenue"}, sid, scope)
                    row, = old["evidence"]
                    store.put_evidence(sid, row)
                candidates = []
                model = QuarterlyRevenueFixture(wrong)
                with SessionStore(path) as store:
                    report = ResearchEngine(store, model, ToolRegistry(store, DATA), candidate_callback=candidates.append).ask(
                        sid, request, budget=Budget(8, 8, 120))["report"]
                self.assertEqual(model.first["scope"]["entities"], ["MSFT"])
                self.assertEqual(model.first["scope"]["period_end"], expected)
                self.assertNotIn("period_start", model.first["scope"])
                self.assertEqual(model.first["episode"]["skill_fiscal_quarter"]["quarter"], "2025Q1")
                self.assertIn(row["id"], [e["id"] for e in model.first["evidence"]])
                issues = [i for i in report["issues"] if i.get("scope") == "answer_period"]
                frozen = FrozenCase.from_context("real-dated-quarter", candidates[0]).context()
                self.assertEqual(issues, [i for i in check_rules(frozen) if i.get("scope") == "answer_period"])
                self.assertEqual(bool(issues), wrong)
                self.assertEqual(report["status"], "partial" if wrong else "complete", report["issues"])
                if not wrong:
                    self.assertIn("65,585", report["claims"][0]["text"])

    def test_qualified_names_choose_one_issuer_and_partial_names_abstain(self):
        with SessionStore(":memory:") as store:
            tools = ToolRegistry(store, DATA)
            catalog = tools.data.manifest["companies"]
            for name in ("NVIDIA", "NViDia", "NVIDIA Corp.", "NVIDIA Corporation", "NVDA"):
                with self.subTest(name=name):
                    self.assertEqual(named_issuers(focus_of_switch(f"换成 {name}，看 FY2025 Q4。"), catalog), ["NVDA"])
            for name in ("NVID", "NVIDIAx"):
                with self.subTest(name=name):
                    self.assertEqual(named_issuers(focus_of_switch(f"换成 {name}，看 FY2025 Q4。"), catalog), [])
            self.assertEqual(named_issuers("Microsoft FY2025 Q2", catalog), ["MSFT"])
            sid = store.create_session()["id"]
            exact = tools.execute("discover_company", {"query": "NVIDIA Corporation", "source": "replay"}, sid, {})
            self.assertEqual([p["entity"] for p in exact["data"]["companies"]], ["NVDA"])
            self.assertEqual(exact["data"]["network_requests"], 0)

    def test_shared_name_and_share_classes_do_not_pin_one_issuer(self):
        catalog = [{"ticker": ticker, "cik": cik, "name": name, "aliases": aliases} for ticker, cik, name, aliases in (
            ("GOOG", "0001652044", "Alphabet Inc.", ["Alphabet"]),
            ("GOOGL", "0001652044", "Alphabet Inc.", ["Alphabet"]),
            ("EX-A", "0000000001", "Example Group Inc.", ["Example Group"]),
            ("EX-B", "0000000002", "Example Group Inc.", ["Example Group"]))]
        scope = {"entities": ["MSFT"]}
        for name, expected in (("Alphabet", ["GOOG", "GOOGL"]), ("Alphabet Inc.", ["GOOG", "GOOGL"]),
                               ("Example Group", ["EX-A", "EX-B"])):
            participants, issuer = resolve_issuer_scope(f"换成 {name}。", [], scope, catalog)
            self.assertEqual(participants, expected)
            self.assertIsNone(issuer)
        self.assertEqual(resolve_issuer_scope("换成 GOOGL。", [], scope, catalog), (["GOOGL"], "GOOGL"))

    def test_reopening_a_microsoft_session_uses_nvda_before_planning_and_completes(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "session.sqlite3"
            cache = Path(folder) / "cache"
            with SessionStore(path) as store:
                sid = store.create_session()["id"]
                tools = ToolRegistry(store, DATA, cache_dir=cache)
                old_scope = {"entities": ["MSFT"], "cutoff": CUT, "period_start": "2024-10-01", "period_end": "2024-12-31"}
                store.update_scope(sid, old_scope)
                old = tools.execute("financial_facts", {"entity": "MSFT", "metric": "revenue"}, sid, old_scope)
                previous, = old["evidence"]
                store.put_evidence(sid, previous)
            model = MarginFixture()
            with SessionStore(path) as store:
                result = ResearchEngine(store, model, ToolRegistry(store, DATA, cache_dir=cache)).ask(
                    sid, QUESTION, as_of=CUT, budget=Budget(16, 16, 240))["report"]
                calls = [event for event in store.events(sid) if event["type"] == "tool_call"]
                self.assertIn(previous["id"], {r["id"] for r in store.get_context(sid, report_mode="none")["evidence"]})
            self.assertEqual(model.first["scope"]["entities"], ["NVDA"])
            self.assertFalse(model.first["evidence"])
            self.assertNotIn("period_end", model.first["scope"])
            self.assertEqual(model.first["quarter"], {"entity": "NVDA", "quarter": "2025Q4"})
            self.assertEqual((result["status"], result["stop_reason"]), ("complete", "completed"), result["issues"])
            self.assertEqual(len(calls), 7)
            self.assertTrue(all(digest(e["observation"]) == e["observation_sha256"] for e in calls))
            self.assertTrue(all(e["observation"]["data"]["network_requests"] == 0 for e in calls))
            self.assertEqual({r["entity"] for r in result["evidence"]}, {"NVDA"})
            results = {r["metric"]: r for r in result["calculations"]}
            self.assertEqual(results["revenue"]["value"], 39_331_000_000)
            self.assertEqual(results["operating_income"]["value"], 24_034_000_000)
            margin = results["operating_income_margin"]
            self.assertAlmostEqual(margin["value"], 24_034 / 39_331 * 100)
            self.assertEqual((margin["period_start"], margin["period_end"]), ("2024-10-28", "2025-01-26"))
