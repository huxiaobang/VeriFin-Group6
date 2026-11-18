"""Protocol integration with scripted decisions and real frozen source tools.

These tests do not evaluate a live model's planning or semantic-review quality.
The model fixture chooses the workflow; real tool outputs supply every value,
operand and citation, and the real controller/rules/storage enforce its state.
"""

import copy
from pathlib import Path
import tempfile
import unittest

from tests.critic_fixture import fixture_verdicts
from verifin.engine import Budget, ResearchEngine
from verifin.model import validate_response
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


DATA = Path(__file__).resolve().parents[1] / "data" / "replay"


def selected(records, *, entity, metric, start, end):
    matches = [record for record in records
               if record.get("entity") == entity and record.get("metric") == metric
               and record.get("period_start") == start and record.get("period_end") == end
               and not record.get("stale")]
    if len(matches) != 1:
        raise AssertionError(f"Fixture expected one fresh {entity}/{metric}/{start}/{end}, found {len(matches)}")
    return matches[0]


def numerical_claim(record, identifier, requirement, *, calculation=False):
    dimensions = {key: record[key] for key in ("entity", "metric", "period_start", "period_end", "unit")}
    return {
        "id": identifier,
        "text": f"{record['entity']} {record['metric']} for {record['period_start']} through {record['period_end']} was {record['value']} {record['unit']}.",
        "evidence_ids": [] if calculation else [record["id"]],
        "calculation_ids": [record["id"]] if calculation else [],
        "requirement_ids": [requirement], "constraints": dimensions,
        "asserted_value": record["value"], "asserted_unit": record["unit"],
    }


def map_followup_answer(role, result, context):
    """The scripted critic maps the whole answer to the actual follow-up, including both figures."""
    if not context.get("request_parts"):
        return
    if role == "researcher":
        values = result["claims"]
        common = {k:v for k,v in values[0]["constraints"].items()
                  if all(c["constraints"].get(k)==v for c in values[1:])}
        values.append({"id":"answer:"+values[0]["id"],"text":" ".join(c["text"] for c in values),
                       "evidence_ids":[i for c in values for i in c["evidence_ids"]],
                       "calculation_ids":[i for c in values for i in c["calculation_ids"]],
                       "constraints":common,"requirement_ids":result["answered_requirement_ids"]})
    elif role == "critic":
        combined = next(c for c in context["claims"] if c["id"].startswith("answer:"))
        result["request_coverage"] = [{"part":"part-1","status":"answered","claim_id":combined["id"],
                                       "answer_sentence":combined["text"]}]


class QuarterFixtureModel:
    """Parameterized scripted plan; no financial answers are embedded in it."""

    mode = "scripted_fixture"
    timeout = 30
    last_usage = {}

    def __init__(self, start, end, cutoff, label):
        self.start, self.end, self.cutoff, self.label = start, end, cutoff, label
        self.step = 0
        self.calls = []

    def complete(self, role, context):
        self.calls.append((role, copy.deepcopy(context)))
        if role == "planner":
            step = self.step
            self.step += 1
            if step == 0:
                result = {
                    "action": "tool", "tool": "financial_facts", "arguments": {"entity": "MSFT", "metric": "revenue"},
                    "scope": {"entities": ["MSFT"], "period_start": self.start, "period_end": self.end,
                              "cutoff": self.cutoff,
                              "requirements": [{"id": "req:revenue", "text": "Revenue for the requested quarter"},
                                               {"id": "req:margin", "text": "Operating margin for the requested quarter"}]},
                }
            elif step == 1:
                result = {"action": "tool", "tool": "financial_facts", "arguments": {"entity": "MSFT", "metric": "operating_income"}}
            elif step == 2:
                operands = [selected(context["evidence"], entity="MSFT", metric=metric, start=self.start, end=self.end)
                            for metric in ("operating_income", "revenue")]
                result = {"action": "tool", "tool": "calculate", "arguments": {"operation": "margin", "operand_ids": [r["id"] for r in operands]}}
            else:
                result = {"action": "research" if step == 3 else "finish"}
        elif role == "researcher":
            revenue = selected(context["evidence"], entity="MSFT", metric="revenue", start=self.start, end=self.end)
            margin = selected(context["calculations"], entity="MSFT", metric="operating_income_margin", start=self.start, end=self.end)
            result = {
                "claims": [numerical_claim(revenue, f"claim:{self.label}:revenue", "req:revenue"),
                           numerical_claim(margin, f"claim:{self.label}:margin", "req:margin", calculation=True)],
                "answered_requirement_ids": ["req:revenue", "req:margin"], "unanswered_requirement_ids": [],
                "summary": "SCRIPTED FIXTURE: inspect period correction, source reuse and review scheduling.",
            }
        elif role == "critic":
            result = {"issues": [], "reviewed_claim_ids": [c["id"] for c in context["claims"]],
                      "reviewed_requirement_ids": [r["id"] for r in context["requirements"]]}
        else:
            raise AssertionError(f"Unexpected fixture role: {role}")
        map_followup_answer(role, result, context)
        return validate_response(role, fixture_verdicts(role, result, context), context)


