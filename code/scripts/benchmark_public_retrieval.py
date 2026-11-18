"""Real Qwen retrieval/reranking on a pre-frozen public ConvFinQA subset.

Candidate pools contain all original supplied context rows/sentences. The task
already gives the report excerpt; no report discovery or full agent is measured.
Encode and rerank are separate processes so models do not share peak memory.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verifin.public_benchmarks import load_public_dataset
from verifin.retrieval import bm25
from verifin.runtime_embeddings import LocalSentenceEncoder
from verifin.reranking import LocalQwenReranker

REVISION = "cf3eed2d5984960bf06bb8145bcea5e80b0222a6"
EMBED_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
QUERY_PREFIX = "Instruct: Given a financial research question, retrieve relevant passages from company financial reports.\nQuery: "


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def question(case, history):
    previous = case.task_input["previous_user_questions"]
    if history and previous:
        return "\n".join(previous + [case.task_input["question"]])
    return case.task_input["question"]


def load(args):
    manifest = json.loads(args.selection.read_text())
    cases = load_public_dataset("convfinqa", args.data, split="dev",
                                source_url=f"https://github.com/czyssrs/ConvFinQA/blob/{REVISION}/data.zip", revision=REVISION)
    expected = set(manifest["case_ids"])
    cases = [c for c in cases if c.case_id in expected or c.original_id in expected]
    if len(cases) != len(expected):
        raise ValueError("Frozen selection does not match the official source IDs")
    if hashlib.sha256(args.data.read_bytes()).hexdigest() != "8a2461be571a3277c443e2dce6e3cce6f4df197db5b7d0b60eed3477f4c952b9":
        raise ValueError("ConvFinQA source hash changed")
    return manifest, cases


def measure(ranked, gold, limit):
    top = ranked[:limit]
    retrieved = {p["id"] for p in top}
    gold = set(gold)
    first = next((i for i, p in enumerate(top, 1) if p["id"] in gold), None)
    return {"evidence_recall": len(gold & retrieved) / len(gold), "all_evidence": gold <= retrieved,
            "mrr": 1 / first if first else 0, "passage_ids": [p["id"] for p in top],
            "context_chars": sum(len(p["text"]) for p in top)}


def aggregate(rows):
    result = {}
    for group in ("all", "initial", "followup"):
        selected = [r for r in rows if group == "all" or (r["turn_index"] == 0) == (group == "initial")]
        if selected:
            result[group] = {"count": len(selected), "methods": {method: {
                "macro_evidence_recall": statistics.mean(r["methods"][method]["evidence_recall"] for r in selected),
                "all_evidence_fraction": statistics.mean(r["methods"][method]["all_evidence"] for r in selected),
                "mrr": statistics.mean(r["methods"][method]["mrr"] for r in selected),
                "mean_context_chars": statistics.mean(r["methods"][method]["context_chars"] for r in selected)}
                for method in selected[0]["methods"]}}
    return result


def resource_info():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024}


def encode(args, manifest, cases):
    blocks = {b["id"]: b for case in cases for b in case.task_input["source_blocks"]}
    texts = {identity: b["text"] for identity, b in blocks.items()}
    queries = list(dict.fromkeys(question(c, h) for c in cases for h in (False, True)))
    encoder = LocalSentenceEncoder(args.model_path, model_id="Qwen/Qwen3-Embedding-0.6B", revision=EMBED_REVISION,
                                   query_prefix=QUERY_PREFIX, batch_size=args.batch_size)
    encoder.encode([queries[0]], "query")
    started = time.perf_counter()
    if args.reuse_passages:
        previous = json.loads(args.reuse_passages.read_text())
        if previous["model"] != encoder.metadata or previous["text_sha256"] != {k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()}:
            raise ValueError("Passage cache differs in model identity, configuration or exact source text")
        vectors = [previous["chunks"][key] for key in texts]
    else:
        vectors = encoder.encode(list(texts.values()), "passage")
    passage_seconds = time.perf_counter() - started
    print(json.dumps({"phase": "passages_encoded", "count": len(texts), "seconds": passage_seconds}), flush=True)
    started = time.perf_counter()
    qvectors = encoder.encode(queries, "query")
    query_seconds = time.perf_counter() - started
    artifact = {"protocol": "public-convfinqa-retrieval-v2", "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
                "model": encoder.metadata, "chunks": dict(zip(texts, vectors)), "queries": dict(zip(queries, qvectors)),
                "text_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()},
                "passage_seconds": passage_seconds, "query_seconds": query_seconds,
                "passage_operation": "verified cache load" if args.reuse_passages else "fresh encoding",
                "original_passage_encoding_seconds": previous["passage_seconds"] if args.reuse_passages else passage_seconds,
                "unique_queries": len(queries), "unique_source_blocks": len(texts), **resource_info()}
    write(args.artifact, artifact)
    evaluate(args, manifest, cases, artifact, reranker=None)


def evaluate(args, manifest, cases, artifact, reranker=None):
    import numpy as np
    if artifact["selection_sha256"] != hashlib.sha256(args.selection.read_bytes()).hexdigest():
        raise ValueError("Embedding artifact selection changed")
    rows, rerank_times = [], []
    for case in cases:
        blocks = case.task_input["source_blocks"]
        for b in blocks:
            if artifact["text_sha256"].get(b["id"]) != hashlib.sha256(b["text"].encode()).hexdigest():
                raise ValueError("Source block differs from encoded input")
        mat = np.asarray([artifact["chunks"][b["id"]] for b in blocks], dtype=np.float64)
        methods, candidate_ids, saved_scores = {}, {}, None
        for history in (False, True):
            prefix = "history_questions" if history else "current_question"
            query = question(case, history)
            qvector = np.asarray(artifact["queries"][query], dtype=np.float64)
            dense_scores = mat @ qvector
            dense = sorted(zip(blocks, dense_scores.tolist()), key=lambda x: (-x[1], x[0]["id"]))
            lexical = bm25(blocks, query)
            fused = {}
            lookup = {b["id"]: b for b in blocks}
            for ranks in (dense[:100], lexical[:100]):
                for i, (b, _) in enumerate(ranks, 1):
                    fused[b["id"]] = fused.get(b["id"], 0) + 1 / (60 + i)
            hybrid = [lookup[key] for key in sorted(fused, key=lambda key: (-fused[key], key))]
            gold = case.reference["source_ids"]
            if not gold or case.reference["unmapped_source_locators"]:
                raise ValueError("Public reference has an unresolved evidence locator")
            for name, ranked in (("bm25", [b for b, _ in lexical]), ("dense", [b for b, _ in dense]), ("hybrid", hybrid)):
                methods[prefix + "_" + name + "_top5"] = measure(ranked, gold, 5)
            if history:
                candidates = hybrid[:20]
                candidate_ids = [b["id"] for b in candidates]
                methods["history_questions_hybrid_candidates20"] = measure(candidates, gold, 20)
                if reranker is not None:
                    started = time.perf_counter()
                    scores = reranker.score(query, [b["text"] for b in candidates])
                    elapsed = time.perf_counter() - started
                    rerank_times.append(elapsed)
                    ranked = [b for b, _ in sorted(zip(candidates, scores), key=lambda x: (-x[1], x[0]["id"]))]
                    methods["history_questions_reranked_top5"] = measure(ranked, gold, 5)
                    saved_scores = dict(zip(candidate_ids, scores))
                    print(json.dumps({"phase": "reranked", "case": case.case_id, "seconds": round(elapsed, 3)}), flush=True)
        rows.append({"case_id": case.case_id, "group_id": case.group_id, "dialogue_id": case.dialogue_id,
                     "turn_index": case.turn_index, "candidate_count": len(blocks), "methods": methods,
                     "reranker_candidates": candidate_ids, "reranker_scores": saved_scores})
    report = {"protocol": "public-convfinqa-retrieval-v2", "created_at": datetime.now(timezone.utc).isoformat(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "adapter_sha256": hashlib.sha256((ROOT / "verifin/public_benchmarks.py").read_bytes()).hexdigest(),
              "selection": manifest, "source": cases[0].source, "encoder": artifact["model"],
              "embedding_artifact_sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest(),
              "reranker": reranker.metadata if reranker else None, "summary": aggregate(rows), "cases": rows,
              "resources": {"passage_encoding_seconds": artifact["passage_seconds"],
                            "passage_operation": artifact.get("passage_operation", "fresh encoding"),
                            "original_passage_encoding_seconds": artifact.get("original_passage_encoding_seconds", artifact["passage_seconds"]),
                            "query_encoding_seconds": artifact["query_seconds"],
                            "unique_source_blocks": artifact["unique_source_blocks"],
                            "unique_queries": artifact["unique_queries"],
                            "encoder_peak_rss_bytes": artifact["peak_rss_bytes"],
                            "rerank_total_seconds": sum(rerank_times),
                            "rerank_latency_scope": "warm loaded scorer: tokenization and forward pass only; excludes weight verification/loading, warm-up, candidate retrieval and final sorting",
                            "rerank_median_seconds": statistics.median(rerank_times) if rerank_times else None,
                            "rerank_p95_seconds": sorted(rerank_times)[math.ceil(.95 * len(rerank_times)) - 1] if rerank_times else None,
                            "cpu_threads": args.threads, "compute": "CPU float32 model; float64 deterministic dot ranking", **resource_info()},
              "controls": {"candidate_pool": "all rows/sentences in provided original report excerpt; no gold filtering",
                           "history": "previous user questions only, no future questions or reference answers",
                           "query_serialization": "newline join of previous questions + current; no enumeration, labels or added numbers",
                           "mrr_cutoff": "5 for top5 methods, 20 for candidate20; no unbounded MRR",
                           "top_k": 5, "candidate_top_k": 20, "context_char_budget": None,
                           "rrf_k": 60, "rrf_component_limit": 100, "no_silent_token_truncation": True},
              "limits": ["Public dev evidence retrieval; no claim of full-dialogue or end-to-end agent accuracy.",
                         "Report excerpt is supplied; source discovery is not evaluated.",
                         "Source rows/sentences vary in length; report context characters; not comparable directly to fixed-character E3.",
                         "100 related turns from 18 source-page groups, not 100 independent reports.",
                         "History questions do not replace the system's own prior answers for later reasoning."]}
    report["resources"]["peak_rss_scope"] = args.stage + " process only; encoder_peak_rss_bytes separately records the actual model encoding process"
    write(args.output, report)
    print(json.dumps({"phase": "complete", "summary": report["summary"], "resources": report["resources"]}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("encode", "rerank", "rescore"))
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--selection", type=Path, required=True)
    p.add_argument("--model-path", type=Path)
    p.add_argument("--artifact", type=Path, required=True)
    p.add_argument("--reuse-passages", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=8)
    args = p.parse_args()
    if not 1 <= args.threads <= 32 or not 1 <= args.batch_size <= 64:
        p.error("invalid CPU/batch limits")
    if len({p.resolve() for p in (args.data, args.selection, args.artifact, args.output)}) != 4:
        p.error("data, selection, artifact and output must be distinct paths")
    if args.output.exists() or (args.stage == "encode" and args.artifact.exists()):
        p.error("Use fresh output/artifact paths; existing experiment files are not overwritten")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    if args.stage != "rescore":
        import torch
        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
    manifest, cases = load(args)
    if args.stage == "encode":
        if args.model_path is None:
            p.error("--model-path required for encoding")
        encode(args, manifest, cases)
    else:
        scorer = LocalQwenReranker(args.model_path, batch_size=args.batch_size) if args.stage == "rerank" else None
        if scorer:
            scorer.score("What is the capital of China?", ["The capital of China is Beijing."])
        evaluate(args, manifest, cases, json.loads(args.artifact.read_text()), scorer)


if __name__ == "__main__":
    main()
