"""Dated statement tools must advertise and enforce the same cutoff contract."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tests.test_income_bridge_source import FilingLibrary, ROWS
from tests import test_income_explanation
from tests.test_tools import DATA
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class StatementToolCutoffs(TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.store = self.enterContext(SessionStore(directory/"session.sqlite3"))
        self.sid = self.store.create_session()["id"]
        self.tools = ToolRegistry(self.store, DATA, cache_dir=directory/"cache")
        self.enterContext(patch("verifin.data.build_opener",side_effect=AssertionError("No network")))

    def test_all_dated_statement_tools_expose_the_optional_user_cutoff(self):
        tools = {t["name"]:t for t in self.tools.list_tools()}
        for name, arguments in (
            ("reconcile_operating_margin", {"entity":"MSFT","period_end":"2024-12-31"}),
            ("reconcile_net_income", {"entity":"MSFT"}),
            ("read_income_explanation", {"entity":"MSFT"}),
            ("analyze_disclosures", {"entity":"MSFT"}),
        ):
            with self.subTest(name=name):
                descriptor = tools[name]
                self.assertIn("cutoff", descriptor["parameters"]["properties"])
                self.assertNotIn("cutoff",descriptor["parameters"]["required"])
                self.assertIn("Requires scope.cutoff",descriptor["description"])
                missing = self.tools.execute(name,arguments,self.sid,{"entities":["MSFT"]})
                self.assertIn("A dated analysis cutoff is required",missing["error"])
                late = self.tools.execute(name,{**arguments,"cutoff":"2025-01-30"},self.sid,
                                          {"entities":["MSFT"],"cutoff":"2025-01-29"})
                self.assertIn("cannot extend",late["error"])
                for result in (missing,late):
                    self.assertFalse(result["evidence"] or result["calculations"])
                    self.assertEqual(result["data"]["network_requests"],0)
        self.assertEqual(ToolRegistry._effective_cutoff({"cutoff":"2099-01-01"},{}),"2099-01-01")

    def test_net_income_only_reads_filing_available_at_explicit_cutoff(self):
        self.tools.data = FilingLibrary(ROWS)
        scope = {"entities":["EXAMPLE"],"cutoff":"2026-08-01"}
        args = {"entity":"EXAMPLE","period_end":"2026-06-30"}
        with patch.object(self.tools,"_entity",return_value={"ticker":"EXAMPLE"}):
            early = self.tools.execute("reconcile_net_income",{**args,"cutoff":"2026-07-20T12:44:59Z"},self.sid,scope)
            exact = self.tools.execute("reconcile_net_income",{**args,"cutoff":"2026-07-20T20:45:00+08:00"},self.sid,scope)
        self.assertFalse(early["data"]["income_bridge"]["complete"])
        self.assertFalse(early["evidence"] or early["calculations"])
        self.assertTrue(exact["data"]["income_bridge"]["complete"])
        self.assertTrue(exact["evidence"] and exact["calculations"])
        self.assertEqual(exact["data"]["cutoff"],"2026-07-20T20:45:00+08:00")

    def test_analyzers_do_not_open_documents_published_after_explicit_cutoff(self):
        self.store.append_message(self.sid,"user","Reconcile segment totals in Microsoft's financial statement.")
        args = {"entity":"MSFT","method":"reconcile-segment-totals"}
        scope = {"entities":["MSFT"],"cutoff":"2025-01-29"}
        with patch.object(self.tools.data,"raw",wraps=self.tools.data.raw) as read:
            early = self.tools.execute("analyze_disclosures",{**args,"cutoff":"2025-01-28"},self.sid,scope)
            read.assert_not_called()
            exact = self.tools.execute("analyze_disclosures",{**args,"cutoff":"2025-01-29"},self.sid,scope)
            self.assertTrue(read.called)
        self.assertFalse(early["data"]["inspected_documents"])
        self.assertFalse(early["evidence"] or early["calculations"])
        self.assertTrue(exact["data"]["inspected_documents"])

    def test_income_explanation_filters_saved_operands_and_publication_cutoff(self):
        example = test_income_explanation.EarningsCauseTests()
        example.setUp()
        text = ("For the three months ended May 31, 2025, interest expense rose compared with "
                "last year due to higher debt.")
        late_discussion = {**example.doc,"text":text,"start":0,"end":len(text),"kind":"document",
                           "published_at":"2025-08-20","version":1}
        calculations = [{**r,"version":1,"published_at":"2025-07-01"} for r in example.calcs]
        question = "Why did operating income rise but net income decline?"
        receipt = {"tool":"reconcile_net_income","scope_version":1,"ok":True,"income_request":question,
                   "arguments":{"entity":"ABC"},"calculation_ids":[r["id"] for r in calculations],
                   "data":{"income_bridge":example.bridge,"cutoff":"2025-07-02"}}
        episode = {"request":question,"status":"running","id":"episode-test","recent_tool_results":[receipt]}
        ctx = {"scope":{},"evidence":[late_discussion],"calculations":calculations,"claims":[],"reports":[],
               "session":{"scope_version":1}}
        scope = {"entities":["ABC"],"cutoff":"2025-09-01"}
        args = {"entity":"ABC"}
        with (patch.object(self.tools,"_entity",return_value={"ticker":"ABC"}),
              patch.object(self.store,"latest_event",return_value={"episode":episode}),
              patch.object(self.store,"get_context",side_effect=lambda *_args, **_kwargs: dict(ctx)),
              patch.object(self.tools,"document_corpus",return_value=SimpleNamespace(documents=[late_discussion]))):
            no_inputs = self.tools.execute("read_income_explanation",{**args,"cutoff":"2025-06-30"},self.sid,scope)
            no_source = self.tools.execute("read_income_explanation",{**args,"cutoff":"2025-07-02"},self.sid,scope)
            full = self.tools.execute("read_income_explanation",args,self.sid,scope)
            explicit = self.tools.execute("read_income_explanation",{**args,"cutoff":"2025-09-01"},self.sid,
                                          {"entities":["ABC"]})
        self.assertFalse(no_inputs["ok"])
        self.assertIn("no current, recorded income comparison",no_inputs["error"])
        self.assertFalse(no_source["data"]["income_explanation"]["findings"])
        self.assertEqual(no_source["data"]["income_explanation"]["documents_examined"],0)
        self.assertIn("higher debt",full["data"]["income_explanation"]["findings"][0]["quote"])
        self.assertEqual(explicit,full)
