import copy
from pathlib import Path
import tempfile
import unittest

from verifin.model import ModelError
from verifin.verification import Verifier, check_rules


def fixture():
    return {
        "session": {"scope_version": 1},
        "scope": {"cutoff": "2025-02-01", "entities": ["MSFT"]},
        "requirements": [{"id": "r1", "text": "State quarterly revenue"}],
        "evidence": [{"id": "e1", "kind": "financial_fact", "entity": "MSFT", "metric": "revenue",
                      "period_start": "2024-10-01", "period_end": "2024-12-31", "unit": "USD", "value": 100,
                      "published_at": "2025-01-29", "source_url": "https://example.test/report",
                      "locator": "income statement, revenue", "snapshot_hash": "abc", "version": 1}],
        "calculations": [],
        "claims": [{"id": "c1", "text": "Quarterly revenue was USD 100.", "requirement_ids": ["r1"],
                    "evidence_ids": ["e1"], "calculation_ids": [], "scope_version": 1,
                    "constraints": {"entity": "MSFT", "metric": "revenue", "period_start": "2024-10-01",
                                    "period_end": "2024-12-31", "unit": "USD"},
                    "asserted_value": 100, "asserted_unit": "USD"}],
    }


def categories(context):
    return {issue["issue_type"] for issue in check_rules(context)}


