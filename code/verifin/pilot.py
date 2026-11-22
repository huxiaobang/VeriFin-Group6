"""Explicit, bounded pilot runs; dry runs never call a model or a provider.

This is an execution/collection tool, not a semantic benchmark or a gold-label
generator. Each dialogue turn reopens the same on-disk session database.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .acceptance import audit_session
from .engine import Budget, ResearchEngine
from .evaluation_cli import capture_callback, write_new_json
from .model import ModelClient, ModelError, _json_loads
from .reporting import export_report
from .storage import SessionStore
from .tools import ToolRegistry


PROTOCOL = "verifin-pilot-v1"


def validate_suite(value):
    if not isinstance(value, dict) or set(value) - {"protocol", "label", "split", "budget", "cases"}:
        raise ValueError("Pilot suite accepts protocol, label, split, budget and cases only")
    if value.get("protocol") != PROTOCOL:
        raise ValueError("Unknown pilot suite protocol")
    if value.get("split") not in {"development", "validation", "heldout"}:
        raise ValueError("Declare the suite split explicitly")
    if not isinstance(value.get("label"), str) or not value["label"].strip():
        raise ValueError("Pilot suite label is required")
    budget = value.get("budget")
    required = {"max_model_calls", "max_tool_calls", "max_seconds"}
    if not isinstance(budget, dict) or not required <= set(budget):
        raise ValueError("Explicit model-call, tool-call and elapsed-time budgets are required")
    try:
        limits = Budget(**budget)
    except TypeError:
        raise ValueError("Unknown pilot budget field") from None
    cases = value.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("Pilot suite requires between 1 and 100 dialogues")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "title", "turns"}:
            raise ValueError("Each pilot case accepts id, title and turns only")
        identifier = case.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identifier):
            raise ValueError("Pilot case IDs must be simple unique file-safe identifiers")
        if identifier in seen:
            raise ValueError("Duplicate pilot case ID")
        seen.add(identifier)
        if "title" in case and (not isinstance(case["title"], str) or not case["title"].strip()):
            raise ValueError("A supplied case title must be nonempty")
        turns = case.get("turns")
        if (not isinstance(turns, list) or not 1 <= len(turns) <= 30
                or any(not isinstance(t, str) or not t.strip() or len(t) > 20_000 for t in turns)):
            raise ValueError("Each dialogue needs 1–30 nonempty turns, at most 20,000 characters each")
    return {**json.loads(json.dumps(value, allow_nan=False)), "budget": asdict(limits)}


def read_suite(path):
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("Pilot suite exceeds the 20 MB limit")
    return validate_suite(_json_loads(path.read_text(encoding="utf-8")))


def _safe_settings(model):
    # Never serialize arbitrary client state, credentials, headers or transport.
    names = ("model", "base_url", "api_mode", "reasoning_effort", "service_tier", "max_output_tokens",
             "max_context_chars", "enable_thinking", "timeout")
    for name in ("researcher_max_output_tokens", "critic_max_output_tokens", "critic_reasoning_effort"):
        if getattr(model, name, None) is not None:
            names += (name,)
    return {name: getattr(model, name, None) for name in names}


def _source_settings(options):
    output = {}
    for key, value in (options or {}).items():
        if key in {"market_data", "retrieval_vectors"}:
            path = Path(value)
            output[key] = {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        elif key == "retrieval_encoder":
            output[key] = json.loads(json.dumps(value, allow_nan=False))
        elif key == "market_adapter":
            output[key] = {"adapter_class": type(value).__name__, "credentials": "not_exported"}
        else:
            raise ValueError("Unsupported pilot source configuration")
    return output


def preflight(suite, data_dir, *, model=None, allow_network=False, source_options=None, env=None):
    """Validate local settings only; key presence is reported as a boolean."""
    suite = validate_suite(suite)
    env = os.environ if env is None else env
    from .data import SnapshotData
    data_dir = Path(data_dir).resolve()
    if not (data_dir / "manifest.json").is_file() and (data_dir / "replay" / "manifest.json").is_file():
        data_dir = data_dir / "replay"
    manifest = data_dir / "manifest.json"
    problems = []
    verified_snapshots = 0
    if not manifest.is_file():
        problems.append("replay_manifest_missing")
    else:
        try:
            snapshots = SnapshotData(data_dir, allow_network=False)
            for name in snapshots.manifest["files"]:
                snapshots.raw(name)
                verified_snapshots += 1
        except (OSError, ValueError, TypeError, KeyError):
            problems.append("replay_snapshot_validation_failed")
    key_names = ("VERIFIN_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY", "ALPHAVANTAGE_API_KEY")
    credentials = {key: bool(env.get(key)) for key in key_names}
    if model is None:
        try:
            model = ModelClient.from_env(env)
        except (ModelError, ValueError, TypeError):
            # Error strings can contain third-party transport details; only the
            # configuration state is exposed here, never environment values.
            problems.append("model_configuration_missing_or_invalid")
    if allow_network and not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", env.get("SEC_USER_AGENT", "")):
        problems.append("SEC_USER_AGENT_contact_missing_for_live_sources")
    turns = sum(len(case["turns"]) for case in suite["cases"])
    budget = suite["budget"]
    return {"protocol": PROTOCOL, "phase": "preflight", "created_at": datetime.now(timezone.utc).isoformat(),
            "ready_to_execute": not problems, "problems": problems,
            "suite_label": suite["label"], "split": suite["split"], "dialogues": len(suite["cases"]),
            "turns": turns, "per_episode_budget": budget,
            "conservative_run_upper_bounds": {"model_calls": turns * budget["max_model_calls"],
                                              "tool_calls": turns * budget["max_tool_calls"],
                                              "active_seconds": turns * budget["max_seconds"]},
            "budget_note": "Clarification continuations share their original episode budget. Active-time limits are cooperative; a running call returns before cancellation. Token/call budgets are not a currency spending cap.",
            "model_mode": getattr(model, "mode", "configured_model") if model else "unconfigured",
            "model_settings": _safe_settings(model) if model else None,
            "credential_presence": credentials, "live_source_network_allowed": bool(allow_network),
            "sources": {"data_dir": str(data_dir.resolve()),
                        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None,
                        "verified_replay_snapshots": verified_snapshots,
                        "optional": _source_settings(source_options)},
            "provider_access_tested": False, "model_calls_made": 0, "network_requests_made": 0,
            "semantic_acceptance": "not_assessed", "truth_scored": False}


def run_pilot(suite, output_dir, data_dir, *, execute=False, model=None, allow_network=False,
              source_options=None, tools_factory=None, env=None):
    """Collect a fresh pilot; explicit injected fixtures remain labelled fixtures.

    ``tools_factory(store, case_id, turn_index)`` is an injection seam for tests.
    Production CLI always constructs ToolRegistry. No hidden model fallback is used.
    """
    suite = validate_suite(suite)
    env = os.environ if env is None else env
    inspection = preflight(suite, data_dir, model=model, allow_network=allow_network,
                           source_options=source_options, env=env)
    folder = Path(output_dir)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError("Pilot output directory must be new or empty")
    folder.mkdir(parents=True, exist_ok=True)
    write_new_json(folder / "suite.json", suite)
    write_new_json(folder / "preflight.json", inspection)
    summary = {"protocol": PROTOCOL, "phase": "execution" if execute else "dry_run", "executed": False,
               "ready_to_execute": inspection["ready_to_execute"], "model_mode": inspection["model_mode"],
               "truth_scored": False, "semantic_acceptance": "not_assessed", "cases": [],
               "output_dir": str(folder.resolve())}
    if not execute or not inspection["ready_to_execute"]:
        write_new_json(folder / "summary.json", summary)
        return summary
    model = model if model is not None else ModelClient.from_env(env)
    limits = Budget(**suite["budget"])
    database = folder / "sessions.sqlite3"
    summary["executed"] = True
    for case in suite["cases"]:
        case_dir = folder / case["id"]
        case_dir.mkdir()
        store = SessionStore(database)
        try:
            sid = store.create_session(case.get("title", case["id"]))["id"]
        finally:
            store.close()
        row = {"case_id": case["id"], "session_id": sid, "turns": []}
        summary["cases"].append(row)
        for index, message in enumerate(case["turns"], 1):
            turn_dir = case_dir / f"turn-{index:02d}"
            turn_dir.mkdir()
            store = SessionStore(database)
            try:
                prior_events = len(store.events(sid))
                tools = (tools_factory(store, case["id"], index) if tools_factory else
                         ToolRegistry(store, Path(data_dir), allow_network=allow_network,
                                      user_agent=env.get("SEC_USER_AGENT"), cache_dir=folder / "snapshots",
                                      **(source_options or {})))
                capture = turn_dir / "candidate-before-review.json"
                result = ResearchEngine(store, model, tools, candidate_callback=capture_callback(capture)).ask(
                    sid, message, budget=limits)
                write_new_json(turn_dir / "result.json", result)
                exports = export_report(result["report"], turn_dir / "report") if "report" in result else {}
                events = store.events(sid)
                write_new_json(turn_dir / "events.json", events[prior_events:])
                audit = audit_session(store, sid)
                write_new_json(turn_dir / "session-audit.json", audit)
                row["turns"].append({"turn": index, "status": result["status"],
                                     "episode_id": result["episode"]["id"],
                                     "stop_reason": result["episode"].get("stop_reason"),
                                     "candidate_captured": capture.is_file(),
                                     "report_exports": exports,
                                     "automatic_log_checks": audit["automatic_log_checks"],
                                     "semantic_acceptance": "not_assessed"})
            finally:
                store.close()
        # Reopen after the final turn as well: exports are not the persistence test.
        store = SessionStore(database)
        try:
            row["audit"] = audit_session(store, sid)
            row["saved_report_versions"] = len(store.list_reports(sid))
            write_new_json(case_dir / "session-audit.json", row["audit"])
        finally:
            store.close()
    summary["automatic_checks_passed"] = all(case["audit"]["automatic_log_checks"] == "passed" for case in summary["cases"])
    summary["execution_errors"] = sum(turn["stop_reason"] == "execution_error" for case in summary["cases"] for turn in case["turns"])
    write_new_json(folder / "summary.json", summary)
    return summary


def main(argv=None):
    from .cli import DEFAULT_DATA
    from .configuration import add_source_arguments, tool_options
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--output", required=True, help="New or empty directory")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA))
    parser.add_argument("--execute", action="store_true", help="Actually call the configured model; omitted means preflight only")
    parser.add_argument("--live", action="store_true", help="Also permit source-provider requests; independent of model API access")
    add_source_arguments(parser)
    args = parser.parse_args(argv)
    try:
        result = run_pilot(read_suite(args.suite), args.output, args.data_dir, execute=args.execute,
                           allow_network=args.live, source_options=tool_options(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if args.execute and (not result["executed"] or result.get("execution_errors") or not result.get("automatic_checks_passed")) else 0
    except (ValueError, TypeError, KeyError, OSError, ModelError) as exc:
        # Do not print provider exceptions or user-supplied credential values.
        print("Pilot stopped: " + type(exc).__name__ + ". Check suite, local configuration and output path.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
