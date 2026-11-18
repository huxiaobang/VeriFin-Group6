import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from verifin.data import DataError, SnapshotData
from verifin.market import AlphaVantageDailyAdapter, PriceSnapshot
from verifin.tools import ToolRegistry

DATA = Path(__file__).resolve().parents[1] / "data"


def mapping():
    return {"entity": "INDEPENDENT", "symbol": "EXAMPLE.LON", "exchange": "XLON",
            "currency": "GBP", "timezone": "Europe/London"}


def sample():
    return {"schema_version": 1, "provider": "Unit-test provider, synthetic", "source_url": "https://example.org/prices",
            "license": "Synthetic unit-test observations only", "license_url": "https://example.org/license",
            "retrieved_at": "2025-02-04T20:00:00Z", "instruments": [dict(mapping(), price_type="close",
                adjustment={"splits": "none", "dividends": "none"}, rows=[
                    {"date": "2025-01-31", "value": 10, "available_at": "2025-01-31T17:00:00Z", "availability_basis": "unit-test fixture"},
                    {"date": "2025-02-03", "value": 11, "available_at": "2025-02-03T17:00:00Z", "availability_basis": "unit-test fixture"}])]} 


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "prices.json"
        self.path.write_text(json.dumps(sample()))
        self.registry = ToolRegistry(None, DATA, cache_dir=Path(self.tmp.name) / "cache", market_data=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generic_import_without_sec_identity_and_provenance(self):
        result = self.registry.execute("market_prices", {"entity": "INDEPENDENT", "start_date": "2025-01-31", "end_date": "2025-02-03", "cutoff": "2025-02-03T17:00:00Z"}, "s", {})
        self.assertTrue(result["ok"], result)
        self.assertEqual([r["value"] for r in result["evidence"]], [10, 11])
        row = result["evidence"][0]
        self.assertEqual(row["snapshot_hash"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(row["timezone"], "Europe/London")
        self.assertIsNone(row["cik"])
        self.assertEqual(result["data"]["network_requests"], 0)
        discovery = self.registry.execute("discover_company", {"query": "INDEPENDENT"}, "s", {})
        self.assertTrue(discovery["ok"])
        self.assertEqual(discovery["data"]["companies"][0]["identity_source"]["mode"], "configured_market_mapping")

    def test_cutoff_and_weekend_are_not_silently_substituted(self):
        snapshot = PriceSnapshot.from_path(self.path)
        with self.assertRaisesRegex(DataError, "endpoint"):
            snapshot.select("INDEPENDENT", "2025-02-01", "2025-02-03")
        with self.assertRaisesRegex(DataError, "endpoint"):
            snapshot.select("INDEPENDENT", "2025-01-31", "2025-02-03", "2025-02-03T16:59:59Z")

    def test_bad_values_duplicate_days_and_invalid_timezone_refused(self):
        for value in (0, -1, True, "10", float("nan")):
            payload = sample()
            payload["instruments"][0]["rows"][0]["value"] = value
            with self.assertRaises((DataError, ValueError)):
                PriceSnapshot(payload)
        payload = sample()
        payload["instruments"][0]["rows"] *= 2
        with self.assertRaisesRegex(DataError, "Duplicate"):
            PriceSnapshot(payload)
        payload = sample()
        payload["instruments"][0]["timezone"] = "Invented/Zone"
        with self.assertRaisesRegex(DataError, "timezone"):
            PriceSnapshot(payload)

    def test_adjusted_vintage_prevents_future_split_lookahead(self):
        payload = sample()
        instrument = payload["instruments"][0]
        instrument["price_type"] = "adjusted_close"
        instrument["adjustment"] = {"splits": "adjusted", "dividends": "adjusted", "as_of": "2025-02-04T20:00:00Z"}
        with self.assertRaisesRegex(DataError, "vintage"):
            PriceSnapshot(payload)
        for row in instrument["rows"]:
            row["available_at"] = "2025-02-04T20:00:00Z"
        snapshot = PriceSnapshot(payload)
        with self.assertRaisesRegex(DataError, "endpoint"):
            snapshot.select("INDEPENDENT", "2025-01-31", "2025-02-03", "2025-02-03", "adjusted_close")

    def test_secret_provenance_and_import_hash_refused(self):
        payload = sample()
        payload["source_url"] += "?apikey=secret"
        with self.assertRaisesRegex(DataError, "API key"):
            PriceSnapshot(payload)
        with self.assertRaisesRegex(DataError, "hash"):
            PriceSnapshot.from_path(self.path, expected_hash="0" * 64)

    def provider_payload(self):
        return {"Meta Data": {"2. Symbol": "EXAMPLE.LON", "5. Time Zone": "Europe/London"},
                "Time Series (Daily)": {"2025-01-31": {"4. close": "10"}, "2025-02-03": {"4. close": "11"}}}

    def adapter_registry(self, **kwargs):
        adapter = AlphaVantageDailyAdapter("unit-test-secret-key", [mapping()], allow_network=True, **kwargs)
        return ToolRegistry(None, DATA, allow_network=True, cache_dir=Path(self.tmp.name) / "cache", market_adapter=adapter)

    def execute_provider(self, registry, **kwargs):
        return registry.execute("market_prices", dict(entity="INDEPENDENT", start_date="2025-01-31", end_date="2025-02-03", **kwargs), "s", {})

    def test_provider_one_request_cache_reopen_and_explicit_refresh(self):
        registry = self.adapter_registry()
        raw = json.dumps(self.provider_payload()).encode()
        with patch("verifin.market.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(raw)
            first = self.execute_provider(registry, source="live")
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["data"]["network_requests"], 1)
        self.assertNotIn("unit-test-secret-key", json.dumps(first))
        self.assertEqual(first["evidence"][0]["locator"], "Time Series (Daily).2025-01-31.4. close")
        with patch("verifin.market.build_opener") as opener:
            second = self.execute_provider(self.adapter_registry())
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["data"]["network_requests"], 0)
            self.assertEqual(second["evidence"][0]["mode"], "cached_live")
            opener.assert_not_called()
            opener.return_value.open.return_value = io.BytesIO(raw)
            refreshed = self.execute_provider(registry, source="live")
            self.assertEqual(refreshed["data"]["network_requests"], 1)

    def test_provider_no_backdating_observed_prices_to_historical_cutoff(self):
        with patch("verifin.market.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(self.provider_payload()).encode())
            result = self.execute_provider(self.adapter_registry(), source="live", cutoff="2025-02-04")
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["network_requests"], 1)
        self.assertIn("cutoff", result["error"])

    def test_explicit_live_bypasses_broken_local_import(self):
        registry = self.adapter_registry()
        registry.market_data = self.path
        self.path.write_text("broken unrelated import")
        with patch("verifin.market.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(self.provider_payload()).encode())
            result = self.execute_provider(registry, source="live")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["network_requests"], 1)

    def test_no_permission_no_key_premium_gate_no_requests(self):
        adapters = [AlphaVantageDailyAdapter(None, [mapping()], True),
                    AlphaVantageDailyAdapter("demo", [mapping()], True),
                    AlphaVantageDailyAdapter("unit-test-secret-key", [mapping()], False),
                    AlphaVantageDailyAdapter("unit-test-secret-key", [mapping()], True, outputsize="full")]
        with patch("verifin.market.build_opener") as opener:
            for adapter in adapters:
                registry = ToolRegistry(None, DATA, allow_network=True, cache_dir=Path(self.tmp.name) / "cache", market_adapter=adapter)
                result = self.execute_provider(registry, source="live")
                self.assertFalse(result["ok"])
                self.assertEqual(result["data"]["network_requests"], 0)
            adjusted = self.execute_provider(self.adapter_registry(), source="live", price_type="adjusted_close")
            self.assertFalse(adjusted["ok"])
            opener.assert_not_called()

    def test_provider_errors_and_credential_echo_never_save_or_expose(self):
        for payload in ({"Note": "quota unit-test-secret-key"}, {"Information": "premium"}, ["bad"],
                        dict(self.provider_payload(), echo="unit-test-secret-key")):
            with patch("verifin.market.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
                result = self.execute_provider(self.adapter_registry(), source="live")
                self.assertFalse(result["ok"])
                self.assertNotIn("unit-test-secret-key", json.dumps(result))
        self.assertFalse((Path(self.tmp.name) / "cache").exists())
        with patch("verifin.market.build_opener") as opener:
            opener.return_value.open.side_effect = URLError("unit-test-secret-key")
            result = self.execute_provider(self.adapter_registry(), source="live")
            self.assertNotIn("unit-test-secret-key", json.dumps(result))

    def test_provider_identity_mismatch_and_corrupt_cache_fail_closed(self):
        payload = self.provider_payload()
        payload["Meta Data"]["2. Symbol"] = "OTHER"
        with patch("verifin.market.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
            failed = self.execute_provider(self.adapter_registry(), source="live")
            self.assertFalse(failed["ok"])
            opener.return_value.open.return_value = io.BytesIO(json.dumps(self.provider_payload()).encode())
            good = self.execute_provider(self.adapter_registry(), source="live")
        Path(good["evidence"][0]["snapshot_path"]).write_bytes(b"modified")
        with patch("verifin.market.build_opener") as opener:
            failed = self.execute_provider(self.adapter_registry())
            self.assertFalse(failed["ok"])
            self.assertIn("hash mismatch", failed["error"])
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
