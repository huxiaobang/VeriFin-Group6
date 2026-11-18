"""Drive the production controller with explicit offline Planner, Researcher and Critic decisions."""
from pathlib import Path
import unittest

from tests.test_engine import CLAIM, FACT, FixtureTools, SCOPE, ScriptedModel
from verifin.engine import ResearchEngine
from verifin.reporting import question_answers
from verifin.request_review import coverage_issues, request_parts
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


QUESTIONS = ("营收和营业利润是多少？", "What were revenue and operating income?")
SEC_AMOUNTS = {
    "correct": "Revenue was USD 69.632 billion and operating income was USD 31.653 billion.",
    "swap": "Revenue was USD 31.653 billion and operating income was USD 69.632 billion.",
    "unrelated": "Revenue was USD 24.108 billion and operating income was USD 31.653 billion.",
    "one_wrong": "Revenue was USD 69.632 billion and operating income was USD 41.653 billion.",
}


def ask_two_metrics(question, outcome):
    """The checklist is paraphrased. The Critic sees two verbatim rows only if the controller identified them."""
    requirement = {"id": "operating-results", "text": "Give the available quarterly operating figures."}
    sales = {**CLAIM, "requirement_ids": [requirement["id"]]}
    profit_fact = {**FACT, "id": "fact:profit", "metric": "operating_income",
                   "value": 31653000000, "locator": "facts.us-gaap.OperatingIncomeLoss"}
    second = {**sales, "id": "claim:second"}
    if outcome in {"correct", "one_sentence"}:
        second.update(text="Operating income was USD 31.653 billion.", evidence_ids=[profit_fact["id"]],
                      asserted_value=profit_fact["value"], constraints={**sales["constraints"], "metric": "operating_income"})
    else:
        second.update(text="Revenue was USD 69.632 billion.")
    if outcome == "one_sentence":
        sales = {**sales, "text": "Revenue was USD 69.632 billion and operating income was USD 31.653 billion.",
                 "evidence_ids": [FACT["id"], profit_fact["id"]]}
    claims = [sales] if outcome in {"omitted", "one_sentence"} else [sales, second]
    reqs = [requirement["id"]]
    review = {"reviewed_claim_ids": [c["id"] for c in claims], "reviewed_requirement_ids": reqs, "issues": []}

    def make_review(role, ctx):
        if role == "critic" and ctx.get("request_parts"):
            review["request_coverage"] = [
                {"part": part, "status": "answered", "claim_id": c["id"], "answer_sentence": c["text"]}
                for part, c in zip(ctx["request_parts"], claims * 2 if outcome == "one_sentence" else claims)]

    model = ScriptedModel([
        ("planner", {"action": "research", "scope": {**SCOPE, "requirements": [requirement]}}),
        ("researcher", {"claims": claims, "answered_requirement_ids": reqs,
                        "unanswered_requirement_ids": [], "summary": claims[0]["text"]}),
        ("critic", review), ("planner", {"action": "finish"})], callback=make_review)
    with SessionStore(":memory:") as store:
        sid = store.create_session()["id"]
        store.put_evidence(sid, FACT)
        store.put_evidence(sid, profit_fact)
        ResearchEngine(store, model, FixtureTools()).ask(sid, question)
        report = store.get_report(sid, 1)
        summary, = store.list_report_summaries(sid)
    return {"question": question, "draft": outcome, "status": report["status"],
            "answers": question_answers(report), "findings": {c["id"]: c["status"] for c in report["claims"]},
            "issue_targets": [i["target_id"] for i in report["issues"]],
            "summary_claim_id": summary.get("summary_claim_id"), "question_counts": summary.get("question_counts")}


