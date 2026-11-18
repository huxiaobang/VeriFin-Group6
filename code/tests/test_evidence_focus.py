import copy
import json
import unittest

from tests.test_model import completion
from verifin.evidence_focus import focused_evidence
from verifin.model import ModelClient


CASH = "Why did operating cash flow differ from net income?"
ACQUISITION = "What integration risks did management disclose for the acquisition?"
ROLES = ("planner", "researcher", "critic")


def passage(key, sentence):
    body = (sentence + " ") * 24
    return {"id": key, "kind": "document", "document_id": "filing-" + key, "entity": "EX",
            "snapshot_hash": "snapshot-" + key, "text_snapshot_hash": "text-" + key,
            "version": 1, "start": 120, "end": 120 + len(body), "text": body}


def context_for(question=CASH):
    evidence = [
        passage("op-cash", "Operating cash flow fell as higher receivables absorbed working capital."),
        passage("acquisition", "The acquisition creates integration risks from combining distribution networks."),
        passage("leases", "The lease terms provide renewal options for regional warehouses."),
        passage("facilities", "The facility has two assembly lines and a separate inspection area."),
    ]
    return {"episode": {"id": "turn", "request": question, "recent_tool_results": []},
            "scope": {"entities": ["EX"]}, "tools": [{"name": "read_document"}],
            "evidence": evidence, "calculations": [], "claims": [], "issues": [],
            "messages": [], "reports": []}


def full_passages(view):
    return {row["id"]: row["text"] for row in view["evidence"] if "text" in row}


