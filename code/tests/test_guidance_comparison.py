import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_engine import ScriptedModel
from verifin.engine import ResearchEngine
from verifin.financial import GUIDANCE_VARIANCE
from verifin.storage import SessionStore
from verifin.tools import DIRECT_GUIDANCE, ToolRegistry
from verifin.verification import check_rules


METHOD = "implied-period-guidance"
QUESTION = "ACME FY2026 Q1 交出的成绩和上一份季报说的差多少？"
ARGS = {"entity": "ACME", "method": METHOD, "comparison": DIRECT_GUIDANCE, "fiscal_quarter": "2026Q1"}
SCOPE = {"entities": ["ACME"], "period_start": "2025-01-01", "period_end": "2025-03-31",
         "cutoff": "2025-04-30", "requirements": [{"id": "r1", "text": QUESTION}]}


def corpus(directory, **options):
    forecast = options.get("forecast", "Revenue is expected to be between USD 1.7 billion and USD 2,300 million.")
    currency = options.get("currency", "USD")
    stated_end = options.get("stated_end", "March 31, 2025")
    outlook = options.get("outlook", "first quarter of fiscal 2026")
    previous = f"""<html><head><title>Document</title></head><body>
    <div><font>Acme Announces Financial Results for Fourth Quarter and Fiscal 2025</font></div>
    <div><font>Acme's outlook for the {outlook} is as follows:</font></div>
    <div><font>•</font><font>{forecast}</font></div>
    <div>Segment revenue is expected to be USD 500 million.</div>
    </body></html>"""
    current = f"""<html><head><title>Document</title></head><body>
    <h1>Acme First Quarter Fiscal 2026 Results</h1>
    <p>Acme today reported revenue of about USD 1.8 billion for the first quarter ended {stated_end}.</p>
    <h2>Q1 Fiscal 2026 Summary</h2><table>
      <tr><th colspan="5">GAAP</th></tr>
      <tr><th>({currency} in millions, except earnings per share)</th>
          <th colspan="2">Q4 FY25</th><th colspan="2">Q1 FY26</th></tr>
      <tr><td>Revenue</td><td colspan="2">2,500</td><td colspan="2">1,800</td></tr>
    </table>
    <h2>Q1 Fiscal 2026 Summary</h2><table>
      <tr><th colspan="2">Non-GAAP</th></tr>
      <tr><th>({currency} in millions)</th><th>Q1 FY26</th></tr>
      <tr><td>Revenue</td><td>9,900</td></tr>
    </table></body></html>"""
    docs, files = [], {}
    for name, raw, start, end, date in (
        ("prior", previous, "2024-10-01", "2024-12-31", options.get("prior_published", "2025-01-30")),
        ("actual", current, "2025-01-01", "2025-03-31", "2025-04-22"),
        ("mirror", current, "2025-01-01", "2025-03-31", "2025-04-22"),
    ):
        src = name + ".html"
        excerpt = name + ".txt"
        docs.append({"entity": "ACME", "source": src, "text": excerpt, "period_start": start, "period_end": end,
                     "published_at": date, "form": "8-K" if name != "mirror" else "issuer_release"})
        files.update({path: {"url": f"https://example.org/{path}", "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                             "retrieved_at": "2025-04-30"} for path in (src, excerpt)})
        for path in (src, excerpt):
            (directory / path).write_text(raw, encoding="utf-8")
    manifest = {"companies": [{"ticker": "ACME", "name": "Acme Corp", "cik": "1"}], "documents": docs, "files": files}
    (directory / "manifest.json").write_text(json.dumps(manifest))


@unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML statement parser is optional")
class GuidanceComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.store = SessionStore(":memory:")
        self.addCleanup(self.store.close)
        self.session = self.store.create_session()["id"]

    def tool(self, scope=None, *, question=QUESTION, arguments=None, **options):
        corpus(self.directory, **options)
        self.store.append_message(self.session, "user", question)
        self.store.add_event(self.session, {"type": "episode_checkpoint", "episode": {
            "status": "running", "request": question, "research_skills": [METHOD], "skill_selection_source": "planner",
        }})
        tools = ToolRegistry(self.store, self.directory)
        return tools.execute("analyze_disclosures", arguments or ARGS, self.session, scope or SCOPE)

    def test_prior_release_range_and_exact_actual_reach_researcher_and_critic(self):
        output = self.tool()
        self.assertTrue(output["ok"], output)
        actual, low, high = output["evidence"]
        middle, variance = output["calculations"]
        self.assertEqual([r["value"] for r in (actual, low, high, middle, variance)], [1800, 1700, 2300, 2000, -10])
        self.assertEqual(actual["source_url"], "https://example.org/actual.html")
        self.assertEqual(low["source_url"], "https://example.org/prior.html")
        self.assertIn("Q1 FY26", actual["locator"])
        self.assertEqual(actual["period_end_locator"], "/html/body/p")
        self.assertEqual(variance["operation"], GUIDANCE_VARIANCE)
        self.assertEqual(variance["operand_ids"], [actual["id"], middle["id"]])
        self.assertEqual(variance["fiscal_quarter"], "2026Q1")

        claim = {"id": "claim:variance", "text": "FY2026 Q1 actual revenue versus the prior guidance midpoint was -10%.",
                 "requirement_ids": ["r1"], "evidence_ids": [r["id"] for r in (actual, low, high)],
                 "calculation_ids": [variance["id"]], "constraints": {"entity": "ACME", "unit": "%"},
                 "asserted_value": -10, "asserted_unit": "%"}
        script = [
            ("planner", {"action": "tool", "tool": "analyze_disclosures", "arguments": ARGS,
                         "scope": SCOPE, "research_skills": [METHOD]}),
            ("planner", {"action": "research"}),
            ("researcher", {"claims": [claim], "summary": claim["text"],
                            "answered_requirement_ids": ["r1"], "unanswered_requirement_ids": []}),
            ("critic", {"issues": [], "reviewed_claim_ids": [claim["id"]], "reviewed_requirement_ids": ["r1"]}),
            ("planner", {"action": "finish"}),
        ]
        model = ScriptedModel(script)
        sid = self.store.create_session()["id"]
        result = ResearchEngine(self.store, model, ToolRegistry(self.store, self.directory)).ask(
            sid, QUESTION, as_of=SCOPE["cutoff"])
        self.assertEqual(result["status"], "complete", (result["report"]["stop_reason"], result["report"]["issues"]))
        context = next(ctx for role, ctx in model.calls if role == "researcher")
        self.assertIn(variance["id"], [r["id"] for r in context["calculations"]])
        self.assertEqual(result["report"]["calculations"][-1]["id"], variance["id"])

        audited = {"claims": [claim], "scope": SCOPE, "evidence": output["evidence"],
                   "calculations": [middle, {**variance, "value": -11}]}
        self.assertIn("numerical_inconsistency", {issue["issue_type"] for issue in check_rules(audited)})
        audited["calculations"][-1] = {**variance, "period_end": "2025-06-30"}
        self.assertIn("context_mismatch", {issue["issue_type"] for issue in check_rules(audited)})

    def test_declared_midpoint_and_fail_closed_on_ineligible_sources(self):
        output = self.tool(question="把 ACME 实际交出的数字拿来和当初说的那个比一下。",
                           forecast="Revenue is expected to be USD 2 billion, plus or minus 3%.")
        variance, = output["calculations"]
        self.assertEqual(variance["value"], -10)
        self.assertEqual(len(output["evidence"]), 2)
        for scope, options in (({**SCOPE, "cutoff": "2025-04-21"}, {}),
                               ({**SCOPE, "period_end": "2025-06-30"}, {}),
                               (SCOPE, {"currency": "CAD"}),
                               (SCOPE, {"prior_published": "2025-04-23"}),
                               (SCOPE, {"stated_end": "March 30, 2025"}),
                               (SCOPE, {"outlook": "second quarter of fiscal 2026"})):
            with self.subTest(scope=scope, options=options):
                missing = self.tool(scope, **options)
                self.assertTrue(missing["ok"], missing)
                self.assertFalse(missing["calculations"])
                self.assertTrue(missing["data"]["skill_gaps"])

    def test_structured_mode_requires_a_canonical_quarter_and_selected_method(self):
        for arguments in ({k: v for k, v in ARGS.items() if k != "fiscal_quarter"},
                          {**ARGS, "fiscal_quarter": "FY2026 Q1"},
                          {**ARGS, "method": "cash-flow-bridge"},
                          {k: v for k, v in ARGS.items() if k != "comparison"}):
            with self.subTest(arguments=arguments):
                self.assertFalse(self.tool(arguments=arguments)["ok"])
        legacy = self.tool(arguments={"entity": "ACME", "method": METHOD})
        self.assertTrue(legacy["ok"])
        self.assertFalse(legacy["calculations"])
