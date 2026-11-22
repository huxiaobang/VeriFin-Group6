"""Inspectable financial tools with replay data and optional generic SEC access."""
from __future__ import annotations

import math
import hashlib
import re
import threading
from collections import Counter, defaultdict
from html.parser import HTMLParser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import cache
from urllib.error import HTTPError, URLError

from . import latest_quarter
from .data import DataError, SnapshotData, available_by, cutoff_time, parse_day, stable_id
from .retrieval import DOCUMENT_READ_LIMIT, Corpus, FrozenEmbeddings, corpus_from_snapshots, search as retrieve, snapshot_document
from .skill_calculations import quarter_mentions, targets_prior_quarter
from .market import PriceSnapshot
from .metric_language import NAMES as METRIC_NAMES
from .statement_analysis import ANALYZERS


from .financial import ADDITIVE_CONCEPTS, CALCULATIONS, METRICS, INSTANT_METRICS, duration_class, quarter_from_cumulative, chart_comparison, check_operand_shape
from .financial import COMMON_INCOME, VALUATION_ALLOWANCE_EXPENSE, TAX_SCENARIO, tax_scenario_dimensions, check_tax_scenario_comparison

METADATA_PREVIEW_ROWS = 4
DEFAULT_FILING_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A", "8-K", "8-K/A", "20-F", "40-F", "6-K")
PERIODIC_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
FACT_FORMS = PERIODIC_FORMS | {"8-K", "8-K/A", "6-K", "6-K/A"}
CONCEPT_ALIASES = {"us-gaap:OperatingIncome": "us-gaap:OperatingIncomeLoss",
                   "us-gaap:OperatingProfit": "us-gaap:OperatingIncomeLoss"}
FACT_NAMES = {**METRIC_NAMES, "營業收入": "revenue", "營收": "revenue", "收入": "revenue",
              "營業利潤": "operating_income", "經營利潤": "operating_income", "營業利益": "operating_income",
              "income from operations": "operating_income", "profit from operations": "operating_income",
              "淨利潤": "net_income", "淨收益": "net_income"}
LATEST_QUARTER_REQUEST = re.compile(
    r"(?:最新|最近)(?:一[个個])?\s*(?:已)?(?:公布|公佈|发布|發佈|披露|报告|報告)?的?\s*(?:财季|財季|季度)"
    r"|\b(?:latest|most recent)\s+(?:(?:publicly\s+)?(?:reported|disclosed|published)\s+)?(?:fiscal\s+)?quarter\b",
    re.I)
QUARTER_COMPARISON = re.compile(
    r"同比|环比|環比|比较|比較|对比|對比|去年同期|上年同期|差额|差額|增长率|增長率|"
    r"\b(?:vs\.?|versus|compare|comparison|growth|year.over.year|quarter.over.quarter|yoy|qoq)\b", re.I)


def ordinary_latest_quarter(request):
    from .reporting_period import reported_period
    from .skill_calculations import latest_reported_target

    return bool(latest_quarter.requested(request) and latest_reported_target(request) == "latest"
                and not (QUARTER_COMPARISON.search(request) or targets_prior_quarter(request)
                         or reported_period(request) or any(quarter_mentions(request))))


class CalculatorInputError(DataError):
    """A different operation or different saved numeric operands can resolve this rejection."""


