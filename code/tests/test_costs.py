import copy
import json
from pathlib import Path
import unittest

from verifin.costs import cost_ledger
from verifin.engine import progress_content
from verifin.revision import _progress_value


CATALOG = json.loads((Path(__file__).resolve().parents[1] / "data/pricing/openai-standard-2026-11-06.json").read_text())


def event(**extra):
    return {"type": "model_call", "mode": "configured_model", "model": "gpt-6.1-sol",
            "base_url": "https://api.openai.com/v1", "service_tier": "default", "ok": True,
            "usage": {"prompt_tokens": 12000, "completion_tokens": 1000, "total_tokens": 13000,
                      "cached_input_tokens": 4000, "cache_write_tokens": 7000,
                      "cache_write_30m_tokens": 3000, "cache_write_12h_tokens": 4000,
                      "reasoning_tokens": 800}, **extra}


class CostTests(unittest.TestCase):
    def test_cached_reads_writes_and_reasoning_are_not_double_counted(self):
        ledger = cost_ledger([event()], CATALOG, expected_attempts=1)
        # (1000*2 + 4000*.1 + 3000*2.5 + 4000*5 + 1000*10)/1M
        self.assertEqual(ledger["estimated_total"], "0.0399")
        self.assertTrue(ledger["coverage_complete"])
        self.assertEqual(ledger["calls"][0]["billed_token_categories"]["output"], 1000)

    def test_failure_usage_is_billable_but_missing_calls_are_unknown(self):
        ledger = cost_ledger([event(ok=False), event(usage={})], CATALOG, expected_attempts=3)
        self.assertEqual(ledger["estimated_calls"], 1)
        self.assertEqual(ledger["unknown_calls"], 1)
        self.assertEqual(ledger["unrecorded_attempts"], 1)
        self.assertIsNone(ledger["estimated_total"])
        self.assertEqual(ledger["priced_calls_subtotal"], "0.0399")

    def test_long_context_applies_to_whole_call_and_unknown_tier_not_priced(self):
        e = event(usage={"prompt_tokens": 300000, "completion_tokens": 1000,
                         "cached_input_tokens": 0, "cache_write_tokens": 0})
        self.assertEqual(cost_ledger([e], CATALOG, expected_attempts=1)["estimated_total"], "1.215")
        for changed in ({"service_tier": "priority"}, {"model": "unpriced-model"},
                        {"base_url": "https://example.test/v1"}, {"response_metadata": {"model": "other-snapshot"}}):
            row = cost_ledger([{**e, **changed}], CATALOG, expected_attempts=1)
            self.assertIsNone(row["estimated_total"])

    def test_bad_usage_and_unknown_cache_split_never_assumed_zero(self):
        for field, value in (("cached_input_tokens", 9000), ("cache_write_30m_tokens", -1),
                             ("reasoning_tokens", 5000), ("total_tokens", 1), ("cache_write_12h_tokens", None)):
            e = event(); e["usage"][field] = value
            self.assertEqual(cost_ledger([e], CATALOG)["calls"][0]["status"], "unknown")
        e = event(); del e["usage"]["cache_write_tokens"]
        self.assertIsNone(cost_ledger([e], CATALOG)["estimated_total"])

    def test_fixture_is_not_reported_as_a_paid_call_and_catalog_is_explicit(self):
        result = cost_ledger([event(mode="scripted_fixture")], CATALOG, expected_attempts=1)
        self.assertEqual(result["excluded_non_live_calls"], 1)
        self.assertIsNone(result["estimated_total"])
        bad = copy.deepcopy(CATALOG); bad["models"]["gpt-6.1-sol"]["short"]["input"] = "NaN"
        with self.assertRaises(ValueError): cost_ledger([], bad)

    def test_retrieval_timing_and_cache_hits_do_not_manufacture_progress(self):
        a = {"evidence": [{"id": "p", "text": "Revenue"}],
             "data": {"ranking_latency_ms": 1, "runtime_encoding": {"cache_hits": 0, "encoding_seconds": 2}}}
        b = {"evidence": [{"id": "p", "text": "Revenue"}],
             "data": {"ranking_latency_ms": 2, "runtime_encoding": {"cache_hits": 1, "encoding_seconds": .01}}}
        for normalize in (progress_content, _progress_value):
            self.assertEqual(normalize(a), normalize(b))
            b["evidence"][0]["text"] = "Revenue changed"
            self.assertNotEqual(normalize(a), normalize(b))
            b["evidence"][0]["text"] = "Revenue"
