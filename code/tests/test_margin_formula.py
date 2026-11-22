"""Regressions for a sourced margin result and its requested calculation."""
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

from tests.critic_fixture import fixture_verdicts
from tests.test_tools import DATA
from verifin.display_numbers import display_check
from verifin.engine import Budget, ResearchEngine
from verifin.evaluation import FrozenCase
from verifin.filing_links import native_fact_quarter
from verifin.margin_formula import claim_receipts, defects, draft_calculation, formula_followup, requested_metric
from verifin.model import validated_output
from verifin.reporting import render_markdown, report_view
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from verifin.verification import check_rules


QUESTION = "What were Microsoft’s revenue and operating margin in FY2025 Q2? Show the source and calculation."
REVENUE = "Microsoft’s FY2025 Q2 total revenue was $69,632 million."
SOURCE_URL = "https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q2/press-release-webcast"
REQUIREMENTS = [{"id": "revenue", "text": "Total revenue"}, {"id": "margin", "text": "Operating margin and calculation"}]
FOLLOWUP = "能给公式或计算过程吗？"


class FilingModel:
    def __init__(self, case):
        self.case = case
        self.steps = 0

    def complete(self, role, ctx):
        if role == "planner":
            self.steps += 1
            if self.steps == 1:
                query = "Microsoft total revenue operating income three months ended December 31 2024"
                if self.case == "missing_operand":
                    query = "Microsoft Cloud and AI Strength Drives Second Quarter Results Satya Nadella January 29 2025"
                response = {"action": "tool", "tool": "search_documents", "arguments": {
                    "query": query, "entity": "MSFT", "limit": 1 if self.case == "missing_operand" else 5},
                    "scope": {"entities": ["MSFT"], "requirements": REQUIREMENTS}}
            elif (self.steps == 2 and self.case != "no_label") or (self.steps == 3 and self.case == "missing_operand"):
                source = next(r for r in ctx["evidence"] if r.get("kind") == "document" and r["source_url"] == SOURCE_URL)
                response = {"action": "tool", "tool": "read_document", "arguments": {"entity": "MSFT", "document_id": source["document_id"]}}
                if self.steps == 3:
                    response["arguments"].update(offset=13214, limit_chars=240)
            elif self.steps == {"no_label": 2, "missing_operand": 4}.get(self.case, 3):
                response = {"action": "research", "reason": "Report the exact quarter from the original statement."}
            else:
                response = {"action": "finish"}
        elif role == "researcher":
            passage = next(r for r in ctx["evidence"] if r.get("kind") == "document" and "Total revenue 69,632" in r["text"])
            calcs = [c["id"] for c in ctx["calculations"]]
            rate = "85.46%" if self.case == "wrong_rate" else "45.46%"
            formula = {"wrong_formula": "69.632 billion ÷ 31.653 billion",
                       "wrong_scaled_inputs": "USD 63,306 million ÷ USD 139,264 million",
                       "wrong_bare_inputs": "63,306 ÷ 139,264",
                       "wrong_accounting_sign": "(31,653) ÷ 69,632",
                       "wrong_accounting_fullwidth": "（31,653） ÷ 69,632",
                       "wrong_accounting_denominator": "31,653 ÷ (69,632)",
                       "wrong_currency": "CAD 31.653 billion ÷ USD 69,632 million",
                       "grouped_quotient": "(31,653 ÷ 69,632)",
                       "mixed_scales": "USD 31.653 billion ÷ USD 69,632 million"}.get(self.case, "31,653 ÷ 69,632")
            result = f"Operating margin in FY2025 Q2 was {rate}."
            arithmetic = "Operating margin is operating income divided by revenue."
            if self.case != "no_numeric_prose":
                scale_note = " (CAD millions)" if self.case == "wrong_shared_currency" else ""
                arithmetic = f"The operating margin calculation is {formula} × 100% = {rate}{scale_note}."
            response = {"claims": [
                {"id": "claim-revenue", "text": REVENUE, "evidence_ids": [passage["id"]], "requirement_ids": ["revenue"],
                 "calculation_ids": [], "constraints": {}},
                {"id": "claim-margin", "text": result + " " + arithmetic, "evidence_ids": [passage["id"]],
                 "calculation_ids": calcs, "requirement_ids": ["margin"], "constraints": {}}], "answered_requirement_ids": ["revenue", "margin"],
                        "unanswered_requirement_ids": [], "summary": REVENUE}
        else:
            # Assume even an overly trusting Critic approves both initial claims.
            margin = next(c for c in ctx["claims"] if c["id"] == "claim-margin")
            response = {"issues": [], "reviewed_claim_ids": [c["id"] for c in ctx["claims"]],
                        "reviewed_requirement_ids": ["revenue", "margin"], "request_coverage": [
                            {"part": "part-1", "status": "answered", "claim_id": "claim-revenue",
                             "answer_sentence": REVENUE, "supporting_claim_ids": ["claim-margin"]},
                            {"part": "part-2", "status": "answered", "claim_id": "claim-margin",
                             "answer_sentence": margin["text"].split(". ", 1)[-1]}]}
        return validated_output(role, fixture_verdicts(role, response, ctx), ctx)


