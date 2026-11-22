from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tests.critic_fixture import fixture_verdicts
from tests.test_engine import FACT, SCOPE
from verifin.engine import Budget, ResearchEngine
from verifin.model import validate_response
from verifin.reporting import question_answers
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class MarginClaims(TestCase):
    def test_direct_margin_questions_use_the_same_measure_as_the_cited_value(self):
        for question, drafts, answered in (
            ("淨利率是多少？", [("營業利潤率為 40%。", "operating_income", True)], ["gap"]),
            ("淨利潤率是多少？", [("淨利潤率為 20%。", "net_income", True)], ["answered"]),
            ("毛利率是多少？", [("毛利率為 40%。", "operating_income", True)], ["gap"]),
            ("毛利率是多少？", [("40% gross margin.", "operating_income", False)], ["gap"]),
            ("毛利率是多少？", [("毛利率為 40%。", "gross_and_operating", False)], ["gap"]),
            ("毛利率是多少？", [("毛利率為 60%。", "gross_profit", True)], ["answered"]),
            ("毛利率是多少？", [("Gross margin was 60%.", "reported_gross", False)], ["answered"]),
            ("毛利率是多少？", [("Gross margin was 40%.", "reported_operating", False)], ["gap"]),
            ("淨利率是多少？", [("40%。", "operating_income", False)], ["gap"]),
            ("利润率是多少？", [("按营业利润率口径，营业利润率为 40%。", "operating_income", True)], ["answered"]),
            ("利润率是多少？", [("利润率为 40%。", "operating_income", False)], ["gap"]),
            ("營業利潤率多少？毛利率多少？",
             [("營業利潤率為 40%。", "operating_income", True),
              ("毛利率為 40%。", "operating_income", True)], ["answered", "gap"]),
            ("毛利率與淨利率是多少？",
             [("毛利率為 60%。", "gross_profit", True), ("淨利率為 20%。", "net_income", True)], ["answered", "answered"]),
        ):
            with self.subTest(question=question, drafts=drafts):
                report = self.ask(question, drafts)
                self.assertEqual([row["status"] for row in question_answers(report)], answered, report["issues"])
                self.assertEqual(report["status"], "complete" if "gap" not in answered else "partial", report["issues"])
                for claim in report["claims"]:
                    if claim["text"] in {"毛利率為 40%。", "40% gross margin.", "Gross margin was 40%."}:
                        self.assertEqual(claim["status"], "Insufficient Evidence")

    def ask(self, question, drafts):
        with TemporaryDirectory() as folder, SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            requirements = [{"id":f"req-{i}", "text":f"Answer item {i}."} for i in range(1, len(drafts) + 1)]
            scope = {**SCOPE, "requirements":requirements}
            store.update_scope(sid, scope)
            tools = ToolRegistry(store, Path(__file__).resolve().parents[1] / "data", cache_dir=Path(folder)/"cache")
            facts = {metric:store.put_evidence(sid, {**FACT, "id":f"fact:{metric}", "metric":metric, "value":value,
                        "business_scope":"consolidated", "basis":"US-GAAP", "currency":"USD", "scale":1})
                     for metric, value in (("revenue", 100_000), ("gross_profit", 60_000),
                                           ("operating_income", 40_000), ("net_income", 20_000))}
            calculations = {}
            for metric in ("gross_profit", "operating_income", "net_income"):
                result = tools.execute("calculate", {"operation":"margin",
                    "operand_ids":[facts[metric]["id"], facts["revenue"]["id"]]}, sid, scope)
                self.assertTrue(result["ok"], result)
                calculations[metric] = store.put_calculation(sid, result["calculations"][0])
            documents = {
                key:store.put_evidence(sid, {"id":key,"entity":"MSFT","kind":"document",
                    "source_url":FACT["source_url"],"snapshot_hash":FACT["snapshot_hash"],"locator":"fixture excerpt",
                    "published_at":FACT["published_at"],"text":text})
                for key,text in (("reported_gross","Gross margin was 60%."),
                                 ("reported_operating","Operating margin was 40%."))
            }
            claims = []
            for i, (text, metric, declare) in enumerate(drafts):
                calcs = (["gross_profit", "operating_income"] if metric == "gross_and_operating" else
                         [metric] if metric in calculations else [])
                claims.append({"id":f"claim-{i}", "text":text, "requirement_ids":[requirements[i]["id"]],
                    "calculation_ids":[calculations[name]["id"] for name in calcs],
                    "evidence_ids":[documents[metric]["id"]] if metric in documents else [],
                    "constraints":{"metric":calculations[metric]["metric"]} if declare else {}})

            class Model:
                def complete(self, role, context):
                    ids = [r["id"] for r in requirements]
                    if role == "planner":
                        result = {"action":"research", "scope":scope}
                    elif role == "researcher":
                        result = {"claims":claims, "summary":" ".join(c["text"] for c in claims),
                                  "answered_requirement_ids":ids, "unanswered_requirement_ids":[]}
                    else:
                        result = {"issues":[], "reviewed_claim_ids":[c["id"] for c in claims],
                            "reviewed_requirement_ids":ids,
                            "request_coverage":[{"part":part, "status":"answered", "claim_id":claim["id"],
                                "answer_sentence":claim["text"]}
                                for part,claim in zip(context["request_parts"], claims, strict=True)]}
                    return validate_response(role, fixture_verdicts(role, result, context), context)

            return ResearchEngine(store, Model(), tools, finish_after_review=True).ask(
                sid, question, budget=Budget(max_model_calls=3))["report"]