class EvidenceFocusTests(unittest.TestCase):
    def test_broad_or_unknown_question_keeps_all_source_text(self):
        for question in ("Give an overview of this quarter.", "How is the pension plan funded?",
                         "How much cash was paid and what changed in the pension plan?"):
            ctx = context_for(question)
            for role in ROLES:
                with self.subTest(question=question, role=role):
                    self.assertEqual(focused_evidence(ctx, role)["evidence"], ctx["evidence"])

    def test_no_matching_passage_or_no_reader_keeps_all_source_text(self):
        for scenario in ("no_match", "no_reader"):
            ctx = context_for()
            if scenario == "no_match":
                ctx["evidence"] = ctx["evidence"][1:]
            else:
                ctx["tools"] = [{"name": "search_documents"}]
            with self.subTest(scenario=scenario):
                self.assertEqual(focused_evidence(ctx, "planner")["evidence"], ctx["evidence"])

    def test_cash_and_tax_request_keeps_both_topics_or_falls_back(self):
        ctx = context_for("How much tax expense was recognized and how much cash was paid?")
        self.assertEqual(focused_evidence(ctx, "researcher")["evidence"], ctx["evidence"])
        tax = passage("tax", "Income tax expense includes a deferred tax valuation allowance.")
        ctx["evidence"].append(tax)
        view = focused_evidence(ctx, "researcher")
        self.assertIn(tax, view["evidence"])
        self.assertIn(ctx["evidence"][0], view["evidence"])
        self.assertNotIn("leases", full_passages(view))

    def test_table_header_in_neighboring_window_keeps_units_and_period(self):
        ctx = context_for()
        cash = ctx["evidence"][0]
        header = passage("header", "Three months ended June 30 (unaudited), in millions.")
        header.update({key: cash[key] for key in ("document_id", "snapshot_hash", "text_snapshot_hash")})
        header.update(start=cash["end"], end=cash["end"] + len(header["text"]))
        ctx["evidence"].append(header)
        view = focused_evidence(ctx, "critic")
        self.assertIn(header, view["evidence"])
        self.assertNotIn("leases", full_passages(view))

    def test_switch_from_cash_to_acquisition_risks_reads_current_turn(self):
        requests = [(CASH, ACQUISITION), ("本季经营现金流为什么低于净利润？", "改看这次收购，整合过程中有哪些风险？")]
        for first, followup in requests:
            with self.subTest(followup=followup):
                ctx = context_for(first)
                originals = full_passages(ctx)
                previous_context = copy.deepcopy(ctx)
                cash = full_passages(focused_evidence(ctx, "planner"))
                self.assertEqual(cash["op-cash"], originals["op-cash"])
                self.assertNotEqual(cash.get("acquisition"), originals["acquisition"])
                self.assertEqual(ctx, previous_context)

                ctx["messages"] = [{"role": "user", "content": first}, {"role": "user", "content": followup}]
                ctx["reports"] = [{"version": 1, "request": first, "summary": "Operating cash flow declined."}]
                ctx["episode"] = {"id": "new-turn", "request": followup, "recent_tool_results": []}
                acquisition = full_passages(focused_evidence(ctx, "planner"))
                self.assertEqual(acquisition["acquisition"], originals["acquisition"])
                self.assertNotEqual(acquisition.get("op-cash"), originals["op-cash"])

    def test_claims_issues_and_nested_operands_remain_readable_across_roles(self):
        ctx = context_for()
        ctx["evidence"].append(passage("supplier", "Supplier contracts expire each July unless renewed."))
        ctx["calculations"] = [{"id": "combined", "operand_ids": ["intermediate"]},
                               {"id": "intermediate", "operand_ids": ["facilities"]}]
        ctx["claims"] = [{"id": "claim", "evidence_ids": ["leases"], "calculation_ids": ["combined"]}]
        ctx["issues"] = [{"target_id": "claim", "issue_type": "missing_context",
                          "evidence_refs": ["supplier"]}]
        ctx["evidence"].append(passage("note", "These figures exclude sales between subsidiaries."))
        ctx["rule_findings"] = [{"target_id": "claim", "evidence_refs": ["note"]}]
        original = copy.deepcopy(ctx)
        for role in ROLES:
            with self.subTest(role=role):
                view = focused_evidence(ctx, role)
                visible = full_passages(view)
                protected = ["leases", "supplier", "facilities"]
                if role == "critic":
                    protected.append("note")
                for key in protected:
                    self.assertEqual(visible[key], full_passages(original)[key])
                self.assertNotEqual(visible.get("acquisition"), full_passages(original)["acquisition"])
                self.assertEqual(view["calculations"], original["calculations"])
                self.assertEqual(ctx, original)

    def test_explicit_read_reopens_an_off_topic_passage(self):
        ctx = context_for()
        row = ctx["evidence"][2]
        before_read = focused_evidence(ctx, "planner")
        locator = next(item for item in before_read["evidence"] if item["id"] == row["id"])
        self.assertIs(locator["deferred"], True)
        self.assertNotIn("text", locator)
        for field in ("document_id", "entity", "start", "end"):
            self.assertEqual(locator[field], row[field])
        ctx["episode"]["recent_tool_results"] = [
            {"tool": "read_document", "ok": True, "evidence_ids": [row["id"]],
             "arguments": {"entity": locator["entity"], "document_id": locator["document_id"],
                           "offset": locator["start"], "limit_chars": locator["end"] - locator["start"]}},
            {"tool": "search_documents", "ok": True, "evidence_ids": ["op-cash"]},
        ]
        for role in ROLES:
            with self.subTest(role=role):
                self.assertEqual(full_passages(focused_evidence(ctx, role))[row["id"]], row["text"])

    def test_incomplete_locator_and_numeric_fact_survive_projection_unchanged(self):
        ctx = context_for()
        invalid = ctx["evidence"][2]
        invalid["end"] += 10
        fact = {"id": "number", "kind": "financial_fact", "metric": "rental_expense",
                "value": 37, "unit": "USD", "version": 1}
        ctx["evidence"].append(fact)
        selected = focused_evidence(ctx, "researcher")["evidence"]
        self.assertIn(invalid, selected)
        self.assertIn(fact, selected)

    def test_hidden_locator_is_not_a_valid_citation_and_reopening_is_progress(self):
        from verifin.planner_evidence import covered_document_read
        ctx = context_for()
        ctx["requirements"] = [{"id":"req", "text":"Check the cash question."}]
        claims = [{"id":"a-" + ref, "text":"A claim", "evidence_ids":[ref],
                   "calculation_ids":[], "requirement_ids":["req"], "constraints":{}}
                  for ref in ("op-cash", "leases")]
        model = ModelClient("offline", "fixture", "https://example.test/v1",
                            transport=lambda *_: completion({"claims":claims, "answered_requirement_ids":[],
                                "unanswered_requirement_ids":["req"], "summary":"Partial."}))
        draft = model.complete("researcher", ctx)
        self.assertEqual([r["id"] for r in draft["claims"]], ["a-op-cash"])
        self.assertIn("leases", str(draft["reference_quarantine"]))
        read = {"ok":True, "evidence":[ctx["evidence"][2]], "data":{"numeric_operand_status":"text_only"}}
        self.assertFalse(covered_document_read(model.evidence_view("planner",ctx), read))
        ctx["episode"]["recent_tool_results"] = [{"tool":"read_document", "ok":True, "evidence_ids":["leases"]}]
        self.assertTrue(covered_document_read(model.evidence_view("planner",ctx), read))
        ctx["episode"].update(recent_tool_results=[], opened_document_ids=["leases"])
        self.assertTrue(covered_document_read(model.evidence_view("planner",ctx), read))

    def test_model_toggle_controls_the_evidence_in_the_outgoing_request(self):
        outgoing = []

        def transport(request, timeout):
            user_input = json.loads(request.data)["messages"][-1]["content"]
            outgoing.append(json.loads(user_input.split("\n", 1)[1]))
            return completion({"action": "finish"})

        model = ModelClient("test-only", "fixture", "https://example.test/v1", transport=transport)
        ctx = context_for()
        for enabled in (True, False):
            model.focus_evidence = enabled
            model.complete("planner", ctx)
        focused, full = map(full_passages, outgoing)
        self.assertEqual(full, full_passages(ctx))
        self.assertEqual(focused["op-cash"], full["op-cash"])
        self.assertNotEqual(focused.get("leases"), full["leases"])
