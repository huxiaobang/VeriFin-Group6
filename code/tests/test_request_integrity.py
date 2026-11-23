import unittest

from verifin.engine import ResearchEngine
from verifin.request_review import coverage_issues, report_coverage, request_parts
from verifin.storage import SessionStore
from tests.test_engine import FACT, FixtureTools, RESEARCH, REVIEW, SCOPE, ScriptedModel


class RequestIntegrityTests(unittest.TestCase):
    def test_explicit_lists_and_shared_qualifiers(self):
        cases = {
            "请分别列出营业收入和经营现金流。": ["请分别列出营业收入", "经营现金流。"],
            "请列出营收（含商品和服务）和毛利。": ["请列出营收（含商品和服务）", "毛利。"],
            "List both cash and cash equivalents and revenue.": ["List both cash and cash equivalents", "revenue."],
            "请列出现金和现金等价物。": [],
            "请给出收入与成本的比值。": [],
            "微软的营收与经营现金流分别是多少？": ["微软的营收","经营现金流分别是多少？"],
            "请分别列出现金流、应收与收入的比值。": ["请分别列出现金流","应收与收入的比值。"],
            "收入多少？现金流多少？另列库存。": ["收入多少？", "现金流多少？", "另列库存。"],
            "收入多少？现金流多少？请用中文回答。": ["收入多少？", "现金流多少？ 请用中文回答。"],
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertEqual(list(request_parts(request).values()), expected)

    def test_only_an_equivalent_question_may_reuse_the_same_answer(self):
        first = "微软这个季度的营业收入是多少？"
        claim = {"id":"n", "text":"本季营业收入为 69.632 十亿美元。"}
        rows = [{"part":p,"status":"answered","claim_id":"n","answer_sentence":claim["text"]}
                for p in ("part-1","part-2")]
        for second, expected in [
            ("换句话说，这一季公司的营收是多少？", []),
            ("换句话说，上季公司的营收是多少？", ["part-2"]),
            ("换句话说，这一季公司的营业利润是多少？", ["part-2"]),
            ("换句话说，苹果这个季度的营收是多少？", ["part-2"]),
            ("这能说明行业需求增长了吗？", ["part-2"]),
        ]:
            with self.subTest(second=second):
                result = coverage_issues({"part-1":first,"part-2":second},{"request_coverage":rows},[claim])
                self.assertEqual([i["target_id"] for i in result], expected)
        changed_basis = {"part-1":"剔除并购后的本季营收是多少？","part-2":"换句话说，本季公司的营收是多少？"}
        self.assertEqual([i["target_id"] for i in coverage_issues(changed_basis,{"request_coverage":rows},[claim])],["part-2"])

    def test_exact_sentence_ending_in_a_quoted_table_column_stays_answered(self):
        question = "给 USD million、季度截止日和表格定位。"
        parts = {"part-2": question}
        requirements = [{"id": "part-2", "text": question}]
        answer = ('Its February 26, 2025 earnings release shows USD 39,331 million in the '
                  '“CONDENSED CONSOLIDATED STATEMENTS OF INCOME” table, in the “Revenue” row '
                  'under “Three Months Ended / January 26, / 2025.”')
        claim = {"id": "locator", "status": "Supported", "requirement_ids": ["part-2"],
                 "text": f"NVIDIA’s FY2025 Q4 ended January 26, 2025. {answer} "
                         "The prior-quarter earnings exhibit refers to the upcoming release."}
        row = {"part": "part-2", "status": "answered", "claim_id": "locator", "answer_sentence": answer}

        self.assertEqual(coverage_issues(parts, {"request_coverage": [row]}, [claim], requirements=requirements), [])
        self.assertEqual(report_coverage(parts, [row], [claim], reviewed=True, requirements=requirements), [row])

        for quoted in (answer.replace("39,331", "39,330"), answer[4:], answer + " The prior-quarter"):
            with self.subTest(quote=quoted):
                changed = {**row, "answer_sentence": quoted}
                coverage = report_coverage(parts, [changed], [claim], reviewed=True, requirements=requirements)
                self.assertEqual(coverage[0]["status"], "gap")

        continued = {**claim, "text": f"{answer} as printed at the head of the table."}
        self.assertTrue(coverage_issues(parts, {"request_coverage": [row]}, [continued], requirements=requirements))
        for change in ({"requirement_ids": ["part-1"]}, {"stale": True}, {"status": "Insufficient Evidence"}):
            with self.subTest(claim_change=change):
                coverage = report_coverage(parts, [row], [{**claim, **change}], reviewed=True, requirements=requirements)
                self.assertEqual(coverage[0]["status"], "gap")

    def test_short_retry_keeps_original_request_and_previously_unanswered_checklist_item(self):
        original = "Assess quarterly operating performance."
        required = [*SCOPE["requirements"], {"id":"cash","text":"Explain cash generation."}]
        script = [("planner", {"action":"research", "scope":{**SCOPE, "requirements":required}}),
                  ("researcher", {**RESEARCH, "unanswered_requirement_ids":["cash"]}),
                  ("critic", {**REVIEW,"reviewed_requirement_ids":["req:revenue","cash"]}),
                  ("planner", {"action":"finish"})]
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            store.put_evidence(sid, FACT)
            first = ResearchEngine(store, ScriptedModel(script), FixtureTools()).ask(sid,original)
            self.assertEqual(first["status"], "partial")
            for version in (1, 2):
                # Simulate a planner dropping the very item that was missing.
                roles = ScriptedModel([("planner", {"action":"research","requirements":SCOPE["requirements"]}), *script[1:]])
                retry = ResearchEngine(store, roles, FixtureTools()).ask(sid,"请再回答一次")
                self.assertEqual(retry["status"],"partial")
                self.assertEqual(retry["report"]["retry_of_version"],version)
                self.assertEqual(retry["report"]["request"],original)
                self.assertEqual(retry["report"]["unanswered"],[required[1]])
                self.assertTrue(all(c["requirements"] == required for _, c in roles.calls))
            self.assertEqual([m["content"] for m in store.get_context(sid)["messages"] if m["role"]=="user"],
                             [original,"请再回答一次","请再回答一次"])

    def test_offline_new_followup_resets_the_checklist(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            store.update_scope(sid,SCOPE)
            engine = ResearchEngine(store,None,FixtureTools())
            engine.ask(sid,"Report revenue.")
            followup = engine.ask(sid,"Where does cash flow come from?")["report"]
            self.assertEqual([r["text"] for r in followup["scope"]["requirements"]],[followup["request"]])
            self.assertEqual(followup["request_parts"],{"part-1":followup["request"]})

    def test_reopen_reports_created_by_a_version_that_stored_only_the_retry_shortcut(self):
        original = "营业收入是多少？经营现金流是多少？"
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            store.update_scope(sid,SCOPE)
            engine = ResearchEngine(store,None,FixtureTools())
            report = engine.ask(sid,original)["report"]
            for _ in range(2):
                scope = {**report["scope"],"requirements":[{"id":"legacy-repeat","text":"请再回答一次"}]}
                store.update_scope(sid,scope)
                old = {**report,"request":"请再回答一次","scope":scope}
                old.pop("request_parts")
                store.save_report(sid,old)
            retry = engine.ask(sid,"请再回答一次")["report"]
            self.assertEqual(retry["retry_of_version"],1)
            self.assertEqual((retry["request"],retry["request_parts"]),(original,report["request_parts"]))
            self.assertEqual(retry["scope"]["requirements"],SCOPE["requirements"])
            self.assertEqual(store.get_report(sid,3)["request"],"请再回答一次")