def ask_with_sec_records(text, explicit_context=True):
    with SessionStore(":memory:") as store:
        sid = store.create_session()["id"]
        tools = ToolRegistry(store, Path(__file__).resolve().parents[1] / "data", allow_network=False)
        facts = {}
        for metric in ("revenue", "operating_income", "net_income"):
            args = {"entity": "MSFT", "metric": metric, "period_start": SCOPE["period_start"],
                    "period_end": SCOPE["period_end"]}
            response = tools.execute("financial_facts", args, sid, SCOPE)
            assert response["ok"], response
            fact, = response["evidence"]
            facts[metric] = store.put_evidence(sid, fact)
        constraints = {k: facts["revenue"][k] for k in ("entity", "period_start", "period_end")} if explicit_context else {}
        claim = {"id": "figures", "text": text, "constraints": constraints, "evidence_ids": [f["id"] for f in facts.values()],
                 "calculation_ids": [], "requirement_ids": ["figures-request"]}
        reqs = claim["requirement_ids"]
        scope = {**SCOPE, "requirements": [{"id": reqs[0], "text": "Give the two requested operating figures."}]}
        review = {"issues": [], "reviewed_claim_ids": ["figures"], "reviewed_requirement_ids": reqs,
                  "request_coverage": [{"part": p, "status": "answered", "claim_id": "figures", "answer_sentence": text}
                                       for p in ("part-1", "part-2")]}
        roles = ScriptedModel([("planner", {"action": "research", "scope": scope}),
                               ("researcher", {"claims": [claim], "answered_requirement_ids": reqs,
                                               "unanswered_requirement_ids": [], "summary": text}),
                               ("critic", review), ("planner", {"action": "finish"})])
        report = ResearchEngine(store, roles, tools).ask(sid, QUESTIONS[1])["report"]
        claim, = report["claims"]
        return {"text": text, "constraints": constraints, "status": report["status"],
                "answers": [r["status"] for r in question_answers(report)], "display_check": claim["display_check"],
                "sources": {m: {k: f[k] for k in ("id", "value", "locator", "source_url")} for m, f in facts.items()}}


class DirectMetricQuestions(unittest.TestCase):
    def test_shared_sentence_currency_amounts_bind_to_their_sec_metrics(self):
        for name, sentence in SEC_AMOUNTS.items():
            for explicit in (True, False):
                with self.subTest(case=name, explicit_context=explicit):
                    report = ask_with_sec_records(sentence, explicit)
                    correct = name == "correct"
                    self.assertEqual(report["status"], "complete" if correct else "partial")
                    self.assertEqual(report["answers"], ["answered", "answered"] if correct else ["gap", "gap"])
                    self.assertEqual(report["display_check"]["method"], "matched_records" if correct else "mismatch")

    def test_each_requested_figure_reaches_the_review_and_the_saved_report(self):
        for question in QUESTIONS:
            for outcome in ("omitted", "wrong_metric", "correct", "one_sentence"):
                with self.subTest(question=question, outcome=outcome):
                    report = ask_two_metrics(question, outcome)
                    correct = outcome in {"correct", "one_sentence"}
                    self.assertEqual(report["status"], "complete" if correct else "partial")
                    self.assertEqual([r["status"] for r in report["answers"]], ["answered", "answered" if correct else "gap"])
                    self.assertEqual(report["question_counts"]["gap"], 0 if correct else 1)
                    self.assertEqual(report["findings"][CLAIM["id"]], "Supported")
                    self.assertEqual(report["summary_claim_id"], CLAIM["id"])

    def test_preserve_relationships_modifiers_and_single_financial_terms(self):
        for question in (
                "营收和营业利润的差额是多少？", "营收与营业利润的合计是多少？",
                "What were the differences between revenue and operating income?",
                "What were the shares of revenue and operating income attributable to the segment?",
                "What were cash and cash equivalents?", "What were the drivers of revenue and operating income?",
                "营收和营业利润的增长率是多少？"):
            with self.subTest(question=question):
                self.assertEqual(request_parts(question), {})

    def test_shared_sentence_only_counts_the_measures_it_actually_states(self):
        for text in ("营收为 10 亿美元，营业利润为 2 亿美元。",
                     "Revenue was USD 1 billion, and operating income was USD 200 million."):
            parts = {"part-1": "What was net income?", "part-2": "营业利润是多少？",
                     "part-3": "What was revenue?", "part-4": "Why was revenue higher?"}
            claim = {"id": "figures", "text": text}
            rows = [{"part": part, "status": "answered", "claim_id": claim["id"], "answer_sentence": text}
                    for part in parts]
            with self.subTest(text=text):
                issues = coverage_issues(parts, {"request_coverage": rows}, [claim])
                self.assertEqual([i["target_id"] for i in issues], ["part-1", "part-4"])
