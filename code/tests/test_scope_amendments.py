import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests.test_engine import CLAIM, FACT, RESEARCH, REVIEW, SCOPE, FixtureTools, ScriptedModel
from verifin.engine import ResearchEngine
from verifin.scope import stated_cutoff
from verifin.skill_calculations import requested_fiscal_year, requested_quarter
from verifin.skill_routing import amended_issuers, issuer_decision_conflict, resolve_issuer_scope, user_issuer_boundary
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class ScopeAmendmentTests(unittest.TestCase):
    catalog = [{"ticker":"AAA","name":"Alpha Limited"}, {"ticker":"BBB","name":"Beta Limited","aliases":["乙企业"]}]
    scope = {"entities":["AAA","BBB"]}

    def test_selecting_or_excluding_an_issuer_respects_clause_order(self):
        for question in ("不要看 AAA，只研究 乙企业。", "只看 BBB，AAA 不用看了。",
                         "Use BBB instead of AAA", "I meant Beta Limited, not Alpha Limited."):
            self.assertEqual(amended_issuers(question,self.scope,self.catalog), (["BBB"],["AAA"]))
        self.assertEqual(amended_issuers("AAA 不看了。",self.scope,self.catalog), (["BBB"],["AAA"]))
        self.assertIsNone(amended_issuers("AAA 不看库存，分析利润。",self.scope,self.catalog))
        self.assertIsNone(amended_issuers("Not only AAA but also BBB",self.scope,self.catalog))

    def test_open_peer_search_cannot_implicitly_restore_excluded_issuer(self):
        boundary = {"entities":["BBB"],"open_peers":True,"excluded":["AAA"]}
        research = {"action":"research","scope":{"entities":["BBB","AAA"]}}
        lookup = {"action":"tool","tool":"discover_company","arguments":{"query":"Alpha Limited"}}
        self.assertIsNotNone(issuer_decision_conflict(boundary,research,self.catalog))
        self.assertIsNotNone(issuer_decision_conflict(boundary,lookup,self.catalog))
        self.assertIsNone(issuer_decision_conflict(boundary,{"scope":{"entities":["BBB","CCC"]}},self.catalog))

    def test_corrections_do_not_promote_an_accounting_date_to_a_disclosure_cutoff(self):
        self.assertEqual(stated_cutoff("研究截止日改为 2025-01-15，只能使用此前发布的资料。"),"2025-01-15")
        self.assertIsNone(stated_cutoff("请研究截至 2024-12-31 的财季收入。"))
        self.assertEqual(requested_quarter(["改看第三季度"],"2024Q4"),"2024Q3")
        self.assertIsNone(requested_quarter(["改看 2023 财年全年"],"2024Q4"))
        self.assertEqual(requested_fiscal_year("改看 2023 财年全年"),2023)

    def test_leading_iso_cutoff_controls_which_filing_reaches_the_next_role(self):
        data = Path(__file__).resolve().parents[1] / "data" / "replay"
        for prefix, permitted in (("As of 2024-11-19, ", False), ("截至 2024-11-19，", False),
                                  ("As of 2024-11-21, ", True)):
            with self.subTest(prefix=prefix), TemporaryDirectory() as cache, SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                model = ScriptedModel([
                    ("planner", {"action":"tool", "tool":"financial_facts", "scope":{"cutoff":"2026-01-01"},
                                 "arguments":{"source":"replay", "entity":"NVDA", "metric":"revenue",
                                              "period_start":"2024-07-29", "period_end":"2024-10-27"}}),
                    ("planner", {"action":"clarify", "question":"Do you want any additional periods?"})])
                engine = ResearchEngine(store, model, ToolRegistry(store, data, cache_dir=cache))
                engine.ask(sid, prefix + "what revenue had NVIDIA disclosed for its quarter ending 2024-10-27?")
                _, last_input = model.calls[-1]
                cutoff = "2024-11-21" if permitted else "2024-11-19"
                self.assertEqual(last_input["scope"]["cutoff"], cutoff)
                records = [r for r in store.get_context(sid)["evidence"] if r.get("metric") == "revenue"]
                self.assertEqual(bool(records), permitted)
                self.assertTrue(all(r["published_at"][:10] <= cutoff for r in records))

    def test_embedded_balance_date_is_not_a_research_cutoff(self):
        for question in ("What was cash as of 2024-11-19?", "截至 2024-11-19 的现金余额是多少？",
                         "What was cash as of November 19, 2024?", "截至 2024 年 11 月 19 日的季度收入是多少？"):
            self.assertIsNone(stated_cutoff(question))

    def test_publication_day_can_be_written_as_a_month_name_or_local_calendar_date(self):
        from verifin.scope import after_cutoff_prefix
        for prefix in ("As of November 19, 2024, ", "As of Nov. 19 2024, ", "As of Nov 19 2024 ",
                       "截至 2024 年 11 月 19 日，", "截至 2024/11/19，"):
            with self.subTest(prefix=prefix):
                question = prefix + "What was the latest reported quarter?"
                self.assertEqual("2024-11-19", stated_cutoff(question))
                self.assertEqual("What was the latest reported quarter?", after_cutoff_prefix(question))
        with self.assertRaises(ValueError):
            stated_cutoff("As of February 30, 2024, what was the latest reported quarter?")

    def test_chinese_publication_cutoff_follows_a_question_boundary(self):
        self.assertEqual(stated_cutoff("截至 2026-11-17T21:20:50Z，最新公告是什么？"), "2026-11-17T21:20:50Z")
        self.assertEqual(stated_cutoff("截至 2026 年 11 月 16 日请问最新公告是什么？"), "2026-11-16")
        self.assertIsNone(stated_cutoff("截至 2026 年 11 月 16 日的季度收入是多少？"))

    def test_day_prefix_preserves_an_explicit_intraday_limit(self):
        query = "As of 2025-02-01, what had Microsoft reported for FY2025 Q2?"
        precise = "2025-02-01T09:00:00-05:00"
        for saved, option, expected in ((precise, None, precise), (None, precise, precise),
                                        (None, "2025-01-31", "2025-01-31")):
            with self.subTest(saved=saved, option=option), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                if saved:
                    store.update_scope(sid, {"cutoff":saved})
                model = ScriptedModel([("planner", {"action":"clarify", "question":"Which measure?"})])
                ResearchEngine(store, model, FixtureTools()).ask(sid, query, as_of=option)
                self.assertEqual(model.calls[0][1]["scope"]["cutoff"], expected)
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            model = ScriptedModel([])
            with self.assertRaisesRegex(ValueError, "conflicts"):
                ResearchEngine(store, model, FixtureTools()).ask(sid, query, as_of="2025-02-02")
            self.assertEqual(model.calls, [])

    def test_coordinated_exclusions_preserve_the_remaining_issuers(self):
        catalog = [*self.catalog, {"ticker":"CCC","name":"Gamma Limited"}]
        scope = {"entities":["AAA","BBB","CCC"]}
        for reply in ("Exclude AAA and Beta Limited.", "这次不看 AAA 和乙企业。",
                      "AAA、乙企业都不用看了。", "Exclude AAA, BBB from the comparison."):
            with self.subTest(reply=reply):
                self.assertEqual(amended_issuers(reply,scope,catalog), (["CCC"],["AAA","BBB"]))
        for reply in ("不看 AAA 和乙企业的库存。", "Ignore Alpha Limited's revenue.",
                      "AAA 和乙企业都不用看库存，只分析利润。"):
            with self.subTest(reply=reply):
                self.assertIsNone(amended_issuers(reply,scope,catalog))

    def test_adding_named_companies_preserves_the_closed_comparison(self):
        catalog = [*self.catalog, {"ticker":"CCC","name":"Gamma Limited"}, {"ticker":"DDD","name":"Delta Limited"}]
        for reply in ("Add CCC and DDD to the comparison.", "再加上 CCC 和 DDD。"):
            with self.subTest(reply=reply):
                entities, single = resolve_issuer_scope(reply, [], self.scope, catalog)
                self.assertEqual((entities,single), (["AAA","BBB","CCC","DDD"],None))
                self.assertEqual(user_issuer_boundary(reply,["CCC","DDD"],entities,self.scope["entities"]),
                                 {"entities":entities,"open_peers":False})
        for reply,expected in (("再加上 CCC。",["AAA","BBB","CCC"]),
                               ("改看 CCC。",["CCC"]),
                               ("Compare CCC and DDD, also include EPS.",["CCC","DDD"])):
            with self.subTest(reply=reply):
                entities, _ = resolve_issuer_scope(reply, [], self.scope, catalog)
                self.assertEqual(entities,expected)
        self.assertEqual(amended_issuers("不看 AAA。",self.scope,catalog),(["BBB"],["AAA"]))
        self.assertEqual(amended_issuers("只看 CCC。",self.scope,catalog),(["CCC"],[]))
        reply = "Add CCC and one industry peer."
        entities, _ = resolve_issuer_scope(reply, [], self.scope, catalog)
        boundary = user_issuer_boundary(reply,["CCC"],entities,self.scope["entities"])
        self.assertTrue(boundary["open_peers"])
        self.assertIsNone(issuer_decision_conflict(boundary, {"scope":{"entities":[*entities,"DDD"]}},catalog))
        self.assertIsNotNone(issuer_decision_conflict(boundary, {"scope":{"entities":["CCC","DDD"]}},catalog))

    def test_pending_question_can_be_replaced_only_by_a_grounded_research_decision(self):
        from verifin.model import ModelError

        for reply, proposed, valid in (
            ("我们一开始比较的乙企业两个季度是哪两个？", "BBB", True),
            ("Which quarters did we compare for Beta Limited originally?", "BBB", True),
            ("Which quarters did we compare for Beta Limited originally?", "AAA", False),
            ("Add Beta Limited to the comparison.", "BBB", False),
            ("Compare that with Beta Limited.", "BBB", False),
            ("Include Beta Limited in that comparison.", "BBB", False),
            ("Compare Beta Limited with the other company.", "BBB", False),
            ("把它和乙企业的业绩比较一下。", "BBB", False),
            ("Use the latest quarter.", "BBB", False),
        ):
            with self.subTest(reply=reply, proposed=proposed), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                tools = FixtureTools()
                tools.data = SimpleNamespace(manifest={"companies":self.catalog})
                model = ScriptedModel([
                    ("planner", {"action":"clarify", "question":"Which Alpha Limited quarter?"}),
                    ("planner", {"action":"research", "clarification_relation":"replace",
                                 "scope":{"entities":[proposed]}}),
                    ("researcher", ModelError("fixture ends at researcher handoff")),
                ])
                engine = ResearchEngine(store, model, tools)
                self.assertEqual(engine.ask(sid, "Review Alpha Limited's margin.")["status"],
                                 "waiting_for_clarification")
                engine.ask(sid, reply)
                research = [ctx for role,ctx in model.calls if role=="researcher"]
                self.assertEqual(bool(research), valid)
                if valid:
                    self.assertEqual(research[0]["episode"]["request"], reply)
                    self.assertEqual(research[0]["scope"]["entities"], ["BBB"])
                    self.assertEqual(research[0]["requirements"][0]["text"], reply)

    def test_resume_keeps_checklist_edited_while_waiting(self):
        from verifin.model import ModelError

        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            tools = FixtureTools()
            tools.data = SimpleNamespace(manifest={"companies":self.catalog})
            model = ScriptedModel([
                ("planner", {"action":"clarify", "question":"Which quarter?"}),
                ("planner", {"action":"research", "scope":{"entities":["BBB"],
                             "requirements":[{"id":"other", "text":"ignore the edited checklist"}]}}),
                ("researcher", ModelError("fixture ends at the handoff")),
            ])
            engine = ResearchEngine(store, model, tools)
            engine.ask(sid, "Review Alpha Limited's margin.")
            scope = store.get_session(sid)["scope"]
            edited = [{"id":"manual", "text":"Cite the quarterly statement."}]
            store.update_scope(sid, {**scope, "requirements":edited})
            engine.ask(sid, "Review Beta Limited's quarter.")
            self.assertEqual(model.calls[-1][0], "researcher")
            self.assertEqual(model.calls[-1][1]["requirements"], edited)

    def test_multi_issuer_clarifications_reach_research_and_report(self):
        tickers = ["NVDA","GM","MSFT"]
        catalog = [{"ticker":s,"name":s} for s in tickers]
        cases = [
            ("Compare quarterly revenue for NVDA and GM.", "Add MSFT to the comparison.", ["MSFT"], False),
            ("Compare quarterly revenue for NVDA and GM.", "Add MSFT to the comparison.", tickers, True),
            ("Compare quarterly revenue for NVDA, GM and MSFT.", "Exclude NVDA and GM.", ["MSFT"], True),
        ]
        for opening, reply, proposed, valid in cases:
            with self.subTest(reply=reply, proposed=proposed), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                tools = FixtureTools()
                tools.data = SimpleNamespace(manifest={"companies":catalog})
                claims = []
                for index,ticker in enumerate(tickers, 1):
                    value = index * 10
                    fact = {**FACT,"id":"revenue:"+ticker,"entity":ticker,"value":value}
                    store.put_evidence(sid,fact)
                    if ticker in proposed:
                        claims.append({**CLAIM,"id":"claim:"+ticker,"text":f"{ticker} revenue was USD {value}.",
                                       "evidence_ids":[fact["id"]],"asserted_value":value,
                                       "constraints":{**CLAIM["constraints"],"entity":ticker}})
                response = {**RESEARCH, "claims":claims, "summary":" ".join(c["text"] for c in claims)}
                review = {**REVIEW, "reviewed_claim_ids":[c["id"] for c in claims]}
                if reply.startswith("Exclude"):
                    review["request_coverage"] = [{"part":"part-1","status":"answered","claim_id":claims[0]["id"],
                                                    "answer_sentence":claims[0]["text"]}]
                model = ScriptedModel([
                    ("planner",{"action":"clarify","question":"Confirm the companies to include?"}),
                    ("planner",{"action":"research","scope":{**SCOPE,"entities":proposed}}),
                    ("researcher",response), ("critic",review), ("planner",{"action":"finish"})])
                engine = ResearchEngine(store,model,tools)
                self.assertEqual(engine.ask(sid,opening)["status"],"waiting_for_clarification")
                result = engine.ask(sid,reply)
                if valid:
                    self.assertEqual(result["status"],"complete",result["report"]["issues"])
                    for role,ctx in model.calls:
                        if role in ("researcher","critic"):
                            self.assertEqual(ctx["scope"]["entities"],proposed)
                            self.assertEqual({e["entity"] for e in ctx["evidence"]},set(proposed))
                    self.assertEqual({e["entity"] for e in result["report"]["evidence"]},set(proposed))
                else:
                    self.assertEqual(result["episode"]["stop_reason"],"planner_issuer_conflicts_with_user")
                    self.assertEqual([role for role,_ in model.calls],["planner","planner"])