class ExplicitComparisonFormulaTests(TestCase):
    def setUp(self):
        question = "比较 AAA FY2025 Q4 与去年同季的 GAAP 营业利润率，并计算两日收盘价收益。"
        self.context = {"request": question, "scope": {"entities": ["AAA"], "period_end": "2025-12-31"}}
        self.metric = "operating_income_margin"
        self.assertEqual(requested_metric(self.context), self.metric)
        self.records = {}
        self.rates = {}
        for year, income in ((2025, 30), (2024, 20)):
            scope = {"entity": "AAA", "period_start": f"{year}-10-01", "period_end": f"{year}-12-31",
                     "basis": "US-GAAP", "business_scope": "consolidated"}
            origin = {"document_id": "document", "table_id": "original-table", "snapshot_hash": "source-hash"}
            for name, value in (("operating_income", income), ("revenue", 100)):
                ref = f"{year}-{name}"
                row = {"id": ref, "metric": name, "value": value, "unit": "USD", "currency": "USD",
                       **scope, **origin}
                if year == 2025:
                    row["fiscal_quarter"] = "2025Q4"
                self.records[ref] = row
            numerator, denominator = f"{year}-operating_income", f"{year}-revenue"
            rate = f"{year}-margin"
            self.rates[year] = rate
            self.records[rate] = {"id": rate, "operation": "margin", "metric": self.metric, "value": income,
                                  "unit": "%", "operand_ids": [numerator, denominator],
                                  "formula": f"{numerator} / {denominator} * 100", **scope}

    def check(self, year, text, *, context=None, records=None):
        records = records or self.records
        ref = self.rates[year]
        return list(defects(context or self.context, {"text": text, "calculation_ids": [ref], "constraints": {}},
                            [records[ref]], records))

    def test_link_digits_are_not_a_formula_but_financial_division_still_counts(self):
        link = "https://www.sec.gov/Archives/edgar/data/1234/000000123425000001/exhibit.htm"
        text = f"AAA FY2025 Q4 GAAP 营业利润率为30%，算式30 ÷ 100 × 100。原始附件：{link}。"
        self.assertEqual(self.check(2025, text), [])
        bad = self.check(2025, text.replace("30 ÷", "3 ÷"))
        self.assertTrue(any("Displayed division" in issue[2] for issue in bad))

    def test_named_comparison_quarter_requires_matching_original_table_period(self):
        text = "AAA FY2024 Q4 GAAP 营业利润率为20%，按20 ÷ 100 × 100计算。"
        self.assertEqual(self.check(2024, text), [])
        self.assertTrue(self.check(2024, text.replace("20 ÷", "200 ÷")))
        self.assertTrue(self.check(2024, text.replace("FY2024 Q4", "FY2024 Q3")))
        self.assertTrue(self.check(2025, text))
        other_table = dict(self.records)
        for ref in ("2024-operating_income", "2024-revenue"):
            other_table[ref] = {**other_table[ref], "table_id": "unrelated-table"}
        self.assertTrue(self.check(2024, text, records=other_table))
        no_comparison = {**self.context, "request": self.context["request"].replace("与去年同季", "")}
        self.assertTrue(self.check(2024, text, context=no_comparison))


