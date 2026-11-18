"""Study, retrieval and fixed-planning commands with explicit model boundaries."""
from __future__ import annotations

import json
from pathlib import Path


COMMANDS = {"study", "retrieval", "planning"}


def read_json(path):
    from .model import _json_loads
    path = Path(path)
    if path.stat().st_size > 100_000_000:
        raise ValueError("JSON input exceeds the 100 MB file limit")
    return _json_loads(path.read_text(encoding="utf-8"))


def _output(value, path=None):
    if path:
        from .evaluation_cli import write_new_json
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_new_json(path, value)
        return {"output": str(Path(path).resolve())}
    return value


def register(sub):
    study = sub.add_parser("study", help="Prepare annotations and score saved runs without model calls")
    actions = study.add_subparsers(dest="study_action", required=True)
    skeleton = actions.add_parser("skeleton", help="Write a blank held-out case plan; no fabricated human labels")
    skeleton.add_argument("--count", type=int, default=12)
    skeleton.add_argument("--output", required=True)
    entry = actions.add_parser("entry", help="Create a grouped study manifest entry from a captured case")
    entry.add_argument("--case", required=True)
    entry.add_argument("--metadata", required=True)
    entry.add_argument("--output", required=True)
    prepare = actions.add_parser("prepare", help="Create a draft study plan from an explicit JSON configuration")
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--output", required=True)
    validate = actions.add_parser("validate", help="Validate a study plan and leakage constraints")
    validate.add_argument("--plan", required=True)
    validate.add_argument("--frozen", action="store_true")
    freeze = actions.add_parser("freeze", help="Freeze a fully specified study plan")
    freeze.add_argument("--plan", required=True)
    freeze.add_argument("--output", required=True)
    packet = actions.add_parser("packet", help="Bind original and revised answers to exact run outputs")
    for name in ("case", "detection", "revisions", "condition", "output"):
        packet.add_argument("--" + name, required=True)
    packet.add_argument("--trial-id", default="trial-1")
    annotations = actions.add_parser("annotations", help="Write independent annotator templates, pending human work")
    annotations.add_argument("--packet", required=True)
    annotations.add_argument("--annotators", nargs="+", required=True)
    annotations.add_argument("--output", required=True)
    check_annotation = actions.add_parser("validate-annotation")
    check_annotation.add_argument("--packet", required=True)
    check_annotation.add_argument("--annotation", required=True)
    adjudicate = actions.add_parser("adjudicate", help="Validate and bind an explicit human resolution")
    adjudicate.add_argument("--packet", required=True)
    adjudicate.add_argument("--annotations", nargs="+", required=True)
    adjudicate.add_argument("--resolution", required=True)
    adjudicate.add_argument("--output", required=True)
    score = actions.add_parser("score", help="Calculate revision outcomes from completed human adjudication")
    for name in ("packet", "adjudication", "output"):
        score.add_argument("--" + name, required=True)
    detection = actions.add_parser("score-detection", help="Score review findings against independent human gold")
    for name in ("case", "detection", "condition", "gold", "judgments", "output"):
        detection.add_argument("--" + name, required=True)
    resources = actions.add_parser("resources", help="Summarize attempts, latency and known token/cost bounds")
    resources.add_argument("--detection", required=True)
    resources.add_argument("--revisions")
    resources.add_argument("--prices", help="Explicit versioned model pricing JSON; omitted means no invented money cost")
    resources.add_argument("--output", required=True)
    resources.add_argument("--trial-id", default="trial-1")
    aggregate = actions.add_parser("aggregate", help="Export study JSON/CSV, preserving missing runs and failures")
    aggregate.add_argument("--plan", required=True)
    aggregate.add_argument("--scores", nargs="*", default=[])
    aggregate.add_argument("--resources", nargs="*", default=[])
    aggregate.add_argument("--detection-scores", nargs="*", default=[])
    aggregate.add_argument("--require-complete", action="store_true")
    aggregate.add_argument("--output", required=True)

    retrieval = sub.add_parser("retrieval", help="Build/search frozen document corpora and run E3 retrieval scoring")
    actions = retrieval.add_subparsers(dest="retrieval_action", required=True)
    corpus = actions.add_parser("corpus", help="Export the hash-verified replay document corpus")
    corpus.add_argument("--output", required=True)
    build = actions.add_parser("build-embeddings", help="Encode with supplied local safetensors weights; no model API or downloads")
    build.add_argument("--corpus")
    build.add_argument("--queries", required=True, help="JSON list of exact query strings")
    build.add_argument("--model-path", required=True)
    build.add_argument("--model-id", required=True)
    build.add_argument("--revision", required=True)
    build.add_argument("--query-prefix", default="")
    build.add_argument("--passage-prefix", default="")
    build.add_argument("--batch-size", type=int, default=16)
    build.add_argument("--output", required=True)
    for name in ("search", "evaluate"):
        child = actions.add_parser(name)
        child.add_argument("--corpus", help="Corpus JSON exported by retrieval corpus; default uses replay documents")
        child.add_argument("--embeddings", help="Frozen, model-identified embeddings matched to this corpus")
        child.add_argument("--limit", type=int, default=5)
        child.add_argument("--max-context-chars", type=int, default=9000)
        child.add_argument("--output", required=name == "evaluate")
        if name == "search":
            child.add_argument("--query", required=True)
            child.add_argument("--filters", help="Metadata filter JSON file")
            child.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="bm25")
        else:
            child.add_argument("--cases", required=True, help="Queries and independent relevant-source spans")
            child.add_argument("--modes", nargs="+", choices=["bm25", "dense", "hybrid"], default=["bm25", "dense", "hybrid"])
            child.add_argument("--repeats", type=int, default=3)

    planning = sub.add_parser("planning", help="Preview a fixed workflow or run a matched E2 model experiment")
    actions = planning.add_subparsers(dest="planning_action", required=True)
    preview = actions.add_parser("preview", help="Run fixed planning and offline source tools; stop before synthesis")
    preview.add_argument("--task", required=True)
    preview.add_argument("--output", required=True)
    preview.add_argument("--tool-calls", type=int, default=16)
    preview.add_argument("--planner-steps", type=int, default=64)
    preview.add_argument("--seconds", type=float, default=240)
    from .configuration import add_source_arguments
    add_source_arguments(preview, live_adapter=False)
    run = actions.add_parser("run", help="Run adaptive/fixed matched research; REQUIRES model configuration")
    run.add_argument("--case", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--model-calls", type=int, default=16)
    run.add_argument("--tool-calls", type=int, default=16)
    run.add_argument("--planner-steps", type=int, default=64)
    run.add_argument("--seconds", type=float, default=240)
    run.add_argument("--order", nargs=2, choices=["adaptive", "fixed"], default=["adaptive", "fixed"])
    add_source_arguments(run, live_adapter=False)
    packet = actions.add_parser("packet", help="Bind an E2 turn's before/after answers and actual tool calls for human assessment")
    for field in ("case", "run", "condition", "output"):
        packet.add_argument("--" + field, required=True)
    packet.add_argument("--turn", type=int, default=1)
    packet.add_argument("--trial-id", default="trial-1")
    annotations = actions.add_parser("annotations", help="Create independent E2 review forms; no truth is prefilled")
    annotations.add_argument("--packet", required=True)
    annotations.add_argument("--annotators", nargs="+", required=True)
    annotations.add_argument("--output", required=True)
    check = actions.add_parser("validate-annotation")
    check.add_argument("--packet", required=True)
    check.add_argument("--annotation", required=True)
    adjudicate = actions.add_parser("adjudicate")
    for field in ("packet", "resolution", "output"):
        adjudicate.add_argument("--" + field, required=True)
    adjudicate.add_argument("--annotations", nargs="+", required=True)
    score = actions.add_parser("score")
    for field in ("packet", "adjudication", "output"):
        score.add_argument("--" + field, required=True)
    summary = actions.add_parser("summarize")
    summary.add_argument("--run", required=True)
    summary.add_argument("--scores", nargs="*", default=[])
    summary.add_argument("--output", required=True)


def _study(args):
    from . import study
    from . import outcome_scoring as scoring
    from .evaluation_cli import load_case
    action = args.study_action
    if action == "skeleton":
        return _output(study.blank_heldout_plan(args.count), args.output)
    if action == "entry":
        return _output(study.case_manifest_entry(load_case(args.case), **read_json(args.metadata)), args.output)
    if action == "prepare":
        return _output(study.create_study_plan(**read_json(args.config)), args.output)
    if action == "validate":
        study.validate_study_plan(read_json(args.plan), require_frozen=args.frozen)
        return {"valid": True, "frozen_required": args.frozen}
    if action == "freeze":
        return _output(study.freeze_study_plan(read_json(args.plan)), args.output)
    if action == "packet":
        packet = scoring.build_outcome_packet(load_case(args.case), read_json(args.detection), read_json(args.revisions), args.condition, trial_id=args.trial_id)
        return _output(packet, args.output)
    if action == "annotations":
        return study.write_annotation_bundle(read_json(args.packet), args.output, args.annotators)
    if action == "validate-annotation":
        scoring.validate_annotation(read_json(args.packet), read_json(args.annotation))
        return {"valid": True}
    if action == "adjudicate":
        result = scoring.adjudicate_annotations(read_json(args.packet), [read_json(p) for p in args.annotations], read_json(args.resolution))
        return _output(result, args.output)
    if action == "score":
        return _output(scoring.score_outcome(read_json(args.packet), read_json(args.adjudication)), args.output)
    if action == "score-detection":
        from .evaluation import score_detection
        run = read_json(args.detection)
        if args.condition not in run.get("conditions", {}):
            raise ValueError("The selected condition is absent from the detection run")
        result = score_detection(load_case(args.case), run["conditions"][args.condition], read_json(args.gold), read_json(args.judgments))
        return _output(result, args.output)
    if action == "resources":
        result = scoring.summarize_resources(read_json(args.detection), read_json(args.revisions) if args.revisions else None,
                                             prices=read_json(args.prices) if args.prices else None, trial_id=args.trial_id)
        return _output(result, args.output)
    if action == "aggregate":
        result = study.aggregate_results(read_json(args.plan), [read_json(p) for p in args.scores],
                                         resources=[read_json(p) for p in args.resources], detection_scores=[read_json(p) for p in args.detection_scores],
                                         require_complete=args.require_complete)
        return study.export_study_summary(result, args.output)
    raise ValueError("Unknown study action")


def _retrieval(args):
    from .retrieval import Corpus, FrozenEmbeddings, search, evaluate, load_replay_corpus
    if getattr(args, "corpus", None):
        record = read_json(args.corpus)
        if not isinstance(record, dict) or not isinstance(record.get("documents"), list):
            raise ValueError("Corpus file must contain documents and its frozen fingerprint")
        corpus = Corpus(record["documents"], **record.get("chunking", {}))
        if record.get("fingerprint") != corpus.fingerprint:
            raise ValueError("Corpus does not match its saved fingerprint")
    else:
        corpus = load_replay_corpus(args.data_dir)
    if args.retrieval_action == "corpus":
        return _output({"fingerprint": corpus.fingerprint, "chunking": {"max_chars": corpus.max_chars, "overlap": corpus.overlap}, "documents": corpus.documents}, args.output)
    if args.retrieval_action == "build-embeddings":
        from .retrieval import freeze_local_embeddings
        result = freeze_local_embeddings(corpus, read_json(args.queries), args.model_path,
                    model_id=args.model_id, revision=args.revision, query_prefix=args.query_prefix,
                    passage_prefix=args.passage_prefix, batch_size=args.batch_size)
        return _output(result, args.output)
    embeddings = FrozenEmbeddings(args.embeddings, corpus) if args.embeddings else None
    if args.retrieval_action == "search":
        result = search(corpus, args.query, read_json(args.filters) if args.filters else None, args.mode,
                        args.limit, embeddings, args.max_context_chars)
    else:
        result = evaluate(corpus, read_json(args.cases), modes=args.modes, embeddings=embeddings,
                          limit=args.limit, max_context_chars=args.max_context_chars, repeats=args.repeats)
    return _output(result, args.output)


def _planning(args):
    from .planning_evaluation import FrozenPlanningCase, run_planning_pair, preview_fixed_workflow
    if args.planning_action not in {"preview", "run"}:
        from . import planning_scoring as scoring
        action = args.planning_action
        if action == "packet":
            result = scoring.build_planning_packet(FrozenPlanningCase.from_dict(read_json(args.case)), read_json(args.run),
                                                   args.condition, turn=args.turn, trial_id=args.trial_id)
        elif action == "annotations":
            return scoring.write_planning_annotation_bundle(read_json(args.packet), args.output, args.annotators)
        elif action == "validate-annotation":
            scoring.validate_planning_annotation(read_json(args.packet), read_json(args.annotation))
            return {"valid": True}
        elif action == "adjudicate":
            result = scoring.adjudicate_planning_annotations(read_json(args.packet), [read_json(p) for p in args.annotations], read_json(args.resolution))
        elif action == "score":
            result = scoring.score_planning_outcome(read_json(args.packet), read_json(args.adjudication))
        else:
            result = scoring.summarize_planning_scores(read_json(args.run), [read_json(p) for p in args.scores])
        return _output(result, args.output)
    from .configuration import tool_options
    from .tools import ToolRegistry
    options = tool_options(args)
    factory = lambda store, sources, cache: ToolRegistry(store, sources, allow_network=False, cache_dir=cache, **options)
    if args.planning_action == "preview":
        return preview_fixed_workflow(read_json(args.task), args.data_dir, output_dir=args.output,
                                      max_tool_calls=args.tool_calls, max_planner_steps=args.planner_steps, max_seconds=args.seconds,
                                      tools_factory=factory)
    from .model import ModelClient
    from .engine import Budget
    case = FrozenPlanningCase.from_dict(read_json(args.case))
    return run_planning_pair(case, ModelClient.from_env, args.data_dir, output_dir=args.output,
                             budget=Budget(args.model_calls, args.tool_calls, args.seconds, max_planner_steps=args.planner_steps),
                             condition_order=tuple(args.order), tools_factory=factory)


def run(args):
    result = {"study": _study, "retrieval": _retrieval, "planning": _planning}[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if args.command == "planning" and args.planning_action == "run":
        failed = any(turn.get("status") == "unavailable" or (turn.get("episode") or {}).get("stop_reason") == "execution_error"
                     for arm in result.get("conditions", {}).values() for turn in arm.get("turns", []))
        return 2 if failed else 0
    return 0
