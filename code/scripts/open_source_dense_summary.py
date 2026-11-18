"""Verify and compare the three frozen ConvFinQA dense retrieval experiments.

No model is loaded. Paired intervals resample complete source-page groups and
remain exploratory diagnostics on a public development subset.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,default=ROOT/"data/evaluation/open_source_dense_public/comparison.json")
    args=p.parse_args()
    if args.output.exists():
        p.error("Use a fresh output path")
    import numpy as np
    parent=ROOT/"data/evaluation/open_source_dense_public"
    paths={"e5-small":parent/"convfinqa-e5-small-results.json", "bge-m3":parent/"convfinqa-bge-m3-results.json",
           "qwen3-0.6b":ROOT/"data/evaluation/open_source_results/convfinqa-qwen0.6-canonical-baselines.json"}
    reports={key:json.loads(path.read_text()) for key,path in paths.items()}
    base=reports["qwen3-0.6b"]
    base_rows={r["case_id"]:r for r in base["cases"]}
    artifacts={}
    for key,report in reports.items():
        assert report["selection"]==base["selection"] and report["source"]==base["source"]
        assert "newline join" in report["controls"]["query_serialization"] and "no enumeration" in report["controls"]["query_serialization"]
        rows={r["case_id"]:r for r in report["cases"]}
        assert set(rows)==set(base_rows)
        for case_id,row in rows.items():
            assert (row["group_id"],row["candidate_count"])==(base_rows[case_id]["group_id"],base_rows[case_id]["candidate_count"])
            for prefix in ("current_question","history_questions"):
                assert row["methods"][prefix+"_bm25_top5"]==base_rows[case_id]["methods"][prefix+"_bm25_top5"]
        if key!="qwen3-0.6b":
            artifact_path=parent/f"convfinqa-{key}-vectors.json"
            assert sha256(artifact_path)==report["embedding_artifact_sha256"]
            artifact=json.loads(artifact_path.read_text())
            assert artifact["model"]==report["encoder"] and len(artifact["chunks"])==550 and len(artifact["queries"])==162
            for vectors in (artifact["chunks"],artifact["queries"]):
                matrix=np.asarray(list(vectors.values()),dtype=np.float64)
                assert np.isfinite(matrix).all() and np.allclose(np.linalg.norm(matrix,axis=1),1,atol=1e-5)
            artifacts[key]=artifact
    assert artifacts["e5-small"]["text_sha256"]==artifacts["bge-m3"]["text_sha256"]
    assert set(artifacts["e5-small"]["queries"])==set(artifacts["bge-m3"]["queries"])
    groups=list(dict.fromkeys(r["group_id"] for r in base["cases"]))
    counts=np.asarray([sum(r["group_id"]==g for r in base["cases"]) for g in groups])
    rng=np.random.default_rng(20261106)
    samples=rng.integers(0,len(groups),size=(5000,len(groups)))
    paired={}
    for key in ("e5-small","bge-m3"):
        rows={r["case_id"]:r for r in reports[key]["cases"]}
        paired[key]={}
        for method in ("history_questions_dense_top5","history_questions_hybrid_top5"):
            paired[key][method]={}
            for metric in ("evidence_recall","all_evidence","mrr"):
                differences={case_id:float(rows[case_id]["methods"][method][metric])-float(b["methods"][method][metric]) for case_id,b in base_rows.items()}
                sums=np.asarray([sum(differences[r["case_id"]] for r in base["cases"] if r["group_id"]==g) for g in groups])
                boot=sums[samples].sum(axis=1)/counts[samples].sum(axis=1)
                values=list(differences.values())
                paired[key][method][metric]={"delta_vs_qwen0_6b":float(np.mean(values)),
                                             "source_page_bootstrap_95pct_interval":np.quantile(boot,[.025,.975]).tolist(),
                                             "turns_improved":sum(v>0 for v in values),"turns_worsened":sum(v<0 for v in values),
                                             "turns_tied":sum(v==0 for v in values)}
    report={"created_at":datetime.now(timezone.utc).isoformat(),"cases":100,"source_page_groups":len(groups),
            "query_serialization":base["controls"]["query_serialization"],
            "files":{key:{"path":str(path.relative_to(ROOT)),"sha256":sha256(path)} for key,path in paths.items()},
            "metrics":{key:r["summary"] for key,r in reports.items()},
            "resources":{key:{"original_passage_encoding_seconds":r["resources"]["original_passage_encoding_seconds"],
                              "query_encoding_seconds":r["resources"]["query_encoding_seconds"],
                              "encoder_peak_rss_bytes":r["resources"]["encoder_peak_rss_bytes"],
                              "passage_operation":r["resources"]["passage_operation"]} for key,r in reports.items()},
            "paired_diagnostics":paired,"bootstrap_replicates":5000,"bootstrap_seed":20261106,
            "checks":{"same_official_source_and_selection":True,"same_query_serialization":True,
                      "same_case_ids_and_candidate_counts":True,"bm25_per_case_outputs_identical":True,
                      "new_dense_artifacts_hashes_counts_finiteness_norms_valid":True,"new_dense_exact_source_texts_and_query_sets_match":True},
            "limits":["Exploratory public-dev results; no unseen-data or training-contamination guarantee.",
                      "Resampling source-page groups, not treating 100 correlated turns as independent.",
                      "Qwen passage time is its original encoding; current canonical query run reused verified passage vectors.",
                      "Memory values are from actual encoder processes, never the model-free rescore process."],
            "script_sha256":sha256(Path(__file__))}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"checks":report["checks"],"paired":paired,"resources":report["resources"]}))


if __name__=="__main__":
    main()
