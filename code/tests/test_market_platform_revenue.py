"""The frozen CFO commentary supports the original market-platform share question."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import Mock

from tests.critic_fixture import fixture_verdicts
from tests.test_tools import DATA, MemoryStore
from verifin.data import stable_id
from verifin.engine import Budget, ResearchEngine
from verifin.model import validate_response
from verifin.reporting import render_markdown
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


QUESTION = ("As of 2025-02-27, for NVIDIA's FY2025 Q4 ended January 26, 2025, what were consolidated revenue "
            "and Data Center revenue, and what share of total revenue did Data Center account for? "
            "Show your inputs and cite the earnings release or CFO commentary. Do not use full-year numbers.")
COMMON = {"entity": "NVDA", "cutoff": "2025-02-27"}
SHARE_QUESTION = ("As of 2025-02-27, for NVIDIA's FY2025 Q4, what was Data Center revenue as a share of total revenue? "
                  "Cite the CFO commentary.")
SOURCE = "snapshots/nvda_fy25q4_cfo.pdf"
SCOPE = {"entities": ["NVDA"], "cutoff": "2025-02-27", "period_start": "2024-10-28", "period_end": "2025-01-26"}


def cells(result):
    return {row["row_label"]: row for row in result["evidence"] if row.get("cell_type") == "market_platform_revenue_cell"}


def tool(name, arguments):
    return {"action": "tool", "tool": name, "arguments": arguments}


class ReplayModel:
    """Fixture roles keep the live source/parser/calculator/controller path intact."""

    def __init__(self, wording="full"):
        self.wording = wording

    def complete(self, role, ctx):
        if role == "planner":
            response = self.plan(ctx)
        elif role == "researcher":
            sources = cells(ctx)
            refs = [sources[name]["id"] for name in ("Data Center", "Total")]
            share, = ctx["calculations"]
            requirements = [r["id"] for r in ctx["requirements"]]
            summary = (f"NVIDIA FY2025 Q4 ended January 26, 2025: consolidated revenue was USD 39,331 million "
                       f"and Data Center revenue was USD 35,580 million, or {share['value']:.2%} of total revenue.")
            share_only = f"NVIDIA Data Center accounted for {share['value']:.2%} of total revenue in FY2025 Q4."
            if self.wording == "omitted":
                summary = share_only
            if self.wording == "one_input":
                summary = "NVIDIA's company-wide sales were USD 39.331 billion. " + share_only
            if self.wording == "billions":
                summary = ("NVIDIA's overall net sales came to USD 39.331 billion and Data Center revenue reached USD "
                           f"35.58 billion, or {share['value']:.2%} of total revenue in FY2025 Q4.")
            if self.wording == "reviewer_omitted":
                summary = ("NVIDIA's company-wide sales were USD 39.331 billion. Data Center revenue was USD 35.580 billion. "
                           + share_only)
            if self.wording == "wrong_issuer":
                summary = summary.replace("NVIDIA", "Microsoft")
            if self.wording == "swapped":
                summary = summary.replace("39,331", "35,580").replace(
                    "Data Center revenue was USD 35,580", "Data Center revenue was USD 39,331")
            if self.wording == "no_row_reference":
                refs = refs[1:]
            citation = ("The two quarterly inputs come from the Revenue by Market Platform table, page 1 of the CFO "
                        "commentary: " + sources["Total"]["source_url"])
            response = {"summary": summary + " " + citation, "claims": [
                {"id": "share", "text": summary, "evidence_ids": refs, "calculation_ids": [] if self.wording == "no_row_reference" else [share["id"]],
                 "requirement_ids": requirements, "constraints": {}},
                {"id": "source", "text": citation, "evidence_ids": refs, "calculation_ids": [],
                 "requirement_ids": requirements, "constraints": {}}],
                "answered_requirement_ids": requirements, "unanswered_requirement_ids": []}
        else:
            claims = ctx["claims"]
            response = {"issues": [], "reviewed_claim_ids": [row["id"] for row in claims],
                        "reviewed_requirement_ids": [r["id"] for r in ctx["requirements"]],
                        "request_coverage": [{"part": part, "status": "answered", "claim_id": claim["id"],
                                              "answer_sentence": claim["text"]}
                                             for part, claim in zip(ctx["request_parts"], claims, strict=True)]}
            if self.wording == "reviewer_omitted":
                response["request_coverage"][0]["answer_sentence"] = claims[0]["text"].rsplit(". ", 1)[1]
        return validate_response(role, fixture_verdicts(role, response, ctx), ctx)

    @staticmethod
    def plan(ctx):
        receipts = ctx["episode"].get("recent_tool_results", [])
        if not receipts:
            return {**tool("search_documents", {**COMMON, "query": "Data Center revenue"}), "scope": SCOPE,
                    "requirements": [{"id": "revenue", "text": ctx["episode"]["request"]}]}
        last = receipts[-1]
        if last["tool"] == "search_documents":
            ids = [r["id"] for r in ctx["evidence"] if r.get("kind") == "document"][:2]
            return tool("calculate", {"operation": "ratio", "operand_ids": ids})
        if not last["ok"]:
            action, = last["data"]["reopen"]
            return tool(action["tool"], action["arguments"])
        if last["tool"] == "read_document":
            available = cells(ctx)
            return tool("calculate", {"operation": "ratio", "operand_ids": [available[name]["id"]
                                         for name in ("Data Center", "Total")]})
        return {"action": "research"}


@skipUnless(importlib.util.find_spec("pdfplumber"), "native PDF parser is optional")
class MarketPlatformRevenueTests(TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.tools = ToolRegistry(self.store, DATA)
        self.doc = next(doc for doc in self.tools.data.manifest["documents"] if doc["source"] == SOURCE)
        self.document_id = stable_id("source_doc_", self.tools.data.info(SOURCE)["url"])

    def read(self, **args):
        return self.tools.execute("read_document", {**COMMON, "document_id": self.document_id, **args}, "session", SCOPE)

    def test_explicit_quarter_only_and_rejects_cross_scope_or_reversed_inputs(self):
        result = self.read()
        operands = cells(result)
        self.assertEqual(operands["Data Center"]["value"], 35_580_000_000)
        self.assertEqual(operands["Total"]["value"], 39_331_000_000)
        self.assertTrue(all(row["fiscal_quarter"] == "2025Q4" and row["unit"] == "USD" for row in operands.values()))
        self.assertTrue(all(row["locator"].startswith("PDF p.1,") for row in operands.values()))
        self.assertFalse({"Compute", "Networking", "Compute & Networking"} & operands.keys())
        self.store.save(result)
        current, total = (operands[name] for name in ("Data Center", "Total"))
        wrong_period = {**total, "id": "wrong_period", "period_start": "2024-01-29"}
        wrong_currency = {**total, "id": "wrong_currency", "unit": "CAD", "currency": "CAD"}
        nonconsolidated = operands["Gaming"]
        self.store.save({"evidence": [wrong_period, wrong_currency]})
        for ids in ([total["id"], current["id"]], [current["id"], nonconsolidated["id"]],
                    [current["id"], wrong_period["id"]], [current["id"], wrong_currency["id"]]):
            with self.subTest(ids=ids):
                rejected = self.tools.execute("calculate", {"operation": "ratio", "operand_ids": ids}, "session", SCOPE)
                self.assertFalse(rejected["ok"], rejected)
                self.assertEqual(rejected["calculations"], [])
        source = self.tools.data.raw(self.doc["text"]).decode()
        annual = self.read(offset=source.index("=== page 2 ==="), limit_chars=1200)
        self.assertTrue(annual["ok"])
        self.assertFalse(cells(annual))
        self.assertIn("$115,186", annual["evidence"][0]["text"])

    def test_missing_quarter_cell_preserves_the_independent_total(self):
        from io import BytesIO
        import pdfplumber
        from verifin.platform_revenue import cells as extract_cells
        from verifin.statement_amount_cells import prior_currency_evidence

        text = self.tools.data.raw(self.doc["text"]).decode()
        page_end = text.index("=== page 2 ===")
        with pdfplumber.open(BytesIO(self.tools.data.raw(SOURCE))) as original:
            lines = original.pages[0].extract_text_lines(return_chars=False)
        row = next(line for line in lines if line["text"].startswith("Data Center $35,580"))
        row["text"] = row["text"].replace("$35,580", "—")
        page = Mock()
        page.extract_text_lines.return_value = lines
        witnesses = prior_currency_evidence(self.tools.data, self.doc, ("revenue",))
        records = extract_cells(page, 1, self.doc, self.tools.data.info(SOURCE), self.document_id, text, 0, page_end, witnesses)
        amounts = {r["row_label"]: r["value"] for r in records}
        self.assertEqual(amounts["Total"], 39_331_000_000)
        self.assertNotIn("Data Center", amounts)

    def test_fresh_turn_correction_from_passages_to_cited_platform_share(self):
        with TemporaryDirectory() as tmp, SessionStore(Path(tmp) / "session.sqlite3") as store:
            sid = store.create_session()["id"]
            engine = ResearchEngine(store, ReplayModel(), ToolRegistry(store, DATA), finish_after_review=True)
            report = engine.ask(sid, QUESTION, as_of=COMMON["cutoff"], budget=Budget(max_model_calls=8))["report"]
            receipts = [event for event in store.events(sid) if event["type"] == "tool_call"]
        self.assertEqual([(row["tool"], row["ok"]) for row in receipts], [
            ("search_documents", True), ("calculate", False), ("read_document", True), ("calculate", True)])
        self.assertTrue(all(row["observation"]["data"]["network_requests"] == 0 for row in receipts))
        self.assertEqual(report["status"], "complete", report["issues"])
        self.assertAlmostEqual(report["calculations"][0]["value"], 35_580 / 39_331)
        rendered = render_markdown(report)
        for part in ("39,331", "35,580", "90.46%", "Q4", self.tools.data.info(SOURCE)["url"]):
            self.assertIn(part, rendered)

    def test_delivers_explicitly_requested_inputs_or_names_the_gap(self):
        cases = [
            ("omitted", QUESTION, "partial", ("Data Center 本季营收", "本季合并营收")),
            ("one_input", QUESTION, "partial", ("Data Center 本季营收",)),
            ("reviewer_omitted", QUESTION, "partial", ("Data Center 本季营收", "本季合并营收")),
            ("billions", QUESTION, "complete", ()),
            ("wrong_issuer", QUESTION, "partial", ("其他发行人",)),
            ("swapped", QUESTION, "partial", ("金额或币种与相应的季度营收记录不符",)),
            ("no_row_reference", QUESTION, "partial", ("所问业务平台的本季营收",)),
            ("omitted", SHARE_QUESTION, "complete", ()),
        ]
        for wording, question, status, missing in cases:
            with self.subTest(wording=wording, question=question), TemporaryDirectory() as tmp, SessionStore(
                    Path(tmp) / "session.sqlite3") as store:
                sid = store.create_session()["id"]
                engine = ResearchEngine(store, ReplayModel(wording), ToolRegistry(store, DATA), finish_after_review=True)
                report = engine.ask(sid, question, as_of=COMMON["cutoff"], budget=Budget(max_model_calls=8))["report"]
                self.assertTrue(report["verification_available"], report)
                self.assertEqual(report["status"], status, report["issues"])
                first, second = report["request_coverage"]
                gaps = " ".join(r.get("missing_support", "") for r in (first, second))
                for label in missing:
                    self.assertIn(label, gaps)
                if wording == "one_input":
                    self.assertNotIn("本季合并营收", gaps)
                if wording in {"omitted", "one_input", "reviewer_omitted"} and question == QUESTION:
                    self.assertEqual(first["status"], "partial")
                    self.assertIn("90.46%", first["answer_sentence"])
                    self.assertIn(self.tools.data.info(SOURCE)["url"], second["answer_sentence"])
                if wording in {"wrong_issuer", "swapped"}:
                    self.assertEqual(first["status"], "gap")
                    self.assertNotIn("Microsoft", first.get("answer_sentence", ""))
                if status == "complete":
                    self.assertEqual([first["status"], second["status"]], ["answered", "answered"])
