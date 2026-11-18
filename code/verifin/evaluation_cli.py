"""File-based E1 entry points; no automatic model fallback or truth scoring."""
from __future__ import annotations

import json
from pathlib import Path

from .engine import Budget
from .evaluation import FrozenCase, evaluate_frozen_case


def case_record(case):
    return {"case_id": case.case_id, "context_sha256": case.context_sha256, "context": case.context()}


def write_new_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as output:
        output.write(encoded)


def capture_callback(path):
    path = Path(path)
    if path.exists():
        raise ValueError("Capture destination already exists; choose a new case file")
    path.parent.mkdir(parents=True, exist_ok=True)

    def capture(context):
        case = FrozenCase.from_context(context["episode"]["id"], context)
        record = case_record(case)
        record["capture_stage"] = "initial_candidate_before_review"
        record["generator"] = context.get("capture_metadata", {})
        write_new_json(path, record)
    return capture


def load_case(path):
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("Case file exceeds the 20 MB input limit")
    from .model import _json_loads
    # Use the same strict parser as model output: no duplicate keys or NaN.
    payload = _json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Case file must contain an object")
    case = FrozenCase.from_context(payload.get("case_id"), payload.get("context"))
    if payload.get("context_sha256") != case.context_sha256:
        raise ValueError("Case context does not match its saved SHA-256")
    return case


def run_evaluation(case_path, output_dir, model, *, revise=False, tools_factory=None, budget=None):
    case = load_case(case_path)
    directory = Path(output_dir)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Evaluation output directory must be new or empty")
    limits = budget or Budget()
    if revise and tools_factory is None:
        raise ValueError("Revision requires an explicit tool factory")
    directory.mkdir(parents=True, exist_ok=True)
    write_new_json(directory / "case.json", case_record(case))
    # Preserve capture-stage/generator labels outside the model's task context.
    from .model import _json_loads
    original = _json_loads(Path(case_path).read_text(encoding="utf-8"))
    if original.get("context_sha256") != case.context_sha256:
        raise ValueError("Case file changed while preparing the run")
    write_new_json(directory / "case-source.json", original)
    detection = evaluate_frozen_case(case, model)
    write_new_json(directory / "detection.json", detection)
    summary = {"case_id": case.case_id, "model_mode": detection["model_mode"],
               "detection_status": {name: result["status"] for name, result in detection["conditions"].items()},
               "truth_scored": False, "revision_run": False, "output_dir": str(directory)}
    if revise:
        from dataclasses import asdict
        from .evaluation import run_revisions
        from .revision import CommonRevisionExecutor
        executor = CommonRevisionExecutor(model, tools_factory)
        revisions = run_revisions(case, detection, executor, budget=asdict(limits))
        write_new_json(directory / "revisions.json", revisions)
        summary["revision_run"] = True
        summary["revision_status"] = {name: result["status"] for name, result in revisions["conditions"].items()}
        summary["revision_stop_reasons"] = {name: result["stop_reason"] for name, result in revisions["conditions"].items()}
    write_new_json(directory / "summary.json", summary)
    return summary