class ComparisonFixtureModel:
    """Add a second issuer with explicit unequal fiscal dates and reuse MSFT."""

    mode = "scripted_fixture"
    timeout = 30
    last_usage = {}

    def __init__(self):
        self.step = 0
        self.calls = []

    def complete(self, role, context):
        self.calls.append((role, copy.deepcopy(context)))
        if role == "planner":
            step = self.step
            self.step += 1
            if step == 0:
                result = {
                    "action": "tool", "tool": "financial_facts",
                    "arguments": {"entity": "NVDA", "metric": "revenue", "period_start": "2024-07-29", "period_end": "2024-10-27"},
                    "scope": {"entities": ["MSFT", "NVDA"], "period_start": None, "period_end": None,
                              "cutoff": "2025-01-31", "display_text": "Compare reported revenues with each issuer's different fiscal dates stated explicitly.",
                              "requirements": [{"id": "req:msft", "text": "Retain Microsoft's October–December 2024 revenue and exact dates"},
                                               {"id": "req:nvda", "text": "Add NVIDIA's July–October 2024 revenue and exact dates"}]},
                }
            else:
                result = {"action": "research" if step == 1 else "finish"}
        elif role == "researcher":
            msft = selected(context["evidence"], entity="MSFT", metric="revenue", start="2024-10-01", end="2024-12-31")
            nvda = selected(context["evidence"], entity="NVDA", metric="revenue", start="2024-07-29", end="2024-10-27")
            result = {
                "claims": [numerical_claim(msft, "claim:comparison:msft", "req:msft"),
                           numerical_claim(nvda, "claim:comparison:nvda", "req:nvda")],
                "answered_requirement_ids": ["req:msft", "req:nvda"], "unanswered_requirement_ids": [],
                "summary": "SCRIPTED FIXTURE: revenue figures have different fiscal periods; no same-period difference is calculated.",
            }
        elif role == "critic":
            result = {"issues": [], "reviewed_claim_ids": [c["id"] for c in context["claims"]],
                      "reviewed_requirement_ids": [r["id"] for r in context["requirements"]]}
        else:
            raise AssertionError(f"Unexpected fixture role: {role}")
        map_followup_answer(role, result, context)
        return validate_response(role, fixture_verdicts(role, result, context), context)


class SessionEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "research.sqlite3"
        self.store = SessionStore(self.path)
        self.sid = self.store.create_session("Multi-turn protocol fixture")["id"]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def ask(self, model, question, *, budget=None):
        engine = ResearchEngine(self.store, model, ToolRegistry(self.store, DATA))
        return engine.ask(self.sid, question, budget=budget)

    def initial_quarter(self):
        model = QuarterFixtureModel("2024-10-01", "2024-12-31", "2025-01-31", "q2")
        result = self.ask(model, "Show MSFT FY2025 Q2 revenue and operating margin, with sources.")
        self.assertEqual(result["status"], "complete", result["episode"])
        return result

    def test_scope_correction_preserves_report_invalidates_old_results_and_reviews_new_quarter(self):
        first = self.initial_quarter()
        old_report = copy.deepcopy(first["report"])
        old_margin = next(c for c in old_report["calculations"] if c["operation"] == "margin")
        old_claim_ids = {c["id"] for c in old_report["claims"]}
        old_source_ids = {e["id"] for e in old_report["evidence"]}
        corrected = QuarterFixtureModel("2024-07-01", "2024-09-30", "2024-11-01", "q1")
        second = self.ask(corrected, "Correction: I meant FY2025 Q1, July to September 2024, using information available by 1 November 2024.")
        self.assertEqual(second["status"], "complete", second["episode"])
        self.assertEqual(second["episode"]["tool_calls"], 3)
        self.assertNotEqual(first["episode"]["id"], second["episode"]["id"])
        self.assertEqual(self.store.list_reports(self.sid)[0], old_report)
        self.assertEqual([r["version"] for r in self.store.list_reports(self.sid)], [1, 2])

        context = self.store.get_context(self.sid)
        stale_margin = next(c for c in context["calculations"] if c["id"] == old_margin["id"])
        self.assertTrue(stale_margin["stale"])
        self.assertTrue(all(c["stale"] for c in context["claims"] if c["id"] in old_claim_ids))
        # Retain original sources without pretending they answer the new scope.
        self.assertTrue(old_source_ids <= {e["id"] for e in context["evidence"]})
        self.assertTrue(all(not e["stale"] for e in context["evidence"] if e["id"] in old_source_ids))
        q1_revenue = selected(context["evidence"], entity="MSFT", metric="revenue", start="2024-07-01", end="2024-09-30")
        self.assertEqual(q1_revenue["value"], 65_585_000_000)
        self.assertTrue(all(c["constraints"]["period_end"] == "2024-09-30" for c in second["report"]["claims"]))
        self.assertTrue(all(c["status"] == "Supported" and not c["stale"] for c in second["report"]["claims"]))
        critic_contexts = [ctx for role, ctx in corrected.calls if role == "critic"]
        self.assertEqual(len(critic_contexts), 1)
        self.assertEqual({c["id"] for c in critic_contexts[0]["claims"]}, {c["id"] for c in second["report"]["claims"]})
        self.assertTrue(all(c["status"] == "Pending Verification" for c in critic_contexts[0]["claims"]))
        self.assertTrue(old_claim_ids.isdisjoint(second["episode"]["active_claim_ids"]))

    def test_comparison_after_reopen_keeps_both_entities_and_reuses_first_company_sources(self):
        first = self.initial_quarter()
        old_report = copy.deepcopy(first["report"])
        msft_revenue = selected(first["report"]["evidence"], entity="MSFT", metric="revenue", start="2024-10-01", end="2024-12-31")
        self.store.close()
        self.store = SessionStore(self.path)
        model = ComparisonFixtureModel()
        second = self.ask(model, "Keep Microsoft and add NVIDIA FY2025 Q3 revenue alongside it. State the different fiscal dates; do not calculate a same-period difference.")
        self.assertEqual(second["status"], "complete", second["episode"])
        self.assertEqual(second["report"]["scope"]["entities"], ["MSFT", "NVDA"])
        self.assertIsNone(second["report"]["scope"]["period_start"])
        self.assertEqual(second["episode"]["tool_calls"], 1)
        tool_events = [event for event in self.store.events(self.sid)
                       if event.get("type") == "tool_call" and event.get("episode_id") == second["episode"]["id"]]
        self.assertEqual([(e["tool"], e["arguments"]["entity"]) for e in tool_events], [("financial_facts", "NVDA")])
        self.assertEqual(len(self.store.history(self.sid, "evidence", msft_revenue["id"])), 1)
        self.assertEqual(self.store.list_reports(self.sid)[0], old_report)
        self.assertTrue(any("FY2025 Q2" in m["content"] for m in model.calls[0][1]["messages"]))
        claims = {c["constraints"]["entity"]: c for c in second["report"]["claims"] if "asserted_value" in c}
        self.assertEqual(set(claims), {"MSFT", "NVDA"})
        self.assertEqual(claims["MSFT"]["asserted_value"], 69_632_000_000)
        self.assertEqual(claims["NVDA"]["asserted_value"], 35_082_000_000)
        self.assertEqual(claims["MSFT"]["constraints"]["period_end"], "2024-12-31")
        self.assertEqual(claims["NVDA"]["constraints"]["period_end"], "2024-10-27")
        self.assertTrue(all(c["scope_version"] == second["report"]["scope_version"] for c in claims.values()))
        self.assertEqual(sum(role == "critic" for role, _ in model.calls), 1)

    def test_corrected_scope_without_remaining_critic_budget_stays_partial(self):
        first = self.initial_quarter()
        corrected = QuarterFixtureModel("2024-07-01", "2024-09-30", "2024-11-01", "q1")
        second = self.ask(corrected, "Correct the request to FY2025 Q1 and recheck it.", budget=Budget(max_model_calls=5))
        self.assertEqual(second["status"], "partial", second["episode"])
        self.assertEqual(second["episode"]["stop_reason"], "model_budget_exhausted")
        self.assertFalse(second["report"]["verification_available"])
        self.assertEqual(sum(role == "critic" for role, _ in corrected.calls), 0)
        self.assertTrue(second["report"]["claims"])
        self.assertTrue(all(c["status"] == "Pending Verification" for c in second["report"]["claims"]))
        self.assertEqual({r["id"] for r in second["report"]["unanswered"]}, {"req:revenue", "req:margin"})
        self.assertEqual(self.store.list_reports(self.sid)[0], first["report"])
        self.assertEqual(self.store.list_reports(self.sid)[0]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