@skipUnless(find_spec("lxml"), "replay HTML statement operands require lxml")
class SourcedMarginTests(TestCase):
    def test_recovered_page_can_use_a_spare_tool_slot_before_the_last_two_model_calls(self):
        class PageModel(FilingModel):
            def complete(self, role, ctx):
                roles.append(role)
                if role == "planner" and self.steps in (2, 3):
                    source = next(r for r in ctx["evidence"] if r.get("kind") == "document"
                                  and r["source_url"] == SOURCE_URL)
                    args = {"entity":"MSFT","document_id":source["document_id"]}
                    if self.steps == 3:
                        args.update(offset=4500,limit_chars=6000)
                    self.steps += 1
                    return {"action":"tool","tool":"read_document","arguments":args}
                return super().complete(role, ctx)

        for slots, expected in ((4, "complete"), (3, "partial")):
            with self.subTest(slots=slots), TemporaryDirectory() as folder, SessionStore(":memory:") as store:
                roles = []
                sid = store.create_session()["id"]
                registry = ToolRegistry(store,DATA,cache_dir=Path(folder)/"snapshots")
                engine = ResearchEngine(store,PageModel("correct"),registry)
                report = engine.ask(sid,QUESTION,as_of="2025-02-01",budget=Budget(6,slots,60))["report"]
                self.assertEqual(roles,["planner"]*4+["researcher","critic"])
                self.assertEqual(report["status"],expected,report["issues"])
                calls = [e["tool"] for e in store.events(sid) if e["type"] == "tool_call"]
                self.assertEqual(calls.count("calculate"), int(expected == "complete"))
                self.assertLessEqual(len(calls), slots)

    def run_case(self, case, capture=None):
        with TemporaryDirectory() as tmp, SessionStore(Path(tmp) / "session.db") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store, DATA, cache_dir=Path(tmp) / "cache")
            engine = ResearchEngine(store, FilingModel(case), tools, candidate_callback=capture)
            report = engine.ask(sid, QUESTION, budget=Budget(9, 5, 40))["report"]
            events = [e for e in store.events(sid) if e["type"] == "tool_call"]
            self.assertEqual(report, store.get_report(sid, 1))
            self.assertTrue(all(e["observation"]["data"]["network_requests"] == 0 for e in events))
            return report, events, report_view(report)["question_answers"]

    def test_original_question_executes_one_real_calculation_and_displays_it(self):
        report, events, answers = self.run_case("no_numeric_prose")
        self.assertEqual(report["status"], "complete", report["issues"])
        self.assertEqual([e["tool"] for e in events], ["search_documents", "read_document", "calculate"])
        calc, = report["calculations"]
        records = {row["id"]: row for row in report["evidence"]}
        profit, revenue = [records[i] for i in calc["operand_ids"]]
        self.assertEqual((profit["value"], revenue["value"]), (31_653_000_000, 69_632_000_000))
        self.assertEqual(profit["period_start"], "2024-10-01")
        self.assertEqual(revenue["period_end"], "2024-12-31")
        self.assertIn(calc["id"], report["claims"][1]["calculation_ids"])
        self.assertEqual([a["status"] for a in answers], ["answered", "answered"])
        self.assertIn("31,653 ÷ 69,632 × 100% = 45.46%", answers[1]["answer_text"])
        self.assertIn(SOURCE_URL, render_markdown(report))

    def test_incorrect_or_unavailable_calculation_preserves_verified_revenue(self):
        for case in ("no_label", "missing_operand", "wrong_rate", "wrong_formula", "wrong_scaled_inputs",
                     "wrong_bare_inputs", "wrong_accounting_sign", "wrong_accounting_fullwidth",
                     "wrong_accounting_denominator", "wrong_currency", "wrong_shared_currency"):
            with self.subTest(case=case):
                report, events, answers = self.run_case(case)
                self.assertEqual(report["status"], "partial", report["issues"])
                self.assertEqual([a["status"] for a in answers], ["partial", "gap"])
                self.assertEqual(answers[0]["answer_sentence"], REVENUE)
                self.assertEqual(report["claims"][0]["status"], "Supported")
                self.assertEqual(report["claims"][1]["status"], "Insufficient Evidence")
                if case == "no_label":
                    self.assertEqual([e["tool"] for e in events], ["search_documents"])
                elif case == "missing_operand":
                    self.assertEqual([e["tool"] for e in events], ["search_documents", "read_document", "read_document"])
                    self.assertFalse(any(row.get("metric") == "operating_income" for row in report["evidence"]))
                else:
                    self.assertTrue(any(i["issue_type"] == "numerical_inconsistency" for i in report["issues"]))
                    self.assertNotIn("answer_sentence", answers[1])

    def test_correct_source_amounts_can_be_shown_in_different_units(self):
        report, _, answers = self.run_case("mixed_scales")
        self.assertEqual(report["status"], "complete", report["issues"])
        self.assertIn("USD 31.653 billion ÷ USD 69,632 million × 100% = 45.46%", answers[1]["answer_sentence"])

    def test_parentheses_around_whole_quotient_keep_positive_source_amounts(self):
        report, _, answers = self.run_case("grouped_quotient")
        self.assertEqual(report["status"], "complete", report["issues"])
        self.assertIn("(31,653 ÷ 69,632) × 100% = 45.46%", answers[1]["answer_sentence"])

    def test_short_formula_followup_uses_the_reviewed_metric_and_exact_quarter(self):
        class FollowupModel:
            def __init__(self, case):
                self.case = case

            def complete(self, role, ctx):
                if role == "planner":
                    if ctx["episode"].get("draft_attempted"):
                        return {"action": "finish"}
                    self.target = ctx["margin_formula_target"]
                    self.request = ctx["episode"]["request"]
                    return {"action": "research", "scope": {"entities": ["NVDA"], "cutoff": "2025-12-31",
                                                            "period_start": "2024-07-01", "period_end": "2024-09-30"}}
                reqs = [r["id"] for r in ctx["requirements"]]
                if role == "researcher":
                    calc, = ctx["calculations"]
                    rows = {r["id"]: r for r in ctx["evidence"]}
                    amounts = [f"{rows[ref]['value'] / rows[ref]['printed_scale']:,.0f}" for ref in calc["operand_ids"]]
                    if self.case == "reversed":
                        amounts.reverse()
                    metric = "毛利率" if self.case == "other_metric" else "营业利润率"
                    quarter = "FY2025 Q1" if self.case == "other_quarter" else "FY2025 Q2"
                    rate = f"{calc['value']:.2f}%"
                    equation = "" if self.case == "rate_only" else f"的计算为 {amounts[0]} ÷ {amounts[1]} × 100% = "
                    text = f"{quarter} 的{metric}{equation or '为 '}{rate}。"
                    if self.case == "symbolic":
                        text = f"{quarter} 的营业利润率等于营业利润除以营业收入 × 100%。"
                    response = {"claims": [{"id": "next-formula", "text": text, "constraints": {},
                                            "evidence_ids": calc["operand_ids"], "calculation_ids": [calc["id"]],
                                            "requirement_ids": reqs}],
                                "answered_requirement_ids": reqs, "unanswered_requirement_ids": [], "summary": text}
                else:
                    claim, = ctx["claims"]
                    response = {"issues": [], "reviewed_claim_ids": [claim["id"]], "reviewed_requirement_ids": reqs,
                                "request_coverage": [{"part": "part-1", "status": "answered", "claim_id": claim["id"],
                                                      "answer_sentence": claim["text"]}]}
                return validated_output(role, fixture_verdicts(role, response, ctx), ctx)

        for case in ("correct", "symbolic", "rate_only", "reversed", "other_metric", "other_quarter"):
            with self.subTest(case=case), TemporaryDirectory() as tmp, SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                tools = ToolRegistry(store, DATA, cache_dir=Path(tmp) / "cache")
                first = ResearchEngine(store, FilingModel("correct"), tools).ask(
                    sid, QUESTION, as_of="2025-02-01", budget=Budget(9, 5, 40))["report"]
                self.assertEqual(first["status"], "complete", first["issues"])
                model = FollowupModel(case)
                captured = []
                report = ResearchEngine(store, model, tools, candidate_callback=captured.append).ask(
                    sid, FOLLOWUP, budget=Budget(6, 5, 40))["report"]
                self.assertEqual(model.request, FOLLOWUP)
                self.assertEqual(model.target, {"entity": "MSFT", "metric": "operating_income_margin", "quarter": "2025Q2",
                                                "period_start": "2024-10-01", "period_end": "2024-12-31", "report_version": 1})
                self.assertEqual(report["scope"]["entities"], ["MSFT"])
                self.assertEqual(report["scope"]["cutoff"], "2025-02-01")
                self.assertEqual(report["scope"]["period_end"], "2024-12-31")
                self.assertEqual(store.get_report(sid, 1), first)
                self.assertFalse(any(e["type"] == "tool_call" and e["episode_id"] == report["episode_id"]
                                     for e in store.events(sid)))
                answer, = report_view(report)["question_answers"]
                if case in ("correct", "symbolic"):
                    self.assertEqual(report["status"], "complete", report["issues"])
                    self.assertEqual(answer["status"], "answered")
                    if case == "correct":
                        self.assertIn("31,653 ÷ 69,632 × 100% = 45.46%", answer["answer_sentence"])
                    else:
                        self.assertIn("营业利润除以营业收入", answer["answer_sentence"])
                        self.assertIn("31,653 ÷ 69,632 × 100% = 45.46%", answer["answer_text"])
                    self.assertIn(SOURCE_URL, render_markdown(report))
                else:
                    self.assertEqual(report["status"], "partial")
                    self.assertEqual(answer["status"], "gap")
                    self.assertNotIn("answer_sentence", answer)
                    if case == "reversed":
                        live, = captured
                        frozen = FrozenCase.from_context("reviewed-formula-followup", live).context()
                        self.assertEqual(check_rules(live), check_rules(frozen))
                        self.assertTrue(any(i["issue_type"] == "numerical_inconsistency" for i in check_rules(frozen)))

    def test_frozen_detection_applies_the_same_rule_to_the_same_candidate(self):
        for case in ("wrong_rate", "wrong_accounting_sign", "wrong_currency"):
            with self.subTest(case=case):
                captured = []
                self.run_case(case, captured.append)
                live, = captured
                frozen = FrozenCase.from_context(case, live).context()
                self.assertEqual(frozen["request"], QUESTION)
                self.assertNotIn("episode", frozen)
                issues = check_rules(live)
                self.assertIn("numerical_inconsistency", {i["issue_type"] for i in issues})
                self.assertEqual(check_rules(frozen), issues)


