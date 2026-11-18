"""A reviewed limit may close an internal checklist item, never a user obligation."""
import copy
import unittest

from verifin.request_review import reviewed_followup_limits


class BoundedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.question = "Can the whole charge be attributed to the acquisition?"
        self.answer = "The issuer attributes only part of this charge to the acquisition."
        self.ctx = {"request_parts": {"part-1": self.question},
                    "requirements": [{"id":"detail", "text":"Trace the exact allocation to counterparties."}],
                    "claims": [{"id":"answer", "text":self.answer, "evidence_ids":["doc"],
                                "requirement_ids":["detail"], "constraints":{"missing_evidence":["allocation by counterparty"]}}]}
        self.episode = {"unanswered_requirement_ids":["detail"], "planner_added_requirements":{
            "detail":"Trace the exact allocation to counterparties."}}
        self.note = {"target_id":"detail", "issue_type":"omission", "evidence_refs":["doc"]}
        self.review = {"issues":[self.note], "claim_verdicts":[{"claim_id":"answer","status":"Supported"}]}
        self.rows = [{"part":"part-1","status":"answered","claim_id":"answer","answer_sentence":self.answer}]

    def limits(self):
        return reviewed_followup_limits(self.ctx, self.episode, self.review, self.rows)

    def test_report_limit_with_and_without_an_additional_checklist_observation(self):
        expected = [{"claim_id":"answer","requirement_ids":["detail"]}]
        self.assertEqual(self.limits(), expected)
        self.review["issues"] = []
        self.assertEqual(self.limits(), expected)

    def test_material_objection_or_an_explicit_scope_item_still_blocks(self):
        for changes in ({"issue_type":"source_time_mismatch"}, {"affected_request_parts":["part-1"]},
                        {"target_id":"answer"}, {"evidence_refs":[]}):
            with self.subTest(changes=changes):
                self.review["issues"] = [{**self.note, **changes}]
                self.assertEqual(self.limits(), [])
        self.review["issues"] = [self.note]
        self.episode["planner_added_requirements"] = {}
        self.assertEqual(self.limits(), [])

    def test_an_answer_to_the_old_question_does_not_release_the_requested_detail(self):
        self.ctx["request_parts"]["part-1"] = self.ctx["requirements"][0]["text"]
        self.assertEqual(self.limits(), [])

    def test_unreviewed_background_or_mismapped_answer_cannot_close_turn(self):
        self.rows[0]["answer_sentence"] = "Another sentence."
        self.assertEqual(self.limits(), [])
        self.rows[0]["answer_sentence"] = self.answer
        self.ctx["claims"].append(copy.deepcopy(self.ctx["claims"][0]))
        self.ctx["claims"][1].update(id="background", text="More details were unavailable.")
        self.ctx["claims"][0]["constraints"] = {}
        self.review["claim_verdicts"].append({"claim_id":"background", "status":"Supported"})
        self.assertEqual(self.limits(), [])
