import unittest
from types import SimpleNamespace

from tests.test_engine import CLAIM, FACT, RESEARCH, REVIEW, SCOPE, FixtureTools, ScriptedModel
from verifin.engine import ResearchEngine
from verifin.scope import stated_cutoff
from verifin.skill_calculations import requested_fiscal_year, requested_quarter
from verifin.skill_routing import amended_issuers, issuer_decision_conflict, resolve_issuer_scope, user_issuer_boundary
from verifin.storage import SessionStore


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
