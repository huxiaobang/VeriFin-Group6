"""Recorded-role tests for comparing the frozen Microsoft fiscal quarters."""
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

from tests.critic_fixture import fixture_verdicts
from tests.test_engine import ScriptedModel
from tests.test_tools import DATA
from verifin.data import stable_id
from verifin.engine import Budget, ResearchEngine, digest
from verifin.model import ModelError, validate_response
from verifin.reporting import report_view
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


QUESTION = ("用我们刚才查到的结果，比较微软 FY2025 Q1 和 Q2 的营业利润率。Q2 比 Q1 高还是低，差多少个百分点？"
            "请列出两个季度的出处。")
FIRST = "Microsoft FY2025 Q2 revenue and operating margin"
SCOPE = {"entities": ["MSFT"], "cutoff": "2025-01-29"}
CORRECT = "微软 FY2025 Q2 营业利润率较 Q1 下降约 1.13 个百分点。"
WRONG = CORRECT.replace("1.13", "1.12")


def call(tools, sid, name, args, scope=SCOPE):
    result = tools.execute(name, args, sid, scope)
    assert result["data"]["network_requests"] == 0
    receipt = {"type": "tool_call", "tool": name, "arguments": args, "ok": result["ok"],
               "observation": result, "observation_sha256": digest(result)}
    tools.store.record_tool_return(sid, receipt)
    return result


def prepare(tools, sid):
    doc = next(row for row in tools.data.manifest["documents"] if row["source"].endswith("msft_fy25q2_ir.html"))
    url = tools.data.info(doc["source"])["url"]
    text = tools.data.raw(doc["text"]).decode()
    result = call(tools, sid, "read_document", {"entity": "MSFT", "document_id": stable_id("source_doc_", url),
                  "offset": text.index("MICROSOFT CORPORATION INCOME STATEMENTS"), "limit_chars": 2600})
    q2 = {r["metric"]: r for r in result["evidence"] if r.get("period_start") == "2024-10-01"
          and r.get("period_end") == "2024-12-31"}
    q1 = {metric: call(tools, sid, "financial_facts", {"entity": "MSFT", "metric": metric,
               "period_start": "2024-07-01", "period_end": "2024-09-30"})["evidence"][0]
          for metric in ("revenue", "operating_income")}
    margins = [call(tools, sid, "calculate", {"operation": "margin", "operand_ids":
                    [quarter[metric]["id"] for metric in ("operating_income", "revenue")]})["calculations"][0]
               for quarter in (q2, q1)]
    change = call(tools, sid, "calculate", {"operation": "percentage_point_change", "operand_ids":
                  [row["id"] for row in margins]})["calculations"][0]
    return result["evidence"][0], q1, q2, margins, change


def claim(ref, text, source, calculation=None):
    return {"id": ref, "text": text, "constraints": {}, "evidence_ids": [source],
            "calculation_ids": [calculation] if calculation else [], "requirement_ids": ["request"]}


class FixtureModel:
    mode = "scripted_fiscal_comparison"

    def __init__(self, question, claims, primary, source=None):
        self.question, self.claims, self.primary, self.source = question, claims, primary, source

    def complete(self, role, ctx):
        if role == "planner":
            response = {"action": "finish"} if ctx["episode"].get("reviewed") else {
                "action": "research", "requirements": [{"id": "request", "text": self.question}]}
        elif role == "researcher":
            response = {"summary": " ".join(c["text"] for c in self.claims), "claims": self.claims,
                        "answered_requirement_ids": ["request"], "unanswered_requirement_ids": []}
        else:
            claims = {c["id"]: c for c in ctx["claims"]}
            response = {"issues": [], "reviewed_claim_ids": list(claims), "reviewed_requirement_ids": ["request"]}
            if self.source:
                response["request_coverage"] = [{"part": part, "status": "answered", "claim_id": ref,
                    "answer_sentence": claims[ref]["text"]} for part, ref in zip(
                        ctx["request_parts"], (self.primary, self.source), strict=True)]
        return validate_response(role, fixture_verdicts(role, response, ctx), ctx)


