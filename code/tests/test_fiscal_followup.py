import unittest
from types import SimpleNamespace

from tests.test_engine import CLAIM, FACT, SCOPE, FixtureTools, ScriptedModel, full_script
from tests.critic_fixture import fixture_verdicts
from verifin.engine import Budget, ResearchEngine
from verifin.fiscal_followup import adjacent_dates, check_answer_periods, inherited_questions, prior_period_only, reviewed_quarter
from verifin.scope import edit_scope
from verifin.skill_calculations import requested_quarter
from verifin.storage import SessionStore
from verifin.verification import check_rules


class FiscalPeriodFollowupTests(unittest.TestCase):
    def test_retry_of_sealed_relative_quarter_preserves_window_or_respects_explicit_edit(self):
        original = {"part-1": "What was revenue?", "part-2": "Did demand improve?"}
        archive = {"request": "What about the previous fiscal quarter?", "quarter_followup_parts": original}
        _, parts = inherited_questions(archive, archive, "2025Q2", "2025Q2")
        dates = {"period_start": SCOPE["period_start"], "period_end": SCOPE["period_end"]}
        previous_dates = {"period_start": "2024-07-01", "period_end": "2024-09-30"}
        quarter = {"entity": "MSFT", "quarter": "2025Q2", **dates}
        for action in ("retry", "edit", "edit_previous", "cutoff", "source_revision", "new_previous"):
            with self.subTest(action=action), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                requirements = [{"id": "request:saved:" + key, "text": value} for key, value in parts.items()]
                store.update_scope(sid, {**SCOPE, "requirements": requirements})
                fact = store.put_evidence(sid, FACT)
                prior_fact = store.put_evidence(sid, {**FACT, "id": "fact:earlier", "value": 68_000_000_000})
                calculation = store.put_calculation(sid, {
                    "id": "calc:saved", "operation": "difference", "formula": "69632000000 - 68000000000",
                    "entity": "MSFT", "value": 1_632_000_000, "unit": "USD",
                    "operand_ids": [fact["id"], prior_fact["id"]], **dates,
                })
                saved = store.save_report(sid, {
                    **archive, "episode_id": "saved", "status": "partial", "stop_reason": "interrupted",
                    "verification_available": False, "skill_fiscal_quarter": quarter, "request_parts": parts,
                    "evidence": [fact, prior_fact], "calculations": [calculation], "claims": [],
                })
                store.add_event(sid, {"type": "episode_checkpoint", "episode": {
                    "id": "saved", "status": "partial", "skill_fiscal_quarter": quarter,
                    "quarter_followup_parts": original,
                }})
                if action in {"edit", "edit_previous"}:
                    session = store.get_session(sid)
                    edit_scope(store, sid, {**session["scope"], **previous_dates},
                               expected_version=session["scope_version"])
                if action == "source_revision":
                    store.put_evidence(sid, {**FACT, "value": FACT["value"] + 1})
                before = store.get_session(sid)
                model = ScriptedModel([("planner", {"action": "clarify", "question": "Please confirm the sources.",
                    **({"scope": dates} if action in {"edit", "edit_previous"} else {})})])
                question = archive["request"] if action in {"new_previous", "edit_previous"} else "再试试"
                kwargs = {"as_of": "2025-01-28"} if action == "cutoff" else {}
                tools = FixtureTools()
                ResearchEngine(store, model, tools).ask(sid, question, **kwargs)
                context = model.calls[0][1]
                calculations = {item["id"]: item for item in context["calculations"]}
                self.assertFalse(tools.calls)
                self.assertEqual(store.get_report(sid, 1), saved)
                if action == "new_previous":
                    self.assertNotIn("retry_of_version", context["episode"])
                    self.assertEqual(context["fiscal_target"]["quarter"], "2025Q1")
                    self.assertTrue(calculations["calc:saved"]["stale"])
                    continue
                self.assertEqual(context["episode"]["retry_of_version"], 1)
                if action in {"edit", "edit_previous"}:
                    self.assertEqual(context["fiscal_target"], {"entity": "MSFT", **previous_dates})
                    self.assertEqual(context["request_parts"], {
                        key: f"指定期间，请重新核对本季情况并回答：「{text}」" for key, text in original.items()})
                    self.assertNotIn("2025Q2", str(context["requirements"]))
                    self.assertTrue(calculations["calc:saved"]["stale"])
                    self.assertEqual({key: store.get_session(sid)["scope"][key] for key in dates}, previous_dates)
                else:
                    self.assertEqual(context["fiscal_target"], quarter)
                    self.assertEqual({key: context["scope"][key] for key in dates}, dates)
                    self.assertEqual(context["requirements"], requirements)
                    if action == "cutoff":
                        self.assertNotIn("calc:saved", calculations)
                        self.assertFalse(context["evidence"])
                    elif action == "source_revision":
                        self.assertTrue(calculations["calc:saved"]["stale"])
                    else:
                        self.assertEqual(calculations["calc:saved"], calculation)
                        self.assertEqual(store.get_session(sid)["scope_version"], before["scope_version"])

    def test_explicit_quarter_keeps_the_edited_dates_with_verified_match_or_conflict(self):
        dates = {k: SCOPE[k] for k in ("period_start", "period_end")}
        for label, known in (("2025Q1", True), ("2025Q2", True), ("2025Q2", False)):
            with self.subTest(label=label, known=known), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                initial = "MSFT FY2025 Q2, what was its revenue?"
                if known:
                    ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(sid, initial)
                else:
                    ResearchEngine(store, None, FixtureTools()).ask(sid, initial)
                ResearchEngine(store, None, FixtureTools()).ask(sid, "那上一季呢？")
                session = store.get_session(sid)
                edit_scope(store, sid, {**session["scope"], **dates}, expected_version=session["scope_version"])
                model = ScriptedModel([("planner", {"action": "clarify", "question": "Which source?",
                    "scope": {"period_start": "2024-07-01", "period_end": "2024-09-30"}})])
                ResearchEngine(store, model, FixtureTools()).ask(sid, f"改为 {label}：营收是多少？")
                planner = model.calls[0][1]
                self.assertEqual({k: planner["scope"][k] for k in dates}, dates)
                self.assertEqual({k: store.get_session(sid)["scope"][k] for k in dates}, dates)
                target = planner["fiscal_target"]
                if known and label == "2025Q2":
                    self.assertEqual(target["quarter"], label)
                else:
                    self.assertEqual(target["requested_quarter"], label)
                    self.assertNotIn("quarter", target)
                    if known:
                        self.assertEqual(target["conflicting_quarter"], "2025Q2")
                        rows = check_answer_periods([{"part": "part-1", "status": "answered",
                            "claim_id": CLAIM["id"]}], [CLAIM], target, planner["request_parts"], [FACT])
                        self.assertEqual(rows[0]["status"], "gap")

    def test_exact_archived_wording_cannot_restore_an_issuer_removed_by_scope_edit(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            tools = FixtureTools()
            tools.data = SimpleNamespace(manifest={"companies": [
                {"name": "Microsoft", "ticker": "MSFT"}, {"name": "Fastenal", "ticker": "FAST"}]})
            original = "MSFT FY2025 Q2, what was its revenue?"
            first = ResearchEngine(store, ScriptedModel(full_script()), tools).ask(sid, original)["report"]
            session = store.get_session(sid)
            edit_scope(store, sid, {**session["scope"], "entities": ["FAST"]},
                       expected_version=session["scope_version"])
            model = ScriptedModel([("planner", {"action": "clarify", "question": "Which disclosure?"})])
            ResearchEngine(store, model, tools).ask(sid, original)
            planner = model.calls[0][1]
            self.assertEqual(planner["episode"]["retry_of_version"], first["version"])
            self.assertEqual(planner["scope"]["entities"], ["FAST"])
            self.assertEqual(planner["fiscal_target"]["entity"], "FAST")
            self.assertNotIn("quarter", planner["fiscal_target"])
            self.assertFalse(planner["evidence"])

    def test_edited_period_requires_cited_records_from_the_user_window(self):
        target = {"entity": "MSFT", "period_start": "2024-07-01", "period_end": "2024-09-30"}
        row = {"part": "part-1", "status": "answered", "claim_id": "claim:answer",
               "answer_sentence": "Revenue was higher."}
        claim = {"id": "claim:answer", "text": row["answer_sentence"], "constraints": target,
                 "evidence_ids": [FACT["id"]]}
        questions = {"part-1": "What was revenue in the specified period?"}
        later_comparison = {**FACT, "document_period_start": target["period_start"],
                            "document_report_date": target["period_end"]}
        for unsupported in (FACT, later_comparison, {**FACT, **target, "stale": True}):
            answer = check_answer_periods([row], [claim], target, questions, [unsupported])
            self.assertEqual(answer[0]["status"], "gap")
        self.assertEqual(check_answer_periods([row], [claim], target, questions, [{**FACT, **target}]), [row])

    def test_dated_but_unrelated_passage_cannot_support_a_rate_from_another_quarter(self):
        target = {"entity": "FAST", "period_start": "2024-10-01", "period_end": "2024-12-31"}
        current = {"id": "q4-dated", "kind": "document", "entity": "FAST", "document_period_start": "2024-10-01",
                   "document_report_date": "2024-12-31", "text": "New machine installations increased."}
        older = {**current, "id": "q3-table", "document_period_start": "2024-07-01",
                 "document_report_date": "2024-09-30", "text": "Non-national accounts -4.1 %"}
        current_table = {**current, "text": "Non-national accounts -1.0 %"}
        questions = {"part-1": "Did customer demand recover?"}
        for text, period_source, expected in (
            ("The group's daily sales declined 4.1%.", current, "gap"),
            ("The group's daily sales declined 1.0%.", current_table, "answered"),
            ("This quarter the group's daily sales declined 1.0%, after declining 4.1% last quarter.",
             current_table, "answered"),
        ):
            with self.subTest(text=text):
                row = {"part": "part-1", "status": "answered", "claim_id": "claim:answer", "answer_sentence": text}
                claim = {"id": "claim:answer", "text": text, "constraints": target,
                         "evidence_ids": [older["id"], period_source["id"]]}
                answer = check_answer_periods([row], [claim], target, questions, [older, period_source])
                self.assertEqual(answer[0]["status"], expected)
                findings = check_rules({"scope": {}, "fiscal_target": target, "claims": [claim],
                                        "evidence": [older, period_source], "calculations": []})
                wrong_rate = [issue for issue in findings if issue["issue_type"] == "context_mismatch"]
                self.assertEqual(bool(wrong_rate), expected == "gap", findings)

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
