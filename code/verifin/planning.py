"""A task-type fixed workflow for the E2 planning comparison.

This module contains no model calls, answers, company allowlist, or gold tool
sequence. Public task specifications describe requested outputs. The controller
executes every yielded tool, synthesis and verification action under its normal
budgets. Natural-language task resolution is a separate, shared input step.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .data import available_by, cutoff_time, parse_day
from .tools import INSTANT_METRICS, METRICS


WORKFLOW_VERSION = "task-type-fixed-v1"
TASK_KINDS = {"fact", "compare_periods", "compare_companies", "margin", "growth", "price", "explanation"}
FORBIDDEN_FIELDS = {"gold", "gold_issues", "annotations", "human_judgments", "expected_answer",
                    "gold_actions", "tool_sequence", "expected_tools", "answer", "actions"}


class TaskSpecError(ValueError):
    pass


def _copy(value):
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise TaskSpecError("Task inputs must be finite JSON") from None


def _no_private_fields(value):
    if isinstance(value, dict):
        if FORBIDDEN_FIELDS & value.keys():
            raise TaskSpecError("Private answers, annotations and action sequences are not task inputs")
        for child in value.values():
            _no_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            _no_private_fields(child)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise TaskSpecError(label + " must be a nonempty string")
    return value.strip()


def _period(value, *, instant=False, explanation=False):
    if not isinstance(value, dict):
        raise TaskSpecError("Each period must be an object")
    if "relative" in value:
        if set(value) - {"relative", "kind", "offset"}:
            raise TaskSpecError("Relative periods accept only relative, kind and offset")
        if value["relative"] != "latest" or value.get("kind") not in {"quarter", "annual", "instant"}:
            raise TaskSpecError("Relative periods require latest and quarter, annual or instant")
        if instant is not None and instant != (value["kind"] == "instant"):
            raise TaskSpecError("The requested duration must match the metric's instant/duration definition")
        if type(value.get("offset", 0)) is not int or not 0 <= value.get("offset", 0) <= 20:
            raise TaskSpecError("Relative-period offset must be an integer from 0 to 20")
        return dict(value, offset=value.get("offset", 0))
    if set(value) - {"start", "end"}:
        raise TaskSpecError("Exact periods accept only start and end; fiscal labels need explicit resolution")
    try:
        parse_day(value.get("end"))
        if value.get("start") is not None:
            parse_day(value["start"])
            if value["start"] > value["end"]:
                raise ValueError()
        elif instant is False and not explanation:
            raise ValueError()
        if instant and value.get("start") is not None:
            raise ValueError()
    except ValueError:
        raise TaskSpecError("Specify valid exact period dates; duration metrics need start/end and instant metrics end only") from None
    return {"start": value.get("start"), "end": value["end"]}


@dataclass(frozen=True)
class TaskSpec:
    """Immutable public, user-resolved request, shared by both E2 conditions."""
    _json: str

    @classmethod
    def from_dict(cls, value):
        value = _copy(value)
        _no_private_fields(value)
        if not isinstance(value, dict) or set(value) - {"version", "cutoff", "source", "items"}:
            raise TaskSpecError("Task specification accepts version, cutoff, source and items only")
        if type(value.get("version", 1)) is not int or value.get("version", 1) != 1:
            raise TaskSpecError("Unsupported task specification version")
        try:
            _text(value.get("cutoff"), "cutoff")
            cutoff_time(value["cutoff"])
        except ValueError:
            raise TaskSpecError("An explicit information cutoff is required") from None
        if value.get("source", "replay") not in {"auto", "replay", "live"}:
            raise TaskSpecError("source must be auto, replay or live")
        items = value.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise TaskSpecError("Specify between one and twenty required-answer items")
        seen = set()
        for item in items:
            if not isinstance(item, dict) or set(item) - {"id", "text", "kind", "entities", "metric", "periods", "periods_by_entity", "unit", "query", "window"}:
                raise TaskSpecError("Unknown required-answer item fields")
            identifier = _text(item.get("id"), "item.id")
            item["id"] = identifier
            if identifier in seen:
                raise TaskSpecError("Required-answer item IDs must be unique")
            seen.add(identifier)
            item["text"] = _text(item.get("text"), "item.text")
            kind = item.get("kind")
            if kind not in TASK_KINDS:
                raise TaskSpecError("Unsupported task type; resolve the request before planning")
            entities = item.get("entities")
            if not isinstance(entities, list) or not 1 <= len(entities) <= 10:
                raise TaskSpecError("Each item needs one to ten explicitly resolved issuer queries")
            item["entities"] = [_text(e, "entity") for e in entities]
            entities = item["entities"]
            if len({e.casefold() for e in item["entities"]}) != len(entities):
                raise TaskSpecError("An item's issuer queries must be distinct")
            if kind == "compare_companies" and len(entities) < 2:
                raise TaskSpecError("Company comparison requires at least two issuers")
            if kind in {"fact", "compare_periods", "growth", "margin", "explanation", "price"} and len(entities) != 1:
                raise TaskSpecError("Use separate required items for each issuer in this task type")
            if kind in {"fact", "compare_periods", "compare_companies", "growth", "margin"}:
                metric = _text(item.get("metric"), "metric")
                item["metric"] = metric
                if metric not in METRICS and not re.fullmatch(r"(?:us-gaap|ifrs-full):[A-Za-z][A-Za-z0-9]*", metric):
                    raise TaskSpecError("Resolve a supported metric alias or explicit taxonomy concept")
                if kind == "margin" and metric not in {"operating_income", "net_income", "gross_profit"}:
                    raise TaskSpecError("Margin metric must identify its supported income numerator")
            elif "metric" in item:
                raise TaskSpecError("This task type does not accept a financial metric")
            if "unit" in item:
                item["unit"] = _text(item["unit"], "unit")
            if kind == "price":
                window = item.get("window")
                if not isinstance(window, dict) or set(window) != {"start", "end"}:
                    raise TaskSpecError("A descriptive price return needs exact endpoint dates")
                window = _period(window)
                item["window"] = window
                if window["start"] == window["end"]:
                    raise TaskSpecError("Price-return endpoints must be different dates")
                if "periods" in item or "periods_by_entity" in item:
                    raise TaskSpecError("Use window for price tasks")
            else:
                if "window" in item:
                    raise TaskSpecError("Only price tasks accept window")
                if ("periods" in item) == ("periods_by_entity" in item):
                    raise TaskSpecError("Supply periods or periods_by_entity, exactly one")
                groups = item.get("periods_by_entity", {e: item.get("periods") for e in entities})
                if not isinstance(groups, dict) or set(groups) != set(entities):
                    raise TaskSpecError("Per-issuer periods must cover the exact requested issuer list")
                normalized = {}
                for entity, periods in groups.items():
                    if not isinstance(periods, list) or not 1 <= len(periods) <= 4:
                        raise TaskSpecError("Each issuer needs one to four requested periods")
                    if kind in {"fact", "explanation", "compare_companies"} and len(periods) != 1:
                        raise TaskSpecError("Use one period per issuer for this task type")
                    if kind in {"growth", "compare_periods"} and len(periods) != 2:
                        raise TaskSpecError("Growth and period comparison need exactly two periods")
                    instantaneous = item.get("metric") in INSTANT_METRICS
                    if ":" in item.get("metric", ""):
                        # Unknown explicit taxonomy concepts may be instant or
                        # duration. Dates are explicit and the adapter validates
                        # the actual concept; do not guess from its spelling.
                        instantaneous = None
                    normalized[entity] = [_period(p, instant=instantaneous,
                                                          explanation=kind == "explanation") for p in periods]
                    if len({json.dumps(p, sort_keys=True) for p in normalized[entity]}) != len(periods):
                        raise TaskSpecError("Comparison periods must be distinct")
                if "periods_by_entity" in item:
                    item["periods_by_entity"] = normalized
                else:
                    item["periods"] = normalized[entities[0]]
            if kind == "explanation":
                item["query"] = _text(item.get("query"), "query")
            elif "query" in item:
                raise TaskSpecError("Only explanation tasks accept a passage query")
        value.update(version=1, source=value.get("source", "replay"))
        return cls(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))

    def to_dict(self):
        return json.loads(self._json)

    @property
    def sha256(self):
        return hashlib.sha256(self._json.encode()).hexdigest()

    def scope(self):
        value = self.to_dict()
        return {"entities": list(dict.fromkeys(e for item in value["items"] for e in item["entities"])),
                "metrics": list(dict.fromkeys(item["metric"] for item in value["items"] if "metric" in item)),
                "cutoff": value["cutoff"], "period_start": None, "period_end": None,
                "requirements": [{"id": item["id"], "text": item["text"]} for item in value["items"]],
                "planning_task": value}


def select_relative_period(selector, available_periods):
    """Resolve from the tool's cutoff-filtered observations, never fiscal labels.

    Quarter/annual here means an explicitly requested duration class. Offset is
    the nth distinct end date, not "same fiscal quarter last year". Ambiguous
    starts for the selected end date must be clarified, not arbitrarily chosen.
    """
    ranges = {"quarter": (70, 100), "annual": (330, 380)}
    candidates = set()
    for p in available_periods:
        try:
            end = parse_day(p["period_end"])
            start = parse_day(p["period_start"]) if p.get("period_start") else None
        except (ValueError, KeyError, TypeError):
            continue
        if selector["kind"] == "instant":
            eligible = start is None
        else:
            low, high = ranges[selector["kind"]]
            eligible = start is not None and low <= (end - start).days + 1 <= high
        if eligible:
            candidates.add((start.isoformat() if start else None, end.isoformat()))
    ends = sorted({p[1] for p in candidates}, reverse=True)
    offset = selector.get("offset", 0)
    if len(ends) <= offset:
        return None, "The requested relative period is outside the returned source coverage"
    matches = sorted((p for p in candidates if p[1] == ends[offset]), key=str)
    if len(matches) != 1:
        return None, "Several fiscal starts match this duration and end date; specify exact dates"
    return {"start": matches[0][0], "end": matches[0][1]}, None


class FixedWorkflowPlanner:
    """Issuer-independent acquisition → synthesis → verification → bounded repair.

    Internal state belongs to one engine instance. A crashed episode is closed
    by ResearchEngine; a new instance starts a fresh workflow using saved data.
    The planner never calls a tool or model itself, including on failures.
    """
    mode = WORKFLOW_VERSION

    def __init__(self, *, max_revision_rounds=1):
        if type(max_revision_rounds) is not int or not 0 <= max_revision_rounds <= 3:
            raise ValueError("max_revision_rounds must be an integer from zero to three")
        self.max_revision_rounds = max_revision_rounds
        self._key = None
        self._workflow = None
        self._done = False
        self._pending_scope = None
        self._clarification = None

    def _decorate(self, decision):
        if self._pending_scope is not None:
            decision = {**decision, "scope": {**self._pending_scope, **decision.get("scope", {})}}
            self._pending_scope = None
        if decision.get("action") == "clarify":
            self._clarification = decision
        return decision

    def decide(self, context):
        public = context.get("scope", {}).get("planning_task")
        try:
            spec = TaskSpec.from_dict(public)
        except TaskSpecError as exc:
            return {"action": "clarify", "question": "Please resolve the public task specification: " + str(exc),
                    "reason": "Fixed workflow needs the same explicit user-resolved task inputs available to the adaptive Planner."}
        key = (context.get("session", {}).get("id"), context.get("episode", {}).get("id"), spec.sha256)
        if key != self._key:
            self._key, self._done = key, False
            self._pending_scope = None
            self._clarification = None
            self._workflow = self._run(spec.to_dict(), context)
            return self._decorate(next(self._workflow))
        if self._clarification is not None:
            return _copy(self._clarification)
        if self._done:
            return {"action": "finish", "reason": "Fixed workflow is finished; unresolved requirements remain explicit."}
        try:
            return self._decorate(self._workflow.send(context))
        except StopIteration:
            self._done = True
            return {"action": "finish", "reason": "Fixed workflow completed its bounded acquisition and revision stages."}

    @staticmethod
    def _tool(context, name, arguments, reason):
        context = yield {"action": "tool", "tool": name, "arguments": arguments, "reason": reason}
        return context, context.get("episode", {}).get("last_tool_result", {})

    @staticmethod
    def _fresh(record, cutoff):
        try:
            return not record.get("stale") and bool(record.get("published_at")) and available_by(record["published_at"], cutoff)
        except ValueError:
            return False

    def _fact(self, context, entity, metric, period, spec, *, unit=None, force=False):
        def matches(ctx):
            return [e for e in ctx.get("evidence", []) if e.get("kind") == "financial_fact"
                    and e.get("entity") == entity and (e.get("metric") == metric or e.get("concept") == metric)
                    and e.get("period_start") == period["start"] and e.get("period_end") == period["end"]
                    and (unit is None or e.get("unit") == unit) and self._fresh(e, spec["cutoff"])]
        records = matches(context)
        query = {"entity": entity, "metric": metric, "period_end": period["end"], "cutoff": spec["cutoff"], "source": spec["source"]}
        if period["start"]:
            query["period_start"] = period["start"]
        if unit:
            query["unit"] = unit
        if not records or force or spec["source"] == "live":
            context, result = yield from self._tool(context, "financial_facts", query, "Acquire the requested metric for the exact resolved period.")
            if not result.get("ok"):
                # A failed normalized fact request does not imply the original
                # disclosure is unavailable. A fixed source fallback supplies
                # passages to the common Researcher, never fabricates a number.
                context = yield from self._documents(context, entity, period,
                    metric.split(":")[-1].replace("_", " "), spec, force=force)
                return context, None
            records = matches(context)
        # Matching scalar values with different accounting definitions are not
        # interchangeable; let the Researcher/Critic describe unresolved data.
        dimensions = {(e.get("value"), e.get("unit"), e.get("basis"), e.get("business_scope"), e.get("concept")) for e in records}
        return context, sorted(records, key=lambda e: e["id"])[0] if len(dimensions) == 1 else None

    def _calculation(self, context, operation, operands, *, force=False):
        if not all(operands):
            return context, None
        ids = [r["id"] for r in operands]
        versions = {r["id"]: r.get("version", 1) for r in operands}
        existing = [c for c in context.get("calculations", []) if not c.get("stale") and c.get("operation") == operation
                    and c.get("operand_ids") == ids and c.get("operand_versions") == versions]
        if existing and not force:
            return context, sorted(existing, key=lambda c: c["id"])[0]
        context, result = yield from self._tool(context, "calculate", {"operation": operation, "operand_ids": ids},
                                               "Calculate from saved operands; the tool enforces comparability.")
        rows = result.get("calculations", []) if result.get("ok") else []
        current = {c["id"]: c for c in context.get("calculations", [])}
        return context, current.get(rows[0]["id"]) if len(rows) == 1 else None

    def _periods(self, context, item, query, entity, spec):
        requested = item.get("periods_by_entity", {}).get(query, item.get("periods", []))
        available = None
        resolved = []
        for period in requested:
            if "relative" not in period:
                resolved.append(period)
                continue
            if available is None:
                metric = item.get("metric", "revenue")
                args = {"entity": entity, "metric": metric, "source": spec["source"], "cutoff": spec["cutoff"]}
                if item.get("unit"):
                    args["unit"] = item["unit"]
                context, result = yield from self._tool(context, "financial_facts", args,
                    "Resolve relative dates from published available periods; do not infer fiscal-quarter dates.")
                available = result.get("data", {}).get("available_periods", [])
            exact, problem = select_relative_period(period, available)
            if exact is None:
                # An unanswered clarification preserves the finite episode and
                # does not select a convenient different period.
                context = yield {"action": "clarify", "question": problem + ". Please specify the requested exact start/end dates.",
                                 "reason": "Relative period could not be uniquely resolved from eligible source observations."}
                return context, []
            resolved.append(exact)
        return context, resolved

    def _documents(self, context, entity, period, query, spec, *, force=False):
        # Existing unrelated passages do not establish coverage for this query.
        context, result = yield from self._tool(context, "search_documents", {
            "entity": entity, "period_end": period["end"], "query": query, "cutoff": spec["cutoff"], "limit": 5},
            "Retrieve issuer/period-filtered passages for the requested explanation.")
        if result.get("ok") and result.get("evidence") and spec["source"] != "live" and not force:
            return context
        context, result = yield from self._tool(context, "discover_filings", {
            "entity": entity, "report_date": period["end"], "cutoff": spec["cutoff"], "source": spec["source"],
            "forms": ["10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F", "6-K"], "limit": 3},
            "A lexical miss does not disqualify the filing; discover original filing metadata.")
        rows = result.get("data", {}).get("filings", [])
        for filing in rows[:2]:
            if not filing.get("primary_document") or not filing.get("url"):
                continue
            base = {"entity": entity, "accession": filing["accession"], "primary_document": filing["primary_document"],
                    "cutoff": spec["cutoff"], "source": spec["source"], "limit_chars": 4000}
            context, passage = yield from self._tool(context, "read_filing", {**base, "query": query},
                                                     "Read the discovered filing, preserving original evidence spans.")
            if passage.get("ok"):
                return context
            if passage.get("data", {}).get("source_observation"):
                # HTML was available but lexical retrieval failed. One fixed
                # preview gives the common Researcher original context, without
                # claiming the preview exhausts a potentially long filing.
                context, preview = yield from self._tool(context, "read_filing", {**base, "offset": 0},
                    "Inspect one bounded filing preview after a lexical miss; missing coverage remains explicit.")
                if preview.get("ok"):
                    return context
        return context

    def _acquire_item(self, context, item, identities, spec, *, force=False):
        for query in item["entities"]:
            entity = identities.get(query)
            if entity is None:
                continue
            kind = item["kind"]
            if kind == "price":
                window = item["window"]
                prices = [e for e in context.get("evidence", []) if e.get("kind") == "market_price"
                          and e.get("entity") == entity and self._fresh(e, spec["cutoff"])]
                pair = [next((e for e in prices if e.get("period_end") == day), None) for day in (window["end"], window["start"])]
                if not all(pair) or force:
                    context, result = yield from self._tool(context, "market_prices", {
                        "entity": entity, "start_date": window["start"], "end_date": window["end"], "cutoff": spec["cutoff"]},
                        "Retrieve the exact requested endpoint closes, without weekend substitution.")
                    rows = result.get("evidence", []) if result.get("ok") else []
                    current = {e["id"]: e for e in context.get("evidence", [])}
                    pair = [next((current.get(e["id"]) for e in rows if e.get("period_end") == day), None)
                            for day in (window["end"], window["start"])]
                context, _ = yield from self._calculation(context, "price_return", pair, force=force)
                continue
            context, periods = yield from self._periods(context, item, query, entity, spec)
            if not periods:
                continue
            if kind == "explanation":
                context = yield from self._documents(context, entity, periods[0], item["query"], spec, force=force)
                continue
            records = []
            for period in periods:
                context, value = yield from self._fact(context, entity, item["metric"], period, spec,
                                                      unit=item.get("unit"), force=force)
                if kind == "margin":
                    context, revenue = yield from self._fact(context, entity, "revenue", period, spec,
                                                            unit=item.get("unit"), force=force)
                    context, value = yield from self._calculation(context, "margin", [value, revenue], force=force)
                records.append(value)
            if len(records) == 2 and all(records):
                ordered = sorted(records, key=lambda r: r["period_end"], reverse=True)
                operation = {"growth": "growth", "compare_periods": "difference", "margin": "percentage_point_change"}.get(kind)
                if operation:
                    context, _ = yield from self._calculation(context, operation, ordered, force=force)
        return context

    def _run(self, spec, context):
        identities = {}
        queries = list(dict.fromkeys(e for item in spec["items"] for e in item["entities"]))
        for query in queries:
            existing = {e["entity"] for e in context.get("evidence", []) if e.get("entity", "").casefold() == query.casefold()
                        and self._fresh(e, spec["cutoff"])}
            if len(existing) == 1 and spec["source"] != "live":
                identities[query] = next(iter(existing))
                continue
            context, result = yield from self._tool(context, "discover_company", {"query": query, "source": spec["source"], "view": "identity"},
                                                    "Resolve this issuer through the shared adapter; no company-specific workflow.")
            companies = result.get("data", {}).get("companies", [])
            if len(companies) > 1 or result.get("data", {}).get("truncated"):
                context = yield {"action": "clarify", "question": "Several issuers match " + query + "; specify its ticker or SEC CIK.",
                                 "reason": "The fixed workflow does not guess ambiguous issuer identity."}
                return
            identities[query] = companies[0]["entity"] if result.get("ok") and len(companies) == 1 else None
        # Preserve unresolved query labels so requirements and failure gaps stay
        # visible. Canonical names otherwise permit correct evidence reuse.
        canonical = list(dict.fromkeys(identities.get(q) or q for q in queries))
        self._pending_scope = {"entities": canonical, "period_start": None, "period_end": None}
        for item in spec["items"]:
            context = yield from self._acquire_item(context, item, identities, spec)
        context = yield {"action": "research",
                         "reason": "Synthesize all requested items, retaining unavailable data as explicit gaps."}
        retrieval_issues = {"source_time_mismatch", "numerical_inconsistency", "omission", "conflicting_evidence",
                            "missing_reference", "stale_reference", "context_mismatch", "missing_provenance", "unsupported_statement"}
        for _ in range(self.max_revision_rounds):
            issues = context.get("issues", [])
            if not issues:
                break
            claim_requirements = {c["id"]: c.get("requirement_ids", []) for c in context.get("claims", [])}
            targets = set()
            for issue in issues:
                if issue.get("issue_type") in retrieval_issues:
                    target = issue.get("target_id")
                    targets.update(claim_requirements.get(target, [target]))
            for item in spec["items"]:
                if item["id"] in targets:
                    context = yield from self._acquire_item(context, item, identities, spec, force=True)
            context = yield {"action": "research", "reason": "Apply the fixed bounded repair pass and verify the revised candidate with the same Critic."}
        yield {"action": "finish", "reason": "Fixed workflow finished; the common controller decides complete versus partial."}
