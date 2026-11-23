"""CLI for real research, persisted sessions and inspectable exports."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import uuid

from .engine import Budget, ResearchEngine, session_lock
from .model import ModelClient, ModelError
from .research_methods import PACK_NAMES
from .reporting import export_report, render_markdown
from .storage import SessionStore
from .tools import ToolRegistry


DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "replay"
if not DEFAULT_DATA.is_dir():
    from importlib.resources import files
    DEFAULT_DATA = Path(str(files("verifin_replay")))


def parser():
    from .configuration import add_source_arguments
    p = argparse.ArgumentParser(prog="verifin", description="Financial research with persistent evidence and verification")
    p.add_argument("--db", default=".verifin/sessions.sqlite3", help="Local SQLite session database")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA), help="Replay snapshots directory")
    sub = p.add_subparsers(dest="command", required=True)
    create = sub.add_parser("new", help="Create a session")
    create.add_argument("--title", default="Untitled")
    sub.add_parser("sessions", help="List saved sessions")
    ask = sub.add_parser("ask", help="Run real model research in a saved session")
    ask.add_argument("session_id")
    ask.add_argument("message")
    ask.add_argument("--request-id", help="Use a stable ID for this question so the same request can be resumed")
    ask.add_argument("--as-of", help="Set the publication cutoff before the first model call (ISO date or timezone-aware timestamp)")
    ask.add_argument("--finish-after-review", action="store_true",
                     help="Deliver a fully checked report immediately after its successful review")
    ask.add_argument("--reference-duplicate-records", action="store_true",
                     help="Refer to complete evidence already visible in this model request")
    ask.add_argument("--live", action="store_true", help="Permit SEC network tools in addition to replay data")
    ask.add_argument("--model-calls", type=int, default=16)
    ask.add_argument("--max-context-chars", type=int, help="Set this session's per-call context character limit, including future follow-ups. Larger inputs may cost more.")
    ask.add_argument("--tool-calls", type=int, default=16)
    ask.add_argument("--seconds", type=float, default=240)
    ask.add_argument("--planner-steps", type=int, default=64)
    ask.add_argument("--capture-case", help="Save the first candidate before review as an E1 case JSON")
    ask.add_argument("--method-pack", choices=PACK_NAMES, help="Override automatic per-turn research skills with a fixed historical method pack")
    ask.add_argument("--offline", action="store_true", help="Run automatic research skills on local disclosures; stop before any model call")
    ask.add_argument("--stop-on-tool-fault", action="store_true", help="Stop for inspection on a calculation failure or identical repeated document result")
    add_source_arguments(ask)
    resume = sub.add_parser("resume", help="Resume an existing request using its saved inputs and remaining allowance")
    resume.add_argument("session_id")
    resume.add_argument("request_id")
    request = sub.add_parser("request-status", help="Inspect a durable request receipt (the latest request by default)")
    request.add_argument("session_id")
    request.add_argument("request_id", nargs="?")
    for command in ("show", "trace", "cancel"):
        child = sub.add_parser(command)
        child.add_argument("session_id")
    recover = sub.add_parser("recover-interrupted", help="Save the last review of a stopped turn without calling a model or financial tool")
    recover.add_argument("session_id")
    audit = sub.add_parser("audit", help="Audit saved session logs without model calls or semantic truth scoring")
    audit.add_argument("session_id")
    audit.add_argument("--output")
    scope = sub.add_parser("scope", help="Inspect or explicitly edit a research scope without a model call")
    scope.add_argument("session_id")
    scope.add_argument("--file", help="JSON scope to save")
    scope.add_argument("--expected-version", type=int, help="Required when saving to reject outdated edits")
    charts = sub.add_parser("charts", help="Export numeric observations and SVG charts from a saved report")
    charts.add_argument("session_id")
    charts.add_argument("destination")
    charts.add_argument("--version", type=int)
    export = sub.add_parser("export")
    export.add_argument("session_id")
    export.add_argument("destination")
    export.add_argument("--version", type=int, help="Export a saved report version instead of the latest")
    evaluate = sub.add_parser("evaluate", help="Run paired E1 reviews; requires configured model access")
    evaluate.add_argument("--case", required=True, help="Pre-review case JSON captured by ask --capture-case")
    evaluate.add_argument("--output", required=True, help="New or empty output directory")
    evaluate.add_argument("--revise", action="store_true", help="Also run both review conditions through the same revision executor")
    evaluate.add_argument("--live", action="store_true", help="Permit SEC requests during revisions")
    evaluate.add_argument("--model-calls", type=int, default=16, help="Model-attempt allowance per revision condition, beyond initial detection")
    evaluate.add_argument("--tool-calls", type=int, default=16)
    evaluate.add_argument("--seconds", type=float, default=240)
    evaluate.add_argument("--planner-steps", type=int, default=64)
    add_source_arguments(evaluate)
    serve = sub.add_parser("serve", help="Open a local browser interface")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--live", action="store_true")
    serve.add_argument("--offline", action="store_true", help="Use local disclosures without loading model credentials")
    add_source_arguments(serve)
    sub.add_parser("config", help="Check configured model settings without revealing credentials")
    from .offline_cli import register
    register(sub)
    return p


_ENGINE_FLAGS = ("finish_after_review", "reference_duplicate_records", "stop_on_tool_fault")


def run_request(store, args):
    from .configuration import source_flags, tool_options
    from .evaluation_cli import capture_callback
    from .research_methods import configure_methods
    from .submissions import lookup_submission, model_binding, research_cutoff, reserve_submission, saved_response

    asking = args.command == "ask"
    message, as_of = (args.message, args.as_of) if asking else (None, None)
    submission = lookup_submission(store, args.session_id, args.request_id, message, as_of)
    if response := saved_response(store, args.session_id, submission):
        return response
    if not asking and not submission:
        raise ValueError("No saved research request has this ID.")
    if submission:
        offline, max_context_chars = submission["offline"], submission["max_context_chars"]
        options = submission.get("execution_options", {})
    else:
        if args.offline and (args.live or args.method_pack):
            raise ValueError("--offline uses automatic skills and local sources only")
        research_cutoff(message, as_of)
        budget = Budget(args.model_calls, args.tool_calls, args.seconds, max_planner_steps=args.planner_steps)
        offline, max_context_chars = args.offline, args.max_context_chars
        options = {"data_dir": str(Path(args.data_dir).resolve()), "live": args.live,
                   "source_flags": source_flags(args), "method_pack": args.method_pack,
                   "capture_case": str(Path(args.capture_case).resolve()) if args.capture_case else None,
                   "engine_flags": {key: True for key in _ENGINE_FLAGS if getattr(args, key)}}
    live = options.get("live", False)
    source_options = tool_options(argparse.Namespace(live=live, **options.get("source_flags", {})))
    model = None if offline else ModelClient.from_env(max_context_chars=max_context_chars)
    configure_methods(model, options.get("method_pack"))
    capture_path = options.get("capture_case")
    capture = None
    if capture_path:
        if submission:
            def capture(context):
                capture_callback(capture_path)(context)
        else:
            capture = capture_callback(capture_path)
    tools = ToolRegistry(store, Path(options.get("data_dir", args.data_dir)), allow_network=live,
                         user_agent=os.environ.get("SEC_USER_AGENT"), cache_dir=Path(args.db).parent / "snapshots",
                         **source_options)
    request_id = args.request_id or "request_" + uuid.uuid4().hex
    if not submission:
        with session_lock(store, args.session_id):
            prior = lookup_submission(store, args.session_id, request_id, message, as_of)
            if prior is None:
                reserve_submission(store, args.session_id, request_id, message=message, as_of=as_of,
                                   budget=asdict(budget), max_context_chars=max_context_chars,
                                   execution_backend="python-staged", offline=offline,
                                   reset_cancel=True, execution_options=options, model_identity=model_binding(model))
            elif prior.get("execution_options", {}) != options or prior["offline"] != offline:
                raise ValueError("This request ID already has a different execution configuration. Use resume.")
    print(f"Request ID: {request_id}", file=sys.stderr, flush=True)
    return ResearchEngine(store, model, tools, candidate_callback=capture, **options.get("engine_flags", {})).ask(
        args.session_id, message, as_of=as_of, request_id=request_id)


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        from .offline_cli import COMMANDS, run
        if args.command in COMMANDS:
            return run(args)
        if args.command == "config":
            client = ModelClient.from_env()
            print(json.dumps({"configured": True, "model": client.model, "base_url": client.base_url,
                              "api_mode": client.api_mode, "reasoning_effort": client.reasoning_effort,
                              "critic_max_output_tokens": client.critic_max_output_tokens or client.max_output_tokens,
                              "critic_reasoning_effort": client.critic_reasoning_effort or client.reasoning_effort,
                              "enable_thinking": client.enable_thinking,
                              "service_tier": client.service_tier,
                              "network_tested": False, "api_key": "configured (hidden)"}, indent=2))
            return 0
        if args.command == "serve":
            from .webapp import serve
            from .configuration import source_flags, tool_options
            if args.offline and args.live:
                raise ValueError("--offline and --live are mutually exclusive")
            serve(args.db, args.data_dir, port=args.port, allow_network=args.live, tool_options=tool_options(args),
                  offline=args.offline, source_flags=source_flags(args))
            return 0
        if args.command == "evaluate":
            from .evaluation_cli import run_evaluation
            from .configuration import tool_options
            source_options = tool_options(args)
            client = ModelClient.from_env()
            revision_index = 0
            def tool_factory(working_store):
                nonlocal revision_index
                revision_index += 1
                return ToolRegistry(working_store, Path(args.data_dir), allow_network=args.live,
                                    user_agent=os.environ.get("SEC_USER_AGENT"),
                                    cache_dir=Path(args.output) / "snapshots" / f"workspace-{revision_index}", **source_options)
            result = run_evaluation(args.case, args.output, client, revise=args.revise,
                                    tools_factory=tool_factory,
                                    budget=Budget(args.model_calls, args.tool_calls, args.seconds, max_planner_steps=args.planner_steps))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            failed = (any(status != "ok" for status in result["detection_status"].values())
                      or any(status == "unavailable" for status in result.get("revision_status", {}).values())
                      or any(reason == "execution_error" for reason in result.get("revision_stop_reasons", {}).values()))
            return 2 if failed else 0
        path = Path(args.db)
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SessionStore(str(path))
        if args.command == "new":
            print(json.dumps(store.create_session(args.title), ensure_ascii=False, indent=2))
        elif args.command == "sessions":
            print(json.dumps(store.list_sessions(), ensure_ascii=False, indent=2))
        elif args.command in {"ask", "resume"}:
            result = run_request(store, args)
            print(result["question"] if "question" in result else render_markdown(result["report"]))
            if result["episode"].get("stop_reason") == "execution_error":
                print("Research stopped on an execution error. See the report's delivery status and 'trace' for the failed step.", file=sys.stderr)
                return 2
        elif args.command == "request-status":
            from .submissions import lookup_submission, submission_status
            store.get_session(args.session_id)
            receipt = (lookup_submission(store, args.session_id, args.request_id) if args.request_id
                       else store.get_submission(args.session_id))
            if args.request_id and receipt is None:
                raise ValueError("No saved research request has this ID.")
            print(json.dumps(submission_status(receipt) if receipt else None, ensure_ascii=False, indent=2))
        elif args.command == "recover-interrupted":
            tools = ToolRegistry(store, Path(args.data_dir))
            report = ResearchEngine(store, None, tools).recover_interrupted(args.session_id)
            print(json.dumps({"recovered_episode": report["episode_id"] if report else None}))
        elif args.command == "cancel":
            store.request_cancel(args.session_id)
            print("Cancellation requested. No further work will be scheduled after the active call returns.")
        elif args.command == "trace":
            print(json.dumps(store.events(args.session_id), ensure_ascii=False, indent=2))
        elif args.command == "show":
            print(json.dumps(store.get_context(args.session_id), ensure_ascii=False, indent=2))
        elif args.command == "scope":
            from .offline_cli import read_json
            from .scope import edit_scope
            result = (edit_scope(store, args.session_id, read_json(args.file), expected_version=args.expected_version)
                      if args.file else store.get_session(args.session_id))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "audit":
            from .acceptance import audit_session
            from .offline_cli import _output
            result = audit_session(store, args.session_id)
            print(json.dumps(_output(result, args.output), ensure_ascii=False, indent=2))
            return 2 if result["violations"] else 0
        elif args.command == "charts":
            from .charts import export_charts
            summaries = store.list_report_summaries(args.session_id, limit=1)
            if not summaries:
                raise ValueError("No report is available for this session")
            report = store.get_report(args.session_id, args.version if args.version is not None else summaries[-1]["version"])
            print(json.dumps(export_charts(report, args.destination), ensure_ascii=False, indent=2))
        elif args.command == "export":
            if args.version is not None:
                report = store.get_report(args.session_id, args.version)
            else:
                reports = store.list_report_summaries(args.session_id, limit=1)
                if not reports:
                    raise ValueError("No report is available for this session yet.")
                report = store.get_report(args.session_id, reports[-1]["version"])
            print(json.dumps(export_report(report, args.destination, display_report=store.report_with_stage(args.session_id, report)),
                             ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, ModelError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