@skipUnless(find_spec("lxml"), "native HTML disclosure parser is optional")
class FiscalMarginComparisonTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "session.sqlite3"
        self.cache = Path(tmp.name) / "cache"
        with SessionStore(self.path) as store:
            self.sid = store.create_session()["id"]
            store.update_scope(self.sid, SCOPE)
            self.span, self.q1, self.q2, self.margins, self.change = prepare(
                ToolRegistry(store, DATA, cache_dir=self.cache), self.sid)

    def test_real_quarter_values_and_cutoff(self):
        self.assertEqual([self.q1[key]["value"] for key in ("revenue", "operating_income")], [65_585_000_000, 30_552_000_000])
        self.assertEqual([self.q2[key]["value"] for key in ("revenue", "operating_income")], [69_632_000_000, 31_653_000_000])
        self.assertAlmostEqual(self.margins[0]["value"], 45.45754825367647)
        self.assertAlmostEqual(self.margins[1]["value"], 46.58382252039338)
        self.assertAlmostEqual(self.change["value"], -1.12627426671691)
        self.assertEqual(self.change["operand_ids"], [r["id"] for r in self.margins])
        with SessionStore(self.path) as store:
            tools = ToolRegistry(store, DATA, cache_dir=self.cache)
            args = {"entity": "MSFT", "period_end": "2024-12-31"}
            missing = call(tools, self.sid, "reconcile_operating_margin", args, {"entities": ["MSFT"]})
            self.assertFalse(missing["ok"])
            self.assertIn("dated analysis cutoff", missing["error"])
            reverse = call(tools, self.sid, "calculate", {"operation": "percentage_point_change",
                           "operand_ids": self.change["operand_ids"][::-1]})
            self.assertFalse(reverse["ok"])
            self.assertEqual(reverse["calculations"], [])
            valid = call(tools, self.sid, "reconcile_operating_margin", args)
            record = next(r for r in valid["calculations"] if r["metric"] == "operating_income_margin_change")
            self.assertAlmostEqual(record["value"], 1.87160823432787)
            context = store.get_context(self.sid, report_mode="none")
        context.update(scope=SCOPE, episode={"request": QUESTION}, requirements=[{"id": "request", "text": QUESTION}])
        yoy = "微软 FY2025 Q2 营业利润率较上年同期上升 1.87 个百分点。"
        for text, calculation, passes in (
                (CORRECT, self.change["id"], True), (yoy, record["id"], True),
                ("微软 FY2025 Q1 营业利润率较 Q2 高 1.13 个百分点。", self.change["id"], True),
                (CORRECT, None, False), (WRONG, self.change["id"], False),
                ("Q2 比 Q1 低 1.12 个百分点。", None, False),
                (CORRECT.replace("下降", "上升"), self.change["id"], False),
                ("微软 FY2025 Q2 营业利润率较 Q1 变动 +1.13 个百分点。", self.change["id"], False),
                (yoy.replace("上年同期", "Q1"), record["id"], False),
                (CORRECT.replace("较 Q1", "同比"), self.change["id"], False)):
            with self.subTest(text=text, calculation=calculation):
                row = claim("test", text, self.span["id"], calculation)
                issues = check_rules({**context, "claims": [row]})
                self.assertEqual(not issues, passes, issues)

    def test_source_printed_margin_delta_keeps_its_own_precision(self):
        from verifin.margin_comparison import defects
        current, prior = self.margins
        records = {r["id"]: r for r in [current, prior, *self.q1.values(), *self.q2.values()]}
        text = "FY2025 Q2 营业利润率摘要表展示为45%对47%，下降2个百分点。"
        source = {**self.span, "entity": "MSFT", "kind": "document",
                  "document_id": self.q2["revenue"]["document_id"], "document_report_date": current["period_end"],
                  "text": "GAAP quarterly results: Operating margin 45% 47% Down 2 ppts"}
        row = {**claim("reported", text, source["id"], self.change["id"]),
               "constraints": {"metric": "operating_income_margin_change"}}
        for wording, doc, supported in (
                (text, source, True), (text.replace("下降", "上升"), source, False),
                (text.replace("2个百分点", "3个百分点"), source, False),
                (text.replace("45%", "46%"), source, False),
                (text.replace("摘要表", "精确"), source, False),
                (text, {**source, "entity": "OTHER"}, False),
                (text, {**source, "document_report_date": prior["period_end"]}, False),
                (text, {**source, "document_id": "other-table"}, False),
                (text, {**source, "text": source["text"].replace("quarterly", "annual")}, False),
                (text, {**source, "text": source["text"].replace("GAAP", "Non-GAAP")}, False),
                (text, {**source, "text": source["text"].replace("Operating", "Gross")}, False)):
            with self.subTest(wording=wording, doc=doc["text"], entity=doc["entity"]):
                issues = list(defects({**row, "text": wording}, [doc, self.change], records, QUESTION))
                self.assertEqual(not issues, supported, issues)

    def test_direct_comparative_quotation_remains_source_reviewable(self):
        text = "Operating margin for the quarter increased 1.50 percentage points from the prior year."
        document = {**self.span, "text": text}
        statement = claim("direct-quotation", text, document["id"])
        context = {"scope": SCOPE, "requirements": [{"id": "request", "text": text}],
                   "claims": [statement], "evidence": [document], "calculations": []}
        self.assertFalse(check_rules(context))
        fabricated = {**statement, "text": text.replace("1.50", "1.51")}
        self.assertEqual([r["issue_type"] for r in check_rules({**context, "claims": [fabricated]})], ["missing_reference"])

    def test_reconciliation_cutoff_is_optional_in_schema_and_limits_actual_published_rows(self):
        entity_scope = {"entities": ["MSFT"]}
        args = {"entity": "MSFT", "period_end": "2024-12-31", "document_id": self.span["document_id"]}
        with SessionStore(self.path) as store:
            tools = ToolRegistry(store, DATA, cache_dir=self.cache)
            schema = next(t["parameters"] for t in tools.available_tools() if t["name"] == "reconcile_operating_margin")
            self.assertIn("cutoff", schema["properties"])
            self.assertNotIn("cutoff", schema["required"])
            scope_before = store.get_session(self.sid)["scope"]
            for tool_cutoff, scope, expected in (
                (None, entity_scope, "Set scope.cutoff or pass the user's explicit cutoff"),
                ("2025-01-29", entity_scope, None),
                (None, SCOPE, None),
                ("2025-01-30T07:59:59.999999+08:00", SCOPE, None),
                ("2025-01-30T08:00:00+08:00", SCOPE, "cannot extend"),
                ("2025-01-28", SCOPE, "No unambiguous retrieved quarterly statement"),
                ("2025-01-29T18:00:00Z", SCOPE, "No unambiguous retrieved quarterly statement"),
                ("2099-01-01", SCOPE, "cannot extend"),
                ("2025-01-29T18:00:00", SCOPE, "timezone-aware"),
            ):
                with self.subTest(cutoff=tool_cutoff, scope=scope):
                    result = tools.execute("reconcile_operating_margin",
                                           {**args, **({"cutoff":tool_cutoff} if tool_cutoff else {})},self.sid,scope)
                    self.assertEqual(result["data"]["network_requests"],0)
                    if expected:
                        self.assertFalse(result["ok"])
                        self.assertIn(expected,result["error"])
                        self.assertFalse(result["evidence"] or result["calculations"])
                    else:
                        change = next(r for r in result["calculations"] if r["metric"] == "operating_income_margin_change")
                        self.assertAlmostEqual(change["value"],1.87160823432787)
            other_doc = tools.execute("reconcile_operating_margin",{**args,"document_id":"other-document"},self.sid,SCOPE)
            self.assertFalse(other_doc["ok"])
            self.assertFalse(other_doc["calculations"])
            self.assertEqual(store.get_session(self.sid)["scope"],scope_before)

    def test_planner_can_supply_cutoff_without_changing_research_scope(self):
        question = "微软 FY2025 Q2 营业利润率同比变动多少个百分点？使用 2025-01-29 的资料。"
        args = {"entity":"MSFT","period_end":"2024-12-31","cutoff":"2025-01-29"}
        script = [("planner",{"action":"tool","tool":"reconcile_operating_margin","arguments":args}),
                  ("planner",ModelError("stop fixture after the tool receipt"))]
        with SessionStore(self.path) as store:
            store.update_scope(self.sid,{"entities":["MSFT"]})
            tools = ToolRegistry(store, DATA, cache_dir=self.cache)
            ResearchEngine(store,ScriptedModel(script),tools,auto_skills=False).ask(self.sid,question)
            receipt = [e for e in store.events(self.sid) if e.get("tool") == "reconcile_operating_margin"][-1]
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["arguments"],args)
            self.assertEqual(receipt["observation"]["data"]["network_requests"],0)
            self.assertNotIn("cutoff",store.get_session(self.sid)["scope"])

    def test_reopened_session_rejects_rounded_difference_in_current_answer(self):
        before = claim("first-answer", "Microsoft FY2025 Q2 consolidated revenue was USD 69,632 million.",
                       self.q2["revenue"]["id"])
        with SessionStore(self.path) as store:
            engine = ResearchEngine(store, FixtureModel(FIRST, [before], before["id"]),
                                    ToolRegistry(store, DATA, cache_dir=self.cache), auto_skills=False)
            old = engine.ask(self.sid, FIRST, budget=Budget(16, 16, 240))["report"]
            self.assertEqual((old["status"], old["delivery_stage"]), ("complete", "reviewed"))
        wrong = claim("rounded", WRONG, self.span["id"])
        right = claim("full-precision", CORRECT, self.span["id"], self.change["id"])
        source = claim("sources", "Q1 SEC Company Facts 申报号 0000950170-24-118967。Q2 出处：" + self.span["source_url"], self.span["id"])
        source["evidence_ids"].append(self.q1["revenue"]["id"])
        with SessionStore(self.path) as store:
            engine = ResearchEngine(store, FixtureModel(QUESTION, [wrong, right, source], wrong["id"], source["id"]),
                                    ToolRegistry(store, DATA, cache_dir=self.cache), auto_skills=False)
            report = engine.ask(self.sid, QUESTION, budget=Budget(16, 16, 240))["report"]
            self.assertEqual(store.get_report(self.sid, 1), old)
            self.assertEqual(store.get_report(self.sid, 2), report)
        self.assertEqual((report["status"], report["delivery_stage"]), ("partial", "reviewed"))
        self.assertEqual({r["id"]: r["status"] for r in report["claims"]}, {
            "rounded": "Insufficient Evidence", "full-precision": "Supported", "sources": "Supported"})
        considered = report_view(report)["question_answers"]
        self.assertEqual([row["status"] for row in considered], ["gap", "answered"])
        self.assertNotIn(WRONG, [r.get("answer_sentence") for r in considered])
        self.assertEqual(report["request"], QUESTION)
        with SessionStore(self.path) as store:
            engine = ResearchEngine(store, FixtureModel(QUESTION, [right, source], right["id"], source["id"]),
                                    ToolRegistry(store, DATA, cache_dir=self.cache), auto_skills=False)
            corrected = engine.ask(self.sid, QUESTION, budget=Budget(16, 16, 240))["report"]
        self.assertEqual((corrected["status"], corrected["delivery_stage"]), ("complete", "reviewed"))
        self.assertEqual(report_view(corrected)["question_answers"][0]["answer_sentence"], CORRECT)
