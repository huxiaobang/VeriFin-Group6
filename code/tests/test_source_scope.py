import unittest

from verifin.source_scope import bound_context, bound_decision, source_constraint, source_limited


class SourceLimitTests(unittest.TestCase):
    def test_explicit_document_restrictions(self):
        for text in ("只看这份新闻稿。", "请仅根据这篇公告回答。", "According to this release, what increased?",
                     "USE ONLY THIS PRESS RELEASE.", "Use the release only.", "请不要去查其他来源，只用这份公告"):
            with self.subTest(text=text):
                self.assertTrue(source_limited(text))

    def test_citation_period_and_negated_restrictions(self):
        for text in ("Cite the relevant passage and distinguish explanation from inference.",
                     "What reasons did the company give? Use current-quarter figures only.",
                     "Use the release and consult other sources if needed.",
                     "Do not limit yourself to this release.",
                     "Don't only use this release.", "不要只看这份新闻稿。", "不局限于这份公告。",
                     "只看这个季度，给我出处。", "包括但不限于这份公告。"):
            with self.subTest(text=text):
                self.assertFalse(source_limited(text))

    def test_publisher_constraints_distinguish_exclusivity_and_exclusion(self):
        for text, publisher in (
            ("請僅使用 NVIDIA 投資人關係網站的業績公告。","issuer"),
            ("只用英偉達官網的業績公告，不要查 SEC。","issuer"),
            ("僅限公司官方網站。","issuer"),
            ("將公司投資人關係網站作為唯一來源。","issuer"),
            ("Use only the issuer's investor relations site.","issuer"),
            ("Use the issuer's investor relations site alone.","issuer"),
            ("不要查 SEC。","non_sec"),
            ("請不要查閱 SEC。","non_sec"),
            ("Don't consult EDGAR for this answer.","non_sec"),
            ("只用 SEC 官方數據。","sec"),
            ("Use only SEC filings.","sec"),
            ("不要只查 SEC，也可以看公司官網。",None),
            ("請使用 SEC 官方數據。",None),
            ("找可核實的來源。",None),
        ):
            with self.subTest(text=text):
                policy = source_constraint(text)
                self.assertEqual(policy.publisher,publisher)
                self.assertEqual(policy.permits_auto_sec,publisher in (None,"sec"))
        self.assertTrue(source_constraint("請僅使用這份業績公告。").reference)

    @staticmethod
    def context(cited=("a",)):
        return {"episode": {"request": "只看这份新闻稿。"}, "scope": {"entities": ["XYZ"]},
                "reports": [{"claims": [{"evidence_ids": list(cited)}]}],
                "evidence": [{"id": "a", "document_id": "release"}, {"id": "b", "document_id": "other"}],
                "tools": [{"name": "search_documents"}, {"name": "financial_facts"}],
                "calculations": [{"id": "local", "operand_ids": ["a", "a"]},
                                 {"id": "mixed", "operand_ids": ["a", "b"]}]}

    def test_open_search_pinned_and_foreign_offset_never_used(self):
        ctx = bound_context(self.context())
        self.assertEqual(["a"], [r["id"] for r in ctx["evidence"]])
        self.assertEqual(["local"], [r["id"] for r in ctx["calculations"]])
        self.assertEqual(["search_documents"], [s["name"] for s in ctx["tools"]])
        wide = {"action": "tool", "tool": "search_documents", "arguments": {"query": "demand", "forms": ["10-Q"]}}
        out = bound_decision(ctx, wide)
        self.assertEqual(out["arguments"], {**wide["arguments"], "document_ids": ["release"]})
        self.assertIs(bound_decision(ctx, out), out)
        read = {"action": "tool", "tool": "read_document", "arguments": {"document_id": "other", "offset": 200}}
        rerouted = bound_decision(ctx, read)
        self.assertEqual(rerouted["tool"], "search_documents")
        self.assertEqual(rerouted["arguments"], {"query": "只看这份新闻稿。", "document_ids": ["release"]})

    def test_clarify_ambiguous_reference_before_retrieval_or_draft(self):
        for cited in ((), ("a", "b")):
            ctx = bound_context(self.context(cited))
            self.assertEqual([], ctx["evidence"])
            for action in ("research", "tool", "finish"):
                self.assertEqual(bound_decision(ctx, {"action": action})["action"], "clarify")

    def test_other_turn_does_not_inherit_ephemeral_filter(self):
        plain = self.context()
        plain["episode"]["request"] = "What explanations did the company give? Cite the passage."
        self.assertIs(bound_context(plain), plain)

    def test_unique_prior_quarterly_filing_is_not_the_requested_press_release(self):
        ctx = self.context()
        ctx["evidence"][0]["form"] = "10-Q"
        locked = bound_context(ctx)
        self.assertEqual([], locked["source_document_scope"]["document_ids"])
        self.assertEqual(bound_decision(locked, {"action":"research"})["action"], "clarify")

    def test_clarification_with_exact_source_url_can_resolve_prior_ambiguity(self):
        ctx = self.context(("a", "b"))
        ctx["evidence"][1]["source_url"] = "https://example.com/press-release"
        ctx["episode"]["request"] += " 链接是 https://example.com/press-release。"
        self.assertEqual(bound_context(ctx)["source_document_scope"]["document_ids"], ["other"])

    def test_historical_answers_and_mixed_tool_receipts_are_not_citable_material(self):
        ctx = self.context()
        ctx["reports"].insert(0, {"request": "Older question", "claims": [{"text": "foreign answer", "evidence_ids": ["b"]}]})
        ctx["episode"]["recent_tool_results"] = [{"tool": "search_documents", "evidence_ids": ["a", "b"],
                                                "data": {"excerpt": "foreign answer"}}]
        locked = bound_context(ctx)
        self.assertNotIn("foreign answer", str(locked))
        self.assertEqual(locked["episode"]["recent_tool_results"][0]["record_status"], "superseded_or_unavailable")
        self.assertIn("foreign answer", str(ctx["reports"]))
