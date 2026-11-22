"""Exercise latest-quarter delivery with real older SEC facts and a lenient review."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests import test_live_latest_quarter as live_fixture
from tests.critic_fixture import fixture_verdicts
from tests.test_live_latest_quarter import EXHIBIT, FILINGS, PRIMARY, QUESTION, URL, FixtureModel
from tests.test_tools import DATA
from verifin.engine import Budget, ResearchEngine
from verifin.evaluation import FrozenCase
from verifin.model import validate_response
from verifin.reporting import render_markdown, report_view
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


CUTOFF = "2026-11-21"
PRIOR = {"entity": "NVDA", "period_start": "2024-07-29", "period_end": "2024-10-27", "source": "replay"}


class LenientReview(FixtureModel):
    def __init__(self, *, margin):
        self.margin = margin

    def complete(self, role, ctx):
        if role == "planner":
            return super().complete(role, ctx)
        requirements = [r["id"] for r in ctx["requirements"]]
        if role == "researcher":
            claim = {"id": "prior-quarter", "constraints": {}, "requirement_ids": requirements,
                     "evidence_ids": [], "calculation_ids": []}
            if self.margin:
                margin, = [c for c in ctx["calculations"] if c.get("period_end") == PRIOR["period_end"]]
                claim.update(text=f"營業利潤率為 {margin['value']:.2f}%。", calculation_ids=[margin["id"]])
            else:
                revenue, = [r for r in ctx["evidence"] if r.get("metric") == "revenue"
                            and r.get("period_end") == PRIOR["period_end"]]
                claim.update(text=f"收入為 {revenue['value'] // 1_000_000:,} 百萬美元。", evidence_ids=[revenue["id"]])
            response = {"summary": claim["text"], "claims": [claim], "answered_requirement_ids": requirements,
                        "unanswered_requirement_ids": []}
        else:
            claim, = ctx["claims"]
            parts = ctx.get("request_parts", {})
            primary = ("part-1" if self.margin else "part-2") if len(parts) > 1 else next(iter(parts), None)
            rows = ([{"part": primary, "status": "answered", "claim_id": claim["id"], "answer_sentence": claim["text"]}]
                    if primary else [])
            rows.extend({"part": part, "status": "gap", "missing_support": "The fixture supplies only one claim."}
                        for part in parts if part != primary)
            response = {"reviewed_claim_ids": [claim["id"]], "reviewed_requirement_ids": requirements, "issues": [],
                        "request_coverage": rows}
        return validate_response(role, fixture_verdicts(role, response, ctx), ctx)


class ReplayPlanner:
    def __init__(self, operands):
        self.step = 0
        self.operands = operands

    def decide(self, ctx):
        self.step += 1
        if self.step == 1:
            return {"action": "tool", "tool": "financial_facts",
                    "arguments": {"entity": "NVDA", "metric": "operating_income", "source": "replay"},
                    "scope": {"entities": ["NVDA"]}, "requirements": [{"id": "latest", "text": QUESTION}]}
        if self.step == 2:
            return {"action": "tool", "tool": "calculate",
                    "arguments": {"operation": "margin", "operand_ids": self.operands}}
        if self.step == 3:
            return {"action": "research"}
        return {"action": "finish"}


class ExactDateReview(LenientReview):
    def __init__(self, *, acquire):
        super().__init__(margin=False)
        self.acquire = acquire

    def complete(self, role, ctx):
        if role != "planner":
            return super().complete(role, ctx)
        if ctx["episode"].get("reviewed"):
            return {"action": "finish"}
        if self.acquire and not ctx["episode"].get("recent_tool_results"):
            return {"action": "tool", "tool": "financial_facts", "arguments": {"metric": "revenue", **PRIOR},
                    "requirements": [{"id": "revenue", "text": ctx["episode"]["request"]}]}
        return {"action": "research"}


class AnswerPeriodTests(TestCase):
    def test_single_quarter_checks_each_displayed_amount_before_allowing_a_baseline(self):
        question = "微软 FY2025 Q1（截至 2024 年 9 月 30 日）的营收是多少？"
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store, DATA)

            def fact(metric, start, end):
                result = tools.execute("financial_facts", {"entity": "MSFT", "source": "replay", "unit": "USD",
                    "metric": metric, "period_start": start, "period_end": end}, sid, {"cutoff": "2025-02-01"})
                row, = result["evidence"]
                return row

            q1 = fact("revenue", "2024-07-01", "2024-09-30")
            income = fact("operating_income", "2024-07-01", "2024-09-30")
            q2 = fact("revenue", "2024-10-01", "2024-12-31")
            prior_q1 = fact("revenue", "2023-07-01", "2023-09-30")
            prior_q2 = fact("revenue", "2023-10-01", "2023-12-31")
        self.assertEqual([r["value"] for r in (q1, income, q2, prior_q1, prior_q2)],
                         [65_585_000_000, 30_552_000_000, 69_632_000_000, 56_517_000_000, 62_020_000_000])
        cases = (
            ("微软 FY2025 Q1 的营收为 69,632 百万美元。", [q1, q2], False),
            ("微软 FY2025 Q1 的营收为 69,632 百万美元，营业利润为 30,552 百万美元。", [q2, income], False),
            ("微软 FY2025 Q1 的营收为 69,632 百万美元，同比基期营收为 62,020 百万美元。", [q2, prior_q2], False),
            ("微软 FY2025 Q1 的营收为 65,585 百万美元，同比基期营收为 56,517 百万美元。", [q1, prior_q1], True),
            ("微软 FY2025 Q1 的营收为 65,585 百万美元，FY2025 Q2 的对比营收为 69,632 百万美元。", [q1, q2], True),
        )
        for statement, evidence, accepted in cases:
            with self.subTest(statement=statement):
                claim = {"id": "claim", "text": statement, "evidence_ids": [r["id"] for r in evidence],
                         "calculation_ids": [], "requirement_ids": ["quarter"], "constraints": {}}
                context = {"episode": {"request": question}, "scope": {"entities": ["MSFT"], "cutoff": "2025-02-01"},
                           "requirements": [{"id": "quarter", "text": question}], "claims": [claim], "evidence": evidence}
                live = [i for i in check_rules(context) if i.get("scope") == "answer_period"]
                frozen = FrozenCase.from_context("single-quarter-amounts", context).context()
                self.assertEqual(live, [i for i in check_rules(frozen) if i.get("scope") == "answer_period"])
                self.assertEqual(not live, accepted)

    def assert_prior_quarter_left_unanswered(self, report, part):
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["verification_available"])
        self.assertEqual(report["claims"][0]["status"], "Insufficient Evidence")
        self.assertEqual(report["claims"][0]["critic_verdict"]["status"], "Supported")
        answer = next(row for row in report_view(report)["question_answers"] if row["part"] == part)
        self.assertEqual(answer["status"], "gap")
        self.assertIn("quarter", answer["missing_support"])
        self.assertNotIn("answer_sentence", answer)
        self.assertNotIn("answered —", render_markdown(report))

    def run_question(self, *, live):
        with TemporaryDirectory() as tmp, SessionStore(Path(tmp) / "test.sqlite") as store:
            sid = store.create_session()["id"]
            root = Path(tmp)
            sources = {"facts.json": ((DATA / "replay/snapshots/sec_nvda_companyfacts.json").read_bytes(),
                                      "https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json"),
                       "submissions.json": (json.dumps({"cik": 1045810, "filings": {"recent": FILINGS}}).encode(),
                                            "https://data.sec.gov/submissions/CIK0001045810.json")}
            manifest = {"companies": [{"ticker": "NVDA", "cik": "0001045810", "name": "NVIDIA CORP",
                                      "facts": "facts.json", "submissions": "submissions.json"}],
                        "documents": [], "files": {}}
            for name, (raw, url) in sources.items():
                (root / name).write_bytes(raw)
                manifest["files"][name] = {"url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                                          "bytes": len(raw), "retrieved_at": "2026-11-21T07:00:00Z"}
            (root / "manifest.json").write_text(json.dumps(manifest))
            registry = ToolRegistry(store, root, live, "Offline test replay@example.org", cache_dir=root / "cache")
            operands = []
            for metric in ("operating_income", "revenue"):
                result = registry.execute("financial_facts", {"metric": metric, **PRIOR}, sid, {"cutoff": CUTOFF})
                evidence, = result["evidence"]
                store.put_evidence(sid, evidence)
                operands.append(evidence["id"])
            with patch("verifin.data.build_opener") as opener:
                opener.return_value.open.side_effect = live_fixture.LatestQuarterTests.responses() if live else AssertionError("Network not permitted")
                engine = ResearchEngine(store, LenientReview(margin=not live), registry,
                                        planner=None if live else ReplayPlanner(operands))
                result = engine.ask(sid, QUESTION, as_of=CUTOFF, budget=Budget(4, 12, 60))
                urls = [c.args[0].full_url for c in opener.return_value.open.call_args_list]
            events = [e for e in store.events(sid) if e["type"] == "tool_call"]
            return result["report"], events, urls

    def test_blocked_latest_candidate_prevents_replayed_prior_margin(self):
        report, events, urls = self.run_question(live=False)
        self.assertEqual(urls, [])
        self.assertEqual(events[0]["observation"]["data"]["status"], "filing_review_required")
        self.assert_prior_quarter_left_unanswered(report, "part-1")

    def test_live_filing_path_rejects_prior_revenue_after_latest_release(self):
        report, events, urls = self.run_question(live=True)
        self.assertEqual(urls[-2:], [URL.replace(EXHIBIT, PRIMARY), URL])
        self.assertTrue(any(e["tool"] == "calculate" for e in events))
        self.assertTrue(any(e.get("period_end") == "2026-10-25" for e in report["evidence"]))
        self.assert_prior_quarter_left_unanswered(report, "part-2")

    def run_exact_lookup(self, cutoff, *, latest=True, acquire=True):
        with TemporaryDirectory() as tmp, SessionStore(Path(tmp) / "test.sqlite") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store, DATA, False, cache_dir=Path(tmp) / "cache")
            with patch("verifin.data.build_opener", side_effect=AssertionError("Network not permitted")) as opener:
                if not acquire:
                    lookup = tools.execute("financial_facts", {"metric": "revenue", **PRIOR}, sid, {"cutoff": cutoff})
                    store.put_evidence(sid, lookup["evidence"][0])
                period = "最新已公布季度" if latest else "截至2024-10-27的季度"
                question = f"截至{cutoff}，根據 SEC 官方數據，NVIDIA {period}的收入是多少？"
                result = ResearchEngine(store, ExactDateReview(acquire=acquire), tools, auto_skills=False).ask(
                    sid, question, as_of=cutoff, budget=Budget(6, 6, 60))
                opener.assert_not_called()
            return result

    def test_exact_lookup_cannot_certify_a_later_cutoff(self):
        for cutoff in ("2026-11-19", CUTOFF):
            with self.subTest(cutoff=cutoff):
                report = self.run_exact_lookup(cutoff)["report"]
                self.assert_prior_quarter_left_unanswered(report, "part-1")
                reason = report_view(report)["question_answers"][0]["missing_support"]
                self.assertIn(f"does not cover the cutoff {cutoff}", reason)
                self.assertIn("2026-10-08", reason)
                self.assertEqual(report["usage"]["tool_calls"], 1)

    def test_exact_lookup_keeps_historical_positive_and_fixed_quarter(self):
        for cutoff, latest in (("2024-11-30", True), (CUTOFF, False)):
            with self.subTest(cutoff=cutoff, latest=latest):
                result = self.run_exact_lookup(cutoff, latest=latest)
                report = result["report"]
                self.assertEqual(report["status"], "complete", report["issues"])
                self.assertEqual(report_view(report)["question_answers"][0]["status"], "answered")
                self.assertEqual(report["usage"]["tool_calls"], 1)

    def test_covered_cutoff_still_requires_amount_for_the_verified_latest_period(self):
        result = self.run_exact_lookup("2026-01-01")
        observed, = result["episode"]["latest_quarter_observations"]
        self.assertEqual(observed["status"], "catalog_verified")
        self.assertNotEqual(observed["period_end"], PRIOR["period_end"])
        self.assert_prior_quarter_left_unanswered(result["report"], "part-1")

    def test_saved_exact_fact_without_a_current_check_leaves_latest_unanswered(self):
        result = self.run_exact_lookup(CUTOFF, acquire=False)
        self.assertFalse(result["episode"].get("latest_quarter_observations"))
        report = result["report"]
        self.assert_prior_quarter_left_unanswered(report, "part-1")
        self.assertIn("No SEC filings catalog", report_view(report)["question_answers"][0]["missing_support"])
        self.assertEqual(report["usage"]["tool_calls"], 0)
