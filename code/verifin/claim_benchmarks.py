"""FinDVer public claim-verification adapter with isolated reference labels.

This is a published component benchmark, not a substitute for natural-output
E1 issue annotation, revision quality, required-content coverage, or E2 planning.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .data import DataError
from .retrieval import bm25

FINDVER_REVISION = "e8bb237def4ce555a606a45edba22666e31df248"
FINDVER_URL = "https://github.com/yilunzhao/FinDVer"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ClaimCase:
    case_id: str
    group_id: str
    subset: str
    split: str
    task_input: dict
    reference: dict
    provenance: dict

    @property
    def model_case_id(self):
        # Official FinDVer sequence-number ranges encode the binary labels.
        # Derive an opaque key ONLY from input content; mapping stays scorer-side.
        return "claim_" + digest(self.task_input)[:32]

    def input_record(self):
        # Whitelist only original claim and original full-report source content.
        # Reference explanations/labels/relevant_context are never model input.
        return json.loads(json.dumps({"case_id": self.model_case_id,
                                      "input": self.task_input, "input_sha256": digest(self.task_input)}, ensure_ascii=False))

    def reference_record(self):
        return {"case_id": self.model_case_id, "official_case_id": self.case_id,
                "subset": self.subset, "split": self.split, "provenance": self.provenance,
                "input_sha256": digest(self.task_input),
                "reference": self.reference, "annotation_origin": "FinDVer published expert annotation; explanations proofread with GPT-4o per upstream README"}


def load_findver(annotation_path, reports_dir, *, split, revision=FINDVER_REVISION, selected_ids=None,
                 expected_report_hashes=None):
    path, reports_dir = Path(annotation_path), Path(reports_dir).resolve()
    rows = json.loads(path.read_text())
    if split not in {"testmini", "test"} or not isinstance(rows, list):
        raise DataError("FinDVer requires an official testmini/test annotation array.")
    selected = set(selected_ids) if selected_ids is not None else None
    available_ids = [r.get("example_id") for r in rows if isinstance(r, dict)]
    if len(available_ids) != len(rows) or len(set(available_ids)) != len(rows):
        raise DataError("FinDVer requires unique official example IDs.")
    if selected is not None and (not selected or selected - set(available_ids)):
        raise DataError("Selection contains missing FinDVer example IDs.")
    cases, cache = [], {}
    data_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    for row in rows:
        if selected is not None and row["example_id"] not in selected:
            continue
        if row.get("split") != split or row.get("subset") not in {"ie", "numeric", "knowledge"}:
            raise DataError("FinDVer split/subset does not match the declared source.")
        label = row.get("entailment_label")
        if type(label) is not bool or not isinstance(row.get("statement"), str) or not row["statement"].strip():
            raise DataError("FinDVer public label and statement are required.")
        name = row.get("report")
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".json"):
            raise DataError("FinDVer report must name a local JSON file.")
        report_path = reports_dir / name
        if not report_path.resolve().is_relative_to(reports_dir) or not report_path.is_file():
            raise DataError("Missing local FinDVer report: " + name)
        if name not in cache:
            raw = report_path.read_bytes()
            if expected_report_hashes is not None and expected_report_hashes.get(name) != hashlib.sha256(raw).hexdigest():
                raise DataError("FinDVer report differs from the pinned official source manifest: " + name)
            report = json.loads(raw)
            context = report.get("context")
            if not isinstance(context, list) or not context or not isinstance(report.get("url"), str):
                raise DataError("FinDVer report lacks full context or original source URL.")
            blocks, ids = [], set()
            for block in context:
                identity, text = block.get("id"), block.get("context")
                if type(identity) is not int or identity in ids or not isinstance(text, str) or not text.strip():
                    raise DataError("FinDVer context IDs and text must be unique and nonempty.")
                ids.add(identity)
                blocks.append({"id": name + "#context-" + str(identity), "context_id": identity,
                               "type": block.get("type"), "text": text,
                               "locator": {"report": name, "context_id": identity}})
            cache[name] = (blocks, report["url"], hashlib.sha256(raw).hexdigest())
        blocks, source_url, report_hash = cache[name]
        relevant = row.get("relevant_context")
        if not isinstance(relevant, list) or not relevant or any(type(x) is not int for x in relevant):
            raise DataError("FinDVer relevant_context must contain integer evidence IDs.")
        block_map = {b["context_id"]: b["id"] for b in blocks}
        if set(relevant) - set(block_map):
            raise DataError("FinDVer evidence IDs are absent from the original full report.")
        cases.append(ClaimCase(
            case_id="findver:" + split + ":" + row["example_id"], group_id=name, subset=row["subset"], split=split,
            task_input={"statement": row["statement"], "source_url": source_url,
                        "source_blocks": blocks, "context_mode": "full_original_report_context"},
            reference={"entailed": label, "evidence_source_ids": [block_map[x] for x in dict.fromkeys(relevant)],
                       "explanation": row.get("explanation"), "evidence_granularity": "published_context_block"},
            provenance={"dataset": "FinDVer", "repository": FINDVER_URL, "revision": revision,
                        "annotations_sha256": data_hash, "report_sha256": report_hash,
                        "pinned_report_bytes_verified": expected_report_hashes is not None,
                        "report": name, "license": "upstream repository MIT; issuer report rights remain separate"}))
    if len({c.model_case_id for c in cases}) != len(cases):
        raise DataError("Duplicate FinDVer model inputs require explicit adjudication before scoring.")
    return cases


def retrieval_diagnostic(cases, *, limit=5):
    """No oracle evidence pool: rank every original paragraph/table in each report.

    Block recall only, no shared character budget and no implication of equal
    context cost with the small 600-character development corpus.
    """
    if type(limit) is not int or limit < 1 or not cases:
        raise DataError("Nonempty cases and positive retrieval limit required.")
    rows = []
    for case in cases:
        passages = [p for p, _ in bm25(case.task_input["source_blocks"], case.task_input["statement"])[:limit]]
        gold = set(case.reference["evidence_source_ids"])
        retrieved = {p["id"] for p in passages}
        first = next((i for i, p in enumerate(passages, 1) if p["id"] in gold), None)
        rows.append({"case_id": case.case_id, "subset": case.subset, "group_id": case.group_id,
                     "candidate_count": len(case.task_input["source_blocks"]),
                     "evidence_recall": len(gold & retrieved) / len(gold),
                     "all_evidence_retrieved": gold <= retrieved, "mrr": 1 / first if first else 0,
                     "retrieved_ids": [p["id"] for p in passages],
                     "retrieved_chars": sum(len(p["text"]) for p in passages)})
    summary = {}
    for subset in ["all"] + sorted({r["subset"] for r in rows}):
        group = [r for r in rows if subset == "all" or r["subset"] == subset]
        summary[subset] = {"count": len(group), "macro_evidence_recall": sum(r["evidence_recall"] for r in group) / len(group),
                           "all_evidence_fraction": sum(r["all_evidence_retrieved"] for r in group) / len(group),
                           "mrr": sum(r["mrr"] for r in group) / len(group)}
    return {"method": "BM25; all original report context blocks; no answer or evidence label in ranker input",
            "top_k": limit, "verification_accuracy": None,
            "notice": "Evidence retrieval diagnostic only, not claim verification. Whole blocks have variable size; not directly comparable to 600-character E3 runs.",
            "summary": summary, "cases": rows}


def score_claim_predictions(cases, predictions):
    """Strict binary-label scoring; omissions/abstentions are not excluded successes."""
    case_map = {c.model_case_id: c for c in cases}
    if not cases or len(case_map) != len(cases) or not isinstance(predictions, list):
        raise DataError("Scoring requires unique cases and a prediction list.")
    predicted = {}
    for row in predictions:
        if not isinstance(row, dict) or row.get("case_id") not in case_map or row["case_id"] in predicted:
            raise DataError("Predictions contain duplicate or unknown case IDs.")
        value = row.get("entailed")
        if value is not None and type(value) is not bool:
            raise DataError("Prediction entailed must be true, false, or null; strings are not coerced.")
        if row.get("input_sha256") != digest(case_map[row["case_id"]].task_input):
            raise DataError("Prediction must bind to the exact gold-free input hash.")
        predicted[row["case_id"]] = value
    rows = [{"case_id": c.model_case_id, "official_case_id": c.case_id, "subset": c.subset, "label": c.reference["entailed"],
             "prediction": predicted.get(c.model_case_id), "submitted": c.model_case_id in predicted,
             "correct": predicted.get(c.model_case_id) is c.reference["entailed"]} for c in cases]
    conf = Counter((r["label"], r["prediction"]) for r in rows)
    class_scores = {}
    for label in (True, False):
        tp = conf[(label, label)]
        fp = conf[(not label, label)]
        fn = sum(n for (gold, pred), n in conf.items() if gold is label and pred is not label)
        class_scores[str(label).lower()] = {"tp": tp, "fp": fp, "fn": fn,
                                          "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0}
    return {"count": len(rows), "submitted": len(predicted),
            "coverage": sum(r["prediction"] is not None for r in rows) / len(rows),
            "accuracy": sum(r["correct"] for r in rows) / len(rows),
            "macro_f1": sum(c["f1"] for c in class_scores.values()) / 2,
            "class_scores": class_scores, "cases": rows,
            "scope": "Published binary claim verification only; does not score explanation quality, omissions or revision effectiveness."}
