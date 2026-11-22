"""CLI engine integration using the ticket's figures and synthetic SEC/model responses."""
from http.client import IncompleteRead
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from tests.critic_fixture import fixture_verdicts
from tests.test_tools import DATA, HTTPBody
from verifin.engine import Budget, ResearchEngine, retry_instruction
from verifin.latest_quarter import requested
from verifin.model import COMMON_PROMPT, ROLE_PROMPTS, ModelClient, validate_response
from verifin.reporting import render_markdown, report_view
from verifin.storage import SessionStore
from verifin.release_summary import quarterly_cells
from verifin.tools import ToolRegistry


QUESTION = ("NVIDIA 最新已公布季度的營業利潤率是多少？請使用 SEC 官方數據，列出收入、營業利潤、"
            "財政年度、季度編號及計算公式，並提供可核實來源。")
ACCESSION = "0001045810-26-000088"
EXHIBIT = "q3fy27pr.htm"
URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000088/" + EXHIBIT
PRIMARY = "nvda-20261117.htm"
SEC_URLS = ["https://data.sec.gov/submissions/CIK0001045810.json", URL.replace(EXHIBIT, PRIMARY), URL]
ACQUISITION = ["discover_filings", "read_filing", "read_filing", "calculate"]
# Future report, unrelated later 8-K, earnings release, and an older quarterly filing.
FILINGS = {
    "accessionNumber": ["0001045810-26-000099", "0001045810-26-000089", ACCESSION, "0001045810-26-000070"],
    "filingDate": ["2026-11-23", "2026-11-19", "2026-11-17", "2026-08-20"],
    "reportDate": ["2026-10-25", "2026-11-18", "2026-11-17", "2026-07-26"],
    "form": ["10-Q", "8-K", "8-K", "10-Q"], "items": ["", "5.02,9.01", "2.02,9.01", ""],
    "primaryDocument": ["future.htm", "unrelated.htm", PRIMARY, "old.htm"]}
PARENT = f'<html><p>Item 2.02. Results of Operations.</p><a href="{EXHIBIT}">Exhibit 99.1</a></html>'
SUMMARY = """<html><h1>Third Quarter Fiscal 2027 Results</h1>
<p>NVIDIA reported revenue for the third quarter ended October 25, 2026.</p>
<table><tr><td colspan="3">GAAP</td></tr>
<tr><th>($ in millions, except per share data)</th><th>Q3 FY27</th><th>Q2 FY27</th></tr>
<tr><td>Revenue</td><td>$115,844</td><td>$99,000</td></tr>
<tr><td>Operating income</td><td>$77,520</td><td>$65,000</td></tr></table>
<table><tr><td>Non-GAAP</td></tr>
<tr><td>($ in millions)</td><td>Q3 FY27</td></tr>
<tr><td>Revenue</td><td>$115,844</td></tr><tr><td>Operating income</td><td>$85,555</td></tr></table></html>"""