class ProfitMarginRequestTests(TestCase):
    def test_quarterly_sec_facts_bind_a_clarified_formula_without_an_issuer_release_heading(self):
        dates = {"Q1": ("2024-07-01", "2024-09-30"), "Q2": ("2024-10-01", "2024-12-31")}
        scope = {"cutoff": "2025-02-01", "entities": ["MSFT"]}
        request = FOLLOWUP + "\n改用 FY2025 Q1 的营业利润率。"
        with TemporaryDirectory() as tmp, SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            tools = ToolRegistry(store, DATA, cache_dir=Path(tmp) / "cache")
            facts, calcs = {}, {}
            for quarter, (start, end) in dates.items():
                operands = []
                for metric in ("operating_income", "revenue"):
                    args = {"entity": "MSFT", "metric": metric, "source": "replay",
                            "period_start": start, "period_end": end, "unit": "USD"}
                    original = tools.execute("financial_facts", args, sid, scope)
                    row, = original["evidence"]
                    self.assertNotIn("fiscal_quarter", row)
                    self.assertEqual(native_fact_quarter(row), "2025" + quarter)
                    proof = row["filing_link_provenance"]
                    wrong_accession = {**row, "filing_link_provenance": {**proof, "locator": "another accession"}}
                    self.assertIsNone(native_fact_quarter(wrong_accession))
                    later = tools.execute("financial_facts", args, sid, {"entities": ["MSFT"]})
                    comparative, = later["evidence"]
                    self.assertNotEqual(comparative["filing_link_provenance"]["report_date"], end)
                    self.assertIsNone(native_fact_quarter(comparative))
                    saved = store.put_evidence(sid, row)
                    facts[saved["id"]] = saved
                    operands.append(saved["id"])
                if quarter == "Q1":
                    ctx = {"episode": {"request": request}, "scope": scope, "evidence": list(facts.values()),
                           "tools": [{"name": "calculate"}], "calculations": []}
                    self.assertEqual(draft_calculation(ctx), {"operation": "margin", "operand_ids": operands})
                result = tools.execute("calculate", {"operation": "margin", "operand_ids": operands}, sid, scope)
                calc, = result["calculations"]
                calcs[quarter] = store.put_calculation(sid, calc)

        records = {**facts, **{calc["id"]: calc for calc in calcs.values()}}
        for quarter, calc in calcs.items():
            with self.subTest(quarter=quarter):
                refs = {"calculation_ids": [calc["id"]], "constraints": {}}
                match = list(claim_receipts(refs, records, "operating_income_margin", scope, request=request))
                self.assertEqual(bool(match), quarter == "Q1")

    def test_only_short_formula_references_are_inherited(self):
        for text in (FOLLOWUP, "能提供計算過程嗎？", "Could you show me the formula or calculation?"):
            self.assertTrue(formula_followup(text))
        for text in ("能给苹果毛利率的算式吗？", "不要公式。", "Which calculation was confirmed?",
                     "Show the formula for Q1.", "What were the calculation inputs for both issuers?"):
            self.assertFalse(formula_followup(text))

    def test_rule_targets_calculated_standard_margins(self):
        for text in ("What was the disclosed operating margin in FY2025 Q2?", "What is an operating margin? Show the formula.",
                     "What was the adjusted operating margin in FY2025 Q2? Show the calculation.",
                     "What was gross margin growth in FY2025 Q2? Show the calculation."):
            with self.subTest(text=text):
                self.assertIsNone(requested_metric({"episode": {"request": text}}))
        request = {"episode": {"request": "請列出 FY2025 Q2 的毛利率和計算公式。"}}
        self.assertEqual(requested_metric(request), "gross_profit_margin")

    def test_a_directly_reported_rate_has_its_own_evidence(self):
        context = {"episode": {"request": QUESTION}}
        claim = {"text": "Operating margin was 45.46% in FY2025 Q2.", "calculation_ids": []}
        source = {"id": "rate", "kind": "financial_fact", "metric": "operating_income_margin", "unit": "%", "value": 45.46}
        self.assertEqual(list(defects(context, claim, [source], {"rate": source})), [])

    def test_percentage_multiplier_is_not_a_second_assertion_with_typed_records(self):
        calc = {"id": "margin", "metric": "operating_income_margin", "unit": "%", "operation": "margin",
                "value": 2354 / 6760 * 100}
        display = display_check({"text": "2,354 ÷ 6,760 × 100% = 34.82%."}, [calc])
        self.assertEqual(display["method"], "matched_records")
        self.assertEqual([shown["display"] for shown in display["matched"]], ["34.82%"])
        self.assertEqual(display_check({"text": "Operating margin was 100%."}, [calc])["method"], "mismatch")
