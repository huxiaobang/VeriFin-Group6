"""Independently re-score exported PPLX token vectors without loading its model.

Checks corpus identity, all top-5 IDs and metric values with NumPy Sum-MaxSim.
An optional paired comparison uses source-page cluster bootstrap, not 100
independent turns. This remains a diagnostic on a public development subset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh output path")
    import numpy as np
    from scripts.benchmark_public_retrieval import load, question, measure, aggregate
    from verifin.retrieval import bm25
    result = json.loads(args.result.read_text())
    _, cases = load(args)
    if digest(args.selection) != result["selection_sha256"]:
        raise ValueError("Frozen selection changed")
    artifact = args.result.parent/result["artifact_file"]
    if digest(artifact) != result["artifact_sha256"]:
        raise ValueError("Exported vectors changed")
    passage_ids = result["artifact_mapping"]["passages"]
    query_texts = result["artifact_mapping"]["query_texts"]
    with np.load(artifact, allow_pickle=False) as saved:
        expected = {f"passage_{i:04d}" for i in range(len(passage_ids))} | {f"query_{i:04d}" for i in range(len(query_texts))}
        if set(saved.files) != expected:
            raise ValueError("Vector keys do not match manifest")
        passages = {identity:saved[f"passage_{i:04d}"] for i,identity in enumerate(passage_ids)}
        queries = {text:saved[f"query_{i:04d}"] for i,text in enumerate(query_texts)}
    original = {r["case_id"]:r for r in result["cases"]}
    if set(original) != {c.case_id for c in cases}:
        raise ValueError("Saved cases differ from official frozen source")
    rows = []
    for case in cases:
        blocks = case.task_input["source_blocks"]
        for b in blocks:
            if hashlib.sha256(b["text"].encode()).hexdigest() != result["source_text_sha256"].get(b["id"]):
                raise ValueError("Source text differs from embedding input")
        methods, lookup = {}, {b["id"]:b for b in blocks}
        for history in (False,True):
            prefix = "history_questions" if history else "current_question"
            text = question(case,history)
            query = queries[text]
            # Deliberately independent of ST's padded/chunked torch implementation.
            scores = [(b,float((query @ passages[b["id"]].T).max(axis=1).sum())) for b in blocks]
            late = sorted(scores,key=lambda item:(-item[1],item[0]["id"]))
            lexical = bm25(blocks,text)
            fused = {}
            for ranking in (late[:100],lexical[:100]):
                for i,(b,_) in enumerate(ranking,1):
                    fused[b["id"]]=fused.get(b["id"],0)+1/(60+i)
            hybrid = [lookup[i] for i in sorted(fused,key=lambda i:(-fused[i],i))]
            methods[prefix+"_maxsim_top5"]=measure([b for b,_ in late],case.reference["source_ids"],5)
            methods[prefix+"_bm25_maxsim_rrf_top5"]=measure(hybrid,case.reference["source_ids"],5)
        if methods != original[case.case_id]["methods"]:
            raise ValueError(f"Independent rescoring differs for {case.case_id}")
        rows.append({"case_id":case.case_id,"group_id":case.group_id,"turn_index":case.turn_index,"methods":methods})
    recomputed = aggregate(rows)
    if recomputed != result["summary"]:
        raise ValueError("Independent metric aggregation differs")
    report = {"protocol":"pplx-independent-rescore-v1","result_sha256":digest(args.result),
              "artifact_sha256":digest(artifact),"selection_sha256":digest(args.selection),
              "cases_verified":len(rows),"top5_rankings_verified":sum(len(r["methods"]) for r in rows),
              "model_loaded":False,"network_used":False,"all_top5_ids_and_metrics_exactly_match":True,
              "script_sha256":digest(Path(__file__))}
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        serialization = baseline.get("controls",{}).get("query_serialization","")
        if "newline join" not in serialization or "no enumeration" not in serialization:
            raise ValueError("Baseline must explicitly use the current pure-newline query serialization")
        base_rows = {r["case_id"]:r for r in baseline["cases"]}
        if set(base_rows) != set(original) or baseline["selection"] != result["selection"]:
            raise ValueError("Baseline uses a different frozen selection")
        groups = list(dict.fromkeys(r["group_id"] for r in rows))
        rng = np.random.default_rng(20261106)
        samples = rng.integers(0,len(groups),size=(5000,len(groups)))
        comparisons = {}
        for ours, other in (("history_questions_maxsim_top5","history_questions_dense_top5"),
                            ("history_questions_bm25_maxsim_rrf_top5","history_questions_hybrid_top5")):
            counts = np.asarray([sum(r["group_id"]==g for r in rows) for g in groups])
            metrics = {}
            for metric in ("evidence_recall","all_evidence","mrr"):
                differences = {r["case_id"]:float(r["methods"][ours][metric])-float(base_rows[r["case_id"]]["methods"][other][metric]) for r in rows}
                sums = np.asarray([sum(differences[r["case_id"]] for r in rows if r["group_id"]==g) for g in groups])
                bootstrap = sums[samples].sum(axis=1)/counts[samples].sum(axis=1)
                values = list(differences.values())
                metrics[metric] = {"mean_paired_delta":float(np.mean(values)),
                                   "cluster_bootstrap_95pct_interval":np.quantile(bootstrap,[.025,.975]).tolist(),
                                   "turns_improved":sum(v>0 for v in values),"turns_worsened":sum(v<0 for v in values),
                                   "turns_tied":sum(v==0 for v in values)}
            comparisons[ours+"_minus_"+other] = metrics
        report["paired_comparison"]={"baseline_sha256":digest(args.baseline),"source_page_groups":len(groups),
                                     "bootstrap_replicates":5000,"seed":20261106,"comparisons":comparisons,
                                     "notice":"Resampling whole source-page groups; public dev diagnostic, not held-out proof or a training-contamination audit."}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps(report,ensure_ascii=False))


if __name__ == "__main__":
    main()
