"""Offline adapters and scoped objective diagnostics for published QA references.

Inputs are explicitly separated from references. No model, network request,
synthetic corruption, inferred financial date, or manufactured human label is
used here. These metrics are NOT VeriFin's independent-human E1/E2 score.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys

from .model import ModelError, _json_loads
from .retrieval import bm25


VERSION = "published-qa-adapter-v1"
DATASETS = {"finqa", "convfinqa", "tatqa"}
SCALES = {"": Decimal(1), "percent": Decimal("0.01"), "thousand": Decimal(1000),
          "million": Decimal(1000000), "billion": Decimal(1000000000)}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be nonempty text")
    return value


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _table(value):
    if not isinstance(value, list) or not value or any(not isinstance(row, list) or any(not isinstance(cell, str) for cell in row) for row in value):
        raise ValueError("Dataset table must be a nonempty list of string rows")
    return value


def _strings(value, name):
    if not isinstance(value, list) or any(not isinstance(text, str) for text in value):
        raise ValueError(name + " must be a string list")
    return value


def _blocks(group_id, table, paragraphs, *, whole_table=False):
    """One deterministic serialization for all rows, independent of qrels."""
    rows = []
    if whole_table:
        rows.append(("table", "\n".join(" | ".join(row) for row in table)))
    else:
        header = table[0]
        for index, row in enumerate(table):
            if index == 0:
                content = " | ".join(row)
            else:
                content = (row[0] if row else "") + "; " + "; ".join(
                    (header[col] if col < len(header) else f"column {col}") + ": " + cell
                    for col, cell in enumerate(row[1:], 1))
            rows.append((f"table_{index}", content))
    rows.extend(paragraphs)
    result = []
    for locator, content in rows:
        # Empty fragments have no retrievable content; qrels that refer to them
        # remain visible as mapping gaps instead of being silently counted hits.
        if not content.strip():
            continue
        result.append({"id": "public_source_" + _hash([group_id, locator, content])[:24],
                       "locator": locator, "text": content, "group_id": group_id})
    return result


@dataclass(frozen=True)
class PublicCase:
    dataset: str
    split: str
    original_id: str
    group_id: str
    dialogue_id: str | None
    turn_index: int | None
    source: dict
    task_input: dict
    reference: dict

    @property
    def case_id(self):
        return self.dataset + ":" + self.split + ":" + self.original_id

    def input_record(self):
        """The ONLY record intended for a model. Never includes gold/history answers."""
        return _copy({"case_id": self.case_id, "dataset": self.dataset, "split": self.split,
                      "original_id": self.original_id, "group_id": self.group_id,
                      "dialogue_id": self.dialogue_id, "turn_index": self.turn_index,
                      "source": self.source, "input": self.task_input,
                      "input_sha256": _hash(self.task_input)})

    def reference_record(self):
        return _copy({"case_id": self.case_id, "input_sha256": _hash(self.task_input),
                      "source": "published_benchmark_reference", "reference": self.reference})


def _source_reference(blocks, locators, *, granularity):
    lookup = {block["locator"]: block["id"] for block in blocks}
    locators = list(dict.fromkeys(locators))
    return {"source_ids": [lookup[key] for key in locators if key in lookup],
            "source_locators": locators,
            "unmapped_source_locators": [key for key in locators if key not in lookup],
            "evidence_granularity": granularity}


def _finqa_cases(rows, dataset, split, provenance):
    for row in rows:
        original_id = _text(row.get("id"), "id")
        filename = _text(row.get("filename"), "filename")
        table = _table(row.get("table"))
        paragraphs = [(f"text_{index}", value) for index, value in enumerate(
            _strings(row.get("pre_text"), "pre_text") + _strings(row.get("post_text"), "post_text"))]
        blocks = _blocks(filename, table, paragraphs)
        if dataset == "convfinqa":
            annotation = row.get("annotation")
            if not isinstance(annotation, dict) or "cur_dial" not in annotation:
                raise ValueError("ConvFinQA adapter requires the official turn-level file (e.g. dev_turn.json)")
            dialogue = _strings(annotation["cur_dial"], "cur_dial")
            index = annotation.get("turn_ind")
            if type(index) is not int or index < 0 or len(dialogue) != index + 1 or not dialogue:
                raise ValueError("ConvFinQA turn index/history length disagree")
            suffix = "_" + str(index)
            if not original_id.endswith(suffix):
                raise ValueError("ConvFinQA turn ID does not match turn_ind")
            dialogue_id = original_id[:-len(suffix)]
            question, history = dialogue[-1], dialogue[:-1]
            answer, program = annotation.get("exe_ans"), annotation.get("cur_program")
            locations = annotation.get("gold_ind", {})
        else:
            qa = row.get("qa")
            if not isinstance(qa, dict):
                raise ValueError("FinQA qa object is required")
            question, history = qa.get("question"), []
            answer, program = qa.get("exe_ans"), qa.get("program")
            locations = qa.get("gold_inds", {})
            dialogue_id, index = None, None
        _text(question, "question")
        if not isinstance(locations, dict) or any(not isinstance(key, str) for key in locations):
            raise ValueError("Published evidence IDs must be a mapping")
        # Original qa, future dialogue_break, turn_program, retrieved rankings,
        # gold evidence strings and all answers are intentionally not copied.
        task = {"question": question, "previous_user_questions": history, "table": table,
                "source_blocks": blocks, "context_mode": "provided_report_excerpt",
                "history_answer_policy": "Use the system's own previous responses; no reference answers supplied"}
        reference = {"answer": answer, "scale": "", "answer_type": "execution_value",
                     "program": program, "answer_available": answer is not None,
                     **_source_reference(blocks, locations.keys(), granularity="annotated_table_row_or_text_sentence")}
        yield PublicCase(dataset, split, original_id, filename, dialogue_id, index, provenance, task, reference)


def _tatqa_cases(rows, split, provenance):
    for context in rows:
        supplied_table = context.get("table")
        if not isinstance(supplied_table, dict):
            raise ValueError("TAT-QA table object is required")
        group = _text(supplied_table.get("uid"), "table.uid")
        table = _table(supplied_table.get("table"))
        paragraphs = context.get("paragraphs")
        if not isinstance(paragraphs, list):
            raise ValueError("TAT-QA paragraphs must be a list")
        source_rows = []
        for paragraph in paragraphs:
            order = paragraph.get("order")
            if type(order) is not int or order < 1:
                raise ValueError("TAT-QA paragraph order must be a positive integer")
            source_rows.append(("paragraph_" + str(order), _text(paragraph.get("text"), "paragraph.text")))
        if len({key for key, _ in source_rows}) != len(source_rows):
            raise ValueError("Duplicate TAT-QA paragraph order")
        blocks = _blocks(group, table, source_rows, whole_table=True)
        questions = context.get("questions")
        if not isinstance(questions, list):
            raise ValueError("TAT-QA questions must be a list")
        for question in questions:
            identity = _text(question.get("uid"), "question.uid")
            raw_locs = question.get("rel_paragraphs", [])
            if not isinstance(raw_locs, list) or any(not isinstance(p, (str, int)) or isinstance(p, bool) for p in raw_locs):
                raise ValueError("TAT-QA rel_paragraphs must contain paragraph orders")
            locations = ["paragraph_" + str(p) for p in raw_locs]
            if question.get("answer_from") in {"table", "table-text"}:
                locations.insert(0, "table")
            task = {"question": _text(question.get("question"), "question"), "previous_user_questions": [],
                    "table": table, "source_blocks": blocks, "context_mode": "provided_hybrid_context"}
            reference = {"answer": question.get("answer"), "answer_available": "answer" in question,
                         "scale": question.get("scale", ""), "answer_type": question.get("answer_type"),
                         "derivation": question.get("derivation"), "answer_from": question.get("answer_from"),
                         **_source_reference(blocks, locations, granularity="annotated_paragraph_and_whole_table_not_cell_spans")}
            yield PublicCase("tatqa", split, identity, group, None, None, provenance, task, reference)


def load_public_dataset(dataset, path, *, split, source_url, revision):
    if dataset not in DATASETS:
        raise ValueError("Supported adapters: " + ", ".join(sorted(DATASETS)))
    if split not in {"train", "dev", "test", "private_test"}:
        raise ValueError("Preserve an explicit official split name")
    _text(source_url, "source_url")
    if not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise ValueError("Pin the upstream 40-character commit revision")
    path = Path(path)
    if path.stat().st_size > 100_000_000:
        raise ValueError("Dataset file exceeds 100 MB")
    raw = path.read_bytes()
    data = _json_loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("Official dataset file must contain a JSON list")
    source = {"url": source_url, "revision": revision, "file_sha256": hashlib.sha256(raw).hexdigest(),
              "file_bytes": len(raw), "adapter_version": VERSION}
    iterator = _tatqa_cases(data, split, source) if dataset == "tatqa" else _finqa_cases(data, dataset, split, source)
    cases = list(iterator)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Duplicate public case IDs")
    return cases


def describe_cases(cases):
    return {"protocol": VERSION, "cases": len(cases), "datasets": dict(Counter(case.dataset for case in cases)),
            "splits": dict(Counter(case.split for case in cases)),
            "context_groups": len({(c.dataset, c.group_id) for c in cases}),
            "dialogues": len({c.dialogue_id for c in cases if c.dialogue_id is not None}),
            "answer_references": sum(c.reference["answer_available"] for c in cases),
            "program_references": sum(bool(c.reference.get("program")) for c in cases),
            "evidence_references": sum(bool(c.reference["source_ids"]) for c in cases),
            "evidence_mapping_gaps": sum(bool(c.reference["unmapped_source_locators"]) for c in cases),
            "answer_types": dict(Counter(str(c.reference["answer_type"]) for c in cases)),
            "scales": dict(Counter(str(c.reference["scale"]) for c in cases)),
            "source": cases[0].source if cases else None,
            "model_calls": 0, "new_human_annotations": 0,
            "critic_semantic_quality": "not_measured", "agent_planning_quality": "not_measured"}


def export_public_inputs(cases, output_dir):
    folder = Path(output_dir)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError("Use a new or empty export directory")
    folder.mkdir(parents=True, exist_ok=True)
    from .evaluation_cli import write_new_json
    write_new_json(folder / "inputs.json", [case.input_record() for case in cases])
    write_new_json(folder / "references.DO_NOT_SEND_TO_MODEL.json", [case.reference_record() for case in cases])
    write_new_json(folder / "coverage.json", describe_cases(cases))
    return {"inputs": str((folder / "inputs.json").resolve()), "references": str((folder / "references.DO_NOT_SEND_TO_MODEL.json").resolve()),
            "note": "Use only inputs.json for model context; references are scorer-only."}


def _number(value, scale=""):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or scale not in SCALES:
        return None
    raw = str(value).strip().replace(",", "")
    if len(raw) > 128:
        return None
    # Explicit percentage notation is meaningful; prose/implicit scale guessing
    # is deliberately rejected. No search for a convenient number in a sentence.
    percent = raw.endswith("%")
    if percent:
        if scale not in {"", "percent"}:
            return None
        raw = raw[:-1].strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw):
        return None
    exponent = re.search(r"[eE]([+-]?\d+)$", raw)
    if exponent and abs(int(exponent.group(1))) > 300:
        return None
    try:
        with localcontext() as context:
            context.prec = 450
            result = Decimal(raw) * (Decimal("0.01") if percent else SCALES[scale])
        return result if result.is_finite() else None
    except DecimalException:
        return None


def match_reference(case, prediction):
    """Scoped objective match, explicitly different from the official full F1."""
    gold = case.reference
    if not gold["answer_available"]:
        return {"scoreable": False, "correct": None, "reason": "reference_unavailable"}
    boolean = isinstance(gold["answer"], str) and gold["answer"].lower() in {"yes", "no"}
    if case.dataset in {"finqa", "convfinqa"} and not boolean and _number(gold["answer"]) is None:
        return {"scoreable": False, "correct": None, "reason": "non_scalar_execution_reference"}
    if case.dataset == "tatqa":
        if gold["answer_type"] not in {"arithmetic", "count", "span", "multi-span"}:
            return {"scoreable": False, "correct": None, "reason": "unknown_reference_type"}
        if gold["answer_type"] in {"arithmetic", "count"} and _number(gold["answer"], gold["scale"]) is None:
            return {"scoreable": False, "correct": None, "reason": "invalid_numeric_reference"}
    if prediction is None:
        return {"scoreable": True, "correct": False, "reason": "missing_prediction"}
    if not isinstance(prediction, dict) or "answer" not in prediction:
        return {"scoreable": True, "correct": False, "reason": "invalid_prediction"}
    answer, scale = prediction["answer"], prediction.get("scale", "")
    if not isinstance(scale, str) or scale not in SCALES:
        return {"scoreable": True, "correct": False, "reason": "invalid_scale"}
    if case.dataset in {"finqa", "convfinqa"}:
        if isinstance(gold["answer"], str) and gold["answer"].lower() in {"yes", "no"}:
            correct = isinstance(answer, str) and answer.strip().lower() == gold["answer"].lower() and scale == ""
            metric = "boolean_exact_match"
        else:
            expected, predicted = _number(gold["answer"]), _number(answer, scale)
            if expected is None:
                return {"scoreable": False, "correct": None, "reason": "non_scalar_execution_reference"}
            with localcontext() as context:
                context.prec = 450
                correct = predicted is not None and round(predicted, 5) == round(expected, 5)
            metric = "execution_value_match_5dp"
    elif gold["answer_type"] in {"arithmetic", "count"}:
        expected, predicted = _number(gold["answer"], gold["scale"]), _number(answer, scale)
        if expected is None:
            return {"scoreable": False, "correct": None, "reason": "invalid_numeric_reference"}
        correct = predicted is not None and predicted == expected
        metric = "scaled_numeric_exact_match"
    else:
        values = answer if isinstance(answer, list) else [answer]
        expected = gold["answer"] if isinstance(gold["answer"], list) else [gold["answer"]]
        if any(not isinstance(v, str) for v in values + expected):
            return {"scoreable": True, "correct": False, "reason": "invalid_span_prediction"}
        normalize = lambda texts: sorted(" ".join(value.split()).casefold() for value in texts)
        correct = normalize(values) == normalize(expected) and scale == gold["scale"]
        metric = "span_multiset_exact_match_with_scale"
    return {"scoreable": True, "correct": bool(correct), "metric": metric}


def score_public_predictions(cases, predictions):
    if not isinstance(predictions, list) or any(not isinstance(row, dict) for row in predictions):
        raise ValueError("Predictions must be a list of {case_id, input_sha256, answer, scale}")
    indexed = {}
    known = {case.case_id: _hash(case.task_input) for case in cases}
    for row in predictions:
        identity = row.get("case_id")
        if not isinstance(identity, str) or identity not in known or identity in indexed:
            raise ValueError("Prediction IDs must be unique and belong to this exact run")
        if row.get("input_sha256") != known[identity]:
            raise ValueError("Prediction must be bound to the exact model-visible input_sha256")
        indexed[identity] = row
    rows = [{"case_id": c.case_id, **match_reference(c, indexed.get(c.case_id))} for c in cases]
    eligible = [row for row in rows if row["scoreable"]]
    return {"protocol": "public-reference-objective-score-v1", "source": "published_benchmark_reference",
            "cases": len(cases), "scoreable_cases": len(eligible), "provided_predictions": len(predictions),
            "missing_predictions": len(cases) - len(predictions), "correct": sum(row["correct"] for row in eligible),
            "accuracy": sum(row["correct"] for row in eligible) / len(eligible) if eligible else None,
            "rows": rows, "official_leaderboard_metric": False,
            "source_snapshots": list({json.dumps(case.source, sort_keys=True): case.source for case in cases}.values()),
            "limitations": ["Strict objective answer matching only; not the full TAT-QA EM/F1 or symbolic program equivalence",
                            "No assessment of explanation truth, all-claim coverage, causality, independent review or planning"]}


def retrieval_probe(cases, *, top_k=5, history="current", max_cases=None):
    """BM25 with existing engine on within-context candidates, never oracle-only."""
    if type(top_k) is not int or not 1 <= top_k <= 100 or history not in {"current", "all_user_questions"}:
        raise ValueError("Invalid retrieval probe options")
    if max_cases is not None and (type(max_cases) is not int or max_cases < 1):
        raise ValueError("max_cases must be a positive integer")
    selected = cases if max_cases is None else cases[:max_cases]
    rows = []
    for case in selected:
        task, reference = case.task_input, case.reference
        # Rank the original source units directly: the same units and IDs used
        # by dense/hybrid/reranking comparisons. Re-chunking would change ID
        # tie breaks and the unit of evidence recall. Long blocks remain whole;
        # this is explicitly a source-unit diagnostic, not fixed-budget RAG.
        docs = task["source_blocks"]
        query = task["question"]
        if history == "all_user_questions":
            query = "\n".join(task["previous_user_questions"] + [query])
        ranked = [block["id"] for block, score in bm25(docs, query)]
        gold = set(reference["source_ids"])
        eligible = bool(gold) and not reference["unmapped_source_locators"]
        hits = len(set(ranked[:top_k]) & gold)
        first = next((i for i, identity in enumerate(ranked, 1) if identity in gold), None)
        rows.append({"case_id": case.case_id, "gold_source_count": len(gold),
                     "candidate_source_count": len(docs), "eligible": eligible,
                     "reason": None if eligible else "no_evidence_labels_or_unmapped_reference",
                     "recall_at_k": hits / len(gold) if eligible else None,
                     "all_evidence_at_k": hits == len(gold) if eligible else None,
                     "reciprocal_rank_at_k": (1 / first if first and first <= top_k else 0) if eligible else None,
                     "reciprocal_rank_unbounded": (1 / first if first else 0) if eligible else None,
                     "ranked_source_ids": ranked[:top_k], "evidence_granularity": reference["evidence_granularity"]})
    eligible = [row for row in rows if row["eligible"]]
    return {"protocol": "public-within-context-retrieval-probe-v2", "model": "bm25", "history": history,
            "top_k": top_k, "cases": len(rows), "eligible_cases": len(eligible), "excluded_cases": len(rows) - len(eligible),
            "mean_recall_at_k": sum(r["recall_at_k"] for r in eligible) / len(eligible) if eligible else None,
            "all_evidence_rate_at_k": sum(r["all_evidence_at_k"] for r in eligible) / len(eligible) if eligible else None,
            "mrr_at_k": sum(r["reciprocal_rank_at_k"] for r in eligible) / len(eligible) if eligible else None,
            "mrr_unbounded": sum(r["reciprocal_rank_unbounded"] for r in eligible) / len(eligible) if eligible else None,
            "corpus_scope": "all supplied source blocks in the question's provided context",
            "ranking_unit": "whole source block; no re-chunking; source ID tie break",
            "query_serialization": "current question, or previous user questions plus current joined with newline; no extra labels",
            "context_char_budget": None,
            "is_full_corpus_E3": False, "model_calls": 0, "rows": rows,
            "note": "Provided-context source selection diagnostic; not full-report retrieval, generalization or Agent quality."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    parser.add_argument("--file", required=True)
    parser.add_argument("--split", required=True, choices=["train", "dev", "test", "private_test"])
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True, help="New JSON report file")
    parser.add_argument("--export-dir", help="New directory; model inputs and scorer references are separate")
    parser.add_argument("--predictions", help="JSON predictions to score; never generate predictions from gold")
    parser.add_argument("--retrieval-probe", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--history", choices=["current", "all_user_questions"], default="current")
    parser.add_argument("--max-probe-cases", type=int)
    args = parser.parse_args(argv)
    try:
        if Path(args.output).exists():
            raise ValueError("Output already exists")
        cases = load_public_dataset(args.dataset, args.file, split=args.split, source_url=args.source_url, revision=args.revision)
        result = {"coverage": describe_cases(cases)}
        if args.predictions:
            result["answer_scores"] = score_public_predictions(cases, _json_loads(Path(args.predictions).read_text(encoding="utf-8")))
        if args.retrieval_probe:
            result["retrieval"] = retrieval_probe(cases, top_k=args.top_k, history=args.history, max_cases=args.max_probe_cases)
        if args.export_dir:
            result["export"] = export_public_inputs(cases, args.export_dir)
        from .evaluation_cli import write_new_json
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        write_new_json(args.output, result)
        print(json.dumps({"output": str(Path(args.output).resolve()), "coverage": result["coverage"]}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, ModelError) as exc:
        print("Public benchmark audit: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
