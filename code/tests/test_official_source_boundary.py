"""Source restrictions apply to automatic acquisition and the dispatched Planner action."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.critic_fixture import fixture_verdicts
from tests.test_tools import DATA
from verifin.data import stable_id
from verifin.engine import Budget, ResearchEngine
from verifin.evaluation import FrozenCase
from verifin.model import validate_response
from verifin.reporting import report_view
from verifin.source_scope import LATEST_COVERAGE_GAP
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


RESTRICTION = "（請僅使用 NVIDIA 投資人關係網站的業績公告，不要查 SEC。）"
RELEASE = "https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-fourth-quarter-and-fiscal-2025"


class Fixture:
    mode = "scripted_issuer_source"

    def __init__(self, case):
        self.case = case
        self.roles = []
        self.first = None

    def complete(self, role, ctx):
        self.roles.append(role)
        if self.first is None:
            self.first = ctx
        if role == "planner":
            if len(self.roles) == 1:
                # Raw out-of-contract attempt to verify enforcement at dispatch,
                # even if a client does not validate the published tool list.
                return {"action":"tool","tool":"financial_facts",
                        "arguments":{"entity":"NVDA","metric":"revenue","source":"live"}}
            if len(self.roles) == 2:
                return {"action":"tool","tool":"read_document","arguments":{
                    "entity":"NVDA","document_id":stable_id("source_doc_",RELEASE)}}
            return {"action":"finish" if ctx["episode"].get("reviewed") else "research"}
        reqs = [r["id"] for r in ctx["requirements"]]
        if role == "researcher":
            original = next(e for e in ctx["evidence"] if e["source_url"] == RELEASE and "$39.3 billion" in e["text"])
            texts = ["NVIDIA FY2025 Q4 營收為 393 億美元。"]
            if self.case in ("mixed","assert_latest"):
                if self.case == "assert_latest":
                    texts = []
                texts.append("NVIDIA 最新已公布季度是 FY2025 Q4，營收為 393 億美元。")
            if self.case == "link":
                texts = [f"NVIDIA FY2025 Q4 的原始業績公告：{RELEASE}"]
            response = {"summary":" ".join(texts),"claims":[{"id":f"claim-{i}","text":text,"constraints":{},
                "evidence_ids":[original["id"]],"calculation_ids":[],"requirement_ids":reqs} for i,text in enumerate(texts)],
                "answered_requirement_ids":reqs,"unanswered_requirement_ids":[]}
        else:
            claims = ctx["claims"]
            response = {"issues":[],"reviewed_claim_ids":[c["id"] for c in claims],"reviewed_requirement_ids":reqs,
                        "request_coverage":[{"part":p,"status":"answered","claim_id":c["id"],
                                             "answer_sentence":c["text"]}
                                            for i,p in enumerate(ctx["request_parts"])
                                            for c in [claims[min(i,len(claims)-1)]]]}
        return validate_response(role,fixture_verdicts(role,response,ctx),ctx)


class OfficialSourceBoundaryTests(TestCase):
    def run_question(self, question, cutoff, case="history", capture=None):
        with TemporaryDirectory() as tmp, SessionStore(Path(tmp)/"session.sqlite") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store,DATA,True,"Offline tests tests@example.org",cache_dir=Path(tmp)/"cache")
            model = Fixture(case)
            with patch("verifin.data.build_opener",side_effect=AssertionError("No network")) as opener:
                report = ResearchEngine(store,model,tools,auto_skills=False,candidate_callback=capture).ask(sid,question,as_of=cutoff,
                           budget=Budget(8,8,60))["report"]
                opener.assert_not_called()
            calls = [e for e in store.events(sid) if e["type"]=="tool_call"]
        self.assertEqual(model.roles[0],"planner")
        self.assertEqual(model.first["episode"]["request"],question)
        self.assertNotIn("financial_facts",{t["name"] for t in model.first["tools"]})
        self.assertTrue(calls)
        self.assertEqual(calls[0]["tool"],"search_documents")
        self.assertEqual(calls[0]["arguments"]["document_ids"],[stable_id("source_doc_",RELEASE)])
        self.assertTrue(all(e["network_requests"]==0 for e in calls))
        self.assertTrue(all(e["source_url"]==RELEASE for e in report["evidence"]))
        return report

    def test_explicit_historical_period_and_cutoff_uses_the_real_issuer_release(self):
        report = self.run_question(f"NVIDIA FY2025 Q4 的營收是多少{RESTRICTION}？","2025-02-27")
        self.assertEqual(report["status"],"complete",report["issues"])
        self.assertTrue(all(a["status"]=="answered" for a in report["request_coverage"]))

    def test_old_issuer_release_cannot_establish_latest_despite_permissive_critic(self):
        report = self.run_question(f"NVIDIA 最新已公布季度的營收是多少{RESTRICTION}？","2026-11-21")
        self.assertEqual(report["status"],"partial")
        answers = report_view(report)["question_answers"]
        self.assertTrue(all(a["status"]=="gap" and LATEST_COVERAGE_GAP in a["missing_support"] for a in answers),answers)
        self.assertEqual(report["claims"][0]["status"],"Supported")
        self.assertIn(report["claims"][0]["id"],report_view(report)["background_claim_ids"])
        misleading = self.run_question(f"NVIDIA 最新已公布季度的營收是多少{RESTRICTION}？","2026-11-21","assert_latest")
        self.assertEqual(misleading["claims"][0]["status"],"Insufficient Evidence")
        self.assertFalse(report_view(misleading)["background_claim_ids"])

    def test_separately_reviewed_historical_answer_survives_the_latest_gap(self):
        question = f"NVIDIA FY2025 Q4 的營收是多少？NVIDIA 最新已公布季度的營收是多少{RESTRICTION}？"
        report = self.run_question(question,"2026-11-21","mixed")
        answers = report_view(report)["question_answers"]
        self.assertEqual(report["status"],"partial")
        self.assertEqual(answers[0]["status"],"answered")
        self.assertIn("FY2025 Q4",answers[0]["answer_sentence"])
        self.assertEqual(answers[1]["status"],"gap")
        self.assertIn(LATEST_COVERAGE_GAP,answers[1]["missing_support"])

    def test_request_for_original_link_only_remains_answerable(self):
        question = f"NVIDIA FY2025 Q4 的業績公告鏈接是什麼{RESTRICTION}？"
        report = self.run_question(question,"2025-02-27","link")
        row, = report_view(report)["question_answers"]
        self.assertEqual(row["status"],"answered")
        self.assertIn(RELEASE,row["answer_sentence"])

    def test_frozen_review_recovers_the_public_request_and_the_same_latest_defect(self):
        question = f"NVIDIA 最新已公布季度的營收是多少{RESTRICTION}？"
        captured = []
        self.run_question(question,"2026-11-21","assert_latest",captured.append)
        live, = captured
        frozen = FrozenCase.from_context("issuer-release-latest-old-quarter",live).context()
        self.assertEqual(frozen["request"],question)
        self.assertNotIn("episode",frozen)
        self.assertNotIn("source_document_scope",frozen)
        self.assertNotIn("rule_findings",frozen)
        issues = check_rules(live)
        self.assertTrue(any(r["missing_support"]==LATEST_COVERAGE_GAP for r in issues))
        self.assertEqual(check_rules(frozen),issues)