class RuleTests(unittest.TestCase):
    def test_parenthesized_joint_amounts_bind_to_each_named_metric_and_scope(self):
        ctx = fixture()
        revenue = ctx["evidence"][0]
        revenue.update(entity="XYZ", value=90_000_000, accession="filing-a", form="10-Q")
        profit = {**revenue, "id":"e2", "metric":"operating_income", "value":20_000_000}
        ctx["evidence"].append(profit)
        text = "The revenue ($90 million) and operating-income ($20 million) facts share one SEC accession."
        claim = ctx["claims"][0]
        claim.pop("asserted_value")
        claim.pop("asserted_unit")
        claim.update(text=text, evidence_ids=["e1", "e2"])
        claim["constraints"].update(entity="XYZ", metric="revenue_and_operating_income", accession="filing-a",
                                    form="10-Q")
        self.assertEqual(check_rules(ctx), [])
        for wrong in (text.replace("$90", "$91"), text.replace("$90", "$20").replace("($20 million) facts",
                      "($90 million) facts"), text.replace("$90", "-$90")):
            with self.subTest(wrong=wrong):
                claim["text"] = wrong
                self.assertIn("numerical_inconsistency", categories(ctx))
        claim["text"] = text
        for field, wrong in (("period_end", "2024-09-30"), ("accession","another-filing"), ("form","10-K")):
            with self.subTest(field=field):
                original = profit[field]
                profit[field] = wrong
                self.assertIn("context_mismatch", categories(ctx))
                profit[field] = original
        claim["constraints"]["period_end"] = "2024-09-30"
        self.assertIn("context_mismatch", categories(ctx))

    def test_exact_assertion_in_saved_constraints_is_checked_including_conflicts(self):
        ctx = fixture()
        claim = ctx["claims"][0]
        for key in ("asserted_value", "asserted_unit"):
            claim["constraints"][key] = claim.pop(key)
        self.assertFalse(check_rules(ctx))
        # The printed 100 still rounds correctly. The exact field must match too.
        claim["constraints"]["asserted_value"] = 100.1
        self.assertIn("numerical_inconsistency", categories(ctx))
        claim["asserted_value"] = 100
        self.assertTrue(any("conflicting exact" in i["missing_support"] for i in check_rules(ctx)))

    def test_display_rounding_does_not_allow_dollar_drift_in_an_exact_field_or_sum(self):
        ctx = fixture()
        sales = 69_632_000_000
        ctx["evidence"][0]["value"] = sales
        claim = ctx["claims"][0]
        claim.update(text="Quarterly revenue was USD 69.632 billion.", asserted_value=sales + 30)
        self.assertIn("numerical_inconsistency",categories(ctx))
        claim["asserted_value"] = sales
        self.assertEqual(check_rules(ctx),[])
        ctx["evidence"].append({**ctx["evidence"][0],"id":"other","value":40})
        ctx["calculations"] = [{"id":"sum","operation":"sum","operand_ids":["e1","other"],
                                "value":sales+70,"unit":"USD","entity":"MSFT"}]
        claim.update(evidence_ids=[],calculation_ids=["sum"],constraints={}, asserted_value=sales+70)
        self.assertIn("numerical_inconsistency",categories(ctx))
        ctx["calculations"][0]["value"] = claim["asserted_value"] = sales + 40
        self.assertEqual(check_rules(ctx),[])

    def test_recomputes_all_terms_and_checks_units_for_statement_aggregates(self):
        for operation, values, expected in (("sum", [120, -39, 4], 85), ("midpoint", [8, 11], 9.5)):
            with self.subTest(operation=operation):
                ctx = fixture()
                base = ctx["evidence"][0]
                ctx["evidence"] = [{**base, "id": f"input{i}", "value": v} for i, v in enumerate(values)]
                ids = [r["id"] for r in ctx["evidence"]]
                calc = {"id":"aggregate", "operation":operation, "operand_ids":ids, "value":expected,
                        "entity":base["entity"], "metric":base["metric"], "unit":base["unit"]}
                ctx["calculations"] = [calc]
                ctx["claims"][0].update(evidence_ids=[], calculation_ids=["aggregate"], constraints={},
                                        asserted_value=expected, text=f"The aggregate is USD {expected}.")
                self.assertEqual(categories(ctx), set())
                calc["value"] = expected + 1
                self.assertIn("numerical_inconsistency", categories(ctx))
                calc["value"] = expected
                ctx["evidence"][-1]["unit"] = "shares"
                self.assertIn("context_mismatch", categories(ctx))
                if operation == "midpoint":
                    calc["operand_ids"] = ids[:1]
                    self.assertIn("invalid_record", categories(ctx))

    def quarter_context(self):
        context = fixture()
        current = context["evidence"][0]
        current.update(period_start="2024-01-01", period_end="2024-06-30", concept="us-gaap:Revenues",
                       basis="US-GAAP", business_scope="consolidated")
        previous = {**current, "id": "e0", "period_end": "2024-03-31", "value": 60}
        context["evidence"].append(previous)
        calculation = {"id": "q2", "operation": "derive_quarter", "operand_ids": ["e1", "e0"],
                       "operand_versions": {"e1": 1, "e0": 1}, "value": 40, "formula": "100-60", "version": 1,
                       "entity": "MSFT", "metric": "revenue", "unit": "USD", "basis": "US-GAAP",
                       "business_scope": "consolidated", "period_start": "2024-04-01", "period_end": "2024-06-30"}
        context["calculations"] = [calculation]
        context["claims"][0].update(text="Second-quarter revenue was USD 40.", evidence_ids=[], calculation_ids=["q2"],
                                    asserted_value=40, constraints={k: calculation[k] for k in ("entity", "metric", "unit", "period_start", "period_end")})
        return context

    def test_quarter_derivation_uses_shared_additive_flow_boundaries(self):
        context = self.quarter_context()
        self.assertEqual(check_rules(context), [])
        for concept in ("us-gaap:EarningsPerShareBasic", "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic", "custom:UnknownFlow"):
            invalid = copy.deepcopy(context)
            for evidence in invalid["evidence"]:
                evidence["concept"] = concept
            with self.subTest(concept=concept):
                self.assertIn("context_mismatch", categories(invalid))

    def test_imported_quarter_calculation_checks_period_and_business_scope(self):
        for change in ("gap", "different_business_scope", "wrong_result_period"):
            context = self.quarter_context()
            if change == "gap":
                context["evidence"][1]["period_end"] = "2024-02-28"
            elif change == "different_business_scope":
                context["evidence"][1]["business_scope"] = "cloud_segment"
            else:
                context["calculations"][0]["period_start"] = "2024-01-01"
            with self.subTest(change=change):
                self.assertIn("context_mismatch", categories(context))

    def test_no_detected_defect_does_not_mutate_or_certify_claims(self):
        context = fixture()
        original = copy.deepcopy(context)
        self.assertEqual(check_rules(context), [])
        self.assertEqual(context, original)
        self.assertNotIn("status", context["claims"][0])

    def test_missing_requirement_without_claim_targets_requirement(self):
        context = fixture()
        context["requirements"].append({"id": "r2", "text": "Explain margin"})
        issues = check_rules(context)
        issue = next(i for i in issues if i["issue_type"] == "omission")
        self.assertEqual(issue["target_id"], "r2")
        self.assertEqual(issue["evidence_refs"], [])

    def test_missing_reference_does_not_invent_source_reference(self):
        context = fixture()
        context["claims"][0]["evidence_ids"] = ["missing"]
        issues = check_rules(context)
        missing = [i for i in issues if i["issue_type"] == "missing_reference"]
        self.assertTrue(missing)
        self.assertTrue(all(i["evidence_refs"] == [] for i in missing))

    def test_cutoff_uses_publication_date_not_financial_period_end(self):
        context = fixture()
        context["scope"]["cutoff"] = "2024-12-31"
        self.assertIn("source_time_mismatch", categories(context))

    def test_date_cutoff_includes_entire_day(self):
        context = fixture()
        context["scope"]["cutoff"] = "2025-01-29"
        context["evidence"][0]["published_at"] = "2025-01-29T23:59:00Z"
        self.assertNotIn("source_time_mismatch", categories(context))

    def test_missing_publication_and_intraday_uncertainty_explicit(self):
        context = fixture()
        context["evidence"][0]["published_at"] = None
        self.assertIn("missing_provenance", categories(context))
        context = fixture()
        context["scope"]["cutoff"] = "2025-01-29T12:00:00Z"
        self.assertIn("missing_provenance", categories(context))

    def test_stale_and_changed_version_rejected(self):
        context = fixture()
        context["claims"][0]["evidence_versions"] = {"e1": 1}
        context["evidence"][0]["version"] = 2
        self.assertIn("stale_reference", categories(context))
        context = fixture()
        context["evidence"][0]["stale"] = True
        self.assertIn("stale_reference", categories(context))

    def test_wrong_entity_period_metric_and_unit_are_defects(self):
        for field, value in (("entity", "NVDA"), ("period_end", "2024-09-30"),
                             ("metric", "net_income"), ("unit", "USD millions")):
            context = fixture()
            context["claims"][0]["constraints"][field] = value
            with self.subTest(field=field):
                self.assertIn("context_mismatch", categories(context))

    def test_segment_evidence_cannot_support_consolidated_context(self):
        context = fixture()
        context["evidence"][0]["business_scope"] = "cloud_segment"
        context["claims"][0]["constraints"]["business_scope"] = "consolidated"
        self.assertIn("context_mismatch", categories(context))

    def test_missing_fields_reach_source_review_and_still_check_value_and_known_scope(self):
        ctx = fixture()
        claim = ctx["claims"][0]
        claim["constraints"].update(basis="GAAP",business_scope="consolidated")
        notes = []
        self.assertEqual(check_rules(ctx,context_notes=notes), [])
        self.assertEqual(notes, [{"claim_id":"c1","check_against_source":{"e1":{
            "basis":"GAAP","business_scope":"consolidated"}}}])
        claim["asserted_value"] += 1
        self.assertIn("numerical_inconsistency", categories(ctx))
        ctx["evidence"][0]["business_scope"] = "segment"
        self.assertIn("context_mismatch", categories(ctx))

    def test_archived_statement_computation_uses_common_operand_periods(self):
        from verifin.data import stable_id
        ctx = fixture()
        base, = ctx["evidence"]
        ctx["evidence"].append({**base,"id":"cost","metric":"expense","value":80})
        args = ["quarter profit in dollars", "difference", ["e1","cost"], "USD"]
        cid = stable_id("calc_", args)
        calc = {"id":cid,"metric":args[0],"operation":args[1],"operand_ids":args[2],
                "unit":"USD","value":20,"entity":"MSFT"}
        ctx["calculations"] = [calc]
        c = ctx["claims"][0]
        c.update(asserted_value=20,calculation_ids=[cid],text="Profit was USD 20.")
        c["constraints"]["metric"] = "revenue less expense"
        notes = []
        self.assertEqual(check_rules(ctx,context_notes=notes), [])
        self.assertEqual(notes[0]["check_against_source"][cid], {"metric":"revenue less expense"})
        c["constraints"]["period_end"] = "2023-12-31"
        self.assertIn("context_mismatch",categories(ctx))
        calc["period_end"] = "2023-12-31"
        self.assertIn("context_mismatch",categories(ctx))

    def test_conflicting_unit_fields_are_not_silently_ignored(self):
        context = fixture()
        context["claims"][0]["asserted_unit"] = "USD millions"
        self.assertIn("context_mismatch", categories(context))

    def test_document_number_requires_semantic_review_not_false_numeric_failure(self):
        context = fixture()
        doc = context["evidence"][0]
        doc.update(kind="document", text="Quarterly revenue was USD 100.")
        for field in ("value", "unit", "metric", "period_start"):
            doc.pop(field)
        self.assertEqual(check_rules(context), [])
        self.assertNotIn("status", context["claims"][0])
        # Known wrong entity is still a defect, even if the original is a document.
        doc["entity"] = "NVDA"
        self.assertIn("context_mismatch", categories(context))

    def test_timezone_omission_does_not_silently_assume_utc(self):
        context = fixture()
        context["scope"]["cutoff"] = "2025-02-01T12:00:00"
        self.assertIn("invalid_record", categories(context))

    def test_number_from_wrong_metric_cannot_match_by_coincidence(self):
        context = fixture()
        context["evidence"].append({**context["evidence"][0], "id": "e2", "metric": "net_income", "value": 50})
        context["claims"][0]["evidence_ids"] = ["e1", "e2"]
        context["claims"][0]["asserted_value"] = 50
        self.assertIn("numerical_inconsistency", categories(context))

    def growth_fixture(self):
        context = fixture()
        context["evidence"].append({**context["evidence"][0], "id": "e0", "value": 80,
                                    "period_start": "2023-10-01", "period_end": "2023-12-31"})
        context["calculations"] = [{"id": "calc1", "operation": "growth", "operand_ids": ["e1", "e0"],
                                    "operand_versions": {"e1": 1, "e0": 1}, "value": 25,
                                    "unit": "%", "entity": "MSFT", "period_start": "2024-10-01",
                                    "period_end": "2024-12-31", "formula": "(100/80-1)*100",
                                    "scope_version": 1, "version": 1}]
        context["claims"][0].update(evidence_ids=["e1", "e0"], calculation_ids=["calc1"],
                                    asserted_value=25, asserted_unit="%", text="Quarterly revenue grew 25%.",
                                    constraints={"entity": "MSFT", "period_start": "2024-10-01",
                                                 "period_end": "2024-12-31", "unit": "%"})
        return context

    def test_comparison_operands_may_use_different_periods(self):
        self.assertEqual(check_rules(self.growth_fixture()), [])

    def test_qualified_base_fact_in_a_mixed_calculation_can_anchor_its_own_exact_value(self):
        context = self.growth_fixture()
        calculation, = context["calculations"]
        calculation["metric"] = "revenue_growth"
        claim, = context["claims"]
        claim.update(text="营业收入为 USD 100，收入增速为25%。", asserted_value=100, asserted_unit="USD")
        claim["constraints"].update(metric="revenue",unit="USD")
        self.assertEqual(check_rules(context),[])
        # The cited 80 belongs to the comparator, not the asserted current revenue.
        claim["asserted_value"] = 80
        self.assertIn("numerical_inconsistency",categories(context))
        claim["asserted_value"] = 100
        claim["text"] = "The values are USD 100 and 25%."
        self.assertIn("numerical_inconsistency",categories(context))

    def test_valid_calculation_can_survive_unrelated_scope_extension(self):
        context = self.growth_fixture()
        context["session"]["scope_version"] = 2
        context["claims"][0]["scope_version"] = 2
        self.assertNotIn("stale_reference", categories(context))

    def test_transitive_dependency_cutoff_and_version_are_checked(self):
        context = self.growth_fixture()
        context["claims"][0]["evidence_ids"] = []
        context["evidence"][1]["published_at"] = "2025-03-01"
        context["evidence"][1]["version"] = 2
        result = categories(context)
        self.assertIn("source_time_mismatch", result)
        self.assertIn("stale_reference", result)

    def test_tampered_calculation_value_is_recomputed_independently(self):
        context = self.growth_fixture()
        context["calculations"][0]["value"] = 20
        context["claims"][0]["asserted_value"] = 20
        self.assertIn("numerical_inconsistency", categories(context))

    def test_zero_denominator_is_explicit_not_a_rule_crash(self):
        context = self.growth_fixture()
        context["evidence"][1]["value"] = 0
        self.assertIn("numerical_inconsistency", categories(context))

    def test_unreproducible_operation_does_not_fall_through_verification(self):
        ctx = self.growth_fixture()
        a, b = ctx["calculations"][0]["operand_ids"]
        ctx["evidence"].append(dict(ctx["evidence"][0],id="extra"))
        for op,ids in (("difference",[a,b,"extra"]),("growth",[a]),("margin",[a,a]),("unrecognized",[a,b])):
            ctx["calculations"][0].update(operation=op,operand_ids=ids)
            with self.subTest(op=op,ids=ids):
                self.assertIn("invalid_record",categories(ctx))

    def test_dependency_cycle_does_not_recurse_forever(self):
        context = self.growth_fixture()
        context["calculations"][0]["operand_ids"] = ["calc1"]
        self.assertIn("invalid_record", categories(context))

    def test_numerical_value_requires_real_number_not_bool(self):
        context = fixture()
        context["claims"][0]["asserted_value"] = True
        self.assertIn("invalid_record", categories(context))


