import tempfile
import unittest
from pathlib import Path

from verifin.market_coverage import check_coverage
from verifin.model import validate_response
from verifin.tools import ToolRegistry


class MarketCoverageTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as cache:
            data = Path(__file__).resolve().parents[1] / "data" / "replay"
            result = ToolRegistry(None, data, allow_network=False, cache_dir=Path(cache)).execute(
                "market_prices", {"entity": "NVDA", "start_date": "2025-02-26", "end_date": "2025-02-27"},
                "test", {"cutoff": "2025-02-28"})
        self.assertTrue(result["ok"], result["summary"])
        prices = result["evidence"]
        self.assertEqual(len(prices), 2)
        self.context = {"scope": {"cutoff": "2025-02-28"}, "evidence": prices,
                        "calculations": [{"id": "ret", "operation": "price_return",
                                          "operand_ids": [p["id"] for p in prices]}],
                        "claims": [{"id": "market", "calculation_ids": ["ret"]}, {"id": "filing"}],
                        "request_parts": {"p": "Give the raw close-to-close return and the filing result."}}
        self.row = {"part": "p", "status": "answered", "claim_id": "market",
                    "answer_sentence": "The provider closes fell 8.48%.", "supporting_claim_ids": ["filing"]}

    def check(self, question=None):
        if question is not None:
            self.context["request_parts"]["p"] = question
        return check_coverage(self.context, [self.row])[0]

    def test_unknown_legacy_basis_preserves_answer_and_other_supported_claims(self):
        for question in ("Give the raw close-to-close return and the filing result.",
                         "给两期利润率并计算收盘到收盘的原始收益。", "Report the unadjusted closing prices."):
            row = self.check(question)
            self.assertEqual(row["status"], "partial")
            self.assertIn("unadjusted", row["missing_support"])
            self.assertIn("cutoff", row["missing_support"])
            self.assertEqual(row["supporting_claim_ids"], ["filing"])
            self.assertEqual(row["answer_sentence"], self.row["answer_sentence"])

    def test_descriptive_and_unrelated_questions(self):
        self.row["requested_price_basis"] = "unadjusted"  # Misclassified by the critic.
        for question in ("Report the observed provider-close return.", "只用供应商收盘价算收益。",
                         "Don't use unadjusted closing prices. Use the provider series.",
                         "Unadjusted closes are not required; give the observed price change.",
                         "I am not asking for raw closes.", "不需要未复权收盘价，只给供应商行情。", "Report raw revenue growth."):
            self.assertEqual(self.check(question), self.row)
        self.row.update(claim_id="filing", supporting_claim_ids=[])
        self.assertEqual(self.check("Show raw closes.")["status"], "partial")

    def test_cited_endpoints_each_require_adjustment_and_availability(self):
        prices = self.context["evidence"]
        for p in prices:
            p.update(adjustment={"splits": "none", "dividends": "none"},
                     availability_basis="Recorded in contemporaneous provider snapshot.")
        self.assertEqual(self.check(), self.row)
        prices[1]["adjustment"]["dividends"] = "adjusted"
        self.assertIn("unadjusted", self.check()["missing_support"])
        self.assertNotIn("cutoff", self.check()["missing_support"])
        prices[1]["adjustment"]["dividends"] = "none"
        del prices[0]["availability_basis"]
        self.assertIn("cutoff", self.check()["missing_support"])
        self.assertNotIn("unadjusted", self.check()["missing_support"])

    def test_direct_close_link_and_existing_gap(self):
        self.context["claims"][0] = {"id": "market", "evidence_ids": [self.context["evidence"][0]["id"]]}
        self.assertEqual(self.check("给原始收盘价。")["status"], "partial")
        self.row.update(status="gap", missing_support="Existing distinct gap.")
        self.assertEqual(self.check(), self.row)


    def test_semantic_intent_and_explicit_fallback_for_legacy_reviews(self):
        self.row["requested_price_basis"] = "unadjusted"
        self.assertEqual(self.check("Use the prices as traded before the stock split.")["status"], "partial")
        self.assertEqual(self.check("给拆股之前实际交易的收市价。")["status"], "partial")
        self.row["requested_price_basis"] = "provider"
        self.assertEqual(self.check("Give raw closing prices.")["status"], "partial")
        del self.row["requested_price_basis"]
        self.assertEqual(self.check("Give raw closing prices.")["status"], "partial")

    def test_contemporaneous_prose_cannot_override_dates_or_staleness(self):
        p = self.context["evidence"][1]
        for item in self.context["evidence"]:
            item.update(availability_basis="contemporaneous", adjustment={"splits":"none","dividends":"none"})
        for date in ("2025-03-01T00:00:01Z", "invalid-timestamp"):
            p["published_at"] = date
            self.assertIn("cutoff", self.check()["missing_support"])
        p["published_at"] = "2025-02-27T23:00:00Z"
        p["adjustment"]["as_of"] = "2025-03-01"
        self.assertIn("cutoff", self.check()["missing_support"])
        del p["adjustment"]["as_of"]
        p["stale"] = True
        self.assertIn("cutoff", self.check()["missing_support"])

    def test_optional_critic_basis_enum_is_validated(self):
        response = dict(reviewed_claim_ids=[], reviewed_requirement_ids=[], issues=[], request_coverage=[self.row])
        for value in ("provider", "unadjusted", "unspecified"):
            self.row["requested_price_basis"] = value
            self.assertEqual(validate_response("critic", response, {})["request_coverage"], [self.row])
        self.row["requested_price_basis"] = "verified"
        other = {"part": "financial", "status": "answered", "claim_id": "other", "answer_sentence": "Revenue rose."}
        response["request_coverage"].append(other)
        rows = validate_response("critic", response, {})["request_coverage"]
        self.assertEqual(rows[0]["status"], "partial")
        self.assertNotIn("requested_price_basis", rows[0])
        self.assertEqual(rows[1], other)
