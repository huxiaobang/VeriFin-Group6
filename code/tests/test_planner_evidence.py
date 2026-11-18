import copy
import unittest

from verifin.planner_evidence import cash_draft_instruction, covered_document_read, covered_statement_read_gap, margin_draft_check, repeated_statement_gap
from verifin.margin_reconciliation import margin_reconciliation


class PlannerEvidenceTests(unittest.TestCase):
    def test_margin_draft_reuses_only_the_matching_current_calculation(self):
        question = "本季度营业利润率同比变化多少，原因是什么？"
        ctx = {"tools":[{"name":"reconcile_operating_margin"}], "episode":{"request":question},
               "scope":{"entities":["EX"],"period_end":"2024-03-31"}, "evidence":[], "calculations":[]}
        for year in (2023, 2024):
            for metric, value in (("revenue", 100), ("operating_income", 20), ("gross_profit", 40)):
                ctx["evidence"].append({"id":f"{metric}-{year}", "metric":metric, "value":value,
                    "cell_type":"statement_amount_cell", "entity":"EX", "unit":"USD", "currency":"USD",
                    "scale":1, "basis":"GAAP", "business_scope":"consolidated", "snapshot_hash":"source",
                    "document_id":"doc", "table_id":"table", "period_start":f"{year}-01-01",
                    "period_end":f"{year}-03-31"})
        args = margin_draft_check(ctx)["arguments"]
        note = margin_reconciliation(ctx["evidence"], **args)
        ctx["calculations"] = note.pop("calculations")
        ctx["episode"]["recent_tool_results"] = [{"tool":"reconcile_operating_margin","ok":True,
                                                 "data":{"margin_reconciliation":note}}]
        self.assertEqual(margin_draft_check(ctx)["receipt_index"], 0)
        ctx["calculations"][0]["stale"] = True
        self.assertNotIn("receipt_index", margin_draft_check(ctx))
        ctx["episode"]["request"] = "那经营现金流呢？"
        self.assertIsNone(margin_draft_check(ctx))

    def context(self):
        dates = {"period_start": "2024-01-01", "period_end": "2024-03-31"}
        notes = [dict(kind=kind, complete=True, text="source calculation", evidence_ids=["amount"],
                      calculation_ids=["calc"], **dates) for kind in ("cash_bridge", "cash_drivers", "cash_reconciliation")]
        tool = {"tool": "analyze_disclosures", "scope_version": 3, "ok": True, "arguments": {"entity": "EXAMPLE"},
                "evidence_ids": ["amount"], "calculation_ids": ["calc"],
                "data": {"skill_notes": notes, "skill_gaps": []}, "planner_instruction": "Preserve the scope."}
        question = "本季经营现金流与净利润差了多少？"
        return {"episode": {"recent_tool_results": [tool], "research_skills": ["cash-flow-bridge"],
                            "skill_selection_source": "planner", "request": question},
                "scope": {"entities": ["EXAMPLE"], **dates}, "evidence": [], "session": {"scope_version": 3},
                "request_parts": {"one": question, "two": "哪些调节项造成了差额？"},
                "requirements": [{"id": "r", "text": question}]}

    def test_draft_only_after_all_current_parts_and_requirements_have_source_calculations(self):
        context = self.context()
        self.assertIn("Preserve the scope.", cash_draft_instruction(context))
        for change in ("extra_part", "extra_requirement", "old_scope", "wrong_quarter", "missing_ref", "incomplete"):
            with self.subTest(change=change):
                ctx = copy.deepcopy(context)
                receipt = ctx["episode"]["recent_tool_results"][0]
                if change == "extra_part":
                    ctx["request_parts"]["three"] = "明年的指引是什么？"
                if change == "extra_requirement":
                    ctx["requirements"].append({"id": "r2", "text": "Explain the change in store count."})
                if change == "old_scope":
                    ctx["session"]["scope_version"] += 1
                if change == "wrong_quarter":
                    ctx["scope"]["period_start"] = "2023-10-01"
                if change == "missing_ref":
                    receipt["calculation_ids"] = []
                if change == "incomplete":
                    receipt["data"]["skill_gaps"] = ["One required row is missing."]
                self.assertIsNone(cash_draft_instruction(ctx))

    def test_covered_read_measures_source_interval_and_preserves_changed_or_new_records(self):
        ctx = self.context()
        recent = ctx["episode"]["recent_tool_results"]
        recent[0]["data"]["skill_gaps"] = ["Missing depreciation in the quarter."]
        def span(start, end, token):
            return {"id": token, "kind": "document", "entity": "EXAMPLE", "document_id": "doc",
                    "snapshot_hash": "raw", "text_snapshot_hash": "text", "start": start, "end": end,
                    "text": "x" * (end - start)}
        ctx["evidence"] = [span(0, 50, "left"), span(49, 100, "right")]
        recent.append({"tool": "search_documents", "scope_version": 3, "ok": True, "evidence_ids": ["left", "right"]})
        returned = {"ok": True, "data": {"numeric_operand_status": "text_only"}, "evidence": [span(24, 76, "slice")]}
        self.assertIn("Missing depreciation", covered_statement_read_gap(ctx, returned))
        for row in (span(24, 101, "extension"), dict(span(24, 76, "revised"), snapshot_hash="new"),
                    dict(span(24, 76, "foreign"), document_id="other")):
            self.assertIsNone(covered_statement_read_gap(ctx, {**returned, "evidence": [row]}))
        for status in ("attached", "extraction_unavailable"):
            self.assertIsNone(covered_statement_read_gap(ctx, {**returned, "data": {"numeric_operand_status": status}}))
        ctx["evidence"][0]["stale"] = True
        self.assertIsNone(covered_statement_read_gap(ctx, returned))

    def test_cross_turn_read_preserves_new_or_revised_cells_and_unseen_spans(self):
        source = dict(entity="EX", document_id="doc", snapshot_hash="html", text_snapshot_hash="text")
        def span(key, start, end):
            return dict(source, id=key, kind="document", start=start, end=end, text="x" * (end-start))
        cell = dict(source, id="cell", kind="financial_fact", cell_type="statement_amount_cell",
                    value=2, locator="/table/tr/td[2]")
        ctx = {"evidence": [span("left", 0, 20), span("right", 20, 40), cell]}
        read = {"ok": True, "data": {"numeric_operand_status": "attached", "numeric_operand_ids": ["cell"]},
                "evidence": [span("window", 10, 30), dict(cell)]}
        self.assertTrue(covered_document_read(ctx, read))
        for changes in ({"value": 3}, {"locator": "/table/tr/td[3]"}, {"id": "new"}):
            self.assertFalse(covered_document_read(ctx, {**read, "evidence": [read["evidence"][0], dict(cell, **changes)]}))
        for changes in ({"start": 21, "text": "x"*19}, {"snapshot_hash": "revised"}, {"entity": "OTHER"}, {"stale": True}):
            changed = {"evidence": [ctx["evidence"][0], dict(ctx["evidence"][1], **changes), cell]}
            self.assertFalse(covered_document_read(changed, read))
        self.assertFalse(covered_document_read({"evidence": ctx["evidence"][:2]}, read))

    def test_repeated_inspection_requires_the_same_open_gap_and_unchanged_records(self):
        context = self.context()
        prior = context["episode"]["recent_tool_results"][0]
        prior["data"].update(skill_gaps=["cash classification unavailable"], skill_assessments=[{"skill":"example"}])
        evidence = {"id":"amount","value":14,"source_url":"filing"}
        calc = {"id":"calc","value":3}
        context.update(evidence=[evidence], calculations=[calc])
        returned = {"ok":True, "evidence":[dict(evidence)], "calculations":[dict(calc)], "data":copy.deepcopy(prior["data"])}
        self.assertTrue(repeated_statement_gap(context, prior["arguments"], returned))
        updated = copy.deepcopy(returned)
        updated["evidence"][0]["value"] = 15
        self.assertIsNone(repeated_statement_gap(context, prior["arguments"], updated))
        updated = copy.deepcopy(returned)
        updated["calculations"].append({"id":"additional","value":1})
        self.assertIsNone(repeated_statement_gap(context, prior["arguments"], updated))
        self.assertIsNone(repeated_statement_gap(context, {"entity":"OTHER"}, returned))

    def test_extra_selected_method_must_be_assessed_before_cash_only_handoff(self):
        context = self.context()
        context["episode"]["research_skills"].append("reconcile-segment-totals")
        self.assertIsNone(cash_draft_instruction(context))
        data = context["episode"]["recent_tool_results"][-1]["data"]
        data["skill_assessments"] = [{"skill":"reconcile-segment-totals", "status":"not_applicable"}]
        self.assertTrue(cash_draft_instruction(context))
        context["request_parts"]["also"] = "各分部收入加起来为什么对不上集团收入？"
        self.assertIsNone(cash_draft_instruction(context))
