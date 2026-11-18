import unittest

from tests.test_engine import CLAIM, FACT, SCOPE, FixtureTools, ScriptedModel, full_script
from tests.critic_fixture import fixture_verdicts
from verifin.engine import Budget, ResearchEngine
from verifin.fiscal_followup import adjacent_dates, inherited_questions, prior_period_only, reviewed_quarter
from verifin.skill_calculations import requested_quarter
from verifin.storage import SessionStore


class FiscalPeriodFollowupTests(unittest.TestCase):
    def test_return_to_opening_quarter_restores_reviewed_fiscal_window_before_planning(self):
        for question in ("回到最开始聊的那一季。账面上的利润为什么跌了？",
                         "Go back to the quarter we started with. Why did earnings drop?"):
            with self.subTest(question=question), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                first = ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(
                    sid, "MSFT FY2025 Q2, what was its revenue?")["report"]
                previous = ResearchEngine(store, None, FixtureTools()).ask(sid, "那上一季呢？")["report"]
                self.assertEqual(previous["skill_fiscal_quarter"]["quarter"], "2025Q1")
                model = ScriptedModel([("planner", {"action":"finish", "reason":"No draft."})])
                result = ResearchEngine(store, model, FixtureTools()).ask(sid, question)
                for target in (model.calls[0][1]["episode"]["skill_fiscal_quarter"], result["report"]["skill_fiscal_quarter"]):
                    self.assertEqual((target["quarter"], target["period_start"], target["period_end"]),
                                     ("2025Q2", "2024-10-01", "2024-12-31"))
                self.assertNotIn("quarter_followup_parts", result["episode"])
                self.assertEqual(first, store.get_report(sid, 1))

    def test_unresolved_reference_uses_only_matching_reviewed_dates_or_remains_unbound(self):
        for end, expected in (("2024-12-31", "2025Q2"), ("2024-12-29", None)):
            with self.subTest(end=end), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(sid, "MSFT FY2025 Q2 revenue?")
                ResearchEngine(store, None, FixtureTools()).ask(sid, "那上一季呢？")
                model = ScriptedModel([("planner", {"action":"finish", "reason":"Resolved dates.",
                    "scope":{"period_start":"2024-10-01", "period_end":end}})])
                result = ResearchEngine(store, model, FixtureTools()).ask(sid, "回到之前谈过的那个季度，利润如何？")
                self.assertNotIn("skill_fiscal_quarter", model.calls[0][1]["episode"])
                self.assertEqual(result["report"]["scope"]["period_end"], end)
                self.assertEqual(result["report"].get("skill_fiscal_quarter", {}).get("quarter"), expected)

    def test_opening_quarter_reference_preserves_a_separately_edited_date_window(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(sid, "MSFT FY2025 Q2 revenue?")
            ResearchEngine(store, None, FixtureTools()).ask(sid, "那上一季呢？")
            custom_dates = {"period_start":"2024-11-01", "period_end":"2024-11-30"}
            store.update_scope(sid, {**store.get_session(sid)["scope"], **custom_dates})
            model = ScriptedModel([("planner", {"action":"finish", "reason":"Prior fiscal quarter.",
                "scope":{"period_start":"2024-10-01", "period_end":"2024-12-31"}})])
            result = ResearchEngine(store, model, FixtureTools()).ask(sid, "回到最开始聊的那一季。利润如何？")
            self.assertNotIn("skill_fiscal_quarter", result["report"])
            self.assertEqual({k:result["report"]["scope"][k] for k in custom_dates}, custom_dates)

    def test_stale_or_missing_referenced_report_does_not_fall_back_to_the_last_quarter(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(sid, "MSFT FY2025 Q2 revenue?")
            ResearchEngine(store, None, FixtureTools()).ask(sid, "那上一季呢？")
            store.put_evidence(sid, {**FACT, "value":FACT["value"] + 1000})
            for question in ("回到第一轮说的季度，利润如何？", "回到第9轮讨论的季度，收入如何？"):
                model = ScriptedModel([("planner", {"action":"finish", "reason":"No checked quarter."})])
                result = ResearchEngine(store, model, FixtureTools()).ask(sid, question)
                self.assertNotIn("skill_fiscal_quarter", model.calls[0][1]["episode"])
                self.assertNotIn("skill_fiscal_quarter", result["report"])

    def test_new_report_persists_reviewed_fiscal_dates_and_advances_on_each_relative_turn(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            first = ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(
                sid, "MSFT FY2025 Q2, what was its revenue?")
            self.assertTrue(first["report"]["verification_available"])
            anchor = first["report"]["skill_fiscal_quarter"]
            self.assertEqual((anchor["quarter"], anchor["period_start"], anchor["period_end"]),
                             ("2025Q2", "2024-10-01", "2024-12-31"))
            self.assertEqual(store.get_session(sid)["scope_version"], first["report"]["scope_version"])
            offline = ResearchEngine(store, None, FixtureTools())
            second = offline.ask(sid, "那上一季呢？")
            self.assertEqual(second["report"]["skill_fiscal_quarter"],
                             {"entity": "MSFT", "quarter": "2025Q1", "period_end": "2024-09-30"})
            self.assertNotIn("period_start", second["report"]["scope"])
            self.assertIn("2025Q1", second["report"]["request_parts"]["part-1"])
            third = offline.ask(sid, "那上一季呢？")
            self.assertEqual(third["report"]["skill_fiscal_quarter"], {"entity": "MSFT", "quarter": "2024Q4"})
            self.assertNotIn("2025Q1", third["report"]["request_parts"]["part-1"])
            self.assertEqual(store.get_report(sid, 1), first["report"])

    def test_legacy_verified_claims_recover_only_current_matched_issuer_and_period(self):
        dates = {"entity": "DEMO", "period_start": "2029-10-29", "period_end": "2030-01-27"}
        evidence = {"id": "doc", "version": 1, "kind": "document", "entity": "DEMO",
                    "published_at": "2030-02-08", "document_period_start": dates["period_start"],
                    "document_report_date": dates["period_end"]}
        claim = {"status": "Supported", "text": "FY2030 Q1 revenue increased versus FY2029 Q1.",
                 "evidence_ids": ["doc"], "constraints": dates}
        report = {"verification_available": True, "scope": {"entities": ["DEMO"]},
                  "request": "What explains the change? Did demand improve?", "claims": [claim], "evidence": [evidence]}
        current = {"doc": evidence}
        anchor = reviewed_quarter(report, "DEMO", current, "2030-02-09")
        self.assertEqual(anchor, {"quarter": "2030Q1", **dates, "evidence_ids": ["doc"]})
        self.assertIsNone(reviewed_quarter(report, "DEMO", current, "2030-02-07"))
        self.assertIsNone(reviewed_quarter(report, "DEMO", {"doc": {**evidence, "version": 2}}, "2030-02-09"))
        self.assertIsNone(reviewed_quarter(report, "OTHER", current, "2030-02-09"))
        for changes in [
            {"verification_available": False},
            {"request": "Compare 2030Q1 and 2030Q2"},
            {"claims": [{**claim, "text": "Sales rose compared with FY2029 Q1."}]},
            {"claims": [{**claim, "constraints": {**dates, "period_start": "2029-10-01"}}]},
        ]:
            self.assertIsNone(reviewed_quarter({**report, **changes}, "DEMO", current, "2030-02-09"), changes)

    def test_adjacent_fiscal_calendar_uses_matching_duration_and_publication(self):
        anchor = {"entity": "DEMO", "quarter": "2030Q1", "period_start": "2029-10-29"}
        quarter = {"entity": "DEMO", "period_start": "2029-07-30", "period_end": "2029-10-28",
                   "published_at": "2029-12-05"}
        annual = {**quarter, "period_start": "2028-10-29"}
        unrelated = {**quarter, "period_start": "2029-07-27", "entity": "OTHER"}
        after_cutoff = {**quarter, "period_start": "2029-07-26", "published_at": "2031-05-26"}
        self.assertEqual(adjacent_dates(anchor, [quarter, annual, unrelated, after_cutoff], "2030-01-01"),
                         {"period_start": "2029-07-30", "period_end": "2029-10-28"})
        self.assertEqual(adjacent_dates(anchor, [annual, unrelated, after_cutoff], "2030-01-01"),
                         {"period_end": "2029-10-28"})

    def test_only_bare_period_change_restores_the_previous_questions(self):
        for text in ("那上一季呢？", "那再往上一季度呢？", "What about the previous fiscal quarter?"):
            self.assertTrue(prior_period_only(text))
        for text in ("2025年第四季度收入多少？", "相比上一季呢？", "上一季度只看利润。", "Switch to XYZ last quarter"):
            self.assertFalse(prior_period_only(text))
        self.assertIsNone(requested_quarter(["Compared with FY2029 Q2, did demand improve?"]))
        self.assertEqual(requested_quarter(["FY2030 Q1 revenue versus FY2029 Q1?"]), "2030Q1")
        report = {"request_parts": {"part-1": "2030年第一季度收入怎样？", "part-2": "客户需求改善了吗？"}}
        raw, shifted = inherited_questions(report, {}, "2030Q1", "2029Q4")
        self.assertEqual(set(shifted), {"part-1", "part-2"})
        self.assertEqual(raw["part-1"], "2029Q4收入怎样？")
        self.assertEqual(raw["part-2"], report["request_parts"]["part-2"])

    def test_all_roles_keep_two_questions_and_reject_a_draft_and_review_for_the_old_quarter(self):
        from verifin.model import validate_response
        class OldQuarterModel:
            def __init__(self):
                self.contexts = {}

            def complete(self, role, ctx):
                self.contexts[role] = ctx
                reqs = [r["id"] for r in ctx["requirements"]]
                parts = list(ctx["request_parts"])
                if role == "planner":
                    result = {"action": "research", "scope": {**SCOPE, "requirements":
                              [{"id": "old", "text": "Explain FY2025 Q2 revenue."}]}}
                elif role == "researcher":
                    claims = [{**CLAIM, "id": f"c{i}", "text": text, "requirement_ids": [reqs[i]]}
                              for i, text in enumerate(("Revenue increased.", "Demand was mixed."))]
                    result = {"claims": claims, "answered_requirement_ids": reqs,
                              "unanswered_requirement_ids": [], "summary": "Previous-quarter figures."}
                else:
                    result = {"issues": [], "reviewed_requirement_ids": reqs,
                              "reviewed_claim_ids": [c["id"] for c in ctx["claims"]],
                              "request_coverage": [{"part": part, "status": "answered", "claim_id": c["id"],
                                                    "answer_sentence": c["text"]} for part, c in zip(parts,ctx["claims"])]}
                return validate_response(role, fixture_verdicts(role, result, ctx), ctx)

        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            store.update_scope(sid, SCOPE)
            fact = store.put_evidence(sid, FACT)
            report = {"episode_id": "prior", "request": "Explain FY2025 Q2 revenue. Why did it change? Did demand recover?",
                      "request_parts": {"part-1": "Why did revenue change?", "part-2": "Did demand recover?"},
                      "status": "complete", "verification_available": True, "evidence": [fact], "calculations": [],
                      "claims": [{**CLAIM, "text": "FY2025 Q2 revenue increased.", "status": "Supported"}]}
            prior = store.save_report(sid, report)
            store.add_event(sid, {"type": "episode_checkpoint", "episode": {"id": "prior", "status": "complete"}})
            model = OldQuarterModel()
            result = ResearchEngine(store, model, FixtureTools()).ask(sid, "那上一季呢？", budget=Budget(max_model_calls=3))
            self.assertEqual(list(model.contexts), ["planner", "researcher", "critic"])
            for role, ctx in model.contexts.items():
                self.assertEqual(ctx["fiscal_target"],
                                 {"entity": "MSFT", "quarter": "2025Q1", "period_end": "2024-09-30"}, role)
                self.assertEqual(ctx["scope"]["period_end"], "2024-09-30")
                self.assertNotIn("period_start", ctx["scope"])
                self.assertEqual(len(ctx["requirements"]), 2)
                self.assertEqual(set(ctx["request_parts"]), {"part-1", "part-2"})
            self.assertEqual(result["report"]["status"], "partial")
            self.assertEqual([r["status"] for r in result["report"]["request_coverage"]], ["gap", "gap"])
            self.assertEqual(store.get_report(sid, 1), prior)