class SemanticReviewTests(unittest.TestCase):
    def review_result(self):
        return {"issues": [], "reviewed_claim_ids": ["c1"], "reviewed_requirement_ids": ["r1"],
                "claim_verdicts":[{"claim_id":"c1","status":"Supported","evidence_refs":["e1"],"basis":"The quarter's revenue is USD 100."}]}

    def test_reviewer_receives_original_evidence_and_rule_findings_once(self):
        calls = []
        class Fake:
            def complete(inner, role, context):
                calls.append((role, context))
                return self.review_result()
        context = fixture()
        context["rule_findings"] = check_rules(context)
        result = Verifier(Fake()).review(context)
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "critic")
        self.assertEqual(calls[0][1]["evidence"][0]["source_url"], "https://example.test/report")

    def test_reviewed_ids_alone_or_an_untraceable_positive_verdict_cannot_pass(self):
        from verifin.model import validate_response
        output = self.review_result()
        row, = output.pop("claim_verdicts")
        for verdicts in (None, [], [dict(row,evidence_refs=[])], [dict(row,evidence_refs=["missing"])],
                         [row,row], [dict(row,status="Partially Supported",supported_text="never in draft")]):
            with self.subTest(verdicts=verdicts), self.assertRaises(ModelError):
                validate_response("critic", dict(output,claim_verdicts=verdicts), fixture())

    def test_semantic_failure_never_degrades_to_rules_only_success(self):
        class Broken:
            def complete(self, role, context):
                raise ModelError("no model available")
        with self.assertRaises(ModelError):
            Verifier(Broken()).review(fixture())

    def test_local_receipt_failures_cannot_certify_or_veto_siblings(self):
        from verifin.model import validate_response
        ctx, base = fixture(), self.review_result()
        ctx["claims"].append(dict(ctx["claims"][0], id="c2"))
        original = base["claim_verdicts"][0]
        second = dict(original, claim_id="c2")
        issue = {"target_id":"c2","issue_type":"unsupported_statement","evidence_refs":["e1"],
                 "missing_support":"No support for the attribution.","proposed_action":"Qualify the claim."}
        for rows, issues in (([],[]), ([second,second],[]), ([dict(second, evidence_refs=["unknown"])],[issue]),
                             ([second],[dict(issue,evidence_refs=["unknown"])]),
                             ([dict(second,status="Partially Supported",supported_text="incorrect excerpt")],[issue])):
            for reverse in (False, True):
                verdicts = [original,*rows]
                if reverse:
                    verdicts.reverse()
                raw = dict(base, reviewed_claim_ids=["c1","c2"],claim_verdicts=verdicts,issues=issues)
                with self.subTest(rows=rows,issues=issues, reverse=reverse):
                    result = validate_response("critic", raw, ctx)
                    self.assertEqual(result["claim_verdicts"][0], original)
                    bad = result["claim_verdicts"][1]
                    self.assertEqual((bad["claim_id"],bad["status"],bad["evidence_refs"]),("c2","Pending Verification",[]))
                    self.assertTrue(any(i.get("contract_diagnostic",{}).get("code")=="invalid_claim_receipt" for i in result["issues"]))
                    self.assertEqual(validate_response("critic",result,ctx),result)
                    if issue in issues:
                        self.assertIn(issue, result["issues"])
        for extra in (dict(second,claim_id="unknown"),None):
            with self.assertRaises(ModelError):
                validate_response("critic",{**base,"reviewed_claim_ids":["c1","c2"],"claim_verdicts":[original,extra]},ctx)

    def test_omission_can_target_requirement_without_claim_or_evidence(self):
        context = fixture()
        context["claims"] = []
        output = {"issues": [{"target_id": "r1", "issue_type": "omission", "evidence_refs": [],
                              "missing_support": "Revenue was not answered.", "proposed_action": "Fetch revenue."}],
                  "reviewed_claim_ids": [], "reviewed_requirement_ids": ["r1"]}
        class Fake:
            def complete(self, *_):
                return output
        self.assertEqual(Verifier(Fake()).review(context), output)

    def test_skipped_review_unknown_target_and_source_ref_are_errors(self):
        outputs = []
        skipped = self.review_result()
        skipped["reviewed_requirement_ids"] = []
        outputs.append(skipped)
        for field, value in (("target_id", "invented"), ("evidence_refs", ["invented"])):
            output = self.review_result()
            issue = {"target_id": "c1", "issue_type": "unsupported_statement", "evidence_refs": ["e1"],
                     "missing_support": "No support.", "proposed_action": "Research further."}
            issue[field] = value
            output["issues"] = [issue]
            outputs.append(output)
        for output in outputs:
            class Fake:
                def complete(self, *_):
                    return output
            with self.subTest(output=output), self.assertRaises(ModelError):
                Verifier(Fake()).review(fixture())

    def test_unknown_category_does_not_mask_invalid_issue_or_verdict(self):
        from verifin.model import validated_output, OutputContractError
        output = self.review_result()
        issue = {"target_id": "c1", "issue_type": "unlisted", "evidence_refs": ["e1"],
                 "missing_support": "The cause has no source.", "proposed_action": "Remove the attribution."}
        verdict = output["claim_verdicts"][0]
        for key, change in (
            ("issues", [dict(issue, target_id="other")]),
            ("issues", [dict(issue, evidence_refs=["unseen"])]),
            ("issues", [dict(issue, missing_support="")]),
            ("issues", [dict(issue, proposed_action=None)]),
            ("reviewed_claim_ids", []),
            ("claim_verdicts", [dict(verdict, claim_id="other")]),
            ("claim_verdicts", [verdict, verdict]),
            ("claim_verdicts", [dict(verdict, evidence_refs=["unseen"])]),
            ("claim_verdicts", [dict(verdict, status="Partially Supported", supported_text="not in the claim")]),
            ("claim_verdicts", []),
        ):
            with self.subTest(key=key, change=change), self.assertRaises(OutputContractError):
                validated_output("critic", {**output, "issues": [issue], key: change}, fixture())
        with self.assertRaisesRegex(OutputContractError, "finding against the affected claim"):
            validated_output("critic", {**output, "issues": [dict(issue, target_id="r1")],
                             "claim_verdicts": [dict(verdict, status="Insufficient Evidence")]}, fixture())


