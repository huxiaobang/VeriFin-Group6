"""Independently audit saved ConvFinQA reranking; no model or network calls."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verifin.public_benchmarks import load_public_dataset

BASELINES = [f"{history}_{method}_top5" for history in ("current_question", "history_questions")
             for method in ("bm25", "dense", "hybrid")]
BASE = "history_questions_hybrid_top5"
RERANKED = "history_questions_reranked_top5"
CANDIDATES = "history_questions_hybrid_candidates20"
METRICS = {"macro_evidence_recall": "evidence_recall", "all_evidence_fraction": "all_evidence",
           "mrr": "mrr", "mean_context_chars": "context_chars"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def recompute(ids, case):
    blocks = {block["id"]: block for block in case.task_input["source_blocks"]}
    gold = set(case.reference["source_ids"])
    require(bool(gold) and not case.reference["unmapped_source_locators"], "Unmapped gold")
    require(len(set(ids)) == len(ids) and set(ids) <= blocks.keys(), "Invalid passage IDs")
    first = next((i for i, value in enumerate(ids, 1) if value in gold), None)
    return {"evidence_recall": len(gold.intersection(ids)) / len(gold),
            "all_evidence": gold.issubset(ids), "mrr": 1 / first if first else 0,
            "passage_ids": ids, "context_chars": sum(len(blocks[value]["text"]) for value in ids)}


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    return ordered[lower] + (position - lower) * (ordered[math.ceil(position)] - ordered[lower])


def bootstrap(rows, iterations, seed):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group_id"]].append(row)
    names = sorted(groups)
    metrics = ("evidence_recall", "all_evidence", "mrr")
    group_deltas = {name: {metric: sum(float(row["methods"][RERANKED][metric]) -
                                    float(row["methods"][BASE][metric]) for row in groups[name])
                           for metric in metrics} for name in names}
    rng = random.Random(seed)
    samples = {metric: [] for metric in metrics}
    for _ in range(iterations):
        selected = rng.choices(names, k=len(names))
        denominator = sum(len(groups[name]) for name in selected)
        for metric in metrics:
            samples[metric].append(sum(group_deltas[name][metric] for name in selected) / denominator)
    return {"unit": "source-page group; all turns retained per sampled group",
            "groups": len(names), "iterations": iterations, "seed": seed,
            "estimand": "turn-weighted paired mean: same-history reranker minus hybrid",
            "interval": "percentile 95%; group resampling with replacement",
            "development_diagnostic_only": True,
            "metrics": {metric: {"observed_difference": sum(group_deltas[name][metric] for name in names) / len(rows),
                                 "ci95": [percentile(samples[metric], .025), percentile(samples[metric], .975)]}
                        for metric in metrics}}


def examples(rows, cases):
    def delta(row, metric):
        return float(row["methods"][RERANKED][metric]) - float(row["methods"][BASE][metric])
    def record(row):
        case = cases[row["case_id"]]
        lookup = {b["id"]: b for b in case.task_input["source_blocks"]}
        before = row["methods"][BASE]["passage_ids"]
        after = row["methods"][RERANKED]["passage_ids"]
        gold = set(case.reference["source_ids"])
        def passages(ids):
            return [{"id": value, "locator": lookup[value]["locator"], "published_gold": value in gold,
                     "text_preview": lookup[value]["text"][:240]} for value in ids]
        return {"case_id": row["case_id"], "group_id": row["group_id"],
                "query": "\n".join(case.task_input["previous_user_questions"] + [case.task_input["question"]]),
                "gold_count": len(gold), "recall_change": delta(row, "evidence_recall"),
                "mrr_change": delta(row, "mrr"), "before": row["methods"][BASE],
                "after": row["methods"][RERANKED], "promoted": passages([v for v in after if v not in before]),
                "dropped": passages([v for v in before if v not in after])}
    improving = sorted((r for r in rows if delta(r, "evidence_recall") > 0),
                       key=lambda r: (-delta(r, "evidence_recall"), -delta(r, "mrr"), r["case_id"]))
    declining = sorted((r for r in rows if delta(r, "evidence_recall") < 0),
                       key=lambda r: (delta(r, "evidence_recall"), delta(r, "mrr"), r["case_id"]))
    return {"selection": "up to three largest positive/negative recall changes; MRR and ID tie break",
            "improved_count": len(improving), "declined_count": len(declining),
            "unchanged_recall_count": len(rows) - len(improving) - len(declining),
            "improvements": [record(row) for row in improving[:3]],
            "regressions": [record(row) for row in declining[:3]]}


def audit(result, canonical, data_path, iterations=4000, seed=20261106):
    source = result["source"]
    require(digest(data_path) == source["file_sha256"] == canonical["source"]["file_sha256"], "Source hash mismatch")
    require(result["selection"] == canonical["selection"], "Frozen selection differs")
    require(result["embedding_artifact_sha256"] == canonical["embedding_artifact_sha256"], "Embedding artifact differs")
    require(result["encoder"] == canonical["encoder"], "Encoder configuration differs")
    cases = load_public_dataset("convfinqa", data_path, split="dev", source_url=source["url"], revision=source["revision"])
    all_cases = {case.case_id: case for case in cases}
    old = {row["case_id"]: row for row in canonical["cases"]}
    rows = result["cases"]
    require(len(rows) == len(old) == 100 and len({r["case_id"] for r in rows}) == 100, "Expected 100 unique turns")
    require(set(old) == {r["case_id"] for r in rows} == set(result["selection"]["case_ids"]), "Case sets differ")
    require(len({r["group_id"] for r in rows}) == 18, "Expected 18 source-page groups")
    score_count = 0
    for row in rows:
        identity = row["case_id"]
        case, before = all_cases[identity], old[identity]
        require(row["group_id"] == case.group_id and row["turn_index"] == case.turn_index, "Case identity differs")
        require(row["candidate_count"] == len(case.task_input["source_blocks"]), "Original pool count differs")
        for method in BASELINES + [CANDIDATES]:
            require(row["methods"][method] == before["methods"][method], "Canonical baseline changed: " + identity + " / " + method)
        ids, scores = row["reranker_candidates"], row["reranker_scores"]
        require(ids == before["reranker_candidates"] == row["methods"][CANDIDATES]["passage_ids"], "Reranker candidate order differs")
        require(len(ids) == min(20, row["candidate_count"]) and len(set(ids)) == len(ids), "Candidate count/uniqueness mismatch")
        require(isinstance(scores, dict) and set(scores) == set(ids), "Score IDs differ")
        require(all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in scores.values()), "Invalid relevance probability")
        score_count += len(scores)
        expected = sorted(ids, key=lambda value: (-scores[value], value))[:5]
        require(row["methods"][RERANKED]["passage_ids"] == expected, "Score ranking or ID tie break differs")
        require(set(row["methods"]) == set(BASELINES + [CANDIDATES, RERANKED]), "Unexpected method set")
        for method, values in row["methods"].items():
            require(values == recompute(values["passage_ids"], case), "Metric/character count differs: " + identity + " / " + method)
    require(score_count == 1968, "Expected 1968 candidate scores")
    for group in ("all", "initial", "followup"):
        subset = [r for r in rows if group == "all" or (r["turn_index"] == 0) == (group == "initial")]
        require(result["summary"][group]["count"] == len(subset), "Summary denominator differs")
        for method in rows[0]["methods"]:
            for aggregate, metric in METRICS.items():
                value = statistics.mean(r["methods"][method][metric] for r in subset)
                require(math.isclose(value, result["summary"][group]["methods"][method][aggregate], abs_tol=1e-12), "Aggregate differs")
    gold_counts = [len(set(all_cases[row["case_id"]].reference["source_ids"])) for row in rows]
    return {"status": "PASS", "case_count": len(rows), "source_groups": 18, "candidate_scores": score_count,
            "top5_oracle_bounds": {"cases_with_more_than_5_gold_blocks": sum(n > 5 for n in gold_counts),
                                   "all_evidence_fraction_upper_bound": statistics.mean(n <= 5 for n in gold_counts),
                                   "macro_recall_upper_bound": statistics.mean(min(5, n) / n for n in gold_counts)},
            "checks": ["six baseline methods and candidate20 equal canonical per case", "all scores finite in [0,1]",
                       "same-history top20 preserved; score descending and ID tie break reproduced",
                       "all per-case recall/all-evidence/MRR/context characters recomputed from published source IDs",
                       "all/initial/followup summaries independently recomputed", "source/selection/embedding provenance matches"],
            "paired_bootstrap": bootstrap(rows, iterations, seed), "examples": examples(rows, all_cases),
            "limits": ["Development subset; correlated turns resampled by source-page group, not 100 independent reports.",
                       "Percentile intervals describe this frozen subset only; no held-out or end-to-end Agent claim.",
                       "Unannotated alternative evidence is not automatically irrelevant.",
                       "Saved scores are checked, not regenerated; elapsed inference times are not remeasured."],
            "model_calls": 0, "network_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Use a new output path")
    result = audit(json.loads(args.result.read_text()), json.loads(args.canonical.read_text()), args.data)
    result.update(created_at=datetime.now(timezone.utc).isoformat(), script_sha256=digest(Path(__file__)),
                  result_sha256=digest(args.result), canonical_sha256=digest(args.canonical), data_sha256=digest(args.data))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "case_count", "source_groups", "candidate_scores", "paired_bootstrap")}, indent=2))


if __name__ == "__main__":
    main()
