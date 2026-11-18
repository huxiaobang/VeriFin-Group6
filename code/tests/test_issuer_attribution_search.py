import unittest

from verifin import issuer_attribution as ia


class AttributionSearchTests(unittest.TestCase):
    def setUp(self):
        self.question = "What reasons did management give for the decline in quarterly net income?"
        self.args = {"entity":"EXAMPLE", "query":"net income decline", "forms":["10-Q"], "document_ids":["release"]}
        self.receipt = {"tool":"search_documents", "arguments":self.args, "scope_version":1,
                        "evidence_ids":["a"], "search_scope":{"entities":["EXAMPLE"]},
                        "data":{"filters":{"entity":"EXAMPLE","period_end":"2025-03-31"},
                                "same_period_search":{"period_end":"2025-03-31"}}}
        self.ctx = {"scope":{"entities":["EXAMPLE"]}, "session":{"scope_version":1},
                    "episode":{"request":self.question, "recent_tool_results":[self.receipt]},
                    "evidence":[{"kind":"document", "id":"a","document_id":"release",
                                 "entity":"EXAMPLE","document_report_date":"2025-03-31",
                                 "text":"Revenue increased due to volume. Net income declined by 14%."}]}
        self.research = {"action":"research","reason":"Draft now."}

    def search(self, decision):
        self.ctx["issuer_attribution"] = ia.evidence_context(self.ctx)
        return ia.next_search(self.ctx,decision)

    def test_redraft_or_repeated_filtered_search(self):
        expected = {"query":"net income decline","entity":"EXAMPLE","period_end":"2025-03-31","forms":["10-Q"]}
        self.assertEqual(self.search(self.research), expected)
        self.ctx["session"]["scope_version"] += 1
        self.ctx["scope"]["requirements"] = [{"id":"r","text":"Explain."}]
        self.assertEqual(self.search(self.research), expected)
        step = {"action":"tool","tool":"search_documents","arguments":{**self.args,"query":"margin reasons"}}
        self.assertEqual(self.search(step), {**expected,"query":"margin reasons"})
        self.ctx["episode"]["attribution_search_expanded"] = True
        self.assertIsNone(self.search(step))

    def test_preserve_explicit_scope_and_stop_on_issuer_reason(self):
        self.ctx["episode"]["request"] += " 只看这份新闻稿。"
        self.assertIsNone(self.search(self.research))
        self.ctx["episode"]["request"] = self.question
        self.ctx["evidence"][0]["text"] = "Net income fell 14% due to increased lease costs."
        self.assertIsNone(self.search(self.research))
        self.assertEqual(self.ctx["issuer_attribution"]["candidates"],
                         [{"evidence_id":"a", "quote":"Net income fell 14% due to increased lease costs."}])
        self.ctx["evidence"][0]["text"] = "Net income may decline due to expected closures."
        self.assertIsNotNone(self.search(self.research))
        self.ctx["episode"]["request"] = "What drove the Q4 decline in net income?"
        self.ctx["evidence"][0]["text"] = "Net income declined in the first quarter due to a factory shutdown."
        self.assertIsNotNone(self.search(self.research))

    def test_changed_source_basis_and_unrelated_question_do_not_expand(self):
        self.ctx["session"]["scope_version"] += 1
        self.ctx["scope"]["period_end"] = "2024-03-31"
        self.assertIsNone(self.search(self.research))
        self.ctx["scope"].pop("period_end")
        self.ctx["episode"]["request"] = "What was quarterly net income?"
        self.assertIsNone(self.search(self.research))
