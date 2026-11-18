import unittest

from verifin.context import issuer_scoped_context
from verifin.skill_calculations import next_action, prepare, requested_quarter, source_request, targets_prior_quarter
from verifin.skill_routing import issuer_decision_conflict, named_issuers, resolve_issuer_scope, select_skills, user_issuer_boundary


class SkillScopeTests(unittest.TestCase):
    catalog = [{"ticker":"AAA", "name":"Alpha Motors"}, {"ticker":"BBB", "name":"Beta Motors", "aliases":["乙汽车"]}]
    scope = {"entities":["AAA"], "cutoff":"2030-06-30", "period_end":"2030-03-31"}
    history = ["甲动力刚发完2030年第一季财报，汽车生意怎么样？"]

    def issuer(self, question, scope=None):
        return resolve_issuer_scope(question, self.history, scope or self.scope, self.catalog)

    def test_switch_comparison_and_unknown_possessor(self):
        for text in ("这季甲动力的经销商库存减少了多少？", "Compared with last quarter, how much did its inventory change?"):
            self.assertEqual(self.issuer(text), (["AAA"], "AAA"))
        self.assertEqual(self.issuer("这季乙汽车的经销商库存减少了多少？"), (["BBB"], "BBB"))
        for text in ("改看乙汽车，和上季度相比，经销商库存减少了多少？",
                     "Compare Beta Motors' inventory with the previous quarter."):
            self.assertEqual(self.issuer(text), (["BBB"], "BBB"))
        for text in ("与乙汽车相比，这季的库存呢？", "Compare Alpha Motors and Beta Motors."):
            companies, single = self.issuer(text)
            self.assertCountEqual(companies, ["AAA","BBB"])
            self.assertIsNone(single)
        for text in ("这季甲动力科技的经销商库存变化多少？", "这季找不到的经销商库存减少了多少？", "换到 ZZZ，库存变化多少？",
                     "天外食品的现金流怎样？", "How is Riverton's operating cash flow?"):
            self.assertEqual(self.issuer(text), ([], None))
        self.assertEqual(self.issuer("库存变化多少？", {"entities":["AAA","BBB"]}), (["AAA","BBB"], None))
        self.assertEqual(self.issuer("它的库存呢？", {"entities":[]}), ([], None))

    def test_shift_is_distinct_from_reference_comparison(self):
        self.assertTrue(targets_prior_quarter("这季明白了，上一季度经销商库存减少了多少？"))
        self.assertFalse(targets_prior_quarter("这季经销商库存比上一季度少了多少？"))
        self.assertFalse(targets_prior_quarter("How much did this quarter change compared with the previous quarter?"))
        self.assertEqual(requested_quarter([*self.history,"那上一季度呢？","那再往上一季度呢？"]), "2029Q3")
        self.assertIsNone(requested_quarter(["Compare 2029Q1 and 2030Q1", "上一季度呢？"]))
        self.assertIsNone(source_request("How did management explain last quarter?", []))

    def test_explicit_user_issuers_bound_planner_scope_and_issuer_directed_tools(self):
        catalog = [*self.catalog[:1], {**self.catalog[1],"cik":"0000001234"}]
        boundary = user_issuer_boundary("请核对乙汽车",["BBB"],["BBB"],["AAA"])
        for target in ("BBB","乙汽车","CIK0000001234","1234"):
            self.assertIsNone(issuer_decision_conflict(boundary,{"scope":{"entities":[target]}},catalog))
        for decision in ({"scope":{"entities":["AAA"]}}, {"scope":{"entities":[]}},
                         {"action":"tool","tool":"discover_company","arguments":{"query":"AAA"}},
                         {"action":"tool","tool":"market_prices","arguments":{"entity":"AAA"}}):
            self.assertIsNotNone(issuer_decision_conflict(boundary,decision,catalog))
        self.assertEqual(named_issuers("查 CIK0000001234",catalog), ["BBB"])
        self.assertEqual(self.issuer("从AAA改看乙汽车，这季库存如何？"), (["BBB"],"BBB"))
        self.assertEqual(self.issuer("换成每股口径，收入怎么变？"), (["AAA"],"AAA"))
        for q in ("改看第四季度。销售增长了多少？能说明行业需求全面回暖吗？",
                  "Look at the previous quarter instead", "Switch to operating cash flow"):
            self.assertEqual(self.issuer(q), (["AAA"],"AAA"))
        self.assertIsNone(user_issuer_boundary("继续",[],["AAA"],["AAA"]))
        peers = user_issuer_boundary("乙汽车和一家同行",["BBB"],["BBB"],["AAA"])
        self.assertIsNone(issuer_decision_conflict(peers,{"scope":{"entities":["AAA","BBB"]}},catalog))
        self.assertIsNotNone(issuer_decision_conflict(peers,{"scope":{"entities":["AAA"]}},catalog))

    def test_prior_quarter_selects_actual_adjacent_cells_at_fiscal_year_boundary(self):
        question = "上一季度经销商库存减少了多少辆？"
        state = prepare(question, self.history, select_skills(question), self.scope)
        self.assertEqual((state["prior_quarter"], state["quarter"]), ("2029Q3","2029Q4"))
        cells = [{"id":quarter, "chart_period":quarter, "entity":"AAA", "cell_type":"chart_inventory_cell",
                  "row_label":"Dealer inventory", "figure_id":"line"} for quarter in ["2029Q3","2029Q4","2030Q1"]]
        state.update(steps=[{"tool":"search_documents","ok":True}], queries=[], operands=[c["id"] for c in cells])
        action = next_action(state, {"evidence":cells})
        self.assertEqual(action["arguments"]["operand_ids"], ["2029Q4","2029Q3"])
        for different in ("这季对照 2028-06-30 经销商库存减少了多少？", "这季不谈了，下一季度的经销商库存如何变化？"):
            self.assertIsNone(prepare(different, self.history, select_skills(different), self.scope))

    def test_scoped_records_exclude_old_issuer_and_derived_operands_but_keep_archived_history(self):
        alpha, beta = {"id":"a", "entity":"AAA"}, {"id":"b","entity":"BBB"}
        report = {"scope":{"entities":["AAA"]}, "claims":[{"evidence_ids":["a"]}]}
        ctx = {"scope":{"entities":["BBB"]}, "evidence":[alpha,beta],
               "calculations":[{"id":"c2","entity":"BBB","operand_ids":["c1","b"]},
                               {"id":"c1","entity":"BBB","operand_ids":["a","b"]}],
               "reports":[report], "episode":{"last_tool_result":{"evidence":[alpha], "summary":"old source"},
                         "recent_tool_results":[{"tool":"calculate","ok":True,"calculation_ids":["c2"],"data":{"value":20}}]}}
        scoped = issuer_scoped_context(ctx)
        self.assertEqual(scoped["evidence"], [beta])
        self.assertEqual(scoped["calculations"], [])
        self.assertEqual(scoped["reports"], [report])
        self.assertTrue(scoped["episode"]["last_tool_result"]["outside_current_issuers"])
        self.assertNotIn("data", scoped["episode"]["recent_tool_results"][0])
        self.assertEqual(len(ctx["evidence"]), 2)
        both = issuer_scoped_context({**ctx,"scope":{"entities":["AAA","BBB"]}})
        self.assertEqual(both["evidence"], [alpha,beta])
        self.assertEqual(both["calculations"], ctx["calculations"])
        for identity in ("乙汽车","Beta Motors","CIK0000001234","1234"):
            alias = issuer_scoped_context({**ctx,"scope":{"entities":[identity]}},
                                         [{**self.catalog[1],"cik":"0000001234"}])
            self.assertEqual(alias["evidence"], [beta])
