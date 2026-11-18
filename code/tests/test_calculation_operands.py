import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_tools import DATA, MemoryStore
from verifin.tools import ToolRegistry


class IssuerComparisonTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.tools = ToolRegistry(self.store, DATA)
        for year, values in [(2022, [70, 65, 60]), (2023, [100, 90, 70]), (2024, [175, 150, 80])]:
            for entity, value in zip(("AAA", "BBB", "CCC"), values):
                self.store.save({"evidence": [dict(
                    id=f"{entity}{year}", entity=entity, kind="financial_fact", metric="revenue",
                    concept="us-gaap:Revenues", unit="USD", currency="USD", basis="US-GAAP",
                    business_scope="consolidated", value=value, scale=1, period_start=f"{year}-01-01",
                    period_end=f"{year}-12-31", published_at=f"{year+1}-02-01")]})

    def calculate(self, first, second, op="difference"):
        result = self.tools.execute("calculate", {"operation": op, "operand_ids": [first, second]},
                                    "session", {})
        self.store.save(result)
        return result

    def spread(self, first, second, year):
        result = self.calculate(f"{first}{year}", f"{second}{year}")
        self.assertTrue(result["ok"], result)
        return result["calculations"][0]["id"]

    def test_same_ordered_pair_keeps_both_issuers_when_comparing_years(self):
        current = self.spread("AAA", "BBB", 2024)
        prior = self.spread("AAA", "BBB", 2023)
        for operation, expected in (("difference", 15), ("growth", 150)):
            with self.subTest(operation=operation):
                result = self.calculate(current, prior, operation)
                self.assertTrue(result["ok"], result)
                record = result["calculations"][0]
                self.assertEqual(record["value"], expected)
                self.assertEqual(record["entities"], ["AAA", "BBB"])

    def test_reversed_pair_and_changed_peer_cannot_be_compared_over_time(self):
        prior = self.spread("AAA", "BBB", 2023)
        for first, second in (("BBB", "AAA"), ("AAA", "CCC")):
            current = self.spread(first, second, 2024)
            for operation in ("difference", "growth"):
                with self.subTest(first=first, second=second, operation=operation):
                    result = self.calculate(current, prior, operation)
                    self.assertFalse(result["ok"], result)
                    self.assertEqual(result["calculations"], [])

    def test_legacy_nested_result_recovers_or_rejects_series_from_its_sources(self):
        early, middle, late = [self.spread("AAA", "BBB", year) for year in (2022, 2023, 2024)]
        past = self.calculate(middle, early)["calculations"][0]
        recent = self.calculate(late, middle)["calculations"][0]
        reversed_middle = self.spread("BBB", "AAA", 2023)
        for first, second, expected in [(late, middle, 10), (late, reversed_middle, None)]:
            with self.subTest(second=second):
                historical = dict(recent, id="historical", entities=[None], operand_ids=[first, second],
                                  operand_versions={first: 1, second: 1}, formula=f"{first} - {second}",
                                  value=15 if expected is not None else 35)
                self.store.save({"calculations": [historical]})
                result = self.calculate("historical", past["id"])
                self.assertEqual(result["ok"], expected is not None, result)
                if expected is not None:
                    self.assertEqual(result["calculations"][0]["value"], expected)
                    self.assertEqual(result["calculations"][0]["entities"], ["AAA", "BBB"])


@unittest.skipUnless(importlib.util.find_spec("lxml") and importlib.util.find_spec("pandas"),
                     "optional HTML parser dependencies are not installed")
class PrintedPercentageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        # Both placements of the percent sign occur in English-language financial tables.
        rows = [("Operating margin", "(3.2%)", "4.0%"),
                ("Net margin", "(1.9 %)", "(2.3) %"),
                ("Loss margin", "−1.5%", "+0.8%"),
                ("Volume growth", "1,200.5%", "1,234,000%"),
                ("Reported margin", "3,2%", "1,,200%"),
                ("Missing margin", "—", "N/A")]
        source = ('<meta charset="utf-8"><table><tr><th></th><th colspan="2">Three Months Ended</th></tr>'
                  '<tr><th></th><th>December 31, 2024</th><th>December 31, 2023</th></tr>'
                  + "".join("<tr>" + "".join(f"<td>{s}</td>" for s in row) + "</tr>" for row in rows)
                  + "</table>").encode()
        (directory / "margins.html").write_bytes(source)
        (directory / "manifest.json").write_text(json.dumps({
            "companies": [{"ticker": "AAA", "cik": "0000000001", "name": "Example issuer"}],
            "documents": [{"entity": "AAA", "source": "margins.html", "published_at": "2025-02-01"}],
            "files": {"margins.html": {"sha256": hashlib.sha256(source).hexdigest(),
                                        "url": "https://example.org/margins"}}}))
        self.store = MemoryStore()
        self.tools = ToolRegistry(self.store, directory)

    def rows(self, label):
        result = self.tools.execute("find_percentage_cells", {"entity": "AAA", "query": label, "limit": 12},
                                    "session", {})
        self.assertEqual(result["data"]["network_requests"], 0)
        self.store.save(result)
        return [r for r in result["evidence"] if r["row_label"] == label]

    def test_accounting_negatives_feed_percentage_point_calculation(self):
        cells = self.rows("Operating margin")
        self.assertEqual([r["value"] for r in cells], [-3.2, 4])
        result = self.tools.execute("calculate", {"operation": "percentage_point_change",
                                    "operand_ids": [r["id"] for r in cells]}, "session", {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["calculations"][0]["value"], -7.2)
        self.assertEqual([r["value"] for r in self.rows("Net margin")], [-1.9, -2.3])

    def test_ambiguous_grouping_and_missing_cells_yield_no_numeric_operands(self):
        self.assertEqual([r["value"] for r in self.rows("Reported margin")], [])
        self.assertEqual([r["value"] for r in self.rows("Missing margin")], [])

    def test_signed_values_and_valid_thousands_groups_keep_their_magnitude(self):
        self.assertEqual([r["value"] for r in self.rows("Loss margin")], [-1.5, 0.8])
        self.assertEqual([r["value"] for r in self.rows("Volume growth")], [1200.5, 1234000])