class FixtureModel:
    def complete(self, role, ctx):
        if role == "planner":
            if ctx["episode"].get("reviewed"):
                return {"action": "finish"}
            # Reproduce the stale fiscal scope a planner might guess.
            return {"action": "tool", "tool": "financial_facts",
                    "arguments": {"entity": "NVDA", "metric": "Revenues", "period_end": "2025-01-26"},
                    "scope": {"period_end": "2025-01-26"}}
        reqs = [r["id"] for r in ctx["requirements"]]
        if role == "researcher":
            cells = {e["metric"]: e for e in ctx["evidence"] if e.get("fiscal_quarter")}
            income, revenue = (cells[k] for k in ("operating_income", "revenue"))
            margin, = ctx["calculations"]
            amounts = [c["value"] // c["printed_scale"] for c in (income, revenue)]
            evidence = [income["id"], revenue["id"]]
            year, q = income["fiscal_quarter"].split()
            sentences = [f"NVIDIA 的營業利潤率為 {margin['value']:.2f}%。",
                         f"收入為 {amounts[1]:,} 百萬美元。", f"營業利潤為 {amounts[0]:,} 百萬美元。",
                         f"財政年度為 {year}。",
                         f"{q} 截至 {income['period_end']}。",
                         f"{amounts[0]:,} ÷ {amounts[1]:,} × 100% = {margin['value']:.2f}%。",
                         f"SEC 附件 {income['source_url']}"]
            claims = [{"id": f"claim-{i}", "text": sentence, "constraints": {}, "requirement_ids": reqs,
                       "evidence_ids": evidence, "calculation_ids": [margin["id"]] if i in (0, 5) else []}
                      for i, sentence in enumerate(sentences)]
            response = {"summary": " ".join(sentences), "claims": claims, "answered_requirement_ids": reqs,
                        "unanswered_requirement_ids": []}
        else:
            response = {"issues": [], "reviewed_claim_ids": [c["id"] for c in ctx["claims"]],
                        "reviewed_requirement_ids": reqs,
                        "request_coverage": [{"part": part, "status": "answered", "claim_id": claim["id"],
                                              "answer_sentence": claim["text"]}
                                             for part, claim in zip(ctx["request_parts"], ctx["claims"], strict=True)]}
        return validate_response(role, fixture_verdicts(role, response, ctx), ctx)


class LatestQuarterTests(unittest.TestCase):
    def run_question(self, replies, model=None, *, bundled_alias=True, question=QUESTION):
        with TemporaryDirectory() as tmp, SessionStore(Path(tmp) / "test.sqlite") as store:
            sid = store.create_session()["id"]
            registry = ToolRegistry(store, DATA, True, "Offline tests tests@example.org", cache_dir=Path(tmp) / "cache")
            if not bundled_alias:
                # Preserve coverage of model-led identity resolution for catalogs without exact aliases.
                for company in registry.data.manifest["companies"]:
                    company.pop("aliases", None)
            with patch("verifin.data.build_opener") as opener:
                opener.return_value.open.side_effect = replies
                result = ResearchEngine(store, model or FixtureModel(), registry).ask(
                    sid, question, as_of="2026-11-21", budget=Budget(16, 16, 360, max_planner_steps=64))
                urls = [c.args[0].full_url for c in opener.return_value.open.call_args_list]
            events = [e for e in store.events(sid) if e["type"] == "tool_call"]
            return result["report"], events, urls

    @staticmethod
    def responses(filings=None):
        return [HTTPBody(json.dumps({"cik": 1045810, "filings": {"recent": filings or FILINGS}}).encode(), "application/json"),
                HTTPBody(PARENT.encode(), "text/html"), HTTPBody(SUMMARY.encode(), "text/html")]

    def test_original_question_and_reviewed_report(self):
        for bundled_alias in (True, False):
            with self.subTest(bundled_alias=bundled_alias):
                report, events, urls = self.run_question(self.responses(), bundled_alias=bundled_alias)
                self.assertEqual(report["status"], "complete", (report["stop_reason"], report["issues"]))
                expected = ([] if bundled_alias else ["discover_company"]) + ACQUISITION
                self.assertEqual([e["tool"] for e in events], expected)
                self.assertEqual(urls, SEC_URLS)
                self.assertEqual(len(report["request_coverage"]), 7)
                self.assertTrue(all(r["status"] == "answered" for r in report["request_coverage"]))
                self.assertTrue(report["verification_available"])
                self.assertNotIn("2025-01-26", str(report["scope"]))
                self.assertTrue(all(e["mode"] == "live" for e in report["evidence"]))
                self.assertAlmostEqual(report["calculations"][0]["value"], 77520 / 115844 * 100)
                for part in ("FY2027", "Q3", "2026-10-25", "77,520 ÷ 115,844 × 100% = 66.92%", URL):
                    self.assertIn(part, render_markdown(report))

    def test_release_also_supplies_the_quarter_when_10q_arrives_later(self):
        filings = {**FILINGS, "filingDate": ["2026-11-20", *FILINGS["filingDate"][1:]]}
        report, _, urls = self.run_question(self.responses(filings))
        self.assertEqual(report["status"], "complete")
        self.assertEqual(urls[-1], URL)
        self.assertIn("66.92%", render_markdown(report))

    def test_critic_length_stop_retains_draft_evidence_and_unreviewed_questions(self):
        class CriticClient(ModelClient):
            def complete(self, role, context):
                if role == "critic":
                    return super().complete(role, context)
                return FixtureModel().complete(role, context)

        client = CriticClient.from_env({"VERIFIN_MODEL": "qwen/qwen3.7-plus", "VERIFIN_API_KEY": "mock",
                                        "VERIFIN_BASE_URL": "https://openrouter.ai/api/v1",
                                        "VERIFIN_MAX_OUTPUT_TOKENS": "4096"})
        client._transport = Mock(return_value=json.dumps({
            "choices": [{"finish_reason": "length", "native_finish_reason": "length",
                         "message": {"role": "assistant", "content": ""}}],
            "usage": {"completion_tokens": 4096, "completion_tokens_details": {"reasoning_tokens": 4096}}
        }).encode())
        report, events, urls = self.run_question(self.responses(), client)
        client._transport.assert_called_once()
        self.assertEqual([e["tool"] for e in events], ACQUISITION)
        self.assertEqual(urls, SEC_URLS)
        self.assertEqual((report["status"], report["delivery_stage"], report["stop_reason"]),
                         ("partial", "draft_unverified", "execution_error"))
        self.assertEqual(report["critic_output_error"], "max_output_tokens")
        self.assertEqual(report["critic_max_output_tokens"], 4096)
        self.assertEqual(client.last_response_metadata["finish_reason"], "length")
        self.assertEqual(client.last_response_metadata["reasoning_effort"], "low")
        self.assertEqual(report["usage"]["reasoning_tokens"], 4096)
        self.assertFalse(report["verification_available"])
        self.assertEqual(len(report["request_parts"]), 7)
        self.assertEqual(report["request_coverage"], [])
        self.assertEqual({c["status"] for c in report["claims"]}, {"Pending Verification"})
        self.assertIn("77,520 ÷ 115,844 × 100% = 66.92%", report["summary"])
        self.assertIn(URL, {e.get("source_url") for e in report["evidence"]})
        self.assertAlmostEqual(report["calculations"][0]["value"], 77520 / 115844 * 100)
        self.assertIn("Critic 达到输出上限（4,096 token）", render_markdown(report))

    def test_interrupted_critic_reuses_the_original_draft_and_sec_records(self):
        roles, requests = [], []

        def transport(request, timeout):
            requests.append(request.data)
            if len(requests) == 1:
                raise IncompleteRead(b'{"choices":', 100)
            message = json.loads(request.data)["messages"][1]["content"]
            ctx = json.loads(message.split("\n", 1)[1])
            review = FixtureModel().complete("critic", ctx)
            return json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(review)}}]}).encode()

        critic = ModelClient("mock", "qwen/qwen3.7-plus", "https://example.test/v1", transport=transport)

        class InterruptedCritic(FixtureModel):
            def complete(self, role, ctx):
                roles.append(role)
                if role == "critic":
                    return critic.complete(role, ctx)
                return super().complete(role, ctx)

        report, events, urls = self.run_question(self.responses(), InterruptedCritic())
        self.assertEqual(report["status"], "complete", report["stop_reason"])
        self.assertEqual(roles.count("researcher"), 1)
        first = roles.index("critic")
        self.assertEqual(roles[first:first + 2], ["critic", "critic"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual([e["tool"] for e in events], ACQUISITION)
        self.assertEqual(urls, SEC_URLS)
        self.assertEqual(report["usage"]["model_calls"], len(roles))
        self.assertEqual([row["status"] for row in report["request_coverage"]], ["answered"] * 7)

    def test_natural_retry_reopens_a_failed_filing_without_using_the_403_as_evidence(self):
        for message, recovers in (("再试试", True), ("再試試", False)):
            with self.subTest(message=message), TemporaryDirectory() as tmp:
                database = Path(tmp) / "research.sqlite"
                cache = Path(tmp) / "cache"
                budget = Budget(16, 16, 60)
                with SessionStore(database) as store:
                    sid = store.create_session()["id"]
                first = None
                for question, deny in ((QUESTION, True), (message, not recovers)):
                    responses = self.responses()
                    if deny:
                        responses[-1] = HTTPError(URL, 403, "Forbidden", {}, None)
                    with SessionStore(database) as store, patch("verifin.data.build_opener") as opener:
                        registry = ToolRegistry(store, DATA, True, "Offline tests tests@example.org", cache_dir=cache)
                        opener.return_value.open.side_effect = responses
                        start = len(store.events(sid))
                        result = ResearchEngine(store, FixtureModel(), registry).ask(
                            sid, question, as_of="2026-11-21" if first is None else None, budget=budget)
                        report = result["report"]
                        calls = [e["tool"] for e in store.events(sid)[start:] if e["type"] == "tool_call"]
                        fetched = [c.args[0].full_url for c in opener.return_value.open.call_args_list]
                        self.assertEqual(fetched, SEC_URLS)
                        self.assertEqual(report["request"], QUESTION)
                        self.assertEqual(report["scope"]["cutoff"], "2026-11-21")
                        self.assertEqual(URL in registry.data.cached_urls(), not deny)
                        self.assertEqual(calls, ACQUISITION[:-1] if deny else ACQUISITION)
                        if first is not None:
                            self.assertEqual(store.get_report(sid, 1), first)
                        if deny:
                            self.assertEqual(report["stop_reason"], "latest_filing_unavailable")
                            self.assertIn("HTTP 403", render_markdown(report))
                            self.assertEqual(report["usage"]["model_calls"], 0)
                            self.assertFalse(report["calculations"])
                        else:
                            self.assertEqual(report["status"], "complete")
                            self.assertIn("66.92%", render_markdown(report))
                        first = report

    def test_retry_shortcuts_leave_changed_periods_and_sources_in_the_request(self):
        for text in ("重试", "重試", "请再试试。", "請再試試？"):
            self.assertTrue(retry_instruction(text))
        for text in ("再试试上一季", "再試試上一季", "再试试，改用公司公告", "再试试，这次只用公司官网的业绩公告。"):
            self.assertFalse(retry_instruction(text))

    def test_negative_source_margin_accepts_accounting_notation_and_rejects_a_sign_flip(self):
        class AccountingModel(FixtureModel):
            def __init__(self, division):
                self.division = division

            def complete(self, role, context):
                response = super().complete(role, context)
                if role == "researcher":
                    claim = response["claims"][5]
                    claim["text"] = claim["text"].replace("-77,520 ÷ 115,844", self.division)
                return response

        release = SUMMARY.replace("$77,520", "(77,520)", 1)
        cases = (("(77,520) ÷ 115,844", "answered"), ("（77,520） ÷ 115,844", "answered"),
                 ("(USD 77.520 billion) ÷ USD 115,844 million", "answered"), ("77,520 ÷ 115,844", "gap"))
        for division, expected in cases:
            with self.subTest(division=division):
                replies = self.responses()
                replies[-1] = HTTPBody(release.encode(), "text/html")
                report, events, urls = self.run_question(replies, AccountingModel(division))
                self.assertEqual([e["tool"] for e in events], ACQUISITION)
                self.assertEqual(urls, SEC_URLS)
                calc, = report["calculations"]
                records = {row["id"]: row for row in report["evidence"]}
                self.assertEqual(records[calc["operand_ids"][0]]["value"], -77_520_000_000)
                self.assertAlmostEqual(calc["value"], -77520 / 115844 * 100)
                answer = report_view(report)["question_answers"][5]
                self.assertEqual(answer["status"], expected, report["issues"])
                if expected == "answered":
                    self.assertEqual(report["status"], "complete", report["issues"])
                    self.assertIn(f"{division} × 100% = -66.92%", answer["answer_sentence"])
                    self.assertIn(URL, render_markdown(report))
                else:
                    self.assertEqual(report["status"], "partial")
                    self.assertNotIn("answer_sentence", answer)

    def test_exclusive_sec_request_still_acquires_and_answers_from_sec(self):
        question = QUESTION.replace("請使用 SEC 官方數據", "請只使用 SEC 官方數據")
        report, events, urls = self.run_question(self.responses(),question=question)
        self.assertEqual(report["status"],"complete",report["issues"])
        self.assertEqual(urls,SEC_URLS)
        self.assertEqual([e["tool"] for e in events],ACQUISITION)

    def test_formula_reference_is_bound_only_to_verified_operands_and_amounts(self):
        class MarginWriter(FixtureModel):
            def __init__(self, linked, wrong_rate):
                self.linked = linked
                self.wrong_rate = wrong_rate
                self.review_refs = {}

            def complete(self, role, ctx):
                response = super().complete(role, ctx)
                if role == "researcher":
                    for index in (0, 5):
                        claim = response["claims"][index]
                        if self.wrong_rate:
                            claim["text"] = claim["text"].replace("66.92%", "86.92%")
                        if not self.linked:
                            claim["calculation_ids"] = []
                elif role == "critic":
                    self.review_refs = {c["id"]: c["calculation_ids"] for c in ctx["claims"]}
                return response

        for linked in (True, False):
            with self.subTest(linked=linked):
                model = MarginWriter(linked, wrong_rate=True)
                report, events, _ = self.run_question(self.responses(), model)
                self.assertEqual(report["status"], "partial")
                answers = report_view(report)["question_answers"]
                self.assertEqual([a["status"] for a in answers],
                                 ["gap", "answered", "answered", "answered", "answered", "gap", "answered"])
                statuses = {c["id"]: c["status"] for c in report["claims"]}
                self.assertEqual(statuses["claim-0"], "Insufficient Evidence")
                self.assertEqual(statuses["claim-5"], "Insufficient Evidence")
                self.assertNotIn("86.92%", " ".join(a.get("answer_sentence", "") for a in answers))
                if not linked:
                    self.assertEqual(model.review_refs["claim-0"], [])
                    self.assertEqual(model.review_refs["claim-5"], [])

        model = MarginWriter(linked=False, wrong_rate=False)
        report, events, _ = self.run_question(self.responses(), model)
        self.assertEqual(report["status"], "complete", report["issues"])
        self.assertEqual([e["tool"] for e in events], ACQUISITION)
        refs = [report["calculations"][0]["id"]]
        self.assertEqual(model.review_refs["claim-0"], refs)
        self.assertEqual(model.review_refs["claim-5"], refs)

    def test_missing_and_conflicting_columns_are_not_operands(self):
        for invalid in (SUMMARY.replace("$115,844", "", 1),
                        SUMMARY.replace("Q3 FY27", "Q2 FY27"),
                        SUMMARY.replace("October 25, 2026", "December 25, 2026"),
                        SUMMARY.replace('colspan="3">GAAP', 'colspan="3">Non-GAAP'),
                        SUMMARY.replace("</table>", "<tr><td>Revenue</td><td>1</td><td>2</td></tr></table>", 1)):
            raw = invalid.encode()
            parser = ToolRegistry._filing_parser(raw, {}, collect_tables=True)
            self.assertEqual(quarterly_cells(parser.tables, {"text": parser.text(), "published_at": "2026-11-17", "source_document_id": "test",
                             "source_revision_authoritative": True, "snapshot_hash": "test"}), [])

    def test_researcher_finish_and_parse_failures_survive_delivery(self):
        planner = json.dumps({"action": "tool", "tool": "discover_company", "arguments": {"query": "NVIDIA"}})

        def completion(finish, content):
            return json.dumps({"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}],
                               "usage": {"completion_tokens": 4094, "completion_tokens_details": {"reasoning_tokens": 3861}}}).encode()

        for bundled_alias in (True, False):
            for finish, code, wording in (("length", "max_output_tokens", "达到输出上限"),
                                          ("stop", "invalid_researcher_json", "无法解析为 JSON")):
                with self.subTest(bundled_alias=bundled_alias, finish=finish):
                    roles = []

                    def reply(request, timeout):
                        prompt = json.loads(request.data)["messages"][0]["content"]
                        role, = [r for r in ("planner", "researcher")
                                 if prompt.startswith(COMMON_PROMPT + "\n" + ROLE_PROMPTS[r])]
                        roles.append(role)
                        return completion("stop", planner) if role == "planner" else completion(finish, "{")

                    model = ModelClient.from_env({"VERIFIN_MODEL": "qwen/qwen3.7-plus", "VERIFIN_BASE_URL": "https://example.test/v1",
                                                  "VERIFIN_API_KEY": "mock", "VERIFIN_MAX_OUTPUT_TOKENS": "4096"})
                    model._transport = Mock(side_effect=reply)
                    report, events, urls = self.run_question(self.responses(), model, bundled_alias=bundled_alias)
                    self.assertEqual(roles, ([] if bundled_alias else ["planner"]) + ["researcher"])
                    self.assertEqual([e["tool"] for e in events], ([] if bundled_alias else ["discover_company"]) + ACQUISITION)
                    self.assertEqual(urls, SEC_URLS)
                    self.assertEqual(report.get("researcher_output_error"), code, report["stop_reason"])
                    self.assertEqual(model.last_response_metadata["finish_reason"], finish)
                    self.assertEqual(model.last_response_metadata["max_output_tokens"], 4096)
                    self.assertEqual(report["delivery_stage"], "no_draft")
                    self.assertIn(wording, render_markdown(report))
                    self.assertEqual(len(report["calculations"]), 1)

    def test_latest_earnings_without_a_named_source_and_fixed_source_opt_out(self):
        self.assertTrue(requested("NVIDIA 最新已公布季度的營業利潤率是多少？"))
        self.assertTrue(requested("What was the latest reported quarter's operating margin?"))
        self.assertFalse(requested("Use only this press release for the latest quarterly SEC revenue."))
        self.assertFalse(requested("What does the transcript say about the latest reported quarter's earnings?"))
        self.assertFalse(requested("What were NVDA's operating margins in its last two fiscal years?"))

    def test_known_bare_concept_matches_qualified_concept_only(self):
        self.assertEqual(ToolRegistry._concepts("Revenues"), ToolRegistry._concepts("us-gaap:Revenues"))
        self.assertEqual(ToolRegistry._concepts("OperatingIncomeLoss"), [("us-gaap", "OperatingIncomeLoss")])
        with self.assertRaisesRegex(ValueError, "Unknown metric"):
            ToolRegistry._concepts("OperatingRevenueInvented")

    def test_failed_index_or_exhibit_discloses_gap(self):
        for prefix in ([], self.responses()[:2]):
            with self.subTest(successful_requests=len(prefix)):
                report, events, urls = self.run_question([*prefix, URLError("fixture outage")])
                self.assertEqual(report["status"], "partial")
                self.assertEqual(report["stop_reason"], "latest_filing_unavailable")
                self.assertIn("最新", report["skill_gap"])
                self.assertIn(report["skill_gap"], render_markdown(report))
                self.assertEqual(urls, SEC_URLS[:len(prefix) + 1])
                self.assertEqual([e["tool"] for e in events], ACQUISITION[:len(prefix) + 1])
                self.assertFalse(report["calculations"])

    def test_sec_refusal_identifies_which_source_could_not_be_verified(self):
        for step, label in enumerate(("申报目录", "申报的正文", "业绩附件")):
            with self.subTest(step=step):
                receipts = self.responses()[:step]
                receipts.append(HTTPError(URL, 403, "Forbidden", {}, None))
                report, events, urls = self.run_question(receipts)
                self.assertEqual(report["stop_reason"], "latest_filing_unavailable")
                self.assertFalse(report["calculations"])
                self.assertEqual(urls, SEC_URLS[:step + 1])
                self.assertEqual([e["tool"] for e in events], ACQUISITION[:step + 1])
                self.assertEqual(events[-1]["observation"]["data"]["http_status"], 403)
                self.assertIn(label, report["skill_gap"])
                self.assertIn("SEC 拒绝了本次访问（HTTP 403）", render_markdown(report))