class _FilingText(HTMLParser):
    """Text extraction only: no scripts, requests, iXBRL interpretation or math."""
    def __init__(self, collect_tables=False):
        super().__init__(convert_charrefs=True)
        self.tables = [] if collect_tables else None
        self.table_layouts = []
        self.table_stack = []
        self.row = self.row_widths = self.cell = None
        self.parts = []
        self.link_targets = set()
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "ix:hidden"}:
            self.hidden += 1

        if self.tables is not None and not self.hidden:
            if tag == "table":
                if self.table_stack:
                    self.table_stack[-1][1]["supported"] = False
                rows = []
                preceding = " ".join(" ".join(self.parts[-80:]).split())[-500:]
                layout = {"preceding": preceding, "widths": [], "supported": True}
                self.tables.append(rows)
                self.table_layouts.append(layout)
                self.table_stack.append((rows, layout))
                self.row = self.row_widths = self.cell = None
            elif self.table_stack and tag == "tr":
                rows, layout = self.table_stack[-1]
                self.row, self.row_widths = [], []
                rows.append(self.row)
                layout["widths"].append(self.row_widths)
                self.cell = None
            elif self.row is not None and tag in {"td", "th"}:
                attrs = dict(attrs)
                width = attrs.get("colspan", "1")
                if not re.fullmatch(r"[1-9][0-9]{0,2}", width or "") or attrs.get("rowspan", "1") != "1":
                    self.table_stack[-1][1]["supported"] = False
                    width = "1"
                self.row_widths.append(int(width))
                self.cell = []

        if tag == "a" and not self.hidden:
            target = dict(attrs).get("href")
            if target:
                self.link_targets.add(target)

    def handle_endtag(self, tag):
        if self.table_stack and not self.hidden:
            if tag in {"td", "th"} and self.cell is not None:
                self.row.append(" ".join(" ".join(self.cell).split()))
                self.cell = None
            elif tag == "tr":
                self.cell = self.row = self.row_widths = None
            elif tag == "table":
                self.table_stack.pop()
                self.row = self.row_widths = self.cell = None
        if tag in {"script", "style", "noscript", "ix:hidden"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.cell is not None:
                self.cell.append(data)

    def text(self):
        return " ".join(" ".join(self.parts).split())


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
ENTITY = {"type": "string", "description": "Ticker/name resolved by discover_company, or SEC CIK digits."}
DATE = {"type": "string", "description": "Exact YYYY-MM-DD; do not guess fiscal-calendar dates."}
CUTOFF = {"type": "string", "description": "Information cutoff: ISO date or timezone-aware timestamp."}
DATED_SCOPE = " Requires scope.cutoff or the user's explicit cutoff."
SOURCE = {"type": "string", "enum": ["auto", "replay", "live"], "default": "auto",
          "description": "auto prefers verified replay/persistent cached snapshots, which are not fresh online observations; live always requests a refresh; replay forbids network."}

DIRECT_GUIDANCE = "direct_quarter_revenue_guidance"

TOOL_SPECS = [
    {"name": "discover_company", "description": "Resolve issuers and display finite replay coverage. For one matched issuer, metadata_evidence_id cites the dated local index rows; document_id opens the source text. An exact live CIK fetches submissions; a live ticker/name query fetches the ticker directory. At most one HTTP request.",
     "parameters": _schema({"query": STRING, "source": SOURCE, "cutoff": CUTOFF,
                            "view": {"type": "string", "enum": ["coverage", "identity"], "default": "coverage",
                                     "description": "Identity resolves issuer names only. Coverage includes the local document catalog."}}, ["query"])},
    {"name": "financial_facts", "description": "Read SEC Company Facts using common measure names (e.g. operating profit or 营业利润) or taxonomy:Concept. For an ordinary 'latest reported quarter' or '最新财季' question use period_selection=latest_reported_quarter without dates. This checks indexed quarter facts against known SEC filings and requires evidence of current submissions coverage; follow its next_actions when a newer filing may lead the index. Otherwise specify exact dates; lists of available_periods are candidates, not facts.",
     "parameters": _schema({"entity": ENTITY, "metric": {"type": "string", "description": "Use a common measure name, alias or exact XBRL concept."},
                            "period_start": DATE, "period_end": DATE, "cutoff": CUTOFF, "unit": STRING, "source": SOURCE,
                            "period_selection": {"type": "string", "enum": ["latest_reported_quarter"],
                                                 "description": "For a latest-as-of quarter lookup; omit both period dates."}}, ["metric"])},
    {"name": "discover_filings", "description": "List SEC recent-submissions metadata by acceptance time. earnings_only keeps periodic reports and 8-K Item 2.02, plus 8-K with unknown items. Use source=live to check current submissions when allowed. Cite metadata_evidence_id for the previewed filings, cutoff and snapshot coverage. Use offset to cite later rows. Filing metadata does not establish press-release contents or results. Uses replay or one live submissions request, without following older-history files.",
     "parameters": _schema({"entity": ENTITY, "forms": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 10},
                            "report_date": DATE, "cutoff": CUTOFF, "source": SOURCE, "earnings_only": {"type": "boolean"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                            "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000}})},
    {"name": "read_filing_history", "description": "Read one older SEC submissions JSON file explicitly listed by discover_filings for this issuer. The exact filename must already be discovered. One HTTP request at most; offset paginates returned rows without hiding archive requests. Filing report dates are metadata, not fiscal-quarter inference.",
     "parameters": _schema({"entity": ENTITY, "filename": STRING,
                            "forms": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 10},
                            "report_date": DATE, "cutoff": CUTOFF, "source": SOURCE,
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                            "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000}}, ["filename"])},
    {"name": "read_filing", "description": "Read an already discovered SEC primary HTML filing as exact extracted-text spans. Copy accession and primary_document from discover_filings (primary_document aliases SEC primaryDocument). An 8-K read lists linked HTML documents. To open its earnings release, keep the parent primary_document and pass its listed exhibit_document. If the primary is unavailable in replay, next_actions may identify a separately archived document from the exact same SEC accession. Uses verified replay/cache or one SEC Archives request. query finds a lexical passage; otherwise offset paginates. Supported consolidated income statement cells and explicitly labelled quarterly GAAP exhibit summary cells may supply operands for calculate. On a 10-Q, quarter_operands requests both quarter amounts and their exact filed XBRL dates.",
     "parameters": _schema({"entity": ENTITY, "accession": STRING, "primary_document": STRING, "exhibit_document": STRING,
                            "query": STRING, "offset": {"type": "integer", "minimum": 0, "maximum": 8_000_000},
                            "limit_chars": {"type": "integer", "minimum": 200, "maximum": 6000},
                            "cutoff": CUTOFF, "source": SOURCE,
                            "quarter_operands": {"type": "boolean"}}, ["accession", "primary_document"])},
    {"name": "search_documents", "description": "Retrieve exact, boundary-aware passages using BM25 and strict issuer/publication/report-date filters. period_end filters document report dates, not the period of every statement. Optional dense/hybrid needs configured genuine embeddings. Retrieval never proves semantic support.",
     "parameters": _schema({"entity": ENTITY, "query": STRING, "period_end": DATE,
                            "cutoff": CUTOFF, "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                            "retrieval_mode": {"type": "string", "enum": ["bm25", "dense", "hybrid"]},
                            "forms": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 10},
                            "document_ids": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 50},
                            "max_context_chars": {"type": "integer", "minimum": 1800, "maximum": 18000}}, ["query"])},
    {"name": "read_document", "description": "Open an exact text span. Search and read attach the same supported numeric cells. Read beyond a cut row to recover a complete row. For an operating cash flow bridge use analyze_disclosures; rereading its text_only passage does not generate cash flow operands. Copy document_id from discovery or search and use next_offset to advance.",
     "parameters": _schema({"entity": ENTITY, "document_id": STRING, "cutoff": CUTOFF,
                            "offset": {"type":"integer","minimum":0,"maximum":8_000_000},
                            "limit_chars":{"type":"integer","minimum":200,"maximum":6000}},["document_id"])},
    {"name": "inspect_sales_comparison", "description": "Inspect all local issuer disclosures available at cutoff for the named fiscal quarter and prior-year quarter, including comparative information in later releases. Returns located total/daily sales, selling days and customer-group daily sales, or question-specific gaps.",
     "parameters": _schema({"entity": ENTITY, "fiscal_quarter": STRING, "cutoff": CUTOFF}, ["entity","fiscal_quarter"])},
    {"name": "reconcile_operating_margin", "description": "Reconcile quarterly year-over-year changes in operating profit and operating margin from visible consolidated income-statement cells. Returns signed money and percentage-point contributions for gross profit, expenses and reconciled other operating income, each quarter's gross-to-operating-income check, and missing rows. Reuse previously retrieved cells, or search the original table if they are missing." + DATED_SCOPE,
     "parameters": _schema({"entity": ENTITY, "period_end": DATE, "period_start": DATE,
                            "document_id": STRING, "cutoff": CUTOFF}, ["entity", "period_end"])},
    {"name":"reconcile_net_income", "description":"Locate the issuer's original quarterly income statement and reconcile the change from operating profit to net income against the year-ago quarter. Finds dates, rows and signed amounts itself. Returns saved cells, calculations and any missing evidence. Not for adjusted earnings, margins, cash flow or sequential comparisons." + DATED_SCOPE,
     "parameters":_schema({"entity":ENTITY, "period_start":DATE, "period_end":DATE, "cutoff":CUTOFF},["entity"])},
    {"name":"read_income_explanation", "description":"Read management's same-quarter discussion for material drivers identified by the active income reconciliation. Reuse explanations already cited. Return exact source passages or a specific gap." + DATED_SCOPE,
     "parameters":_schema({"entity":ENTITY, "cutoff":CUTOFF},["entity"])},
    {"name": "find_percentage_cells", "description": "Find dated percentage cells in original replay HTML tables. Supply an issuer and English metric keywords, optionally an exact period or document ID. Returns alternative row labels, period headers, raw values and saved cell IDs. Check issuer, row, dates, accounting basis and table scope; select the matching ID(s) yourself for calculate. Covers explicit calendar-month duration/date headers and values carrying a percent sign.",
     "parameters": _schema({"entity": ENTITY, "query": STRING, "document_id": STRING, "period_start": DATE,
                            "period_end": DATE, "cutoff": CUTOFF, "limit":{"type":"integer","minimum":1,"maximum":12},
                            "offset":{"type":"integer","minimum":0,"maximum":10000}}, ["entity","query"])},
    {"name": "calculate", "description": "Use two saved IDs: numerator/denominator for ratio or margin, current/prior for growth or difference, later/prior cumulative for derive_quarter, end/start for price_return. Difference can also subtract identified same-period flows. exclude_tax_expense_item adds a signed monetary tax-reconciliation valuation-allowance change to reported basic common earnings, with all other allocations fixed. Check attribution in the filing. Compare that scenario to earlier reported common earnings using growth or difference.",
     "parameters": _schema({"operation": {"type": "string", "enum": CALCULATIONS},
                            "operand_ids": {"type": "array", "items": STRING, "minItems": 2, "maxItems": 2}}, ["operation", "operand_ids"])},
    {"name": "analyze_disclosures", "description": "Run selected specialized methods. For reported consolidated quarterly revenue versus the preceding issuer release’s direct midpoint, select implied-period-guidance and explicitly pass comparison=direct_quarter_revenue_guidance and fiscal_quarter. Returns separate source records and a signed percentage calculation from verified local releases. Omit method to run all selected supported methods for other comparisons." + DATED_SCOPE,
     "parameters": _schema({"entity": ENTITY, "cutoff": CUTOFF, "method": {"type": "string", "enum": list(ANALYZERS)},
                            "comparison": {"type": "string", "enum": [DIRECT_GUIDANCE],
                                           "description": "Direct prior-quarter issuer guidance midpoint versus reported revenue. Requires method=implied-period-guidance and fiscal_quarter."},
                            "fiscal_quarter": {"type": "string", "description": "Target quarter as YYYYQn (for example 2026Q2). Required for direct_quarter_revenue_guidance."}}, ["entity"])},
    {"name": "market_prices", "description": "Retrieve an explicit listing's provider daily prices for exact trading-date endpoints, with currency/timezone/split/dividend basis and provenance. Uses configured validated imports, an opt-in provider adapter, or finite legacy replay. No nearest-date substitution. source=live explicitly refreshes the configured adapter.",
     "parameters": _schema({"entity": ENTITY, "start_date": DATE, "end_date": DATE,
                            "cutoff": CUTOFF, "source": SOURCE,
                            "price_type": {"type": "string", "enum": ["close", "adjusted_close"]}}, ["start_date", "end_date"])},
]


class ToolRegistry:
    def __init__(self, store, data_dir, allow_network=False, user_agent=None, cache_dir=None, retrieval_vectors=None, market_data=None, market_adapter=None, retrieval_encoder=None):
        self.store = store
        self.data = SnapshotData(data_dir, allow_network, user_agent, cache_dir=cache_dir)
        self._discovered = list(self.data.manifest["companies"])
        self._discovery_lock = threading.Lock()
        self._filing_lock = threading.Lock()
        self._filings = {}
        self._percentage_index = {}
        self._disclosure_cell_cache = {}
        self.retrieval_vectors = retrieval_vectors
        if retrieval_vectors is not None and retrieval_encoder is not None:
            raise DataError("Choose frozen vectors or a runtime encoder, not both.")
        self.retrieval_encoder = retrieval_encoder
        self._runtime_embeddings = None
        self.market_data = market_data
        self.market_adapter = market_adapter

    @property
    def timeout(self):
        return self.data.timeout

    @timeout.setter
    def timeout(self, value):
        self.data.timeout = max(0.01, min(float(value), 60.0))

    def list_tools(self):
        import copy
        return copy.deepcopy(TOOL_SPECS)

    def available_tools(self):
        """Model-visible tools usable in this workspace; public schema stays complete."""
        specs=self.list_tools()
        has_html = any(d["source"].lower().endswith((".html",".htm")) for d in self.data.manifest["documents"])
        if self.data.allow_network or self.data.cached_urls():
            return [s for s in specs if has_html or s["name"] not in {"find_percentage_cells","reconcile_net_income","read_income_explanation"}]
        enabled={"discover_company"}
        if self.data.manifest["documents"]:
            enabled.update({"search_documents","read_document","analyze_disclosures","inspect_sales_comparison"})
        if has_html:
            enabled.update({"find_percentage_cells", "reconcile_operating_margin", "reconcile_net_income", "read_income_explanation"})
        if any(c.get("facts") or c.get("submissions") for c in self.data.manifest["companies"]):
            enabled.add("financial_facts")
        if any(c.get("submissions") for c in self.data.manifest["companies"]):
            enabled.update({"discover_filings","read_filing_history","read_filing"})
        if self.market_adapter or self.market_data or any(c.get("prices") for c in self.data.manifest["companies"]):
            enabled.add("market_prices")
        enabled.add("calculate")
        return [s for s in specs if s["name"] in enabled]

    def evidence_access(self):
        """Runtime capabilities, not evidence; never expose a future filing title or figure."""
        return {"sec_network_enabled": self.data.allow_network,
                "has_local_document_corpus": bool(self.data.manifest["documents"]),
                "has_local_sec_facts": any(c.get("facts") for c in self.data.manifest["companies"]),
                "has_local_submissions": any(c.get("submissions") for c in self.data.manifest["companies"]),
                "document_tools": ["search_documents","read_document"]}

    def current_document_catalog(self, scope):
        """Compact metadata for this turn's issuer and cutoff; no body or HTTP access."""
        from .context import scope_issuer_keys
        allowed = scope_issuer_keys(scope.get("entities") or [], self.data.manifest["companies"])
        groups = {}
        for doc in self.data.manifest["documents"]:
            if doc["entity"].casefold() not in allowed or not available_by(doc["published_at"], scope.get("cutoff")):
                continue
            key = (doc["entity"], doc.get("form"))
            count, latest = groups.get(key, (0, doc))
            if cutoff_time(doc["published_at"]) > cutoff_time(latest["published_at"]):
                latest = doc
            groups[key] = (count + 1, latest)
        # One entry per form/issuer, so successive quarterly filings do not hide
        # an annual report. discover_company can enumerate a larger collection.
        rows = []
        ordered = sorted(groups.values(), key=lambda group: cutoff_time(group[1]["published_at"]), reverse=True)
        for count, doc in ordered[:8]:
            rows.append({"entity": doc["entity"], "form": doc.get("form"), "count": count,
                         "latest_period_end": doc.get("period_end"),
                         "latest_document_id": stable_id("source_doc_", self.data.info(doc["source"])["url"])})
        return {"cutoff": scope.get("cutoff"), "form_groups_total": len(groups), "forms": rows}

    def execute(self, name, arguments, session_id, scope):
        self.data.network_requests = 0
        try:
            spec = next((s for s in TOOL_SPECS if s["name"] == name), None)
            if spec is None:
                raise DataError("Unknown tool: " + str(name))
            try:
                self._validate_args(arguments, spec["parameters"])
            except DataError as exc:
                if name == "calculate":
                    raise CalculatorInputError(str(exc)) from exc
                raise
            if not isinstance(scope, dict):
                raise DataError("Scope must be an object.")
            if "_source_document_ids" in scope:
                from .source_scope import tool_allowed
                publisher = scope.get("_source_publisher")
                if not tool_allowed(publisher,name):
                    raise DataError("The requested tool would use sources outside the user's source restriction.")
                if name == "calculate" and not set(arguments["operand_ids"]) <= set(scope.get("_source_record_ids",[])):
                    raise CalculatorInputError("Use calculation operands inside the user's source restriction.")
                if name == "discover_company" and publisher != "sec":
                    arguments = {**arguments,"source":"replay","view":"identity"}
            result = getattr(self, "_" + name)(arguments, session_id, scope)
            result.setdefault("ok", True)
            result.setdefault("evidence", [])
            result.setdefault("calculations", [])
            result.setdefault("data", {})
            if name in ("read_document", "search_documents", "read_filing") and result["ok"]:
                if name != "read_filing":
                    from .disclosure_cells import attach
                    attach(result, self.data, self._disclosure_cell_cache)
                data = result["data"]
                if data.get("numeric_operand_ids") or data.get("quarterly_gaap_operands"):
                    data["numeric_operand_status"] = "attached"
                elif data.get("numeric_extraction_unavailable"):
                    data["numeric_operand_status"] = "extraction_unavailable"
                else:
                    data["numeric_operand_status"] = "text_only"
        except CalculatorInputError as exc:
            result = self._failure(str(exc))
            result["data"]["recoverable_input_error"] = True
        except HTTPError as exc:
            result = self._failure("SEC HTTP error " + str(exc.code) + "; no retry was attempted.")
            result["data"]["http_status"] = exc.code
        except (DataError, ValueError, KeyError, OSError, URLError, TimeoutError) as exc:
            result = self._failure(str(exc))
        result["data"]["network_requests"] = self.data.network_requests
        return result

    @staticmethod
    def _failure(message):
        return {"ok": False, "summary": message, "error": message,
                "evidence": [], "calculations": [], "data": {}}

    @staticmethod
    def _effective_cutoff(args, scope, *, required=False):
        requested = args.get("cutoff")
        scoped = scope.get("cutoff")
        requested_time, scoped_time = cutoff_time(requested), cutoff_time(scoped)
        if requested_time is not None and scoped_time is not None and requested_time > scoped_time:
            raise DataError("Tool cutoff cannot extend the active scope cutoff. Update the visible research scope explicitly first.")
        if required and requested is None and scoped is None:
            raise DataError("A dated analysis cutoff is required. Set scope.cutoff or pass the user's explicit cutoff "
                            "as an ISO date or timezone-aware timestamp. If the date is unknown, ask the user. "
                            "The quarter-end and the filing publication date do not set the research cutoff.")
        return requested if requested is not None else scoped

    @staticmethod
    def _validate_args(args, schema):
        if not isinstance(args, dict):
            raise DataError("Tool arguments must be an object.")
        unknown = set(args) - set(schema["properties"])
        if unknown:
            raise DataError("Unknown argument(s): " + ", ".join(sorted(unknown)))
        for key in schema["required"]:
            if key not in args:
                raise DataError("Missing argument: " + key)
        for key, value in args.items():
            rule = schema["properties"][key]
            kind = rule["type"]
            if kind == "string" and (not isinstance(value, str) or not value.strip()):
                raise DataError(key + " must be a non-empty string.")
            if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise DataError(key + " must be an integer.")
            if kind == "boolean" and not isinstance(value, bool):
                raise DataError(key + " must be a boolean.")
            if kind == "array" and (not isinstance(value, list) or not all(isinstance(x, str) and x for x in value)):
                raise DataError(key + " must be an array of non-empty strings.")
            if "enum" in rule and value not in rule["enum"]:
                raise DataError("Unsupported " + key + ": " + str(value))
            if kind == "integer" and not rule.get("minimum", value) <= value <= rule.get("maximum", value):
                raise DataError(key + " is outside the permitted range.")
            if kind == "array" and not rule.get("minItems", len(value)) <= len(value) <= rule.get("maxItems", len(value)):
                raise DataError(key + " has the wrong number of items.")

    def _entity(self, args, scope):
        query = args.get("entity")
        if query is None:
            entities = scope.get("entities", [])
            if len(entities) != 1 or not isinstance(entities[0], str):
                raise DataError("Specify one entity, or resolve a single active entity in scope.")
            query = entities[0]
        explicit_cik = bool(re.fullmatch(r"(?:CIK)?\d{1,10}", query, re.I))
        matches = self._matches(query, restore=not (explicit_cik and args.get("source") == "live"))
        key = query.casefold().strip()
        identifiers = [c for c in matches if key in {c["ticker"].casefold(), c["cik"], str(int(c["cik"]))}]
        if len(identifiers) == 1:
            return identifiers[0]
        exact = [c for c in matches if key in {name.casefold() for name in (c.get("name"), *c.get("aliases", [])) if name}]
        if len(exact) == 1:
            return exact[0]
        if len(matches) == 1:
            return matches[0]
        if re.fullmatch(r"(?:CIK)?\d{1,10}", query, re.I):
            cik = re.sub("^CIK", "", query, flags=re.I).zfill(10)
            return {"cik": cik, "name": None, "ticker": "CIK" + cik}
        raise DataError("Issuer is unresolved or ambiguous; call discover_company first and select its ticker or CIK.")

    def _matches(self, query, restore=True):
        q = query.casefold().strip()
        numeric = re.sub("^cik", "", q).lstrip("0")
        def matched():
            with self._discovery_lock:
                return [dict(c) for c in self._discovered if q == c.get("ticker", "").casefold()
                        or q in (c.get("name") or "").casefold()
                        or any(q == alias.casefold() for alias in c.get("aliases", []))
                        or numeric == str(int(c["cik"]))]
        result = matched()
        if not result and restore:
            self._restore_companies(query)
            result = matched()
        return result

    def _merge_companies(self, candidates):
        with self._discovery_lock:
            existing = {(c["cik"], c["ticker"]): c for c in self._discovered}
            for incoming in candidates:
                key = (incoming["cik"], incoming["ticker"])
                previous = existing.get(key)
                if previous:
                    before = previous.get("identity_source", {}).get("retrieved_at")
                    after = incoming.get("identity_source", {}).get("retrieved_at")
                    if before and after and cutoff_time(after) < cutoff_time(before):
                        continue
                    previous.update(incoming)
                else:
                    copy = dict(incoming)
                    self._discovered.append(copy)
                    existing[key] = copy

    def _restore_companies(self, query):
        """Rebuild public issuer identities only from verified original SEC JSON."""
        ticker_url = "https://www.sec.gov/files/company_tickers.json"
        urls = self.data.cached_urls()
        urls.sort(key=lambda url: (url != ticker_url, url))
        for url in urls:
            if url != ticker_url and not re.fullmatch(r"https://data\.sec\.gov/submissions/CIK\d{10}\.json", url):
                continue
            restored = self.data.cached_sec_json(url)
            if restored is None:
                continue
            raw, info = restored
            if url == ticker_url:
                candidates = [{"cik": str(r["cik_str"]).zfill(10), "name": r["title"], "ticker": r["ticker"], "identity_source": info}
                              for r in raw.values()]
            else:
                cik = str(raw["cik"]).zfill(10)
                if not url.endswith("CIK" + cik + ".json"):
                    raise DataError("Cached submissions identity does not match its source URL.")
                candidates = [{"cik": cik, "name": raw.get("name") or "CIK" + cik, "ticker": ticker, "identity_source": info}
                              for ticker in (raw.get("tickers") or ["CIK" + cik])]
            self._merge_companies(candidates)
            q = query.casefold().strip()
            numeric = re.sub("^cik", "", q).lstrip("0")
            if any(q == c["ticker"].casefold() or q in c["name"].casefold() or numeric == str(int(c["cik"])) for c in candidates):
                return

    def _sec_json(self, url, source):
        if source != "live":
            cached = self.data.cached_sec_json(url)
            if cached is not None:
                return cached
        if source == "replay":
            raise DataError("No verified replay or persistent cached JSON snapshot for this SEC source.")
        return self.data.get_sec_json(url)

    def _company_json(self, company, field, url, source):
        snapshot = company.get(field)
        if source == "live":
            return self.data.get_sec_json(url)
        bundled_info = self.data.info(snapshot) if snapshot else None
        cached = self.data.cached_sec_json(url) if source == "auto" or not snapshot else None
        if cached and (not bundled_info or cutoff_time(cached[1]["retrieved_at"]) >= cutoff_time(bundled_info["retrieved_at"])):
            return cached
        if snapshot:
            return self.data.json(snapshot), bundled_info
        if source == "replay":
            raise DataError("No verified replay or persistent cached JSON snapshot for this SEC source.")
        return self.data.get_sec_json(url)

    def _discover_company(self, args, session_id, scope):
        source = args.get("source", "auto")
        query = args["query"].strip()
        restricted = scope.get("_source_publisher") in {"issuer","non_sec"}
        matches = self._matches(query, restore=source != "live" and not restricted)
        if restricted:
            local_tickers = {c["ticker"] for c in self.data.manifest["companies"]}
            matches = [{**c,"identity_source":None} for c in matches if c["ticker"] in local_tickers]
        if not matches and source != "live" and not restricted:
            market_identities = (PriceSnapshot.from_path(self.market_data).identities() if self.market_data else [])
            if self.market_adapter:
                market_identities += list(self.market_adapter.instruments)
            market_matches = {row["entity"]: row for row in market_identities if row["entity"].casefold() == query.casefold()
                              or (row.get("cik") and query.removeprefix("CIK").zfill(10) == row["cik"])}
            matches = [{"ticker": row["entity"], "cik": row.get("cik"), "name": row.get("name") or row["entity"],
                        "market_mapping": dict(row), "identity_source": {"mode": "configured_market_mapping"}}
                       for row in market_matches.values()]
        info = matches[0].get("identity_source") if len(matches) == 1 else None
        if source == "live" or (not matches and source == "auto" and self.data.allow_network):
            if re.fullmatch(r"(?:CIK)?\d{1,10}", query, re.I):
                cik = re.sub("^CIK", "", query, flags=re.I).zfill(10)
                raw, info = self._sec_json("https://data.sec.gov/submissions/CIK" + cik + ".json", source)
                matches = [{"cik": cik, "name": raw["name"], "ticker": (raw.get("tickers") or ["CIK" + cik])[0], "identity_source": info}]
            else:
                raw, info = self._sec_json("https://www.sec.gov/files/company_tickers.json", source)
                q = query.casefold()
                matches = [{"cik": str(r["cik_str"]).zfill(10), "name": r["title"], "ticker": r["ticker"], "identity_source": info}
                           for r in raw.values() if q == r["ticker"].casefold() or q in r["title"].casefold()]
            self._merge_companies(matches)
        coverage = []
        cutoff = self._effective_cutoff(args, scope)
        evidence = []
        for c in matches[:20]:
            documents = []
            catalog_rows = []
            catalog = self.data.manifest["documents"] if args.get("view") != "identity" else []
            for index, entry in enumerate(catalog):
                if entry["entity"] != c["ticker"] or not available_by(entry["published_at"], cutoff):
                    continue
                info_doc = self.data.info(entry["source"])
                doc = {"document_id": stable_id("source_doc_", info_doc["url"]), "source_url": info_doc["url"],
                       "form": entry.get("form"), "period_end": entry.get("period_end"),
                       "published_at": entry["published_at"], "open_with": "read_document"}
                documents.append(doc)
                catalog_rows.append((index, entry, doc))
            if len(matches) == 1 and catalog_rows and self.data.manifest_hash:
                preview = sorted(catalog_rows, key=lambda item: cutoff_time(item[1]["published_at"]), reverse=True)[:METADATA_PREVIEW_ROWS]
                locator = ", ".join(f"documents[{index}]" for index, _, _ in preview)
                summary = [f"Finite local replay catalog for {c['ticker']}: {len(documents)} eligible document(s) "
                           f"at cutoff {cutoff or 'unspecified'}, newest publication first. Catalog metadata only."]
                for _, entry, doc in preview:
                    summary.append(f"{doc['form'] or 'Document'} | document report date {doc['period_end']} | "
                                   f"published_at {doc['published_at']} ({entry.get('publication_basis') or 'catalog metadata'}) | "
                                   f"{doc['source_url']}")
                record = self._metadata_record(c["ticker"], "manifest.json", self.data.manifest_hash, "manifest.json",
                                               locator, preview[0][1]["published_at"], self.data.manifest.get("copied_at"),
                                               cutoff, "replay", "\n".join(summary))
                evidence.append(record)
                for _, _, doc in preview:
                    doc["metadata_evidence_id"] = record["id"]
            coverage.append({"entity": c["ticker"], "name": c["name"], "cik": c["cik"],
                             "replay_facts": bool(c.get("facts")),
                             "replay_documents": documents,
                             "replay_prices": bool(c.get("prices"))})
            coverage[-1]["identity_source"] = c.get("identity_source")
            coverage[-1]["market_mapping"] = c.get("market_mapping")
        return {"ok": bool(matches), "summary": f"Found {len(matches)} issuer candidate(s). Replay coverage is finite; other SEC issuers require explicit network access.",
                "evidence": evidence,
                "data": {"companies": coverage, "truncated": len(matches) > 20,
                         "network_enabled": self.data.allow_network, "source": info,
                         "replay_entities": [c["ticker"] for c in self.data.manifest["companies"]]}}

    def _reconcile_operating_margin(self, args, session_id, scope):
        from .context import scope_issuer_keys
        from .margin_reconciliation import margin_reconciliation
        entity = self._entity(args, scope)["ticker"]
        allowed = scope_issuer_keys(scope.get("entities"), self.data.manifest["companies"])
        if allowed is not None and entity.casefold() not in allowed:
            raise DataError("Issuer is outside the current research scope.")
        cutoff = self._effective_cutoff(args, scope, required=True)
        rows = [r for r in self.store.get_context(session_id, report_mode="none")["evidence"]
                if r.get("cell_type") == "statement_amount_cell" and r.get("entity") == entity
                and r.get("published_at") and available_by(r["published_at"], cutoff)
                and (not args.get("document_id") or r.get("document_id") == args["document_id"])]
        bridge = margin_reconciliation(rows, entity=entity, period_end=args["period_end"], period_start=args.get("period_start"))
        if not bridge:
            raise DataError("No unambiguous retrieved quarterly statement for the named issuer and period at the "
                            "information cutoff. Locate eligible original revenue, gross profit, expense and operating income rows.")
        calculations = bridge.pop("calculations")
        return {"summary": bridge["summary"], "calculations": calculations, "data": {"margin_reconciliation": bridge}}

    def _reconcile_net_income(self, args, session_id, scope):
        from .income_bridge import analyze
        entity=self._entity(args,scope)["ticker"]
        if scope.get("entities") and entity not in scope["entities"]:
            raise DataError("Requested issuer is not in the current scope.")
        cutoff=self._effective_cutoff(args,scope,required=True)
        dates={}
        for field in ("period_start","period_end"):
            if scope.get(field) and args.get(field) and scope[field]!=args[field]:
                raise DataError("Quarter does not match the active scope.")
            dates[field]=args.get(field) or scope.get(field)
            if dates[field]:
                parse_day(dates[field])
        result = analyze(self.data,entity,cutoff,**dates)
        result["data"]["cutoff"] = cutoff
        return result

    def _read_income_explanation(self, args, session_id, scope):
        from . import income_explanation, income_followup
        from .context import as_of_context, current_tool_receipts

        entity = self._entity(args, scope)["ticker"]
        cutoff = self._effective_cutoff(args, scope, required=True)
        if scope.get("entities") != [entity]:
            raise DataError("A single active issuer is required.")
        event = self.store.latest_event(session_id, "episode_checkpoint") or {}
        episode = event.get("episode", {})
        if episode.get("status") != "running" or not income_followup.wants_explanation(episode.get("request", "")):
            raise DataError("The active question must request an explanation.")
        ctx = self.store.get_context(session_id, report_mode="none")
        ctx.update(scope={**scope, "cutoff":cutoff}, episode=episode,
                   request_parts=episode.get("clarified_parts") or episode.get("inherited_parts"))
        ctx = current_tool_receipts(as_of_context(ctx))
        bridge = income_followup.current_bridge(ctx)
        if not bridge or not income_followup.wants_explanation(episode["request"], ctx["request_parts"], bridge["part_ids"]):
            raise DataError("This question has no current, recorded income comparison visible at the information cutoff.")
        return income_explanation.inspect(self.document_corpus(), entity, cutoff, bridge, ctx["calculations"],
                                          existing_evidence=ctx["evidence"])

    def _analyze_disclosures(self, args, session_id, scope):
        from .skill_routing import select_skills
        from .statement_analysis import analyze
        entity = self._entity(args, scope)["ticker"]
        cutoff = self._effective_cutoff(args, scope, required=True)
        turns = [m["content"] for m in self.store.get_context(session_id, report_mode="none")["messages"]
                 if m["role"] == "user"]
        if not turns:
            raise DataError("No active user question")
        question, history = turns[-1], turns[:-1]
        latest_event = getattr(self.store, "latest_event", None)
        checkpoint = latest_event(session_id, "episode_checkpoint") if latest_event else None
        active = (checkpoint or {}).get("episode", {})
        if active.get("status") == "running":
            question = active["request"]
        from .skill_routing import named_issuers, same_issuer_history
        from .skill_calculations import targets_prior_quarter
        companies = self.data.manifest["companies"]
        named = named_issuers(question, companies)
        if named and entity not in named and entity not in scope.get("entities", []):
            raise DataError("The selected issuer does not match the active question or comparison scope.")
        history = same_issuer_history(history, entity, companies)
        planner_owned = active.get("status") == "running" and active.get("skill_selection_source") in {"planner", "not_specified"}
        if planner_owned:
            allowed = [s for s in active.get("research_skills", []) if s in ANALYZERS]
            if args.get("method") and args["method"] not in allowed:
                raise DataError("The requested analyzer was not selected for this turn.")
            methods = [args["method"]] if args.get("method") else allowed
            if not methods:
                raise DataError("No specialized disclosure analyzer applies; use document search/read and calculation tools.")
        else:
            methods = [args["method"]] if args.get("method") else select_skills(question, history)
        quarter = args.get("fiscal_quarter")
        direct_guidance = args.get("comparison") == DIRECT_GUIDANCE
        if direct_guidance:
            if args.get("method") != "implied-period-guidance" or not re.fullmatch(r"20\d{2}Q[1-4]", quarter or ""):
                raise DataError("Direct quarterly revenue guidance needs method=implied-period-guidance and fiscal_quarter=YYYYQn.")
        elif quarter is not None:
            raise DataError("fiscal_quarter is only supported with comparison=direct_quarter_revenue_guidance.")
        elif targets_prior_quarter(question):
            raise DataError("Resolve the requested prior fiscal quarter before running a statement comparison.")
        focus = active.get("followup_focus", {}) if active.get("status") == "running" else {}
        window = {key: scope[key] for key in ("period_start", "period_end") if scope.get(key)}
        if direct_guidance:
            return analyze(self.data, entity, cutoff, question, history, methods,
                           guidance_fiscal_quarter=quarter, **window)
        if focus.get("metric") == "operating_cash_flow" and focus.get("entity") == entity:
            end = focus.get("period_end") or scope.get("period_end")
            if not end:
                raise DataError("需先从发行人公告确定所指财季的期末日期。")
            if scope.get("period_end") not in (None, end):
                raise DataError("The active fiscal quarter conflicts with the cash flow request.")
            return analyze(self.data, entity, cutoff, question, history, methods, cash_quarter_end=end, **window)
        return analyze(self.data, entity, cutoff, question, history, methods, **window)

    @staticmethod
    def _concepts(metric):
        if metric in METRICS:
            return [("us-gaap", c) for c in METRICS[metric]]
        if any(metric in tags for tags in METRICS.values()):
            return [("us-gaap", metric)]
        if re.fullmatch(r"(?:us-gaap|ifrs-full):[A-Za-z][A-Za-z0-9]*", metric):
            return [tuple(metric.split(":"))]
        raise DataError("Unknown metric. Use a common measure name such as operating income, an alias such as operating_income, or an exact us-gaap:Concept / ifrs-full:Concept.")

    def _financial_source_actions(self, company, cutoff, filing=None, refresh=False):
        common = {"entity": company["ticker"], "cutoff": cutoff}
        discover = dict(common, earnings_only=True)
        if refresh and self.data.allow_network:
            discover["source"] = "live"
        actions = self._document_source_actions(company, cutoff, (filing or {}).get("filingDate"))
        if company.get("submissions") or self.data.allow_network or filing or not actions:
            actions.insert(0, {"tool": "discover_filings", "arguments": {
                key: value for key, value in discover.items() if value is not None}})
        if filing and filing["url"]:
            arguments = dict(common, accession=filing["accession"], primary_document=filing["primary_document"])
            if filing["form"] in PERIODIC_FORMS:
                arguments["query"] = "consolidated statements of income operations revenue operating income"
            actions.insert(0, {"tool": "read_filing", "arguments": {
                key: value for key, value in arguments.items() if value is not None}})
        return actions

    def _document_source_actions(self, company, cutoff, not_before=None):
        documents = [doc for doc in self.data.manifest["documents"]
                     if doc["entity"] == company["ticker"] and available_by(doc["published_at"], cutoff)
                     and (not not_before or doc["published_at"][:10] >= not_before)]
        if documents:
            arguments = {"entity": company["ticker"], "query": "consolidated results earnings release"}
            if cutoff is not None:
                arguments["cutoff"] = cutoff
            if not_before:
                arguments["document_ids"] = [stable_id("source_doc_", self.data.info(doc["source"])["url"])
                                             for doc in documents[:50]]
            return [{"tool": "search_documents", "arguments": arguments}]
        return []

    def _active_latest_quarter(self, session_id, scope, company):
        if scope.get("planning_task") or len(scope.get("entities") or []) > 1:
            return False
        latest_event = getattr(self.store, "latest_event", None)
        event = latest_event(session_id, "episode_checkpoint") if latest_event else None
        episode = (event or {}).get("episode", {})
        request = episode.get("request", "")
        if episode.get("status") != "running" or not ordinary_latest_quarter(request):
            return False
        from .skill_routing import named_issuers
        named = named_issuers(request, self.data.manifest["companies"])
        return not named or named == [company["ticker"]]

    def _latest_fact_quarter(self, company, cutoff, rows, root_info, filings, data):
        """Resolve latest only when periodic and earnings-submission coverage agrees."""
        if root_info:
            data["submissions_snapshot"] = root_info
        quarters = [r for r in rows if r[4].get("end") and available_by(r[4]["end"], cutoff) and
                    duration_class(r[4].get("start"), r[4]["end"]) == "quarter_length"]
        end = max((r[4]["end"] for r in quarters), default=None)
        matches = [r for r in quarters if r[4]["end"] == end]
        first = min((cutoff_time(r[5]) for r in matches), default=None)
        if matches:
            data["latest_indexed_periods"] = self._periods(sorted({
                (r[4]["start"], end, r[2], f"{r[0]}:{r[1]}") for r in matches}))
        eligible = self._filter_filings(filings, {"forms": sorted(FACT_FORMS), "earnings_only": True}, cutoff)
        possible_newer = next((row for row in eligible if
            (row["form"] in PERIODIC_FORMS and row["reportDate"] and (not end or row["reportDate"] > end))
            or (row["form"] in {"8-K", "8-K/A", "6-K", "6-K/A"} and
                (not first or cutoff_time(row["published_at"]) > first)
                and (not end or not row["reportDate"] or row["reportDate"] > end)
                and not any(r[4].get("accn") == row["accession"] for r in matches))), None)
        if possible_newer:
            data.update(status="filing_review_required", latest_filing_candidate=possible_newer,
                        next_actions=self._financial_source_actions(company, cutoff, possible_newer))
            return None, end, "A newer eligible earnings filing may have reported a quarter ahead of Company Facts. Read the filing and its earnings release to verify the reported period and figures."
        if (not root_info or cutoff_time(root_info["retrieved_at"]) < cutoff_time(cutoff)
                or (first and not any(cutoff_time(row["published_at"]) <= first for row in filings))):
            data.update(status="submissions_coverage_required",
                        next_actions=self._financial_source_actions(company, cutoff, refresh=True))
            return None, end, "Verified recent-submissions coverage is incomplete at the cutoff. Check earnings filings, then specify this same cutoff when requesting the latest quarter again."
        starts = {r[4]["start"] for r in matches}
        if len(starts) != 1:
            data.update(status="ambiguous_quarter" if starts else "no_indexed_quarter",
                        next_actions=self._financial_source_actions(company, cutoff))
            return None, end, "Company Facts does not establish one exact quarter duration at the cutoff. Inspect the issuer's original disclosures."
        return starts.pop(), end, None

    def _financial_facts(self, args, session_id, scope):
        company = self._entity(args, scope)
        metric = args["metric"].strip()
        metric = CONCEPT_ALIASES.get(metric, metric)
        metric = FACT_NAMES.get(re.sub(r"[\s_-]+", " ", metric).casefold(), metric)
        concepts = self._concepts(metric)
        source = args.get("source", "auto")
        cutoff = self._effective_cutoff(args, scope)
        cutoff_time(cutoff)
        active_latest = self._active_latest_quarter(session_id, scope, company)
        exact_dates = any(key in args or scope.get(key) for key in ("period_start", "period_end"))
        latest = args.get("period_selection") == "latest_reported_quarter" or (active_latest and not exact_dates)
        if latest and ("period_start" in args or "period_end" in args):
            raise DataError("Choose either latest_reported_quarter or explicit period dates.")
        if latest and not cutoff:
            cutoff = datetime.now(timezone.utc).isoformat()
        start = None if latest else args.get("period_start", scope.get("period_start"))
        end = None if latest else args.get("period_end", scope.get("period_end"))
        if start:
            parse_day(start)
        if end:
            parse_day(end)
        if start and end and start > end:
            raise DataError("Period start must not follow period end.")
        lookup = {"entity": company["ticker"], "metric": metric, "cutoff": cutoff,
                  "resolved_concepts": [f"{namespace}:{concept}" for namespace, concept in concepts]}
        if args["metric"] != metric:
            lookup["requested_metric"] = args["metric"]
        try:
            payload, info = self._company_json(company, "facts", "https://data.sec.gov/api/xbrl/companyfacts/CIK" + company["cik"] + ".json", source)
            if not isinstance(payload, dict) or str(payload.get("cik", "")).zfill(10) != company["cik"]:
                raise DataError("SEC facts issuer identity does not match the requested CIK.")
            if not isinstance(payload.get("facts"), dict):
                raise DataError("Invalid SEC Company Facts taxonomy data.")
        except (ValueError, KeyError, OSError) as exc:
            reason = f"SEC HTTP error {exc.code}; no retry was attempted." if isinstance(exc, HTTPError) else str(exc)
            result = self._failure("Company Facts unavailable. " + reason)
            result["data"].update(lookup, status="company_facts_unavailable",
                                  next_actions=self._financial_source_actions(company, cutoff, refresh=True))
            return result
        lookup["mode"] = info["mode"]
        publications = {}
        filing_sources = defaultdict(dict)
        root_info, root_rows = None, []
        try:
            known = self._known_submissions(company)
            for root_payload, observation in known:
                indexed = self._index_filings(root_payload, company, observation)
                root_info, root_rows = observation, indexed
                recent = root_payload.get("filings", {}).get("recent", {})
                publications.update({accession: time for accession, time in
                                     zip(recent.get("accessionNumber", []), recent.get("acceptanceDateTime", [])) if time})
            # Already downloaded historical pages can refine filing times.
            self._restore_filing_history(company, known)
        except (ValueError, KeyError, OSError) as exc:
            lookup["submissions_unavailable"] = str(exc)
        with self._filing_lock:
            publications.update({accession: row["acceptanceDateTime"]
                                 for (cik, accession, _), row in self._filings.items()
                                 if cik == company["cik"] and row.get("acceptanceDateTime")})
            for (cik, accession, _), row in self._filings.items():
                if cik == company["cik"] and row.get("url") and available_by(row["published_at"], cutoff):
                    filing_sources[accession, row["form"]][row["url"]] = row
        rows = []
        found_concept = False
        for namespace, concept in concepts:
            taxonomy = payload["facts"].get(namespace, {})
            found_concept |= concept in taxonomy
            units = taxonomy.get(concept, {}).get("units", {})
            for unit, facts in units.items():
                for index, fact in enumerate(facts):
                    pub = publications.get(fact.get("accn")) or fact.get("filed")
                    if fact.get("form") not in FACT_FORMS:
                        continue
                    if not pub or not available_by(pub, cutoff):
                        continue
                    rows.append((namespace, concept, unit, index, fact, pub))
        requested_unit = args.get("unit")
        units = sorted({row[2] for row in rows})
        if requested_unit:
            rows = [row for row in rows if row[2] == requested_unit]
        periods = sorted({(r[4].get("start"), r[4].get("end"), r[2], r[0] + ":" + r[1]) for r in rows},
                         key=lambda x: (x[1] or "", x[0] or "", x[2], x[3]), reverse=True)
        if latest:
            start, end, gap = self._latest_fact_quarter(company, cutoff, rows, root_info, root_rows, lookup)
            if gap:
                if not found_concept:
                    lookup["concept_status"] = "concept_unavailable_in_snapshot"
                return {"ok": False, "summary": gap, "data": dict(lookup, snapshot=info)}
        elif active_latest and end:
            # A Planner may supply exact dates. Keep that lookup intact and
            # check its period against the already loaded filings catalog.
            check = {"cutoff": cutoff or datetime.now(timezone.utc).isoformat()}
            verified_start, verified_end, gap = self._latest_fact_quarter(
                company, check["cutoff"], rows, root_info, root_rows, check)
            if not gap:
                check.update(status="catalog_verified", period_start=verified_start, period_end=verified_end)
            lookup["latest_quarter_check"] = check
        if rows and all(not r[4].get("start") for r in rows):
            if "period_start" in args:
                raise DataError("This is an instant fact. Specify period_end only, without period_start.")
            # An active income-statement duration does not turn assets into a flow.
            start = None
        narrowed = [r for r in rows if (not start or r[4].get("start") == start) and (not end or r[4].get("end") == end)]
        need_start = any(r[4].get("start") for r in narrowed)
        if not narrowed:
            if not found_concept:
                status, detail = "concept_unavailable_in_snapshot", "The resolved concept is absent from the issuer's Company Facts snapshot."
            elif units and requested_unit and requested_unit not in units:
                status, detail = "unit_unavailable", "The requested unit has no eligible facts at the cutoff."
            elif not rows:
                status, detail = "no_facts_at_cutoff", "The concept has no eligible facts at the information cutoff."
            else:
                status, detail = "no_exact_period", "No eligible fact matches the requested dates. Other available periods are listed for inspection."
            return {"ok": False, "summary": detail, "data": dict(
                lookup, status=status, snapshot=info, available_units=units, available_periods=self._periods(periods[:30]),
                next_actions=self._financial_source_actions(company, cutoff))}
        if not end or (need_start and not start):
            available = [p for p in periods if (not end or p[1] == end) and (not start or p[0] == start)]
            return {"summary": "These are indexed candidate periods. Specify exact dates to read a fact. For a latest-reported-quarter question use period_selection=latest_reported_quarter.",
                    "data": dict(lookup, status="period_selection_required", available_periods=self._periods(available[:100]),
                                 periods_truncated=len(available) > 100, snapshot=info, supported_metrics=list(METRICS))}
        # Do not silently order date-only and timestamp publications on the same day.
        latest_day = max(cutoff_time(r[5]).date() for r in narrowed)
        candidates = [r for r in narrowed if cutoff_time(r[5]).date() == latest_day]
        same_day_uncertain = any("T" not in r[5] for r in candidates)
        if not same_day_uncertain:
            latest_publication = max(cutoff_time(r[5]) for r in candidates)
            candidates = [r for r in candidates if cutoff_time(r[5]) == latest_publication]
        unique = {}
        for r in candidates:
            key = (r[0], r[1], r[2], r[4].get("val"), r[4].get("accn"))
            unique.setdefault(key, r)
        by_observation = {}
        for r in unique.values():
            key = (r[0], r[1], r[2], r[4].get("start"), r[4].get("end"))
            by_observation.setdefault(key, []).append(r)
        unresolved = [group for group in by_observation.values() if len({r[4].get("val") for r in group}) > 1]
        if unresolved:
            return {"ok": False, "summary": "Conflicting values for one logical observation at the latest eligible publication; no value was selected. Inspect filings or retrieve another source.",
                    "data": dict(lookup, status="conflicting_facts", conflicting_values=True,
                             same_day_order_uncertain=same_day_uncertain, snapshot=info,
                             candidates=[{"concept": r[0] + ":" + r[1], "unit": r[2],
                                 "value": r[4].get("val"), "accession": r[4].get("accn"), "published_at": r[5],
                                 "period_start": r[4].get("start"), "period_end": r[4].get("end")}
                                 for group in unresolved for r in group])}
        # Equal duplicates in separate same-day filings represent one observation.
        selected = [sorted(group, key=lambda r: r[4].get("accn", ""))[-1] for group in by_observation.values()]
        evidence = []
        for namespace, concept, unit, index, fact, pub in selected:
            value = self._number(fact.get("val"))
            locator = f"facts.{namespace}.{concept}.units.{unit}[{index}]; accession={fact.get('accn')}; start={fact.get('start')}; end={fact.get('end')}"
            logical_key = [company["cik"], namespace, concept, unit, fact.get("start"), fact.get("end")]
            record = {"id": stable_id("fact_", logical_key), "kind": "financial_fact",
                      "entity": company["ticker"], "cik": company["cik"],
                      "metric": next((alias for alias, tags in METRICS.items() if namespace == "us-gaap" and concept in tags), metric),
                      "concept": namespace + ":" + concept, "period_start": fact.get("start"), "period_end": fact.get("end"),
                      "unit": unit, "currency": unit[:3] if re.fullmatch(r"[A-Z]{3}(?:/shares)?", unit) else None,
                      "value": self._serial_number(value), "scale": 1,
                      "basis": "US-GAAP" if namespace == "us-gaap" else "IFRS", "business_scope": "consolidated",
                      "published_at": pub, "publication_precision": "timestamp" if "T" in pub else "date_only",
                      "source_url": info["url"], "locator": locator, "snapshot_hash": info["sha256"],
                      "snapshot_path": info["snapshot"], "retrieved_at": info["retrieved_at"],
                      "accession": fact.get("accn"), "form": fact.get("form"), "mode": info["mode"]}
            associated = filing_sources.get((record["accession"], record["form"]), {})
            if len(associated) == 1:
                filing = next(iter(associated.values()))
                record.update(filing_url=filing["url"], filing_link_provenance={
                    "source_url": filing["source_url"], "locator": filing["metadata_locator"],
                    "snapshot_hash": filing["snapshot_hash"], "report_date": filing["reportDate"]})
            record["concept_label"] = payload["facts"][namespace][concept].get("label")
            record["concept_description"] = payload["facts"][namespace][concept].get("description")
            record["period_length_class"] = duration_class(record["period_start"], record["period_end"])
            record["fiscal_period_context"] = {"fy": fact.get("fy"), "fp": fact.get("fp"), "frame": fact.get("frame"), "selection_role": "context only; exact dates select facts"}
            record["duration_days"] = (parse_day(record["period_end"]) - parse_day(record["period_start"])).days + 1 if record["period_start"] else None
            evidence.append(record)
        conflicting = len({(e["unit"], e["value"]) for e in evidence}) > 1
        return {"summary": f"Returned {len(evidence)} fact(s) for exact period from the latest eligible filing in the selected {info['mode']} snapshot. " + ("Conflicting values or units require resolution." if conflicting else ""),
                "evidence": evidence, "data": dict(lookup, status="facts_found", conflicting_values=conflicting,
                            selection="latest_reported_quarter" if latest else "latest_public_filing_at_cutoff",
                            warning="Tag definitions and restatements must be reviewed. Full-year and YTD values are never silently treated as a quarter.")}

    @staticmethod
    def _periods(periods):
        return [{"period_start": p[0], "period_end": p[1], "unit": p[2], "concept": p[3]} for p in periods]

    @staticmethod
    def _metadata_record(entity, url, digest, path, locator, newest_publication, retrieved, cutoff, mode, text):
        # A citation to the filtered page also asserts its coverage. An earlier
        # filing's publication alone cannot date that later coverage observation.
        bounds = [date for date in (cutoff, retrieved) if date]
        observed_through = min(bounds, key=cutoff_time) if bounds else newest_publication
        available_at = max((newest_publication, observed_through), key=cutoff_time)
        return {"id": stable_id("metadata_", [entity, url, digest, locator, text]), "kind": "source_metadata",
                "entity": entity, "text": text, "source_url": url, "snapshot_hash": digest,
                "snapshot_path": path, "locator": locator, "published_at": available_at,
                "publication_basis": "effective availability of filtered metadata preview", "coverage_cutoff": cutoff,
                "retrieved_at": retrieved, "mode": mode}

    @staticmethod
    def _filing_url(cik, accession, primary_document):
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise DataError("Invalid SEC accession format.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.html?", primary_document):
            raise DataError("Only a plain SEC primary HTML filename is supported.")
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_document}"

    def _index_filings(self, payload, company, info, history=False):
        if payload.get("cik") is not None and str(payload["cik"]).zfill(10) != company["cik"]:
            raise DataError("SEC submissions issuer identity does not match the requested CIK.")
        recent = payload if history else payload.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict) or not isinstance(recent.get("accessionNumber", []), list):
            raise DataError("Invalid SEC submissions columnar filing data.")
        count = len(recent.get("accessionNumber", []))
        for column in ("filingDate", "form", "primaryDocument", "reportDate", "acceptanceDateTime", "items"):
            if column in recent and (not isinstance(recent[column], list) or len(recent[column]) != count):
                raise DataError("SEC submissions columns have inconsistent lengths.")
        rows = []
        for index, accession in enumerate(recent.get("accessionNumber", [])):
            def field(key):
                values = recent.get(key, [])
                return values[index] if index < len(values) and values[index] else None
            filing_date = field("filingDate")
            if not filing_date or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                continue
            parse_day(filing_date)
            published = field("acceptanceDateTime") or filing_date
            cutoff_time(published)
            primary = field("primaryDocument")
            try:
                url = self._filing_url(company["cik"], accession, primary or "")
            except DataError:
                url = None
            row = {"entity": company["ticker"], "cik": company["cik"], "accession": accession,
                   "form": field("form"), "items": field("items"), "reportDate": field("reportDate"), "filingDate": filing_date,
                   "acceptanceDateTime": field("acceptanceDateTime"), "primaryDocument": primary, "primary_document": primary, "url": url,
                   "published_at": published, "publication_precision": "timestamp" if "T" in published else "date_only",
                   "source_url": info["url"], "snapshot_hash": info["sha256"], "mode": info["mode"],
                   "retrieved_at": info["retrieved_at"],
                   "metadata_locator": ("history " if history else "filings.recent ") + f"accessionNumber[{index}]={accession}"}
            rows.append(row)
        with self._filing_lock:
            for row in rows:
                key = (company["cik"], row["accession"], row["primaryDocument"])
                existing = self._filings.get(key)
                if not existing or cutoff_time(row["retrieved_at"]) >= cutoff_time(existing["retrieved_at"]):
                    self._filings[key] = dict(row)
        return rows

    def _known_submissions(self, company):
        """Read verified local root metadata only; no implicit HTTP request."""
        sources = []
        if company.get("submissions"):
            sources.append((self.data.json(company["submissions"]), self.data.info(company["submissions"])))
        cached = self.data.cached_sec_json("https://data.sec.gov/submissions/CIK" + company["cik"] + ".json")
        if cached:
            sources.append(cached)
        return sorted(sources, key=lambda item: cutoff_time(item[1]["retrieved_at"]))

    @staticmethod
    def _history_files(payload, company):
        rows = payload.get("filings", {}).get("files", [])
        if not isinstance(rows, list):
            raise DataError("Invalid SEC historical submissions file manifest.")
        output = []
        for row in rows:
            if not isinstance(row, dict) or not re.fullmatch(r"CIK" + company["cik"] + r"-submissions-\d{3,6}\.json", str(row.get("name", ""))):
                raise DataError("Historical submissions filename does not match the issuer.")
            first, last = row.get("filingFrom"), row.get("filingTo")
            if first:
                parse_day(first)
            if last:
                parse_day(last)
            if first and last and first > last:
                raise DataError("Historical submissions date range is invalid.")
            output.append(dict(row, filename=row["name"], url="https://data.sec.gov/submissions/" + row["name"]))
        return output

    def _restore_filings(self, company):
        known = self._known_submissions(company)
        for payload, info in known:
            self._index_filings(payload, company, info)
        return self._restore_filing_history(company, known)

    def _restore_filing_history(self, company, known):
        histories = {}
        for payload, _ in known:
            for row in self._history_files(payload, company):
                histories[row["filename"]] = row
        for row in histories.values():
            cached = self.data.cached_sec_json(row["url"])
            if cached:
                self._index_filings(cached[0], company, cached[1], history=True)
        return histories

    @staticmethod
    def _filter_filings(rows, args, cutoff):
        report_date = args.get("report_date")
        if report_date:
            parse_day(report_date)
        forms = set(args.get("forms", DEFAULT_FILING_FORMS))
        selected = [r for r in rows if r["form"] in forms and (not report_date or r["reportDate"] == report_date)
                    and available_by(r["published_at"], cutoff)]
        if args.get("earnings_only"):
            selected = [r for r in selected if r["form"] in PERIODIC_FORMS or r["form"] in {"6-K", "6-K/A"}
                        or (r["form"] in {"8-K", "8-K/A"} and
                            (not r["items"] or re.search(r"(?<![\d.])2\.02(?![\d.])", r["items"])))]
        return sorted(selected, key=lambda r: (cutoff_time(r["published_at"]), r["accession"]), reverse=True)

    def _filing_page(self, selected, info, args, cutoff, coverage):
        offset, limit = args.get("offset", 0), args.get("limit", 10)
        page = [dict(row) for row in selected[offset:offset + limit]]
        data = {"filings": page, "snapshot": info, "offset": offset, "coverage": coverage,
                "truncated": offset + limit < len(selected),
                "next_offset": offset + limit if offset + limit < len(selected) else None}
        if not page:
            return {"ok": False, "data": data, "evidence": []}
        preview = page[:METADATA_PREVIEW_ROWS]
        forms = ", ".join(sorted(set(args.get("forms", DEFAULT_FILING_FORMS))))
        text = [f"SEC submissions metadata for {page[0]['entity']} | snapshot {info['mode']} retrieved {info['retrieved_at']} | "
                f"filing cutoff {cutoff or 'unspecified'} | forms {forms} | reportDate filter {args.get('report_date') or 'any'}. "
                + ("Earnings-related forms or items only. " if args.get("earnings_only") else "")
                + f"Filtered matches {len(selected)}; rows {offset + 1}–{offset + len(preview)} by newest publication first. "
                f"Coverage: {coverage}. Filing metadata only; no filing content read."]
        for row in preview:
            text.append(f"{row['accession']} | form {row['form']} | reportDate {row['reportDate']} | "
                        f"filingDate {row['filingDate']} | acceptanceDateTime {row['acceptanceDateTime'] or 'unavailable'} | "
                        f"primaryDocument {row['primaryDocument']}")
        locator = ", ".join(row["metadata_locator"] for row in preview)
        record = self._metadata_record(page[0]["entity"], info["url"], info["sha256"], info["snapshot"], locator,
                                       page[0]["published_at"], info["retrieved_at"], cutoff, info["mode"], "\n".join(text))
        if args.get("earnings_only"):
            record["earnings_filings"] = [{key: row[key] for key in ("accession", "form", "reportDate")}
                                           for row in preview]
            record["earnings_page_offset"] = offset
        for row in preview:
            row["metadata_evidence_id"] = record["id"]
        return {"ok": True, "data": data, "evidence": [record]}

    def _discover_filings(self, args, session_id, scope):
        company = self._entity(args, scope)
        source = args.get("source", "auto")
        cutoff = self._effective_cutoff(args, scope)
        cutoff_time(cutoff)
        report_date = args.get("report_date")
        if report_date:
            parse_day(report_date)
        payload, info = self._company_json(company, "submissions", "https://data.sec.gov/submissions/CIK" + company["cik"] + ".json", source)
        rows = self._index_filings(payload, company, info)
        selected = self._filter_filings(rows, args, cutoff)
        history_files = self._history_files(payload, company)
        result = self._filing_page(selected, info, args, cutoff,
                                   "this recent page only; use read_filing_history explicitly for each listed history file")
        result["summary"] = f"Found {len(selected)} eligible filing(s) in SEC recent-submissions metadata. reportDate is preserved without inferring a fiscal duration."
        result["data"].update(history_files=history_files, history_files_not_fetched=payload.get("filings", {}).get("files", []))
        return result

    def _read_filing_history(self, args, session_id, scope):
        company = self._entity(args, scope)
        filename = args["filename"]
        if not re.fullmatch(r"CIK" + company["cik"] + r"-submissions-\d{3,6}\.json", filename):
            raise DataError("Historical submissions filename must match the resolved issuer CIK.")
        known = self._known_submissions(company)
        listed = {row["filename"]: row for payload, _ in known for row in self._history_files(payload, company)}
        if filename not in listed:
            raise DataError("History filename has not been discovered for this issuer. Call discover_filings first; no hidden lookup was attempted.")
        cutoff = self._effective_cutoff(args, scope)
        bound = cutoff_time(cutoff)
        if bound and listed[filename].get("filingFrom") and parse_day(listed[filename]["filingFrom"]) > bound.date():
            raise DataError("The selected historical file contains only filings after the information cutoff.")
        payload, info = self._sec_json(listed[filename]["url"], args.get("source", "auto"))
        rows = self._index_filings(payload, company, info, history=True)
        selected = self._filter_filings(rows, args, cutoff)
        result = self._filing_page(selected, info, args, cutoff, "one selected history file; no other page was fetched")
        result["summary"] = f"Read one explicit SEC historical submissions file; {len(selected)} filing(s) meet the filters."
        result["data"]["history_file"] = listed[filename]
        return result

    @staticmethod
    def _filing_parser(raw, info, *, collect_tables=False):
        try:
            html = raw.decode(info.get("charset") or "utf-8")
        except (UnicodeDecodeError, LookupError) as exc:
            raise DataError("Filing encoding could not be decoded safely.") from exc
        parser = _FilingText(collect_tables=collect_tables)
        parser.feed(html)
        parser.close()
        return parser

    @staticmethod
    def _linked_documents(parser, parent_url):
        directory, primary = parent_url.rsplit("/", 1)
        names = {target.removeprefix(directory + "/").removeprefix("./")
                 for target in parser.link_targets}
        return sorted(name for name in names if name != primary and
                      re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.html?", name, re.I))

    def _local_filing_html(self, url):
        cached = self.data.cached_sec_html(url)
        if cached is not None:
            return cached
        for name, info in self.data.manifest["files"].items():
            if info.get("url") == url and name.lower().endswith((".html", ".htm")):
                raw = self.data.raw(name)
                self.data.check_sec_html(raw)
                return raw, self.data.info(name)
        return None

    def _archived_filing_documents(self, company, primary_url, cutoff):
        directory = primary_url.rsplit("/", 1)[0]
        documents = {}
        for doc in self.data.manifest["documents"]:
            if (doc["entity"] != company["ticker"] or not doc.get("published_at")
                    or not available_by(doc["published_at"], cutoff)):
                continue
            source = doc["source"]
            if not source.lower().endswith((".htm", ".html")):
                continue
            url = self.data.manifest["files"][source]["url"]
            parent, _, name = url.rpartition("/")
            if parent == directory and url != primary_url and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.html?", name, re.I):
                documents[name] = doc
        return documents

    def _read_filing(self, args, session_id, scope):
        company = self._entity(args, scope)
        url = self._filing_url(company["cik"], args["accession"], args["primary_document"])
        key = (company["cik"], args["accession"], args["primary_document"])
        self._restore_filings(company)
        with self._filing_lock:
            metadata = dict(self._filings[key]) if key in self._filings else None
        if metadata is None and company.get("submissions"):
            self._index_filings(self.data.json(company["submissions"]), company, self.data.info(company["submissions"]))
            with self._filing_lock:
                metadata = dict(self._filings[key]) if key in self._filings else None
        if metadata is None:
            cached_submissions = self.data.cached_sec_json("https://data.sec.gov/submissions/CIK" + company["cik"] + ".json")
            if cached_submissions:
                self._index_filings(cached_submissions[0], company, cached_submissions[1])
                with self._filing_lock:
                    metadata = dict(self._filings[key]) if key in self._filings else None
        if metadata is None:
            raise DataError("Filing identity has not been discovered. Call discover_filings for this issuer first; no hidden lookup was attempted.")
        cutoff = self._effective_cutoff(args, scope)
        cutoff_time(cutoff)
        if not available_by(metadata["published_at"], cutoff):
            raise DataError("Filing was not available at the information cutoff.")
        if args.get("query") and args.get("offset", 0):
            raise DataError("Use query-based retrieval or offset pagination in one call, not both.")
        query = args.get("query")
        words = re.findall(r"[a-z0-9]+", query.casefold()) if query else []
        if query and not words:
            raise DataError("Provide English lexical query terms for this MVP.")
        source = args.get("source", "auto")
        exhibit = args.get("exhibit_document")
        archived_doc = None
        published_at = metadata["published_at"]
        precision = metadata["publication_precision"]
        if exhibit is not None:
            if metadata["form"] not in ("8-K", "8-K/A"):
                raise DataError("The discovered filing must be an 8-K to select an exhibit.")
            parent = self._local_filing_html(url)
            if parent is not None:
                if exhibit not in self._linked_documents(self._filing_parser(*parent), url):
                    raise DataError("Select an HTML document linked from this 8-K.")
                identity_basis = "parent_link"
            else:
                if source != "live":
                    archived_doc = self._archived_filing_documents(company, url, cutoff).get(exhibit)
                if archived_doc is None:
                    raise DataError("Select a linked HTML document or a dated snapshot from this exact SEC accession.")
                identity_basis = "dated_snapshot_in_same_sec_accession"
                if cutoff_time(archived_doc["published_at"]) > cutoff_time(published_at):
                    published_at = archived_doc["published_at"]
                    precision = archived_doc.get("publication_precision") or ("timestamp" if "T" in published_at else "date_only")
            url = self._filing_url(company["cik"], args["accession"], exhibit)
            metadata = dict(metadata, exhibit_document=exhibit, exhibit_identity_basis=identity_basis)
        try:
            if archived_doc is not None:
                raw = self.data.raw(archived_doc["source"])
                self.data.check_sec_html(raw)
                cached = raw, self.data.info(archived_doc["source"])
            else:
                cached = self._local_filing_html(url) if source != "live" else None
            if cached is not None:
                raw, info = cached
            elif source == "replay" or not self.data.allow_network:
                raise DataError("No successful SEC HTML snapshot is available in replay coverage. Prior access-failure bodies are not filings; enable live SEC access explicitly.")
            else:
                raw, info = self.data.get_sec_html(url)
        except (DataError, OSError) as exc:
            reason = f"SEC HTTP error {exc.code}; no retry was attempted." if isinstance(exc, HTTPError) else str(exc)
            result = self._failure(reason)
            result["data"].update(status="filing_body_unavailable", filing=metadata,
                                  next_actions=self._document_source_actions(company, cutoff, metadata["filingDate"]))
            if exhibit is None and metadata["form"] in ("8-K", "8-K/A") and source != "live":
                alternatives = sorted(self._archived_filing_documents(company, url, cutoff))
                for filename in reversed(alternatives[:5]):
                    recovery = {**args, "exhibit_document": filename, "source": "replay"}
                    recovery.pop("offset", None)
                    result["data"]["next_actions"].insert(0, {"tool": "read_filing", "arguments": recovery})
            if isinstance(exc, HTTPError):
                result["data"]["http_status"] = exc.code
            return result
        parser = self._filing_parser(raw, info, collect_tables=bool(exhibit))
        linked = {}
        if exhibit is None and metadata["form"] in ("8-K", "8-K/A"):
            linked["linked_documents"] = self._linked_documents(parser, url)
        text = parser.text()
        if not text:
            raise DataError("The filing contains no extracted visible text.")
        text_bytes = text.encode("utf-8")
        text_path = str(self.data.save_live_bytes(text_bytes, ".text.txt"))
        text_hash = hashlib.sha256(text_bytes).hexdigest()
        authoritative = source != "replay" and info["mode"] != "replay"
        observation = {"source_document_id": stable_id("sec_filing_", url), "source_revision": info["sha256"],
                       "source_observed_at": info["retrieved_at"], "source_revision_authoritative": authoritative,
                       "source_url": url}
        width = args.get("limit_chars", 2500)
        start = args.get("offset", 0)
        if query:
            terms = set(words)
            matches = []
            for position in range(0, len(text), max(100, width // 2)):
                passage = text[position:position + width]
                tokens = Counter(re.findall(r"[a-z0-9]+", passage.casefold()))
                hits = terms & set(tokens)
                if hits:
                    matches.append((len(hits) + sum(math.log1p(tokens[t]) for t in hits) / len(terms), position))
            if not matches:
                return {"ok": False, "summary": "No lexical passage matched the query; the saved filing remains available for other queries or pagination.",
                        "data": {**linked, "filing": metadata, "snapshot": info, "text_length": len(text), "query": query,
                                 "source_observation": observation}}
            start = max(matches, key=lambda item: (item[0], -item[1]))[1]
        if start >= len(text):
            return {"ok": False, "summary": f"Offset lies beyond the extracted filing text ({len(text)} characters).",
                    "data": {"text_length": len(text), "snapshot": info, "source_observation": observation}}
        passage = text[start:start + width]
        end = start + len(passage)
        identity = [url, "htmlparser-v1", info["sha256"], start, len(passage), "current" if authoritative else "historical"]
        record = {"id": stable_id("doc_", identity), "kind": "document",
                  "entity": company["ticker"], "text": passage, "period_start": None, "period_end": None,
                  "filing_report_date": metadata["reportDate"], "form": metadata["form"], "accession": metadata["accession"],
                  "published_at": published_at, "publication_precision": precision,
                  "source_url": url, "locator": f"HTMLParser visible-text characters[{start}:{end}] (zero-based Unicode offsets)",
                  "snapshot_hash": info["sha256"], "snapshot_path": info["snapshot"],
                  "text_snapshot_hash": text_hash, "text_snapshot_path": text_path,
                  "retrieved_at": info["retrieved_at"], "mode": info["mode"],
                  "extraction_method": "stdlib HTMLParser; scripts, styles, noscript and ix:hidden excluded; whitespace normalized"}
        if metadata["form"] in ("8-K", "8-K/A"):
            # A fiscal label and its printed end date must belong to the same
            # clause. Guidance for a future quarter is not a reported period.
            disclosures = []
            for match, label in quarter_mentions(passage):
                end_date = re.match(r"\s+ended\s+([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})\b",
                                    passage[match.end():])
                if end_date:
                    stop = match.end() + end_date.end()
                    disclosures.append({"quarter": label, "end_date_text": end_date[1],
                                        "quote": passage[match.start():stop], "evidence_id": record["id"],
                                        "locator": f"HTMLParser visible-text characters[{start + match.start()}:{start + stop}]"})
            if disclosures:
                linked["disclosed_quarters"] = disclosures
                record["disclosed_quarters"] = disclosures
        record.update(observation)
        from .release_summary import quarterly_cells
        cells = quarterly_cells(parser.tables, record, layouts=parser.table_layouts, source_text=text) if exhibit else []
        if args.get("quarter_operands") and metadata["form"] == "10-Q":
            from .tagged_profit_cells import reported_quarter_cells
            try:
                dated = reported_quarter_cells({"raw": raw, "cik": company["cik"], "entity": record["entity"],
                                               "url": url, "source": info["snapshot"], "snapshot_hash": info["sha256"],
                                               "published_at": record["published_at"], "form": record["form"]},
                                              metadata["reportDate"])
            except ImportError as exc:
                linked["tagged_quarter_unavailable"] = f"Missing optional parser: {exc.name}"
            else:
                cells = [{**cell, **{key: record[key] for key in
                                       ("accession", "mode", "retrieved_at", "publication_precision")}} for cell in dated]
        if cells:
            linked["quarterly_gaap_operands"] = {c["metric"]: c["id"] for c in cells}
        result = {"summary": "Returned one exact extracted-text span. Filing report date is metadata, not an asserted period for every statement or table.",
                "evidence": [record, *cells], "data": {**linked, "filing": metadata, "text_length": len(text), "start": start, "end": end,
                                             "next_offset": end if end < len(text) else None, "query": query,
                                             "snapshot": info, "source_observation": observation,
                                             "warning": "Verify the printed measure, duration and accounting basis before using figures."}}
        from .disclosure_cells import attach_filing
        if exhibit is not None or metadata["form"] in PERIODIC_FORMS:
            attach_filing(result, self.data, self._disclosure_cell_cache, raw, text, info, record)
        if exhibit:
            for source_record in result["evidence"]:
                source_record["filing_url"] = metadata["url"]
        return result

    def source_catalog(self, scope):
        """Frozen publication metadata for matching a user's source restriction."""
        from .context import scope_issuer_keys
        allowed = scope_issuer_keys(scope.get("entities"),self.data.manifest["companies"])
        documents = []
        for row in self.data.manifest["documents"]:
            if ((allowed is not None and row["entity"].casefold() not in allowed)
                    or not available_by(row["published_at"],scope.get("cutoff"))):
                continue
            url = self.data.info(row["source"])["url"]
            documents.append({"document_id":stable_id("source_doc_",url),"entity":row["entity"],
                              "form":row.get("form"), "title":row.get("title"),"source_url":url,
                              "published_at":row["published_at"],"document_report_date":row.get("period_end")})
        return documents

    def document_corpus(self, document_ids=None):
        """Verified frozen and already-discovered cached filing documents; no HTTP."""
        if document_ids is None:
            documents = corpus_from_snapshots(self.data).documents
        else:
            documents = [snapshot_document(self.data,row) for row in self.data.manifest["documents"]
                         if stable_id("source_doc_",self.data.info(row["source"])["url"]) in document_ids]
        cached_urls = self.data.cached_urls()
        for url in cached_urls:
            if document_ids is not None and stable_id("source_doc_",url) not in document_ids:
                continue
            match = re.fullmatch(r"https://www\.sec\.gov/Archives/edgar/data/([1-9]\d{0,9})/(\d{18})/([A-Za-z0-9][A-Za-z0-9_.-]*\.html?)", url)
            if not match:
                continue
            cik, compact, primary = match.groups()
            cik = cik.zfill(10)
            company = self._entity({"entity": cik}, {})
            self._restore_filings(company)
            accession = compact[:10] + "-" + compact[10:12] + "-" + compact[12:]
            with self._filing_lock:
                metadata = self._filings.get((cik, accession, primary))
            if not metadata:
                continue
            raw, info = self.data.cached_sec_html(url)
            try:
                html = raw.decode(info.get("charset") or "utf-8")
            except (UnicodeDecodeError, LookupError) as exc:
                raise DataError("Cached filing text cannot be decoded.") from exc
            parser = _FilingText()
            parser.feed(html)
            parser.close()
            text = parser.text()
            text_hash = hashlib.sha256(text.encode()).hexdigest()
            documents.append({"id": stable_id("source_doc_", url), "text": text,
                              "entity": company["ticker"], "document_report_date": metadata["reportDate"],
                              "form": metadata["form"], "published_at": metadata["published_at"],
                              "publication_precision": metadata["publication_precision"],
                              "source_url": url, "snapshot_hash": info["sha256"], "snapshot_path": info["snapshot"],
                              "text_snapshot_hash": text_hash, "text_snapshot_path": "HTMLParser visible-text",
                              "retrieved_at": info["retrieved_at"], "extraction_method": "htmlparser-v1", "mode": info["mode"],
                              "source_document_id": stable_id("sec_filing_", url), "source_revision": info["sha256"],
                              "source_observed_at": info["retrieved_at"], "source_revision_authoritative": True})
        chunking = self.retrieval_encoder or {}
        return Corpus(documents, max_chars=chunking.get("chunk_chars", 1800), overlap=chunking.get("chunk_overlap", 250))

    def _inspect_sales_comparison(self, args, session_id, scope):
        from .fiscal_followup import confirmed_dates
        from .request_review import request_parts
        from .sales_comparison import inspect
        entity = self._entity(args, scope)["ticker"]
        cutoff = self._effective_cutoff(args, scope)
        latest = getattr(self.store, "latest_event", None)
        event = latest(session_id, "episode_checkpoint") if latest else None
        episode = (event or {}).get("episode", {})
        if episode.get("status") != "running" or not episode.get("request"):
            raise DataError("A current user question is required for a quarterly sales comparison.")
        target = episode.get("skill_fiscal_quarter")
        if target and (target["entity"] != entity or target["quarter"] != args["fiscal_quarter"]):
            raise DataError("The requested comparison differs from the active issuer or fiscal quarter.")
        bounds = episode.get("retry_period_bounds") or {}
        if (bounds.get("entity") and bounds["entity"] != entity or
                bounds.get("requested_quarter") and bounds["requested_quarter"] != args["fiscal_quarter"]):
            raise DataError("The requested comparison differs from the user's active issuer or stated fiscal quarter.")
        parts = episode.get("clarified_parts", episode.get("inherited_parts"))
        if parts is None:
            parts = request_parts(episode["request"]) or {"part-1": episode["request"]}
        from .sales_calendar import attach_calendar_calculation
        result = inspect(self.document_corpus(), entity, cutoff, args["fiscal_quarter"], parts)
        if bounds:
            known = {key: bounds[key] for key in ("period_start", "period_end") if bounds.get(key)}
            if not known or not confirmed_dates(result.get("data", {}).get("fiscal_period", {}), known):
                raise DataError("The comparison filing does not match the user's active date window.")
        attach_calendar_calculation(result, entity)
        return result

    def _read_document(self, args, session_id, scope):
        company = self._entity(args, scope)
        cutoff = self._effective_cutoff(args, scope)
        allowed = scope.get("_source_document_ids")
        corpus = self.document_corpus(allowed) if allowed is not None else self.document_corpus()
        doc = next((d for d in corpus.documents if d["id"] == args["document_id"]
                    and d["entity"] == company["ticker"] and available_by(d["published_at"],cutoff)),None)
        if doc is None:
            raise DataError("Document is not available for this issuer and cutoff.")
        start = args.get("offset",0)
        end = min(start+args.get("limit_chars",DOCUMENT_READ_LIMIT),len(doc["text"]))
        if start >= end:
            raise DataError("Requested offset is outside this document.")
        record = {**doc,"document_id":doc["id"],"id":stable_id("span_",[doc["id"],doc["text_snapshot_hash"],start,end]),
                  "text":doc["text"][start:end],"kind":"document","start":start,"end":end,"period_start":None,"period_end":None,
                  "locator":f"{doc['text_snapshot_path']}:characters[{start}:{end}] (zero-based Unicode offsets)"}
        return {"summary":f"Read characters [{start}:{end}] from original disclosure.",
                "evidence":[record],"data":{"start":start,"end":end,"next_offset":end if end < len(doc["text"]) else None}}

    def replay_document_unchanged(self, passage):
        """Verify the selected frozen assets without dispatching a read or parsing cells."""
        if self.data.allow_network:
            return False
        matches = [doc for doc in self.data.manifest["documents"]
                   if doc["source"] == passage["snapshot_path"] and doc["text"] == passage["text_snapshot_path"]]
        if len(matches) != 1:
            return False
        try:
            if passage["source_url"] in self.data.cached_urls():
                return False
            doc = snapshot_document(self.data, matches[0])
        except (DataError, KeyError, OSError, UnicodeError):
            return False
        source_id, text = doc.pop("id"), doc.pop("text")
        return (source_id == passage["document_id"] and all(passage.get(k) == v for k, v in doc.items())
                and text[passage["start"]:passage["end"]] == passage["text"])

    def _search_documents(self, args, session_id, scope):
        company = self._entity(args, scope)
        end = args.get("period_end", scope.get("period_end"))
        cutoff = self._effective_cutoff(args, scope)
        cutoff_time(cutoff)
        allowed = scope.get("_source_document_ids")
        corpus = self.document_corpus(allowed) if allowed is not None else self.document_corpus()
        mode = args.get("retrieval_mode", "bm25")
        embeddings = FrozenEmbeddings(self.retrieval_vectors, corpus) if self.retrieval_vectors and mode != "bm25" else None
        encoding = None
        if self.retrieval_encoder and mode != "bm25":
            import time
            from .runtime_embeddings import LocalSentenceEncoder, RuntimeEmbeddings
            deadline = time.monotonic() + self.timeout
            def checkpoint():
                if self.store.is_cancelled(session_id):
                    raise DataError("Local encoding cancelled; no more batches will be scheduled.")
                if time.monotonic() >= deadline:
                    raise DataError("Local encoding time budget exhausted; completed cached vectors remain reusable.")
            if self._runtime_embeddings is None:
                config = {k: v for k, v in self.retrieval_encoder.items() if k not in {"chunk_chars", "chunk_overlap"}}
                self._runtime_embeddings = RuntimeEmbeddings(LocalSentenceEncoder(**config, checkpoint=checkpoint), self.data.cache_dir / "embeddings")
            embeddings = self._runtime_embeddings.for_query(corpus, args["query"], checkpoint=checkpoint)
            encoding = dict(self._runtime_embeddings.last_stats)
        filters = {"entity": company["ticker"], "period_end": end, "cutoff": cutoff,
                   "forms": args.get("forms"), "document_ids": args.get("document_ids")}
        result = retrieve(corpus, args["query"], filters, args.get("retrieval_mode", "bm25"),
                          args.get("limit", 5), embeddings, args.get("max_context_chars", 9000), complete_starts=True)
        if len(filters.get("document_ids") or []) == 1 and result["eligible_document_ids"]:
            source_id = filters["document_ids"][0]
            source = next(doc for doc in corpus.documents if doc["id"] == source_id)
            period = source.get("document_report_date")
            if period:
                result["single_document_period_end"] = period
                # Catalog metadata only. Do not read or return another document on a narrow search.
                alternatives = {row["document_id"]:row.get("document_period_start") for row in
                                corpus.eligible({**filters, "document_ids":None, "period_end":period})}
                start = source.get("document_period_start")
                allowed = sorted(doc_id for doc_id, date in alternatives.items() if date == start)
                if len(allowed) > 1 and start:
                    if len(allowed) == len(alternatives):
                        result["same_period_search"] = {"period_end":period}
                    elif len(allowed) <= 50:  # search_documents' public document_ids bound
                        result["same_period_search"] = {"period_end":period, "document_ids":allowed}
        if end and not result["eligible_document_ids"]:
            # Only show metadata of the same issuer/form/document IDs already
            # visible at this cutoff. Never silently change the requested filter.
            eligible = corpus.eligible({**filters, "period_end": None})
            dates = sorted({d["document_report_date"] for d in eligible if d.get("document_report_date")})
            if dates:
                result["available_report_dates"] = dates[-24:]
                result["period_hint"] = "period_end means fiscal report end, not publication cutoff. Select a listed date or search without an exact report-date filter."
        if encoding is not None:
            result["runtime_encoding"] = encoding
        evidence = []
        result["source_windows"] = {}
        for passage in result.pop("passages"):
            # Ranking metadata describes this tool call. A newly explained window
            # must not create a new version of identical source evidence.
            result["source_windows"][passage["id"]] = {
                **passage.pop("source_window"), "retrieved_chunk_id": passage.pop("retrieved_chunk_id", passage["id"])}
            evidence.append(dict(passage, kind="document", period_start=None, period_end=None,
                                 locator=f"{passage['text_snapshot_path']}:characters[{passage['start']}:{passage['end']}] (zero-based Unicode offsets)"))
        result.update(corpus_complete=False, eligible_documents=result["eligible_document_ids"],
                      retrieval=result["scoring"],
                      passage_scope="Source windows retain ranked offsets and may extend left to the start of a sentence or bullet. "
                                    "Use read_document with the document_id and neighboring offsets if the subject, speaker "
                                    "or reporting period continues beyond either edge. Document dates do not date every claim.",
                      document_period_semantics="report metadata only; statement periods require checking the actual passage")
        return {"ok": bool(evidence), "summary": f"Found {len(evidence)} {result['mode']} passage(s) in {len(result['eligible_document_ids'])} eligible document(s). A retrieved passage is not a verified conclusion.",
                "evidence": evidence, "data": result}

    def _find_percentage_cells(self, args, session_id, scope):
        from .table_cells import index_percentages, rank_cells
        company = self._entity(args, scope)
        cutoff = self._effective_cutoff(args, scope)
        start, end = args.get("period_start"), args.get("period_end")
        if start:
            parse_day(start)
        if end:
            parse_day(end)
        if start and end and start > end:
            raise DataError("Period start follows period end.")
        records = []
        allowed = scope.get("_source_document_ids")
        for doc in self.data.manifest["documents"]:
            path = doc["source"]
            if (doc["entity"] != company["ticker"] or not available_by(doc["published_at"], cutoff)
                    or not path.lower().endswith((".htm",".html"))):
                continue
            info = self.data.info(path)
            document_id = stable_id("source_doc_", info["url"])
            if allowed is not None and document_id not in allowed:
                continue
            if args.get("document_id") and args["document_id"] != document_id:
                continue
            raw = self.data.raw(path)
            key = (path, info["sha256"], document_id, doc["published_at"], doc["entity"])
            if key not in self._percentage_index:
                meta = {"id":document_id, "source_url":info["url"], "snapshot_hash":info["sha256"], "snapshot_path":path,
                        "entity":doc["entity"], "published_at":doc["published_at"], "mode":"replay"}
                self._percentage_index[key] = index_percentages(raw, meta)
            records.extend(c for c in self._percentage_index[key]
                           if (not start or c["period_start"] == start) and (not end or c["period_end"] == end))
        ranked = rank_cells(records, args["query"])
        offset = args.get("offset", 0)
        matches = ranked[offset:offset + args.get("limit", 6)]
        return {"ok":bool(matches), "evidence":matches,
                "summary":f"Found {len(ranked)} candidate percentage cells. Inspect labels and dates before using cell IDs.",
                "data":{"candidate_count":len(ranked),
                        "next_offset":offset+len(matches) if offset+len(matches)<len(ranked) else None}}

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise DataError("Calculation operands must be numeric values, not strings or booleans.")
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise DataError("Invalid numeric operand.") from exc
        if not result.is_finite():
            raise DataError("Non-finite numeric operand.")
        return result

    @staticmethod
    def _serial_number(value):
        if value == value.to_integral_value():
            return int(value)
        result = float(value)
        if not math.isfinite(result):
            raise DataError("Result is not a finite representable number.")
        return result

    @staticmethod
    def _same(a, b, fields):
        for field in fields:
            if a.get(field) is None or b.get(field) is None or a[field] != b[field]:
                raise DataError("Operands have missing or incompatible " + field + ".")

    @staticmethod
    def _period(record):
        end = parse_day(record.get("period_end"))
        start = parse_day(record.get("period_start")) if record.get("period_start") else None
        if start and start > end:
            raise DataError("Invalid operand period.")
        return start, end

    def _compare_periods(self, a, b):
        sa, ea = self._period(a)
        sb, eb = self._period(b)
        if (sa is None) != (sb is None):
            raise DataError("Cannot compare an instant with a duration.")
        if ea <= eb or (sa and sa <= eb):
            raise DataError("Current/prior periods must be chronological and non-overlapping.")
        if sa and abs((ea - sa).days - (eb - sb).days) > 7:
            raise DataError("Periods differ in duration by more than seven days; quarterly/cumulative comparison refused.")

    @staticmethod
    def _operand_publication(saved, ids):
        """A derived value is available only after all of its original inputs are available."""
        pending, seen, stamps = list(ids), set(), []
        undated_leaf = False
        while pending:
            ref = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            item = saved.get(ref)
            if not item or item.get("stale"):
                raise CalculatorInputError("A calculation dependency is missing or stale. Refresh its source operands.")
            dependencies = item.get("operand_ids", [])
            if item.get("published_at"):
                stamps.append(item["published_at"])
            elif not dependencies:
                undated_leaf = True
            pending.extend(dependencies)
        return max(stamps, key=cutoff_time) if stamps and not undated_leaf else None

    @staticmethod
    def _comparison_issuers(saved, ids):
        """Recover ordered issuer pairs even for older nested differences saved with entities=[None]."""
        @cache
        def series(ref):
            row = saved[ref]
            if isinstance(row.get("entity"), str) and row["entity"]:
                return (row["entity"],)
            inputs = row.get("operand_ids", ())
            if len(inputs) != 2 or row.get("operation") not in {"difference", "growth", "percentage_point_change"}:
                raise DataError("A comparison operand lacks a defined issuer series.")
            left, right = (series(i) for i in inputs)
            if left == right:
                return left
            if len(left) == len(right) == 1 and row["operation"] != "growth":
                return left + right
            raise DataError("Saved comparison uses different issuer pairs or reverses their order.")

        return series(ids[0]), series(ids[1])

    def _passage_operand_error(self, operands, saved):
        """Offer a reread only if it can materialize a numeric cell missing from this session."""
        from .disclosure_cells import attach
        from .platform_revenue import CELL_TYPE

        available, windows = set(), {}
        for row in operands:
            if (row.get("kind") != "document" or row.get("mode") != "replay"
                    or not row.get("document_id") or not row.get("text")
                    or type(row.get("start")) is not int or type(row.get("end")) is not int):
                continue
            preview = {"evidence":[row], "data":{}, "summary":""}
            attach(preview, self.data, self._disclosure_cell_cache)
            for cell in preview["evidence"][1:]:
                current = saved.get(cell["id"])
                if current and not current.get("stale") and current.get("snapshot_hash") == cell.get("snapshot_hash"):
                    available.add(cell["id"])
                elif current is None:
                    start = cell.get("source_context_start", cell.get("source_row_start", row["start"]))
                    end = cell.get("source_row_end", row["end"])
                    windows[(row["document_id"], start, end)] = {
                        "tool":"read_document", "arguments":{"document_id":row["document_id"],
                        "entity":row["entity"], "offset":start, "limit_chars":max(200,min(6000,end-start))}}
        document_revisions = {(row.get("document_id"), row.get("snapshot_hash")) for row in operands
                              if row.get("kind") == "document" and row.get("document_id")}
        platform_tables = {}
        for cell in saved.values():
            if (cell.get("cell_type") == CELL_TYPE and not cell.get("stale")
                    and (cell["document_id"], cell["snapshot_hash"]) in document_revisions):
                available.add(cell["id"])
                platform_tables.setdefault(cell["table_id"], []).append(cell)
        for cells in platform_tables.values():
            cell = cells[0]
            if len(cells) >= cell["source_table_operand_count"]:
                continue
            start, end = cell["source_table_start"], cell["source_table_end"]
            windows[(cell["document_id"], start, end)] = {
                "tool": "read_document", "arguments": {"document_id": cell["document_id"], "entity": cell["entity"],
                                                          "offset": start, "limit_chars": max(200, min(6000, end-start))}}
        result = self._failure("Operands require explicit metric definitions. Select saved numeric cell IDs by row, period and unit. Only the listed windows can supply additional cells. If no applicable cells or windows are available, use another source or state the missing input.")
        result["data"].update(recoverable_input_error=True, numeric_operand_ids=sorted(available), reopen=list(windows.values()))
        return result

    def _calculate(self, args, session_id, scope):
        context = self.store.get_context(session_id, report_mode="none")
        saved = {r["id"]: r for r in context.get("evidence", []) + context.get("calculations", [])}
        ids = args["operand_ids"]
        try:
            check_operand_shape(args["operation"], ids)
        except DataError as exc:
            raise CalculatorInputError(str(exc)) from exc
        if any(i not in saved for i in ids):
            raise CalculatorInputError("An operand is missing from this session; retrieve and persist it first.")
        a, b = [saved[i] for i in ids]
        from .context import outside_issuer_scope, scope_issuer_keys
        allowed = scope_issuer_keys(scope.get("entities"), self.data.manifest["companies"])
        excluded = outside_issuer_scope(context["evidence"], context["calculations"], allowed)
        if excluded.intersection(ids):
            raise CalculatorInputError("Operand belongs to an issuer outside the current scope. Resolve the issuer before calculating.")
        if a.get("stale") or b.get("stale"):
            raise CalculatorInputError("Stale operands must be refreshed before calculation.")
        publication = self._operand_publication(saved, ids)
        if scope.get("cutoff") and (not publication or not available_by(publication, scope["cutoff"])):
            raise CalculatorInputError("Operand publication is unknown or after the current information cutoff.")
        if any(not isinstance(r.get("metric"), str) or not r["metric"] for r in (a, b)):
            return self._passage_operand_error((a, b), saved)
        try:
            return self._calculate_values(args["operation"], ids, a, b, saved, publication,
                                          context.get("session", {}).get("scope_version", 1))
        except DataError as exc:
            raise CalculatorInputError(str(exc)) from exc

    def _calculate_values(self, op, ids, a, b, saved, publication, scope_version):
        x, y = self._number(a.get("value")), self._number(b.get("value"))
        scenario_comparison = a.get("operation") == TAX_SCENARIO and op in {"growth", "difference"}
        from .platform_revenue import CELL_TYPE, validate_share
        if scenario_comparison:
            check_tax_scenario_comparison(a, b)
        elif op == "ratio" and any(row.get("cell_type") == CELL_TYPE for row in (a, b)):
            validate_share(a, b)
        else:
            self._same(a, b, ["unit", "basis", "business_scope"])
        if a.get("currency") != b.get("currency"):
            raise DataError("Operands use different currencies; currency conversion is not implemented.")
        if a.get("scale", 1) != 1 or b.get("scale", 1) != 1:
            raise DataError("Operands must use base units with scale 1.")
        metric = a.get("metric")
        unit = a["unit"]
        entity = a.get("entity")
        entities = [entity]
        period_start, period_end = a.get("period_start"), a.get("period_end")
        warnings = []
        chart = any(r.get("cell_type") == "chart_inventory_cell" for r in (a, b))
        if chart:
            first_quarter, last_quarter = chart_comparison(a, b, op)
            warnings.append(f"Approximate change from rounded chart labels, {first_quarter} to {last_quarter}.")
        else:
            self._period(a)
            self._period(b)
        basis = a["basis"]
        if op == TAX_SCENARIO:
            dimensions = tax_scenario_dimensions(a, b)
            value, metric, basis = x + y, dimensions["metric"], dimensions["basis"]
            formula = f"{ids[0]} + {ids[1]} (signed tax expense; other allocations held fixed)"
            warnings.append("Single-item sensitivity assuming the entire identified tax effect flows to common earnings. Verify attribution and fixed preferred dividends in the filing.")
        elif op == "difference" and {a.get("concept"), b.get("concept")} == {COMMON_INCOME, VALUATION_ALLOWANCE_EXPENSE}:
            raise DataError("The valuation-allowance item is signed as tax expense. To remove its earnings effect, use exclude_tax_expense_item with reported basic common earnings first and the signed expense item second.")
        elif op in {"ratio", "margin"}:
            self._same(a, b, ["entity", "period_end"])
            if a.get("period_start") != b.get("period_start"):
                raise DataError("Ratio operands require the same exact period.")
            if y == 0:
                raise DataError("Division by zero is undefined.")
            if op == "margin" and (a.get("metric") not in {"operating_income", "net_income", "gross_profit"} or b.get("metric") != "revenue" or y <= 0):
                raise DataError("Margin requires positive revenue and operating_income, net_income or gross_profit for the same period.")
            value = x / y * (100 if op == "margin" else 1)
            unit = "%" if op == "margin" else "ratio"
            metric = a.get("metric", "unknown") + ("_margin" if op == "margin" else "_to_" + b.get("metric", "unknown"))
            formula = f"{ids[0]} / {ids[1]}" + (" * 100" if op == "margin" else "")
        elif op == "derive_quarter":
            self._same(a, b, ["entity", "metric", "concept"])
            quarter = quarter_from_cumulative(a, b)
            value = x - y
            period_start, period_end = quarter["period_start"], quarter["period_end"]
            formula = f"{ids[0]} - {ids[1]} (later cumulative flow minus prior cumulative flow)"
            warnings.append("Derived quarter may differ from an explicit quarterly table because of rounding or restatement; cross-check disclosures.")
        elif op == "difference" and a["metric"] != b["metric"] and not scenario_comparison:
            self._same(a, b, ["entity", "period_start", "period_end"])
            if (not a.get("currency") or unit != a["currency"]
                    or any(r.get("concept") not in ADDITIVE_CONCEPTS for r in (a, b))):
                raise DataError("Different metrics must both be identified monetary flows in the same base currency.")
            value = x - y
            metric = a["metric"] + "_minus_" + b["metric"]
            formula = f"{ids[0]} - {ids[1]}"
        elif op == "price_return":
            self._same(a, b, ["entity", "metric"])
            if a.get("kind") != "market_price" or b.get("kind") != "market_price" or a["period_end"] <= b["period_end"] or y <= 0:
                raise DataError("Price return requires positive start price and later end price for the same issuer.")
            value = (x / y - 1) * 100
            unit, metric, period_start = "%", "price_return", b["period_end"]
            formula = f"({ids[0]} / {ids[1]} - 1) * 100"
            adjustment = a.get("adjustment", {})
            if adjustment.get("dividends") == "adjusted":
                warnings.append("Descriptive provider-adjusted price change; incorporates its dividend/split convention, not a fully specified investor total return or causal event effect.")
            else:
                warnings.append("Descriptive provider-close change; excludes cash dividends and does not establish event causality. Raw or unknown split adjustment can distort economic-return interpretation.")
        else:
            if scenario_comparison:
                metric += "_vs_reported"
                warnings.append("Current single-item scenario compared with prior reported basic common earnings.")
            else:
                self._same(a, b, ["metric"])
                if a.get("concept") or b.get("concept"):
                    self._same(a, b, ["concept"])
            first, second = self._comparison_issuers(saved, ids)
            if first != second:
                if len(first) != 1 or len(second) != 1:
                    raise DataError("Compare the same ordered issuer pair in both periods.")
                self._same(a, b, ["period_end"])
                if a.get("period_start") != b.get("period_start") or op == "growth":
                    raise DataError("Cross-company differences require identical periods; growth is within one company.")
                entity = None
                entities = list(first + second)
            else:
                entities = list(first)
                if not chart:
                    self._compare_periods(a, b)
            if op == "growth":
                if y <= 0:
                    raise DataError("Growth with a zero or negative base is undefined or ambiguous; use difference.")
                value, unit = (x / y - 1) * 100, "%"
                metric = metric + "_growth"
                formula = f"({ids[0]} / {ids[1]} - 1) * 100"
            elif op == "percentage_point_change":
                if unit != "%":
                    raise DataError("Percentage-point change requires operands expressed in percent.")
                value, unit = x - y, "percentage_points"
                metric = metric + "_change"
                formula = f"{ids[0]} - {ids[1]} (percentage points)"
            elif op == "difference":
                if unit == "%":
                    raise DataError("Use percentage_point_change to distinguish points from percent growth.")
                value = x - y
                metric = metric + "_difference"
                formula = f"{ids[0]} - {ids[1]}"
            else:
                raise DataError("Unsupported operation.")
        versions = {r["id"]: r.get("version", 1) for r in [a, b]}
        calc = {"id": stable_id("calc_", [op, ids, versions]), "operation": op, "operand_ids": ids,
                "operand_versions": versions, "value": self._serial_number(value), "unit": unit, "formula": formula,
                "entity": entity, "entities": entities,
                "metric": metric, "period_start": period_start, "period_end": period_end,
                "basis": basis, "business_scope": a["business_scope"],
                "currency": None if unit in {"%", "ratio", "percentage_points"} else a.get("currency"),
                "operand_currencies": [a.get("currency"), b.get("currency")],
                "concept": a.get("concept") if op == "derive_quarter" else None,
                "published_at": publication,
                "scope_version": scope_version, "stale": False, "warnings": warnings}
        if chart:
            calc.update(from_chart_period=b["chart_period"], to_chart_period=a["chart_period"])
        return {"summary": f"Calculated {op}: {calc['value']} {unit}.", "calculations": [calc], "data": {"warnings": warnings}}

    def _market_prices(self, args, session_id, scope):
        start, end = args["start_date"], args["end_date"]
        if parse_day(start) > parse_day(end):
            raise DataError("Price window start must not follow end.")
        cutoff = self._effective_cutoff(args, scope)
        cutoff_time(cutoff)
        source, price_type = args.get("source", "auto"), args.get("price_type", "close")
        query = args.get("entity")
        if query is None:
            entities = scope.get("entities", [])
            if len(entities) != 1:
                raise DataError("Specify an explicit market entity or a single active entity.")
            query = entities[0]
        snapshot = PriceSnapshot.from_path(self.market_data) if self.market_data and source != "live" else None
        configured = (snapshot.identities() if snapshot else []) + (list(self.market_adapter.instruments) if self.market_adapter else [])
        identities = {i["entity"] for i in configured if query.casefold() == i["entity"].casefold()
                      or (i.get("cik") and query.removeprefix("CIK").zfill(10) == i["cik"])}
        if len(identities) > 1:
            raise DataError("Market issuer mapping is ambiguous; specify its configured entity identifier.")
        entity = next(iter(identities)) if identities else None
        if source == "live":
            if not self.market_adapter:
                raise DataError("No live market adapter is configured.")
            snapshot = self.market_adapter.fetch(entity or query, price_type, self.data, source="live")
            return snapshot.select(entity or query, start, end, cutoff, price_type)
        if entity and snapshot and any(i["entity"] == entity and i["price_type"] == price_type for i in snapshot.instruments):
            return snapshot.select(entity, start, end, cutoff, price_type)
        if entity and self.market_adapter:
            provider_snapshot = self.market_adapter.fetch(entity, price_type, self.data, source=source)
            return provider_snapshot.select(entity, start, end, cutoff, price_type)
        company = self._entity(args, scope)
        if price_type != "close":
            raise DataError("The legacy replay exposes provider close only; configure an explicit adjusted-price source.")
        if not company.get("prices"):
            raise DataError("No price snapshot or configured provider listing for this issuer.")
        raw = self.data.json(company["prices"])["chart"]["result"][0]
        info = self.data.info(company["prices"])
        closes = raw["indicators"]["quote"][0]["close"]
        from zoneinfo import ZoneInfo
        zone = raw["meta"].get("exchangeTimezoneName")
        if not zone:
            raise DataError("Legacy price snapshot lacks an exchange timezone.")
        days = [datetime.fromtimestamp(t, ZoneInfo(zone)).date().isoformat() for t in raw["timestamp"]]
        available = [d for d in days if available_by(d, cutoff)]
        if start not in available or end not in available:
            return {"ok": False, "summary": "Exact endpoint trading prices are unavailable at the cutoff. No nearest-date substitution was made.",
                    "data": {"available_dates": available, "mode": "replay"}}
        evidence = []
        currency = raw["meta"]["currency"]
        for index, day in enumerate(days):
            if not start <= day <= end or not available_by(day, cutoff) or closes[index] is None:
                continue
            price = self._number(closes[index])
            locator = f"chart.result[0].indicators.quote[0].close[{index}]; timestamp={raw['timestamp'][index]}"
            evidence.append({"id": stable_id("price_", ["yahoo-chart-close", company["cik"], day, currency]), "kind": "market_price",
                             "entity": company["ticker"], "metric": "provider_close", "value": self._serial_number(price),
                             "unit": currency + "/share", "currency": currency, "basis": "provider_close_no_dividend_return",
                             "business_scope": "listed_common_equity", "period_start": None, "period_end": day,
                             "published_at": day, "publication_precision": "date_only",
                             "source_url": info["url"], "locator": locator, "snapshot_hash": info["sha256"],
                             "snapshot_path": info["snapshot"], "retrieved_at": info["retrieved_at"], "mode": "replay"})
            evidence[-1].update(provider="Yahoo chart legacy snapshot", provider_symbol=raw["meta"].get("symbol"),
                                exchange=raw["meta"].get("exchangeName"), timezone=zone, trading_date=day,
                                price_type="close", adjustment={"splits": "unknown", "dividends": "none"},
                                availability_basis="legacy date-only availability assumption; point-in-time source bytes unverified",
                                license="Legacy supplied snapshot; redistribution permission and adjustment semantics are unverified.",
                                license_url="https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html")
        if not all(any(e["period_end"] == d for e in evidence) for d in [start, end]):
            raise DataError("A requested endpoint has a null price.")
        return {"summary": f"Returned {len(evidence)} historical provider closes. Intraday publication time is unverified; date-only cutoff handling is conservative.",
                "evidence": evidence, "data": {"mode": "replay", "available_dates": available, "adjustment": "provider close, not adjusted-close or total return"}}
