"""Generate pending human worksheets from real saved runs, or empty plan slots.

Run from the repository root: python3 -m examples.study.prepare_materials --help
No model or network call occurs. No completed human annotations are generated.
"""
import argparse
import json
from pathlib import Path

from verifin.evaluation_cli import load_case
from verifin.model import _json_loads
from verifin.outcome_scoring import build_outcome_packet
from verifin.study import blank_heldout_plan, create_study_plan, write_annotation_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--case")
    parser.add_argument("--detection")
    parser.add_argument("--revisions")
    parser.add_argument("--condition", default="generic_review")
    parser.add_argument("--annotator", action="append", default=[])
    args = parser.parse_args(argv)
    supplied = [args.case, args.detection, args.revisions]
    if any(supplied) and not all(supplied):
        parser.error("Supply --case, --detection and --revisions together")
    directory = Path(args.output)
    if directory.exists() and any(directory.iterdir()):
        parser.error("Output directory must be new or empty; completed human work is never overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    if all(supplied):
        packet = build_outcome_packet(load_case(args.case), _json_loads(Path(args.detection).read_text()),
                                      _json_loads(Path(args.revisions).read_text()), args.condition)
        result = write_annotation_bundle(packet, directory, args.annotator)
    else:
        values = {"heldout-slots.json": blank_heldout_plan(),
                  "draft-plan.json": create_study_plan([], study_id="group6-e1-pending", heldout_group_ids=[]),
                  "application-acceptance.json": _json_loads(Path(__file__).with_name("application-acceptance.template.json").read_text())}
        for name, value in values.items():
            with (directory / name).open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        result = {"status": "pending_human_case_selection_and_annotation", "completed_cases": 0,
                  "completed_annotations": 0, "files": [str((directory / name).resolve()) for name in values]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
