import unittest

from verifin.reporting import question_answers, render_markdown, report_view


class AnswerDeliveryTests(unittest.TestCase):
    def test_rejected_year_comparison_keeps_current_amount_and_marks_partial(self):
        report = self.report()
        question = "How much liquidity was available compared to last year?"
        report["request_parts"] = {"liquidity":question}
        report["scope"]["requirements"] = [{"id":"r2", "text":question}]
        current, comparison = report["claims"][2:]
        comparison.update(text="Liquidity grew 10%.", status="Insufficient Evidence", stale=False,
                          requirement_ids=["r2"], constraints={"comparison_period_end":"2024-06-30"})
        report["request_coverage"] = [{"part":"liquidity", "status":"answered", "claim_id":current["id"],
            "answer_sentence":current["text"], "supporting_claim_ids":[current["id"], comparison["id"]]}]
        answer, = question_answers(report)
        self.assertEqual(answer["status"], "partial")
        self.assertEqual(answer["answer_sentence"], current["text"])
        self.assertIn("比较期", answer["missing_support"])
        self.assertNotIn(comparison["id"], answer["supporting_claim_ids"])

    def report(self):
        def claim(cid, text, req, status="Supported", **verdict):
            return {"id":cid, "text":text, "requirement_ids":[req], "evidence_ids":["source-"+cid],
                    "status":status, "critic_verdict":{"claim_id":cid, "status":status,
                    "basis":"Reviewed against the filing.", "evidence_refs":["source-"+cid], **verdict}}
        parts = {"why":"What drove the change?", "liquidity":"How much liquidity was available?"}
        claims = [claim("cost","Wages increased.","r1"),
                  claim("fuel","Fuel costs fell. A new supplier caused the decline.","r1","Partially Supported",
                        supported_text="Fuel costs fell."),
                  claim("cash","Available liquidity was USD 50 million.","r2"),
                  claim("prior","Earlier margins were unusually high.","r1")]
        claims[-1]["stale"] = True
        return {"verification_available":True, "status":"partial","model_mode":"recorded_provider_replay",
                "request_parts":parts,"scope":{"requirements":[{"id":r,"text":t} for r,t in zip(("r1","r2"),parts.values())]},
                "claims":claims,"evidence":[{"id":"source-"+c["id"]} for c in claims],
                "request_coverage":[{"part":"why","claim_id":"cost","status":"partial","answer_sentence":"Wages increased.",
                                     "missing_support":"The filing did not quantify the remaining drivers.",
                                     "supporting_claim_ids":["fuel","cash","prior","made-up","fuel"]},
                                    {"part":"liquidity","status":"answered","claim_id":"cash",
                                     "answer_sentence":claims[2]["text"]}]}

    def test_explicit_links_join_only_supported_text_within_this_question(self):
        report = self.report()
        first, second = question_answers(report)
        self.assertEqual(first["answer_text"], "Wages increased. Fuel costs fell.")
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["missing_support"], "The filing did not quantify the remaining drivers.")
        self.assertEqual([p["claim_ids"] for p in first["answer_fragments"]],[["cost"],["fuel"]])
        self.assertNotIn("answer_text",second)
        self.assertEqual(report_view(report_view(report))["question_answers"],[first,second])
        text = render_markdown(report).split("## Answers to your questions")[1].split("\n## ")[0]
        self.assertIn(first["answer_text"]+" "+first["missing_support"],text)
        self.assertNotIn("new supplier",text)

    def test_superseded_support_does_not_enter_a_current_answer(self):
        report = self.report()
        report["claims"][1]["evidence_versions"] = {"source-fuel":1}
        report["evidence"][1]["version"] = 2
        first = question_answers(report)[0]
        self.assertNotIn("answer_text",first)
        self.assertEqual(first["status"],"partial")

    def test_shared_reviewed_comparison_is_complete_in_each_answer_and_printed_once(self):
        report = self.report()
        report["request_parts"] = {"change":"Which factor contributed more?", "detail":"What can the filing break out?"}
        report["scope"]["requirements"] = [{"id":"joint","text":"Compare the factors and identify the disclosure limits."}]
        primary, partial, other, stale = report["claims"]
        for c in report["claims"]:
            c["requirement_ids"] = ["joint"]
        primary["text"] = "Wages contributed more. Wages added USD 9m."
        other["text"] = "The filing gives a cost bridge. No unit-level data were provided."
        report["request_coverage"] = [
            {"part":"change","claim_id":"cost","status":"answered","answer_sentence":"Wages contributed more.",
             "supporting_claim_ids":["cost","fuel","prior"]},
            {"part":"detail","claim_id":"cash","status":"answered","answer_sentence":"The filing gives a cost bridge.",
             "supporting_claim_ids":["cost","fuel","cash"]}]
        view = report_view(report)
        a,b = view["question_answers"]
        self.assertIn("USD 9m",a["answer_text"])
        self.assertIn("USD 9m",b["answer_text"])
        self.assertIn("No unit-level data", b["answer_text"])
        sections = view["answer_sections"]
        self.assertIn("USD 9m",sections[0]["answer_text"])
        self.assertNotIn("USD 9m",sections[1]["answer_text"])
        # Keep the qualifying, partially approved evidence available in both parts.
        for row in [a,b,*sections]:
            self.assertIn("Fuel costs fell.",row["answer_text"])
            self.assertNotIn("new supplier",row["answer_text"])
            self.assertNotIn("Earlier margins",row["answer_text"])
        partial["status"] = "Unsupported"
        partial["critic_verdict"]["status"] = "Unsupported"
        self.assertNotIn("Fuel costs fell.",report_view(report)["question_answers"][0]["answer_text"])

    def test_two_explicit_primary_answers_both_keep_the_reviewed_text(self):
        report = self.report()
        report["request_parts"] = {"one":"Which cost changed?", "two":"How much did wages add?"}
        report["scope"]["requirements"] = [{"id":"r1","text":"Identify the changed cost and its amount."}]
        report["claims"][0]["text"] = "Wages increased. Wages added USD 9m."
        report["request_coverage"] = [
            {"part":part,"status":"answered","claim_id":"cost","answer_sentence":sentence,"supporting_claim_ids":["cost"]}
            for part,sentence in (("one","Wages increased."),("two","Wages added USD 9m."))]
        for row in report_view(report)["answer_sections"]:
            self.assertIn("Wages added USD 9m.",row["answer_text"])

    def test_unverified_or_ambiguous_mapping_cannot_supply_supplementary_text(self):
        report = self.report()
        report["verification_available"] = False
        self.assertFalse(any(r.get("answer_fragments") for r in question_answers(report)))
        report["verification_available"] = True
        report["request_coverage"].append(dict(report["request_coverage"][0]))
        first = question_answers(report)[0]
        self.assertEqual(first["status"],"gap")
        self.assertNotIn("Wages increased",first.get("answer_text",""))
