import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

from verifin.data import DataError, SnapshotData, available_by
from verifin.tools import ToolRegistry


DATA = Path(__file__).resolve().parents[1] / "data"


class MemoryStore:
    def __init__(self):
        self.context = {"session": {"scope_version": 1}, "evidence": [], "calculations": []}

    def get_context(self, session_id, **kwargs):
        if session_id != "session":
            raise KeyError("missing session")
        return copy.deepcopy(self.context)

    def save(self, result):
        for kind in ("evidence", "calculations"):
            self.context[kind].extend(dict(row, version=1) for row in result.get(kind, []))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.tools = ToolRegistry(self.store, DATA)

    def run_tool(self, name, args, scope=None, save=False):
        result = self.tools.execute(name, args, "session", scope or {})
        self.assertIsInstance(result["ok"], bool)
        self.assertIn("network_requests", result["data"])
        if save:
            self.store.save(result)
        return result

    def fact(self, metric="revenue", start="2024-10-01", end="2024-12-31", entity="MSFT"):
        result = self.run_tool("financial_facts", {"entity": entity, "metric": metric,
                "period_start": start, "period_end": end, "cutoff": "2025-02-28"}, save=True)
        self.assertTrue(result["ok"], result)
        return result["evidence"][0]

    def test_followup_catalog_tracks_current_published_forms_without_reading_bodies(self):
        data = self.tools.data
        example = data.manifest["documents"][0]
        scope = {"entities": [example["entity"]], "cutoff": "2025-04-08T12:30:00Z"}
        quarter = {**example, "form": "10-Q", "published_at": "2025-01-01"}
        annual = {**quarter, "source": "annual.htm", "form": "10-K", "published_at": scope["cutoff"]}
        files = {**data.manifest["files"], annual["source"]: {"url": "https://example.com/annual.htm"}}
        with patch.dict(data.manifest, documents=[quarter], files=files), patch.object(data, "raw", side_effect=AssertionError):
            original = self.tools.current_document_catalog(scope)
            data.manifest["documents"].append(annual)
            current = self.tools.current_document_catalog(scope)
            self.assertEqual(["10-K", "10-Q"], [r["form"] for r in current["forms"]])
            self.assertEqual(self.tools.current_document_catalog(scope), current)
            prior = self.tools.current_document_catalog({**scope, "cutoff": "2025-04-08T12:29:59Z"})
            self.assertEqual(prior["forms"], original["forms"])
            self.assertEqual(self.tools.current_document_catalog({**scope, "entities": ["ANOTHER"]})["forms"], [])

    def test_text_only_passages_do_not_offer_a_futile_calculator_reread(self):
        doc = self.tools.document_corpus().documents[0]
        ids = []
        for start in (0, 200):
            result = self.run_tool("read_document", {"entity":doc["entity"], "document_id":doc["id"],
                                   "offset":start, "limit_chars":200}, save=True)
            self.assertEqual(result["data"]["numeric_operand_status"], "text_only")
            ids.append(result["evidence"][0]["id"])
        failure = self.run_tool("calculate", {"operation":"margin", "operand_ids":ids})
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["data"]["reopen"], [])
        self.assertEqual(failure["data"]["numeric_operand_ids"], [])

    def test_calculation_cutoff_uses_original_sources_through_a_saved_aggregate(self):
        leaf = self.fact()
        other = dict(leaf, id="other", value=5, published_at="2025-01-10")
        self.store.save({"evidence":[other]})
        aggregate = {**leaf, "id":"aggregate", "operation":"sum", "operand_ids":[leaf["id"], other["id"]],
                     "value":leaf["value"]+other["value"], "published_at":"2025-01-12"}
        self.store.save({"calculations":[aggregate]})
        arguments = {"operation":"ratio", "operand_ids":["aggregate", "other"]}
        early = self.run_tool("calculate", arguments, {"cutoff":"2025-01-20"})
        self.assertFalse(early["ok"])
        self.assertIn("cutoff", early["error"])
        self.store.context["calculations"][0].pop("published_at")
        allowed = self.run_tool("calculate", arguments, {"cutoff":"2025-02-01"})
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual(leaf["published_at"], allowed["calculations"][0]["published_at"])

    def test_report_date_hint_keeps_cutoff_and_returns_no_evidence(self):
        query={"entity":"MSFT","query":"cloud","period_end":"2025-01-30"}
        later=self.run_tool("search_documents",{**query,"cutoff":"2025-02-01"})
        self.assertFalse(later["ok"])
        self.assertEqual(later["evidence"],[])
        self.assertEqual(later["data"]["available_report_dates"],["2024-12-31"])
        earlier=self.run_tool("search_documents",{**query,"cutoff":"2025-01-28"})
        self.assertNotIn("available_report_dates",earlier["data"])

    def test_open_local_disclosed_document_and_advance_without_crossing_cutoff(self):
        base={"query":"MSFT"}
        hidden=self.run_tool("discover_company",base,{"cutoff":"2025-01-28"})
        self.assertEqual(hidden["data"]["companies"][0]["replay_documents"],[])
        doc=self.run_tool("discover_company",base,{"cutoff":"2025-01-31"})["data"]["companies"][0]["replay_documents"][0]
        args={"entity":"MSFT","document_id":doc["document_id"],"limit_chars":300}
        first=self.run_tool("read_document",args,{"cutoff":"2025-01-31"})
        self.assertTrue(first["ok"])
        rec=first["evidence"][0]
        self.assertEqual(rec["text"],self.tools.data.raw(rec["text_snapshot_path"]).decode()[:300])
        self.assertEqual(first["data"]["next_offset"],300)
        for bad,scope in [({**args,"entity":"NVDA"},{}),(args,{"cutoff":"2025-01-28"})]:
            self.assertFalse(self.run_tool("read_document",bad,scope)["ok"])

    def test_discovery_metadata_is_citable_without_becoming_a_financial_source(self):
        from verifin.storage import SessionStore
        from verifin.verification import check_rules

        cutoff = {"cutoff": "2025-01-31"}
        result = self.run_tool("discover_company", {"query": "MSFT"}, cutoff)
        metadata, = result["evidence"]
        doc = result["data"]["companies"][0]["replay_documents"][0]
        self.assertEqual(metadata["id"], doc["metadata_evidence_id"])
        self.assertNotEqual(metadata["id"], doc["document_id"])
        self.assertEqual(metadata["kind"], "source_metadata")
        self.assertEqual(metadata["published_at"], cutoff["cutoff"])
        self.assertNotIn("value", metadata)
        self.assertEqual(metadata["snapshot_hash"], hashlib.sha256((DATA / "replay/manifest.json").read_bytes()).hexdigest())
        self.assertIn(doc["source_url"], metadata["text"])
        self.assertIn("Issuer earnings-release date", metadata["text"])
        self.assertFalse(self.run_tool("read_document", {"entity": "MSFT", "document_id": metadata["id"]}, cutoff)["ok"])
        self.assertFalse(self.run_tool("discover_company", {"query": "MSFT"}, {"cutoff": "2025-01-28"})["evidence"])
        self.store.save(result)
        revenue = self.fact()
        calculation = self.run_tool("calculate", {"operation": "difference", "operand_ids": [metadata["id"], revenue["id"]]})
        self.assertFalse(calculation["ok"])
        self.assertEqual(calculation["calculations"], [])
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            saved = store.put_evidence(sid, metadata)
            self.assertEqual(saved["id"], metadata["id"])
            with self.assertRaisesRegex(ValueError, "numeric operand"):
                store.put_evidence(sid, dict(metadata, value=100))
            context = store.get_context(sid)
            context["claims"] = [{"id": "c", "text": "Revenue was USD 100.", "evidence_ids": [saved["id"]],
                                  "calculation_ids": [], "requirement_ids": [], "constraints": {"metric": "revenue"},
                                  "asserted_value": 100, "asserted_unit": "USD"}]
            self.assertIn("numerical_inconsistency", {issue["issue_type"] for issue in check_rules(context)})
            claim = context["claims"][0]
            del claim["asserted_value"], claim["asserted_unit"]
            self.assertIn("numerical_inconsistency", {issue["issue_type"] for issue in check_rules(context)})
            claim.update(text="The issuer released its results on 2025-01-29.", constraints={})
            self.assertFalse(check_rules(context))

    def test_offline_document_only_profile_does_not_advertise_sec_calls(self):
        self.tools.data.manifest["companies"]=[{"ticker":"XYZ","cik":"0000000001","name":"Test"}]
        self.assertEqual({s["name"] for s in self.tools.available_tools()},
                         {"discover_company","search_documents","read_document","find_percentage_cells",
                          "calculate","analyze_disclosures","inspect_sales_comparison","reconcile_operating_margin","reconcile_net_income","read_income_explanation"})
        self.assertIn("discover_filings",{s["name"] for s in self.tools.list_tools()})

    def calc(self, op, a, b):
        return self.run_tool("calculate", {"operation": op, "operand_ids": [a["id"], b["id"]]}, save=True)

    def test_company_discovery_and_explicit_finite_coverage(self):
        r = self.run_tool("discover_company", {"query": "Microsoft"})
        self.assertEqual(r["data"]["companies"][0]["cik"], "0000789019")
        self.assertTrue(r["data"]["companies"][0]["replay_facts"])
        self.assertFalse(self.run_tool("discover_company", {"query": "Unseen issuer"})["ok"])

    def test_real_exact_fact_and_provenance(self):
        f = self.fact()
        self.assertEqual(f["value"], 69_632_000_000)
        self.assertEqual(f["published_at"], "2025-01-29T21:11:54.000Z")
        raw = self.tools.data.raw(f["snapshot_path"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), f["snapshot_hash"])
        self.assertIn("start=2024-10-01", f["locator"])
        self.assertEqual(f["duration_days"], 92)

    def test_same_record_id_and_canonical_metric_for_explicit_concept(self):
        alias = self.fact()
        explicit = self.fact("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")
        self.assertEqual(alias["id"], explicit["id"])
        self.assertEqual(explicit["metric"], "revenue")

    def test_cutoff_prevents_future_filing_use(self):
        r = self.run_tool("financial_facts", {"entity": "MSFT", "metric": "revenue", "period_start": "2024-10-01",
                                            "period_end": "2024-12-31", "cutoff": "2025-01-29T21:00:00Z"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["evidence"], [])

    def test_tool_cannot_widen_information_cutoff_behind_scope(self):
        for name, args in [
            ("financial_facts", {"entity": "MSFT", "metric": "revenue", "period_start": "2024-10-01", "period_end": "2024-12-31"}),
            ("search_documents", {"entity": "MSFT", "query": "AI"}),
            ("market_prices", {"entity": "MSFT", "start_date": "2025-01-29", "end_date": "2025-01-30"}),
            ("discover_filings", {"entity": "MSFT"}),
            ("read_filing", {"entity": "MSFT", "accession": "0000950170-25-010491", "primary_document": "msft-20241231.htm"}),
        ]:
            with self.subTest(name=name):
                result = self.run_tool(name, dict(args, cutoff="2025-02-01"), {"cutoff": "2025-01-28"})
                self.assertFalse(result["ok"])
                self.assertIn("cannot extend", result["summary"])
                self.assertEqual(result["data"]["network_requests"], 0)

    def test_incomplete_period_returns_discovery_not_annual_substitution(self):
        r = self.run_tool("financial_facts", {"entity": "MSFT", "metric": "revenue", "period_end": "2024-12-31"})
        self.assertEqual(r["evidence"], [])
        starts = {x["period_start"] for x in r["data"]["available_periods"]}
        self.assertIn("2024-10-01", starts)
        self.assertIn("2024-07-01", starts)

    def test_scope_entity_and_dates_are_defaults(self):
        r = self.run_tool("financial_facts", {"metric": "revenue"},
                          {"entities": ["MSFT"], "period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-01-31"})
        self.assertEqual(r["evidence"][0]["value"], 69_632_000_000)
        self.assertFalse(self.run_tool("financial_facts", {"metric": "revenue"}, {"entities": ["MSFT", "NVDA"]})["ok"])

    def test_instant_fact_uses_end_date_without_start(self):
        r = self.run_tool("financial_facts", {"entity": "MSFT", "metric": "assets", "period_end": "2024-12-31", "cutoff": "2025-02-01"})
        self.assertTrue(r["ok"])
        self.assertIsNone(r["evidence"][0]["period_start"])
        scoped = self.run_tool("financial_facts", {"entity": "MSFT", "metric": "assets"},
                               {"period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-02-01"})
        self.assertTrue(scoped["ok"])
        self.assertIsNone(scoped["evidence"][0]["period_start"])

    def test_margin_growth_and_points_use_saved_records(self):
        rev = self.fact()
        inc = self.fact("operating_income")
        prev_rev = self.fact(start="2023-10-01", end="2023-12-31")
        prev_inc = self.fact("operating_income", "2023-10-01", "2023-12-31")
        margin = self.calc("margin", inc, rev)
        self.assertTrue(margin["ok"], margin)
        self.assertAlmostEqual(margin["calculations"][0]["value"], 45.45754825367647)
        prev_margin = self.calc("margin", prev_inc, prev_rev)
        change = self.calc("percentage_point_change", margin["calculations"][0], prev_margin["calculations"][0])
        self.assertTrue(change["ok"], change)
        self.assertEqual(change["calculations"][0]["unit"], "percentage_points")
        self.assertGreater(change["calculations"][0]["value"], 0)
        growth = self.calc("growth", rev, prev_rev)
        self.assertAlmostEqual(growth["calculations"][0]["value"], (69_632 / 62_020 - 1) * 100)
        self.assertEqual(growth["calculations"][0]["operand_versions"], {rev["id"]: 1, prev_rev["id"]: 1})

    def test_derive_quarter_and_rounding_warning(self):
        full = self.fact("revenue", "2024-01-29", "2025-01-26", "NVDA")
        ytd = self.fact("revenue", "2024-01-29", "2024-10-27", "NVDA")
        r = self.calc("derive_quarter", full, ytd)
        self.assertTrue(r["ok"], r)
        calc = r["calculations"][0]
        self.assertEqual(calc["value"], 39_331_000_000)
        self.assertEqual(calc["period_start"], "2024-10-28")
        self.assertIn("rounding", calc["warnings"][0])

    def test_cumulative_quarter_comparison_refused(self):
        q = self.fact()
        half = self.fact(start="2023-07-01", end="2023-12-31")
        self.assertFalse(self.calc("growth", q, half)["ok"])

    def test_margin_mismatched_period_refused(self):
        current = self.fact("operating_income")
        prior = self.fact(start="2023-10-01", end="2023-12-31")
        self.assertFalse(self.calc("margin", current, prior)["ok"])

    def test_signed_tax_scenario_preserves_the_common_earnings_comparison(self):
        from verifin.financial import COMMON_INCOME, TAX_SCENARIO, VALUATION_ALLOWANCE_EXPENSE
        from verifin.verification import check_rules

        base = self.fact()
        common = dict(base, id="common", metric=COMMON_INCOME, concept=COMMON_INCOME, value=300)
        item = dict(base, id="allowance", metric=VALUATION_ALLOWANCE_EXPENSE,
                    concept=VALUATION_ALLOWANCE_EXPENSE, value=-75)
        prior = dict(common, id="prior", period_start="2023-10-01", period_end="2023-12-31", value=200)
        self.store.save({"evidence":[common, item, prior]})
        refused = self.calc("difference", common, item)
        self.assertTrue(refused["data"]["recoverable_input_error"])
        self.assertIn(TAX_SCENARIO, refused["error"])
        self.assertEqual(refused["calculations"], [])
        scenario = self.calc(TAX_SCENARIO, common, item)["calculations"][0]
        self.assertEqual(scenario["value"], 225)
        self.assertNotEqual(scenario["basis"], common["basis"])
        self.assertEqual(self.calc("difference", scenario, prior)["calculations"][0]["value"], 25)
        growth = self.calc("growth", scenario, prior)["calculations"][0]
        self.assertEqual(growth["value"], 12.5)
        context = self.store.get_context("session")
        context["claims"] = [{"id":"claim:growth", "text":"单项试算后仍高于上年已报普通股利润。",
                              "constraints":{}, "requirement_ids":[], "evidence_ids":[],
                              "calculation_ids":[growth["id"]]}]
        self.assertFalse(check_rules(context))
        context["calculations"][0]["value"] = 375
        self.assertIn("numerical_inconsistency", {i["issue_type"] for i in check_rules(context)})
        context["calculations"][0]["operation"] = "difference"
        self.assertIn("context_mismatch", {i["issue_type"] for i in check_rules(context)})

        charge = dict(item, id="increase", value=75)
        self.store.save({"evidence":[charge]})
        self.assertEqual(self.calc(TAX_SCENARIO, common, charge)["calculations"][0]["value"], 375)
        self.assertFalse(self.calc(TAX_SCENARIO, item, common)["ok"])
        self.assertFalse(self.calc("growth", scenario, base)["ok"])
        for field, value in (("entity", "OTHER"), ("period_start", "2024-01-01"), ("unit", "USD/shares"),
                             ("concept", "us-gaap:IncomeTaxExpenseBenefit"), ("business_scope", "subsidiary")):
            with self.subTest(field=field):
                changed = dict(item, id="mismatch-" + field, **{field:value})
                self.store.save({"evidence":[changed]})
                rejected = self.calc(TAX_SCENARIO, common, changed)
                self.assertFalse(rejected["ok"])
                self.assertTrue(rejected["data"]["recoverable_input_error"])

    def test_calculator_internal_failure_does_not_request_input_retry(self):
        with patch.object(self.store, "get_context", side_effect=OSError("cannot read session")):
            result = self.run_tool("calculate", {"operation":"growth", "operand_ids":["a","b"]})
        self.assertFalse(result["ok"])
        self.assertNotIn("recoverable_input_error", result["data"])

    def test_zero_negative_nonfinite_and_stale_are_refused(self):
        a = self.fact()
        b = self.fact(start="2023-10-01", end="2023-12-31")
        for value in (0, -1, float("nan"), float("inf"), True, "1"):
            with self.subTest(value=value):
                self.store.context["evidence"][1]["value"] = value
                self.assertFalse(self.calc("growth", a, b)["ok"])
        self.store.context["evidence"][1]["value"] = b["value"]
        self.store.context["evidence"][1]["stale"] = True
        self.assertFalse(self.calc("growth", a, b)["ok"])

    def test_business_scope_currency_and_scale_mismatch_refused(self):
        a = self.fact("operating_income")
        b = self.fact()
        original = copy.deepcopy(self.store.context["evidence"][1])
        for key, value in (("business_scope", "Cloud"), ("currency", "EUR"), ("unit", "EUR"), ("scale", 1_000_000), ("basis", "non-GAAP")):
            with self.subTest(key=key):
                self.store.context["evidence"][1] = dict(original, **{key: value})
                self.assertFalse(self.calc("margin", a, b)["ok"])

    def test_calculation_is_session_bound_and_no_eval(self):
        self.assertFalse(self.run_tool("calculate", {"operation": "ratio", "operand_ids": ["unknown", "also_missing"]})["ok"])
        self.assertFalse(self.run_tool("calculate", {"operation": "__import__('os')", "operand_ids": ["a", "b"]})["ok"])

    def test_document_search_returns_exact_source_span_and_correct_time(self):
        r = self.run_tool("search_documents", {"entity": "MSFT", "query": "Microsoft Cloud gross margin AI infrastructure", "period_end": "2024-12-31", "limit": 3, "cutoff": "2025-01-31"})
        self.assertTrue(r["ok"])
        passages = [e for e in r["evidence"] if e["kind"] == "document"]
        self.assertTrue(any("scaling our AI infrastructure" in e["text"] for e in passages))
        for e in passages:
            start, end = map(int, re.search(r"characters\[(\d+):(\d+)\]", e["locator"]).groups())
            source = self.tools.data.raw(e["text_snapshot_path"]).decode()
            self.assertEqual(source[start:end], e["text"])
        for e in r["evidence"]:
            if e["kind"] == "financial_fact":
                self.assertIn(e["id"], r["data"]["numeric_operand_ids"])
                self.assertTrue(e["locator"])
        none = self.run_tool("search_documents", {"entity": "MSFT", "query": "Cloud", "cutoff": "2025-01-29T12:00:00Z"})
        self.assertFalse(none["ok"])

    def test_prices_and_descriptive_return(self):
        r = self.run_tool("market_prices", {"entity": "MSFT", "start_date": "2025-01-29", "end_date": "2025-01-30", "cutoff": "2025-01-31"}, save=True)
        self.assertTrue(r["ok"])
        result = self.calc("price_return", r["evidence"][1], r["evidence"][0])
        self.assertAlmostEqual(result["calculations"][0]["value"], (414.989990234375 / 442.3299865722656 - 1) * 100)
        self.assertFalse(self.run_tool("market_prices", {"entity": "MSFT", "start_date": "2025-01-25", "end_date": "2025-01-30"})["ok"])

    def test_schema_rejects_null_unknown_boolean_limit_and_bad_dates(self):
        for args in ({"query": None}, {"query": "MSFT", "secret": "x"}):
            self.assertFalse(self.run_tool("discover_company", args)["ok"])
        self.assertFalse(self.run_tool("search_documents", {"entity": "MSFT", "query": "margin", "limit": True})["ok"])
        self.assertFalse(self.run_tool("financial_facts", {"entity": "MSFT", "metric": "revenue", "period_end": "2025-02-30"})["ok"])
        self.assertFalse(self.run_tool("financial_facts", {"entity": "MSFT", "metric": "revenue", "cutoff": "2025-01-01T12:00:00"})["ok"])


class DataSafetyTests(unittest.TestCase):
    def setUp(self):
        SnapshotData._last_sec_request_at = 0

    def test_date_only_publication_is_conservative(self):
        self.assertFalse(available_by("2025-01-29", "2025-01-29T23:00:00Z"))
        self.assertTrue(available_by("2025-01-29", "2025-01-29"))
        self.assertTrue(available_by("2025-01-29T21:00:00Z", "2025-01-29T22:00:00Z"))

    def test_snapshot_hash_and_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.json").write_text("{}")
            (root / "manifest.json").write_text(json.dumps({"files": {"x.json": {"sha256": "bad"}}, "companies": [], "documents": []}))
            data = SnapshotData(root)
            with self.assertRaisesRegex(DataError, "integrity"):
                data.raw("x.json")
            with self.assertRaisesRegex(DataError, "escapes"):
                data.raw("../secret")

    def test_network_requires_opt_in_and_identifying_agent(self):
        data = SnapshotData(DATA)
        with self.assertRaises(DataError):
            data.get_sec_json("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
        data.allow_network = True
        with self.assertRaisesRegex(DataError, "User-Agent"):
            data.get_sec_json("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
        data.user_agent = "VeriFin tests test@example.org"
        with self.assertRaisesRegex(DataError, "SEC endpoints"):
            data.get_sec_json("https://evil.example/companyfacts.json")
        self.assertEqual(data.network_requests, 0)

    def test_network_failure_is_counted_once_without_retry(self):
        tools = ToolRegistry(MemoryStore(), DATA, True, "VeriFin tests test@example.org")
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.side_effect = URLError("offline")
            result = tools.execute("financial_facts", {"entity": "320193", "metric": "revenue", "source": "live"}, "session", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["network_requests"], 1)
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_unseen_issuer_uses_generic_sec_fact_logic(self):
        # A synthetic third issuer tests mechanics only; it is not a research result.
        payload = {"cik": 1234567, "entityName": "Synthetic Test Issuer", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2024-01-01", "end": "2024-03-31", "val": 120, "filed": "2024-05-01", "accn": "0001234567-24-000001", "form": "10-Q"}
        ]}}}}}
        with tempfile.TemporaryDirectory() as tmp:
            tools = ToolRegistry(MemoryStore(), tmp, True, "VeriFin tests test@example.org")
            with patch("verifin.data.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
                r = tools.execute("financial_facts", {"entity": "1234567", "metric": "revenue", "period_start": "2024-01-01", "period_end": "2024-03-31", "cutoff": "2024-05-02"}, "session", {})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["evidence"][0]["value"], 120)
            self.assertEqual(r["evidence"][0]["entity"], "CIK0001234567")
            self.assertEqual(r["data"]["network_requests"], 1)
            self.assertTrue(Path(r["evidence"][0]["snapshot_path"]).is_relative_to(Path(tmp).resolve()))

    def test_json_loads_do_not_share_mutable_payloads(self):
        data = SnapshotData(DATA)
        path = "snapshots/sec_msft_submissions.json"
        first = data.json(path)
        first["tickers"].append("MUTATED")
        self.assertNotIn("MUTATED", data.json(path)["tickers"])

    def test_concurrent_http_accounting_and_timeouts_are_isolated(self):
        entered = threading.Event()
        release = threading.Event()
        payload = json.dumps({"cik": 1234567, "facts": {}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            tools = ToolRegistry(MemoryStore(), tmp, True, "VeriFin tests test@example.org")

            def response(*args, **kwargs):
                entered.set()
                self.assertEqual(kwargs["timeout"], 7)
                release.wait(3)
                return io.BytesIO(payload)

            def first():
                tools.timeout = 7
                return tools.execute("financial_facts", {"entity": "1234567", "metric": "revenue"}, "session", {})

            with patch("verifin.data.build_opener") as opener, ThreadPoolExecutor(max_workers=2) as pool:
                opener.return_value.open.side_effect = response
                ongoing = pool.submit(first)
                self.assertTrue(entered.wait(3))
                tools.timeout = 3
                second = tools.execute("financial_facts", {"entity": "1234567", "metric": "revenue"}, "session", {})
                release.set()
                first_result = ongoing.result(3)
            self.assertEqual(first_result["data"]["network_requests"], 1)
            self.assertEqual(second["data"]["network_requests"], 0)
            self.assertIn("rate limited", second["summary"])
            self.assertEqual(tools.timeout, 3)


class FactSelectionTests(unittest.TestCase):
    """Small SEC-shaped inputs isolate conflicts/revisions rather than preset answers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = MemoryStore()

    def tearDown(self):
        self.tmp.cleanup()

    def registry(self, facts, accepted=None):
        raw = json.dumps({"cik": 1234567, "facts": {"us-gaap": {"Revenues": {"units": {"USD": facts}}}}}).encode()
        filings = accepted or {}
        subs = json.dumps({"filings": {"recent": {"accessionNumber": list(filings), "acceptanceDateTime": list(filings.values())}}}).encode()
        (self.root / "facts.json").write_bytes(raw)
        (self.root / "submissions.json").write_bytes(subs)
        files = {name: {"sha256": hashlib.sha256(value).hexdigest(), "url": "https://data.sec.gov/" + name,
                        "retrieved_at": "2026-10-08T00:00:00Z", "bytes": len(value)}
                 for name, value in [("facts.json", raw), ("submissions.json", subs)]}
        manifest = {"companies": [{"ticker": "TEST", "name": "Synthetic issuer", "cik": "0001234567",
                                   "facts": "facts.json", "submissions": "submissions.json"}], "documents": [], "files": files}
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        return ToolRegistry(self.store, self.root)

    @staticmethod
    def fact(value=120, filed="2025-01-29", accn="0001234567-25-000001"):
        return {"start": "2024-10-01", "end": "2024-12-31", "val": value, "filed": filed, "accn": accn, "form": "10-Q"}

    def get(self, tools, cutoff="2025-02-01"):
        return tools.execute("financial_facts", {"entity": "TEST", "metric": "revenue", "period_start": "2024-10-01",
                             "period_end": "2024-12-31", "cutoff": cutoff}, "session", {})

    def test_same_logical_fact_id_survives_snapshot_and_filing_change(self):
        first = self.get(self.registry([self.fact()]))["evidence"][0]
        updated = self.get(self.registry([self.fact(121, "2025-01-30", "0001234567-25-000002")]))["evidence"][0]
        self.assertEqual(first["id"], updated["id"])
        self.assertNotEqual(first["snapshot_hash"], updated["snapshot_hash"])
        self.assertNotEqual(first["accession"], updated["accession"])

    def test_refreshed_logical_fact_invalidates_sqlite_dependents(self):
        from verifin.storage import SessionStore
        first = self.get(self.registry([self.fact()]))["evidence"][0]
        second = self.get(self.registry([self.fact(121, "2025-01-30", "0001234567-25-000002")]))["evidence"][0]
        store = SessionStore(self.root / "session.sqlite")
        try:
            session = store.create_session()
            sid = session["id"]
            store.put_evidence(sid, first)
            prior = dict(first, id="prior", value=100, period_start="2023-10-01", period_end="2023-12-31")
            store.put_evidence(sid, prior)
            store.put_calculation(sid, {"id": "growth", "operation": "growth", "operand_ids": [first["id"], "prior"],
                                        "value": 20, "unit": "%", "formula": "(current/prior-1)*100"})
            store.put_claim(sid, {"id": "claim", "text": "Revenue grew 20%.", "evidence_ids": [],
                                  "calculation_ids": ["growth"], "requirement_ids": []})
            store.put_evidence(sid, second)
            context = store.get_context(sid)
            self.assertTrue(context["calculations"][0]["stale"])
            self.assertTrue(context["claims"][0]["stale"])
            self.assertEqual(next(e for e in context["evidence"] if e["id"] == first["id"])["version"], 2)
        finally:
            store.close()

    def test_latest_eligible_filing_and_historical_cutoff(self):
        tools = self.registry([self.fact(), self.fact(121, "2025-02-01", "0001234567-25-000002")])
        self.assertEqual(self.get(tools, "2025-01-31")["evidence"][0]["value"], 120)
        self.assertEqual(self.get(tools, "2025-02-01")["evidence"][0]["value"], 121)

    def test_conflicting_same_day_unknown_order_never_silently_selects(self):
        first, second = self.fact(), self.fact(121, accn="0001234567-25-000002")
        tools = self.registry([first, second], {first["accn"]: "2025-01-29T22:00:00Z"})
        result = self.get(tools)
        self.assertFalse(result["ok"])
        self.assertEqual(result["evidence"], [])
        self.assertTrue(result["data"]["same_day_order_uncertain"])
        self.assertEqual({r["value"] for r in result["data"]["candidates"]}, {120, 121})

    def test_known_same_day_publication_order_uses_later_filing(self):
        first, second = self.fact(), self.fact(121, accn="0001234567-25-000002")
        tools = self.registry([first, second], {first["accn"]: "2025-01-29T21:00:00Z", second["accn"]: "2025-01-29T22:00:00Z"})
        self.assertEqual(self.get(tools)["evidence"][0]["value"], 121)

    def test_conflicting_rows_in_same_accession_refused(self):
        result = self.get(self.registry([self.fact(), self.fact(121)]))
        self.assertFalse(result["ok"])
        self.assertTrue(result["data"]["conflicting_values"])

    def test_duplicate_equal_rows_are_deduplicated(self):
        result = self.get(self.registry([self.fact(), self.fact(), self.fact(accn="0001234567-25-000002")]))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["evidence"]), 1)


class HTTPBody(io.BytesIO):
    def __init__(self, data, content_type="text/html; charset=utf-8"):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class FilingToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "separate-live-cache"
        self.tools = ToolRegistry(MemoryStore(), DATA, True, "VeriFin tests test@example.org", cache_dir=self.cache)
        SnapshotData._last_sec_request_at = 0
        self.args = {"entity": "MSFT", "accession": "0000950170-25-010491", "primary_document": "msft-20241231.htm", "cutoff": "2025-01-31"}

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, name, args):
        return self.tools.execute(name, args, "session", {})

    def html(self):
        return ("<!DOCTYPE html><html><head><script>SECRET_SCRIPT</script><style>SECRET_CSS</style></head><body>"
                "<ix:hidden>HIDDEN_XBRL</ix:hidden><h1>Form 10-Q</h1>"
                + "<p>General information about the issuer.</p>" * 70
                + "<p>Revenue increased and operating income grew. Research must inspect the source.</p>"
                + "</body></html>").encode()

    def test_replay_filing_discovery_preserves_original_metadata(self):
        result = self.execute("discover_filings", {"entity": "MSFT", "forms": ["10-Q"], "report_date": "2024-12-31", "cutoff": "2025-01-31"})
        self.assertTrue(result["ok"], result)
        record = result["data"]["filings"][0]
        self.assertEqual(record["accession"], "0000950170-25-010491")
        self.assertEqual(record["primaryDocument"], "msft-20241231.htm")
        self.assertEqual(record["primary_document"], record["primaryDocument"])
        self.assertEqual(record["url"], "https://www.sec.gov/Archives/edgar/data/789019/000095017025010491/msft-20241231.htm")
        self.assertEqual(record["reportDate"], "2024-12-31")
        self.assertNotIn("fiscal_quarter", record)
        self.assertEqual(result["data"]["network_requests"], 0)
        blocked = self.execute("discover_filings", {"entity": "MSFT", "forms": ["10-Q"], "report_date": "2024-12-31", "cutoff": "2025-01-29T21:00:00Z"})
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["evidence"], [])
        metadata, = result["evidence"]
        self.assertEqual(metadata["id"], record["metadata_evidence_id"])
        self.assertEqual(metadata["source_url"], result["data"]["snapshot"]["url"])
        self.assertEqual(metadata["locator"], record["metadata_locator"])
        self.assertIn("filingDate " + record["filingDate"], metadata["text"])
        self.assertIn("acceptanceDateTime " + record["acceptanceDateTime"], metadata["text"])
        self.assertFalse(available_by(metadata["published_at"], record["acceptanceDateTime"]))
        self.assertNotIn("period_start", metadata)

    def test_filing_metadata_preview_remains_bounded_and_pages_to_other_rows(self):
        query = {"entity": "MSFT", "forms": ["10-Q", "8-K"], "cutoff": "2025-01-31", "limit": 50}
        result = self.execute("discover_filings", query)
        page = result["data"]["filings"]
        self.assertGreater(len(page), 4)
        metadata, = result["evidence"]
        self.assertEqual(metadata["kind"], "source_metadata")
        self.assertIn("rows 1–4 by newest publication first", metadata["text"])
        self.assertIn("this recent page only", metadata["text"])
        self.assertNotIn(page[4]["accession"], metadata["text"])
        self.assertNotIn("metadata_evidence_id", page[4])
        later = self.execute("discover_filings", dict(query, offset=4, limit=1))
        self.assertIn(page[4]["accession"], later["evidence"][0]["text"])
        self.assertNotEqual(later["evidence"][0]["id"], metadata["id"])
        self.assertEqual(metadata["id"], self.execute("discover_filings", query)["evidence"][0]["id"])

    def test_live_discovery_makes_only_one_request_and_preserves_history_limit(self):
        payload = {"cik": "0001234567", "filings": {"recent": {
            "accessionNumber": ["0001234567-25-000001"], "form": ["10-Q"], "reportDate": ["2024-12-31"],
            "filingDate": ["2025-01-29"], "acceptanceDateTime": ["2025-01-29T21:00:00Z"], "primaryDocument": ["test.htm"]},
            "files": [{"name": "CIK0001234567-submissions-001.json", "filingCount": 1000}]}}
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(json.dumps(payload).encode(), "application/json")
            result = self.execute("discover_filings", {"entity": "1234567", "source": "live", "cutoff": "2025-01-30"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["network_requests"], 1)
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(result["data"]["filings"][0]["entity"], "CIK0001234567")
        self.assertEqual(len(result["data"]["history_files_not_fetched"]), 1)

    def test_read_filing_does_not_fetch_unmatched_identity_or_unsafe_filename(self):
        with patch("verifin.data.build_opener") as opener:
            for changes in ({"primary_document": "../other.htm"}, {"primary_document": "other.htm"}, {"accession": "garbage"},
                            {"entity": "1234567", "accession": "0001234567-25-000001", "primary_document": "test.htm"}):
                with self.subTest(changes=changes):
                    result = self.execute("read_filing", dict(self.args, **changes))
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["data"]["network_requests"], 0)
            opener.assert_not_called()

    def test_read_filing_cutoff_and_missing_replay_never_fetch(self):
        with patch("verifin.data.build_opener") as opener:
            early = self.execute("read_filing", dict(self.args, cutoff="2025-01-29T20:00:00Z"))
            self.assertFalse(early["ok"])
            replay = self.execute("read_filing", dict(self.args, source="replay"))
            self.assertFalse(replay["ok"])
            self.assertIn("access-failure", replay["summary"])
            opener.assert_not_called()

    def test_html_extracts_exact_safe_span_with_hash_and_separate_cache(self):
        raw = self.html()
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(raw)
            result = self.execute("read_filing", dict(self.args, limit_chars=200))
        self.assertTrue(result["ok"], result)
        e = result["evidence"][0]
        full_text = Path(e["text_snapshot_path"]).read_text()
        self.assertEqual(e["text"], full_text[:200])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), e["snapshot_hash"])
        self.assertTrue(Path(e["snapshot_path"]).is_relative_to(self.cache.resolve()))
        for secret in ["SECRET_SCRIPT", "SECRET_CSS", "HIDDEN_XBRL"]:
            self.assertNotIn(secret, full_text)
        self.assertIsNone(e["period_start"])
        self.assertIsNone(e["period_end"])
        self.assertEqual(e["filing_report_date"], "2024-12-31")
        self.assertEqual(result["data"]["network_requests"], 1)
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_cached_queries_and_pagination_use_no_http(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(self.html())
            first = self.execute("read_filing", dict(self.args, limit_chars=200))
            query = self.execute("read_filing", dict(self.args, query="revenue operating income", limit_chars=400))
            page = self.execute("read_filing", dict(self.args, offset=first["data"]["next_offset"], limit_chars=200))
            missing = self.execute("read_filing", dict(self.args, query="zzzzzzzzzzzz"))
        self.assertTrue(query["ok"], query)
        self.assertIn("operating income", query["evidence"][0]["text"])
        self.assertEqual(query["data"]["network_requests"], 0)
        self.assertEqual(query["evidence"][0]["mode"], "cached_live")
        full_text = Path(first["evidence"][0]["text_snapshot_path"]).read_text()
        self.assertEqual(page["evidence"][0]["text"], full_text[200:400])
        self.assertFalse(missing["ok"])
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_returned_filing_metadata_cannot_mutate_cached_publication(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(self.html())
            result = self.execute("read_filing", self.args)
            result["data"]["filing"]["published_at"] = "2000-01-01"
            blocked = self.execute("read_filing", dict(self.args, cutoff="2025-01-28"))
        self.assertFalse(blocked["ok"])
        self.assertIn("not available", blocked["summary"])

    def test_source_observation_is_returned_even_when_query_has_no_match(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(self.html())
            result = self.execute("read_filing", dict(self.args, query="zzzzzzzzzzzz"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["evidence"], [])
        observation = result["data"]["source_observation"]
        self.assertTrue(observation["source_revision_authoritative"])
        self.assertEqual(observation["source_revision"], hashlib.sha256(self.html()).hexdigest())
        self.assertEqual(observation["source_observed_at"], result["data"]["snapshot"]["retrieved_at"])

    def test_cached_observation_keeps_original_http_timestamp_and_historical_replay_is_separate(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(self.html())
            current = self.execute("read_filing", dict(self.args, limit_chars=200))
            cached = self.execute("read_filing", dict(self.args, limit_chars=200))
            historical = self.execute("read_filing", dict(self.args, limit_chars=200, source="replay"))
        first = current["evidence"][0]
        self.assertEqual(first["id"], cached["evidence"][0]["id"])
        self.assertEqual(first["source_observed_at"], cached["evidence"][0]["source_observed_at"])
        self.assertNotEqual(first["id"], historical["evidence"][0]["id"])
        self.assertFalse(historical["evidence"][0]["source_revision_authoritative"])
        self.assertEqual(first["source_revision"], historical["evidence"][0]["source_revision"])
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_changed_html_and_different_span_keep_source_identity(self):
        changed = self.html().replace(b"operating income grew", b"operating income fell")
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(self.html())
            first = self.execute("read_filing", dict(self.args, limit_chars=200))
            SnapshotData._last_sec_request_at = 0
            opener.return_value.open.return_value = HTTPBody(changed)
            second = self.execute("read_filing", dict(self.args, source="live", query="revenue operating income", limit_chars=400))
        old, new = first["evidence"][0], second["evidence"][0]
        self.assertEqual(old["source_document_id"], new["source_document_id"])
        self.assertNotEqual(old["source_revision"], new["source_revision"])
        self.assertNotEqual(old["id"], new["id"])
        self.assertIn("income fell", new["text"])

    def test_live_source_refresh_invalidates_old_spans_and_claims_without_erasing_history(self):
        from verifin.storage import SessionStore
        store = SessionStore(Path(self.tmp.name) / "lineage.sqlite")
        try:
            sid = store.create_session()["id"]
            with patch("verifin.data.build_opener") as opener:
                opener.return_value.open.return_value = HTTPBody(self.html())
                first = self.execute("read_filing", dict(self.args, limit_chars=200))
                old = first["evidence"][0]
                store.observe_document_source(sid, first["data"]["source_observation"])
                store.put_evidence(sid, old)
                historical = self.execute("read_filing", dict(self.args, limit_chars=200, source="replay"))["evidence"][0]
                store.put_evidence(sid, historical)
                store.put_claim(sid, {"id": "old_claim", "text": "The issuer filed Form 10-Q.", "evidence_ids": [old["id"]],
                                     "calculation_ids": [], "requirement_ids": []})
                store.put_claim(sid, {"id": "historical_claim", "text": "The frozen source said Form 10-Q.", "evidence_ids": [historical["id"]],
                                     "calculation_ids": [], "requirement_ids": []})
                report = store.save_report(sid, {"text": "Original report is preserved.", "claim_ids": ["old_claim"]})
                SnapshotData._last_sec_request_at = 0
                opener.return_value.open.return_value = HTTPBody(self.html().replace(b"operating income grew", b"operating income fell"))
                newer = self.execute("read_filing", dict(self.args, source="live", query="revenue operating income", limit_chars=400))
            observation = store.observe_document_source(sid, newer["data"]["source_observation"])
            self.assertIn(old["id"], observation["invalidated_evidence_ids"])
            self.assertNotIn(historical["id"], observation["invalidated_evidence_ids"])
            store.put_evidence(sid, newer["evidence"][0])
            context = store.get_context(sid)
            evidence = {r["id"]: r for r in context["evidence"]}
            claims = {r["id"]: r for r in context["claims"]}
            self.assertTrue(evidence[old["id"]]["stale"])
            self.assertTrue(claims["old_claim"]["stale"])
            self.assertFalse(evidence[historical["id"]]["stale"])
            self.assertFalse(claims["historical_claim"]["stale"])
            self.assertFalse(evidence[newer["evidence"][0]["id"]]["stale"])
            self.assertEqual(store.list_reports(sid)[0]["text"], report["text"])
            # Re-reading an older cache cannot roll the source back or stale the new span.
            ignored = store.observe_document_source(sid, first["data"]["source_observation"])
            self.assertFalse(ignored["applied"])
            store.put_evidence(sid, old)
            current = {r["id"]: r for r in store.get_context(sid)["evidence"]}
            self.assertFalse(current[newer["evidence"][0]["id"]]["stale"])
            self.assertTrue(current[old["id"]]["stale"])
        finally:
            store.close()

    def test_query_miss_observation_still_invalidates_previous_source(self):
        from verifin.storage import SessionStore
        store = SessionStore(Path(self.tmp.name) / "query-miss.sqlite")
        try:
            sid = store.create_session()["id"]
            with patch("verifin.data.build_opener") as opener:
                opener.return_value.open.return_value = HTTPBody(self.html())
                first = self.execute("read_filing", self.args)
                store.put_evidence(sid, first["evidence"][0])
                store.put_claim(sid, {"id": "claim", "text": "A claim from the original filing.",
                                     "evidence_ids": [first["evidence"][0]["id"]], "calculation_ids": [], "requirement_ids": []})
                SnapshotData._last_sec_request_at = 0
                opener.return_value.open.return_value = HTTPBody(self.html().replace(b"operating income grew", b"operating income fell"))
                miss = self.execute("read_filing", dict(self.args, source="live", query="zzzzzzzzzzzz"))
            self.assertFalse(miss["ok"])
            self.assertEqual(miss["evidence"], [])
            store.observe_document_source(sid, miss["data"]["source_observation"])
            self.assertTrue(store.get_context(sid)["claims"][0]["stale"])
        finally:
            store.close()

    def test_invalid_lexical_query_is_rejected_before_http(self):
        with patch("verifin.data.build_opener") as opener:
            result = self.execute("read_filing", dict(self.args, query="！！！"))
            self.assertFalse(result["ok"])
            opener.assert_not_called()

    def test_cached_filing_integrity_is_rechecked(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(self.html())
            first = self.execute("read_filing", self.args)
            Path(first["evidence"][0]["text_snapshot_path"]).write_text("tampered")
            result = self.execute("read_filing", self.args)
        self.assertFalse(result["ok"])
        self.assertIn("hash mismatch", result["summary"])
        self.assertEqual(result["data"]["network_requests"], 0)

    def test_pdf_bad_html_and_sec_access_denial_are_not_accepted(self):
        cases = [(b"%PDF fake", "application/pdf"), (b"not html", "text/html"),
                 (b"<html><title>Your Request Originates from an Undeclared Automated Tool</title></html>", "text/html")]
        for raw, mime in cases:
            with self.subTest(mime=mime, raw=raw):
                SnapshotData._last_sec_request_at = 0
                with patch("verifin.data.build_opener") as opener:
                    opener.return_value.open.return_value = HTTPBody(raw, mime)
                    result = self.execute("read_filing", self.args)
                self.assertFalse(result["ok"])
                self.assertEqual(result["evidence"], [])
                self.assertEqual(result["data"]["network_requests"], 1)

    def test_html_byte_limit_is_enforced(self):
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(b"<html>" + b"x" * 8_000_000)
            result = self.execute("read_filing", self.args)
        self.assertFalse(result["ok"])
        self.assertIn("8 MB", result["summary"])

    def test_sec_html_allowlist_and_redirect_policy(self):
        from verifin.data import _NoRedirect
        data = self.tools.data
        for url in ["https://evil.example/Archives/edgar/data/1/000000000000000001/x.htm",
                    "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/../x.htm",
                    "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/x.htm?url=evil",
                    "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/x.pdf"]:
            with self.assertRaises(DataError):
                data.get_sec_html(url)
        with self.assertRaisesRegex(DataError, "Redirect refused"):
            _NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://evil.example/")
        self.assertEqual(data.network_requests, 0)

    def test_cache_path_boundary(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("not a snapshot")
        with self.assertRaisesRegex(DataError, "escapes"):
            self.tools.data.read_live_bytes(str(outside), hashlib.sha256(outside.read_bytes()).hexdigest())
        self.cache.mkdir()
        cached_name = self.cache / (hashlib.sha256(outside.read_bytes()).hexdigest() + ".html")
        cached_name.symlink_to(outside)
        with self.assertRaisesRegex(DataError, "escapes"):
            self.tools.data.save_live_bytes(outside.read_bytes(), ".html")


class DurableCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache"
        self.tools = ToolRegistry(MemoryStore(), DATA, True, "VeriFin tests test@example.org", cache_dir=self.cache)
        self.url = "https://data.sec.gov/submissions/CIK0001234567.json"
        self.html_args = {"entity": "NEW", "accession": "0001234567-25-000001", "primary_document": "test.htm", "cutoff": "2025-01-31"}
        self.submissions = {"cik": "0001234567", "name": "NEW ISSUER", "tickers": ["NEW"], "filings": {"recent": {
            "accessionNumber": ["0001234567-25-000001"], "form": ["10-Q"], "reportDate": ["2024-12-31"],
            "filingDate": ["2025-01-29"], "acceptanceDateTime": ["2025-01-29T21:00:00Z"], "primaryDocument": ["test.htm"]}, "files": []}}

    def tearDown(self):
        self.tmp.cleanup()

    def live(self, name, args, payload, mime="application/json"):
        SnapshotData._last_sec_request_at = 0
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        with patch("verifin.data.build_opener") as opener:
            opener.return_value.open.return_value = HTTPBody(raw, mime)
            result = self.tools.execute(name, dict(args, source="live"), "session", {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["network_requests"], 1)
        return result

    def populate(self):
        self.live("discover_company", {"query": "NEW"}, {"0": {"cik_str": 1234567, "ticker": "NEW", "title": "NEW ISSUER"}})
        self.live("discover_filings", {"entity": "NEW"}, self.submissions)
        return self.live("read_filing", self.html_args,
                         b"<html><body><p>Form 10-Q. Revenue increased; operating income increased.</p></body></html>", "text/html")

    def test_new_registry_restores_issuer_filings_and_html_without_http(self):
        first = self.populate()
        reopened = ToolRegistry(MemoryStore(), DATA, False, cache_dir=self.cache)
        with patch("verifin.data.build_opener") as opener:
            company = reopened.execute("discover_company", {"query": "NEW"}, "session", {})
            filings = reopened.execute("discover_filings", {"entity": "NEW", "cutoff": "2025-01-31"}, "session", {})
            read = reopened.execute("read_filing", dict(self.html_args, query="operating income"), "session", {})
            opener.assert_not_called()
        self.assertTrue(company["ok"], company)
        self.assertEqual(company["data"]["source"]["mode"], "cached_live")
        self.assertTrue(filings["ok"], filings)
        self.assertEqual(filings["data"]["snapshot"]["mode"], "cached_live")
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["evidence"][0]["source_observed_at"], first["evidence"][0]["source_observed_at"])
        self.assertEqual(read["data"]["network_requests"], 0)
        early = reopened.execute("read_filing", dict(self.html_args, cutoff="2025-01-28"), "session", {})
        self.assertFalse(early["ok"])

    def test_actual_new_process_reopens_sqlite_session_and_cached_filing(self):
        from verifin.storage import SessionStore
        first = self.populate()
        database = Path(self.tmp.name) / "session.sqlite"
        store = SessionStore(database)
        sid = store.create_session()["id"]
        store.put_evidence(sid, first["evidence"][0])
        store.put_claim(sid, {"id": "claim", "text": "The source says revenue increased.", "evidence_ids": [first["evidence"][0]["id"]],
                              "calculation_ids": [], "requirement_ids": []})
        store.close()
        program = """
import json,sys
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
store=SessionStore(sys.argv[1]); tools=ToolRegistry(store,sys.argv[2],False,cache_dir=sys.argv[3])
result=tools.execute('read_filing',json.loads(sys.argv[5]),sys.argv[4],{})
assert result['ok'],result
observed=store.observe_document_source(sys.argv[4],result['data']['source_observation'])
for e in result['evidence']: store.put_evidence(sys.argv[4],e)
print(json.dumps({'mode':result['evidence'][0]['mode'],'requests':result['data']['network_requests'],
'changed':observed['changed'],'stale':store.get_context(sys.argv[4],report_mode='none')['claims'][0]['stale']}))
store.close()
"""
        completed = subprocess.run([sys.executable, "-c", program, str(database), str(DATA), str(self.cache), sid,
                                    json.dumps(dict(self.html_args, query="revenue"))], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(completed.stdout), {"mode": "cached_live", "requests": 0, "changed": False, "stale": False})

    def test_financial_fact_snapshot_is_reusable_after_restart(self):
        self.populate()
        payload = {"cik": 1234567, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2024-10-01", "end": "2024-12-31", "val": 777, "filed": "2025-01-29", "form": "10-Q", "accn": "0001234567-25-000001"}
        ]}}}}}
        args = {"entity": "NEW", "metric": "revenue", "period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-01-31"}
        self.live("financial_facts", args, payload)
        reopened = ToolRegistry(MemoryStore(), DATA, False, cache_dir=self.cache)
        result = reopened.execute("financial_facts", args, "session", {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["evidence"][0]["value"], 777)
        self.assertEqual(result["evidence"][0]["published_at"], "2025-01-29T21:00:00Z")
        self.assertEqual(result["data"]["mode"], "cached_live")

    def test_explicit_live_refresh_is_not_hidden_by_cache(self):
        first = self.populate()
        newer = self.live("read_filing", self.html_args,
                          b"<html><body>Form 10-Q. Revenue decreased.</body></html>", "text/html")
        self.assertNotEqual(first["evidence"][0]["source_revision"], newer["evidence"][0]["source_revision"])
        reopened = ToolRegistry(MemoryStore(), DATA, False, cache_dir=self.cache)
        current = reopened.execute("read_filing", self.html_args, "session", {})
        self.assertEqual(current["evidence"][0]["source_revision"], newer["evidence"][0]["source_revision"])
        unavailable = reopened.execute("read_filing", dict(self.html_args, source="live"), "session", {})
        self.assertFalse(unavailable["ok"])
        self.assertEqual(unavailable["data"]["network_requests"], 0)

    def test_metadata_corruption_fails_without_automatic_network_fallback(self):
        self.populate()
        with sqlite3.connect(self.cache / "sources.sqlite3") as db:
            db.execute("UPDATE sources SET metadata='{}' WHERE url=?", (self.url,))
        reopened = ToolRegistry(MemoryStore(), DATA, True, "VeriFin tests test@example.org", cache_dir=self.cache)
        with patch("verifin.data.build_opener") as opener:
            result = reopened.execute("discover_filings", {"entity": "NEW"}, "session", {})
            opener.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("metadata hash mismatch", result["summary"])

    def test_damaged_database_fails_visibly(self):
        self.cache.mkdir()
        (self.cache / "sources.sqlite3").write_bytes(b"not a sqlite database")
        reopened = ToolRegistry(MemoryStore(), DATA, True, "VeriFin tests test@example.org", cache_dir=self.cache)
        with patch("verifin.data.build_opener") as opener:
            result = reopened.execute("discover_company", {"query": "NEW"}, "session", {})
            opener.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("damaged", result["summary"])

    def test_older_cache_write_never_downgrades_newer_source(self):
        first = self.populate()
        newer = self.live("read_filing", self.html_args, b"<html><body>New text.</body></html>", "text/html")
        self.tools.data._remember_source(first["data"]["snapshot"], "html")
        raw, info = SnapshotData(DATA, cache_dir=self.cache).cached_sec_html(newer["evidence"][0]["source_url"])
        self.assertEqual(info["sha256"], newer["evidence"][0]["snapshot_hash"])
        self.assertIn(b"New text", raw)

    def test_auto_does_not_replace_newer_cached_facts_with_older_bundled_replay(self):
        payload = {"cik": 789019, "facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"start": "2024-10-01", "end": "2024-12-31", "val": 123, "filed": "2025-01-29", "form": "10-Q", "accn": "0000950170-25-010491"}
        ]}}}}}
        raw = json.dumps(payload).encode()
        path = self.tools.data.save_live_bytes(raw, ".json")
        self.tools.data._remember_source({"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
            "sha256": hashlib.sha256(raw).hexdigest(), "snapshot": str(path), "retrieved_at": "2030-01-01T00:00:00Z"}, "json")
        reopened = ToolRegistry(MemoryStore(), DATA, False, cache_dir=self.cache)
        args = {"entity": "MSFT", "metric": "revenue", "period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-01-31"}
        latest = reopened.execute("financial_facts", args, "session", {})
        historical = reopened.execute("financial_facts", dict(args, source="replay"), "session", {})
        self.assertEqual(latest["data"]["mode"], "cached_live")
        self.assertEqual(latest["evidence"][0]["value"], 123)  # Synthetic revision mechanics only.
        self.assertEqual(historical["evidence"][0]["value"], 69_632_000_000)

    def test_corrupt_snapshot_bytes_fail_without_network_fallback(self):
        first = self.populate()
        Path(first["evidence"][0]["snapshot_path"]).write_bytes(b"corrupted HTML")
        reopened = ToolRegistry(MemoryStore(), DATA, True, "VeriFin tests test@example.org", cache_dir=self.cache)
        with patch("verifin.data.build_opener") as opener:
            result = reopened.execute("read_filing", self.html_args, "session", {})
            opener.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("hash mismatch", result["summary"])

    def test_replay_does_not_create_a_cache_or_index(self):
        tools = ToolRegistry(MemoryStore(), DATA, False, cache_dir=self.cache)
        self.assertTrue(tools.execute("discover_company", {"query": "MSFT"}, "session", {})["ok"])
        self.assertFalse(tools.execute("discover_company", {"query": "unseen"}, "session", {})["ok"])
        self.assertFalse(self.cache.exists())

    def test_concurrent_index_writes_preserve_different_sources(self):
        self.cache.mkdir()
        data = SnapshotData(DATA, cache_dir=self.cache)
        def insert(number):
            raw = json.dumps({"number": number}).encode()
            path = data.save_live_bytes(raw, ".json")
            info = {"url": f"https://data.sec.gov/submissions/CIK{number:010d}.json", "sha256": hashlib.sha256(raw).hexdigest(),
                    "snapshot": str(path), "retrieved_at": "2026-10-13T01:00:00Z"}
            # Separate SnapshotData instances mimic independent request owners.
            SnapshotData(DATA, cache_dir=self.cache)._remember_source(info, "json")
            return info["url"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            urls = list(pool.map(insert, range(1, 13)))
        self.assertEqual(set(data.cached_urls()), set(urls))
        for number, url in enumerate(urls, 1):
            self.assertEqual(data.cached_sec_json(url)[0]["number"], number)


if __name__ == "__main__":
    unittest.main()