class StoredToolVerificationTests(unittest.TestCase):
    def test_real_replay_calculation_and_source_correction(self):
        """Exercise actual tool -> SQLite -> rules interfaces with frozen SEC data."""
        from verifin.storage import SessionStore
        from verifin.tools import ToolRegistry
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "research.sqlite3")
            try:
                sid = store.create_session()["id"]
                scope = {"entities": ["MSFT"], "period_start": "2024-10-01", "period_end": "2024-12-31",
                         "cutoff": "2025-02-01", "requirements": [{"id": "r1", "text": "Compute operating margin"}]}
                store.update_scope(sid, scope)
                tools = ToolRegistry(store, Path(__file__).resolve().parents[1] / "data")
                facts = []
                for metric in ("operating_income", "revenue"):
                    result = tools.execute("financial_facts", {"metric": metric}, sid, scope)
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(result["data"]["network_requests"], 0)
                    fact = result["evidence"][0]
                    store.put_evidence(sid, fact)
                    facts.append(fact)
                result = tools.execute("calculate", {"operation": "margin", "operand_ids": [f["id"] for f in facts]}, sid, scope)
                self.assertTrue(result["ok"], result)
                calculation = result["calculations"][0]
                store.put_calculation(sid, calculation)
                store.put_claim(sid, {"id": "c1", "text": "Operating margin was approximately 45.46%.",
                                     "requirement_ids": ["r1"], "evidence_ids": [],
                                     "calculation_ids": [calculation["id"]],
                                     "constraints": {k: calculation[k] for k in ("entity", "metric", "period_start", "period_end", "unit")},
                                     "asserted_value": calculation["value"], "asserted_unit": "%",
                                     "scope_version": store.get_session(sid)["scope_version"]})
                context = store.get_context(sid)
                self.assertEqual(check_rules(context), [])
                revised = {**facts[0], "value": facts[0]["value"] + 1_000_000, "snapshot_hash": "corrected-snapshot-test"}
                store.put_evidence(sid, revised)
                self.assertIn("stale_reference", categories(store.get_context(sid)))
                self.assertIn("numerical_inconsistency", categories(store.get_context(sid)))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
