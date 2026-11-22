"""Disambiguate a disclosure-relative period and review the same evidence in E1."""

from unittest import TestCase

from verifin.evaluation import FrozenCase
from verifin.latest_quarter import acquire, requested
from verifin.skill_calculations import latest_reported_target, previous_quarter, targets_prior_quarter
from verifin.verification import check_rules

from tests.test_dated_latest_quarter import filing


QUESTION = "NVIDIA 最新已公布季度的上一季的營業利潤率是多少？請使用 SEC 官方數據。"
CUTOFF = "2026-11-21"
RELEASE = "0001045810-26-000088"
PREVIOUS = "0001045810-26-000075"


def filed_cells(accession, label, end, *, start=None, form="8-K"):
    directory = accession.replace("-", "")
    common = {
        "kind": "document" if form == "8-K" else "financial_fact", "form": form,
        "entity": "NVDA", "accession": accession, "fiscal_quarter": label,
        "period_start": start, "period_end": end, "snapshot_hash": "archive",
        "source_url": f"https://www.sec.gov/Archives/edgar/data/1045810/{directory}/source.htm",
        "published_at": "2026-11-18", "unit": "USD", "currency": "USD", "basis": "US-GAAP",
        "business_scope": "consolidated",
    }
    if form == "10-Q":
        common.update(cell_type="ixbrl_profit_cell", origin_context="quarter")
    else:
        common["period_quote"] = f"{label} ended {end}"
    return [{**common, "id": f"{accession}-{metric}", "metric": metric, "value": value}
            for metric, value in (("revenue", 100), ("operating_income", 60))]


def receipt(cells):
    return {"ok": True, "data": {"quarterly_gaap_operands": {r["metric"]: r["id"] for r in cells}}, "evidence": cells}


class RelativeQuarterTests(TestCase):
    def test_current_multiple_explicit_and_discourse_periods_stay_distinct(self):
        cases = {
            QUESTION: "previous",
            "NVIDIA 最近披露的财季的前一财季营业利润率": "previous",
            "What was the operating margin in the quarter immediately preceding the most recently disclosed fiscal quarter?": "previous",
            "What was the latest reported quarter’s previous quarter operating margin?": "previous",
            "NVIDIA 最新已公布季度的營業利潤率是多少？": "latest",
            "比較最新已公布季度和上一季的營業利潤率": "check_source",
            "NVIDIA 最新兩個財季的營業利潤率": "check_source",
            "Compare the latest reported quarter and previous quarter": "check_source",
            "NVIDIA FY2027 Q2 的營業利潤率，並核對最新公告": "check_source",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(latest_reported_target(question), expected)
        for followup in ("那上一季呢？", "上一財季的營業利潤率呢", "What was its prior quarter's margin?"):
            self.assertTrue(targets_prior_quarter(followup))
            self.assertFalse(requested(followup))
        self.assertEqual(previous_quarter("2027Q1"), "2026Q4")

    def test_q1_does_not_treat_an_annual_10k_as_previous_q4(self):
        route = acquire("NVDA", CUTOFF, QUESTION)
        self.assertEqual(next(route)["tool"], "discover_filings")
        current = filing("10-Q", RELEASE, "current.htm", "2026-04-26", "2026-05-18")
        annual = filing("10-K", PREVIOUS, "annual.htm", "2026-01-25", "2026-02-01")
        current_read = route.send({"data": {"snapshot": {"mode": "live"}, "filings": [current, annual]}})
        self.assertTrue(current_read["arguments"]["quarter_operands"])
        self.assertEqual(current_read["arguments"]["accession"], RELEASE)
        anchor = filed_cells(RELEASE, "FY2027 Q1", "2026-04-26", start="2026-01-26", form="10-Q")
        with self.assertRaises(StopIteration) as stopped:
            route.send(receipt(anchor))
        self.assertIn("2026Q4", stopped.exception.value)
        self.assertIn("未找到可唯一对应上一季的独立季度申报及精确金额", stopped.exception.value)

    def test_frozen_review_and_product_use_the_same_period_proof(self):
        catalog = {"id": "catalog", "kind": "source_metadata", "entity": "NVDA", "mode": "live",
                   "source_url": "https://data.sec.gov/submissions/CIK0001045810.json", "coverage_cutoff": CUTOFF,
                   "published_at": CUTOFF, "retrieved_at": "2026-11-21T11:00:00Z", "earnings_page_offset": 0,
                   "earnings_filings": [{"accession": RELEASE, "form": "8-K", "reportDate": "2026-11-18"}]}
        latest = filed_cells(RELEASE, "FY2027 Q3", "2026-10-25")
        prior = filed_cells(PREVIOUS, "FY2027 Q2", "2026-07-26", start="2026-04-27", form="10-Q")
        requirement = {"id": "request", "text": QUESTION}
        context = {"episode": {"request": QUESTION}, "scope": {"entities": ["NVDA"], "cutoff": CUTOFF},
                   "requirements": [requirement], "evidence": [catalog, *latest, *prior], "calculations": []}

        def period_issues(statement, citations, evidence=None):
            claim = {"id": "claim", "requirement_ids": ["request"], "text": statement,
                     "evidence_ids": [r["id"] for r in citations], "constraints": {}}
            candidate = {**context, "claims": [claim]}
            if evidence is not None:
                candidate["evidence"] = evidence
            frozen = FrozenCase.from_context("relative-quarter", candidate).context()
            self.assertNotIn("episode", frozen)
            live = [issue for issue in check_rules(candidate) if issue.get("scope") == "answer_period"]
            self.assertEqual(live, [issue for issue in check_rules(frozen) if issue.get("scope") == "answer_period"])
            return live

        wrong, = period_issues("NVIDIA FY2027 Q3 截至 2026-10-25。", latest)
        self.assertIn("2027Q2", wrong["missing_support"])
        self.assertFalse(period_issues("NVIDIA FY2027 Q2 截至 2026-07-26。", prior))
        missing, = period_issues("NVIDIA FY2027 Q3 截至 2026-10-25。", latest, [catalog, *latest])
        self.assertIn("尚未核实", missing["missing_support"])
        mislabeled, = period_issues("NVIDIA FY2027 Q3 截至 2026-07-26。", prior)
        self.assertIn("2027Q2", mislabeled["missing_support"])
        pending = {**catalog, "earnings_filings": [
            {"accession": "0001045810-26-000090", "form": "8-K", "reportDate": "2026-11-20"},
            *catalog["earnings_filings"]]}
        unresolved, = period_issues("NVIDIA FY2027 Q2 截至 2026-07-26。", prior, [pending, *latest, *prior])
        self.assertIn("尚未核实", unresolved["missing_support"])
