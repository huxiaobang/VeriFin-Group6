"""A short source request points to the last checked answer's actual disclosure."""
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from tests.test_margin_formula import FilingModel, QUESTION, SOURCE_URL
from tests.test_tools import DATA
from verifin.engine import Budget, ResearchEngine
from verifin.evaluation import FrozenCase
from verifin.reporting import render_markdown, report_view
from verifin.source_link_followup import requested
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


FOLLOWUP = "能给个链接吗？"
OTHER_QUARTER = SOURCE_URL.replace("FY-2025-Q2", "FY-2025-Q1")
OTHER_PAGE = SOURCE_URL.replace("press-release-webcast", "performance")


@skipUnless(find_spec("lxml"), "archived Microsoft statement operands require lxml")
class SourceLinkFollowupTests(TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.store = self.enterContext(SessionStore(":memory:"))
        self.sid = self.store.create_session()["id"]
        self.tools = ToolRegistry(self.store, DATA, cache_dir=Path(folder) / "cache")
        self.network = self.enterContext(patch("verifin.data.build_opener", side_effect=AssertionError("offline")))
        self.first = ResearchEngine(self.store, FilingModel("correct"), self.tools).ask(
            self.sid, QUESTION, as_of="2025-02-01", budget=Budget(9, 5, 40))["report"]
        self.assertEqual(self.first["status"], "complete", self.first["issues"])

    def ask_source(self, question=FOLLOWUP, *, configured=False, as_of=None):
        class UnexpectedModel:
            def complete(self, role, context):
                raise AssertionError("An archived source does not need a new model review")

        report = ResearchEngine(self.store, UnexpectedModel() if configured else None, self.tools).ask(
            self.sid, question, as_of=as_of, budget=Budget(6, 5, 40))["report"]
        self.assertEqual(self.store.get_report(self.sid, 1), self.first)
        self.network.assert_not_called()
        self.assertEqual(tuple(report["usage"][k] for k in ("model_calls", "tool_calls", "planner_steps")), (0, 0, 0))
        answer, = report_view(report)["question_answers"]
        return report, answer

    def test_configured_and_offline_followups_keep_the_original_publisher_file(self):
        # The second and third asks resolve the pointer from source reports back
        # to the actual reviewed turn, including when the same wording repeats.
        for question, configured in ((FOLLOWUP, False), (FOLLOWUP, True), ("原文在哪？", False)):
            with self.subTest(question=question, configured=configured):
                report, answer = self.ask_source(question, configured=configured)
                self.assertFalse(report["verification_available"])
                self.assertEqual(answer["status"], "source_excerpt")
                self.assertEqual(answer["answer_sentence"], SOURCE_URL)
                self.assertEqual(answer["archived_source_version"], 1)
                self.assertEqual(set(answer["archived_source_claim_ids"]), {"claim-revenue", "claim-margin"})
                self.assertIn(f"<{SOURCE_URL}>", render_markdown(report))
                self.assertNotIn(OTHER_QUARTER, str(answer))

    def test_an_earlier_cutoff_hides_the_previous_original(self):
        report, answer = self.ask_source(configured=True, as_of="2024-12-31")
        self.assertEqual(answer["status"], "gap")
        self.assertNotIn("archived_source", report)
        self.assertNotIn(SOURCE_URL, render_markdown(report))

    def test_changed_evidence_cannot_be_presented_as_the_checked_original(self):
        calc, = self.first["calculations"]
        evidence = next(r for r in self.first["evidence"] if r["id"] == calc["operand_ids"][0])
        self.store.put_evidence(self.sid, {**evidence, "value": evidence["value"] + 1})
        report, answer = self.ask_source(configured=True)
        self.assertEqual(answer["status"], "gap")
        self.assertNotIn("archived_source", report)
        self.assertNotIn("answer_sentence", answer)

    def test_repeated_lookup_keeps_only_original_claim_versions_that_remain_current(self):
        first, _ = self.ask_source()
        self.assertEqual(set(first["archived_source"]["claim_ids"]), {"claim-revenue", "claim-margin"})

        def replace_claim(ref):
            ctx = self.store.get_context(self.sid, report_mode="none")
            claim = next(c for c in ctx["claims"] if c["id"] == ref)
            self.assertEqual(claim.get("invalidation_reasons"), ["scope_changed"])
            draft = {**claim, "text": "A replacement draft is waiting for its own review.",
                     "status": "Pending Verification", "stale": False, "scope_version": ctx["session"]["scope_version"]}
            draft.pop("critic_verdict", None)
            saved = self.store.put_claim(self.sid, draft)
            self.assertGreater(saved["version"], claim["version"])

        replace_claim("claim-margin")
        second, answer = self.ask_source(configured=True)
        self.assertEqual(answer["answer_sentence"], SOURCE_URL)
        self.assertEqual(second["archived_source"]["claim_ids"], ["claim-revenue"])
        replace_claim("claim-revenue")
        third, answer = self.ask_source(configured=True)
        self.assertEqual(answer["status"], "gap")
        self.assertNotIn("answer_sentence", answer)
        self.assertNotIn("archived_source", third)
        self.assertEqual(self.store.get_report(self.sid, 2), first)

    def test_record_versions_are_rechecked_when_saving_the_lookup(self):
        calc, = self.first["calculations"]
        evidence = next(r for r in self.first["evidence"] if r["id"] == calc["operand_ids"][0])
        build_report = ResearchEngine._report

        def replace_source(engine, sid, episode):
            self.assertIn("archived_source", episode)
            self.store.put_evidence(sid, {**evidence, "value": evidence["value"] + 1})
            return build_report(engine, sid, episode)

        with patch.object(ResearchEngine, "_report", replace_source):
            report, answer = self.ask_source(configured=True)
        self.assertEqual(answer["status"], "gap")
        self.assertNotIn("archived_source", report)
        self.assertNotIn("answer_sentence", answer)


class SourceLinkRuleTests(TestCase):
    def test_only_a_reference_to_the_previous_source_uses_the_archive(self):
        for question in (FOLLOWUP, "只給原文連結", "Can you give me the link?", "原文在哪？"):
            self.assertTrue(requested(question), question)
        for question in ("能给个 NVIDIA 的链接吗？", "给个 FY2025 Q1 的链接", "能给个链接吗？请只用 SEC EDGAR。"):
            self.assertFalse(requested(question), question)

    def test_a_link_only_draft_cannot_swap_issuer_documents_in_a_frozen_case(self):
        evidence = {"id": "disclosure", "kind": "document", "entity": "MSFT", "source_url": SOURCE_URL}
        for url in (SOURCE_URL, OTHER_PAGE, OTHER_QUARTER):
            context = {"request": FOLLOWUP, "evidence": [evidence], "claims": [
                {"id": "source", "text": url, "requirement_ids": [], "evidence_ids": [evidence["id"]],
                 "calculation_ids": [], "constraints": {}}]}
            live = check_rules(context)
            self.assertEqual(check_rules(FrozenCase.from_context("link-followup", context).context()), live)
            self.assertEqual(any("displayed link" in issue["missing_support"] for issue in live), url != SOURCE_URL)
