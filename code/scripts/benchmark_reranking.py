"""Fixed Qwen hybrid top-20 -> local cross-encoder -> top-5 development probe.

Uses existing real frozen embeddings, no API calls, no label or prompt tuning.
Run --download explicitly once; subsequent inference is entirely offline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
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

from verifin.reranking import MODEL_ID, REVISION, LocalQwenReranker, select_reranked
from verifin.retrieval import Corpus, FrozenEmbeddings, corpus_from_snapshots, evaluate, search


def metrics(passages, gold):
    hits = [any(p["document_id"] == g["document_id"] and p["start"] <= g["start"] and p["end"] >= g["end"]
                for p in passages) for g in gold]
    ranks = [i for i, p in enumerate(passages, 1) if any(
        p["document_id"] == g["document_id"] and p["start"] <= g["start"] and p["end"] >= g["end"] for g in gold)]
    return {"span_recall": sum(hits) / len(hits) if hits else None,
            "reciprocal_rank": 1 / min(ranks) if ranks else 0,
            "passage_ids": [p["id"] for p in passages], "context_chars": sum(len(p["text"]) for p in passages)}


def summarize(rows):
    result = {}
    for group in ["all_positive"] + sorted({r["group"] for r in rows}):
        selected = [r for r in rows if r["candidate_top20"]["span_recall"] is not None and
                    (group == "all_positive" or r["group"] == group)]
        if not selected:
            continue
        result[group] = {"count": len(selected), **{mode: {
            "macro_span_recall": statistics.mean(r[mode]["span_recall"] for r in selected),
            "mrr": statistics.mean(r[mode]["reciprocal_rank"] for r in selected)}
            for mode in ("candidate_top20", "hybrid_top5", "reranked_top5")}}
    return result


def run(args):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    corpus = Corpus(corpus_from_snapshots(ROOT / "data/replay").documents, max_chars=600, overlap=80)
    embeddings = FrozenEmbeddings(args.embeddings, corpus)
    cases = json.loads(args.queries.read_text())
    # Validate the source spans before loading weights. This does not establish
    # independent human annotation or tune any retrieval parameter.
    baseline = evaluate(corpus, cases, modes=("hybrid",), embeddings=embeddings,
                        limit=5, max_context_chars=3000, repeats=1)
    if baseline["methods"]["hybrid"]["status"] != "evaluated":
        raise ValueError(baseline["methods"]["hybrid"])
    print(json.dumps({"phase": "loading", "model_id": MODEL_ID}), flush=True)
    reranker = LocalQwenReranker(args.model_path, batch_size=args.batch_size)
    reranker.score("What is the capital of China?", ["The capital of China is Beijing."])
    rows, times, token_counts = [], [], []
    for case in cases:
        candidate = search(corpus, case["query"], case.get("filters"), mode="hybrid", limit=20,
                           embeddings=embeddings, max_context_chars=12000, candidate_limit=100)
        passages = candidate["passages"]
        before = search(corpus, case["query"], case.get("filters"), mode="hybrid", limit=5,
                        embeddings=embeddings, max_context_chars=3000, candidate_limit=100)["passages"]
        started = time.perf_counter()
        scores = reranker.score(case["query"], [p["text"] for p in passages])
        elapsed = time.perf_counter() - started
        after = select_reranked(passages, scores, limit=5, max_context_chars=3000)
        if passages:
            times.append(elapsed)
        token_counts.extend(reranker.last_token_counts)
        rows.append({"id": case["id"], "query": case["query"], "filters": case.get("filters", {}),
                     "group": case["diagnostic_group"], "eligible_chunks": candidate["eligible_chunks"],
                     "candidate_top20": metrics(passages, case["relevant"]),
                     "hybrid_top5": metrics(before, case["relevant"]),
                     "reranked_top5": metrics(after, case["relevant"]),
                     "scores": dict(zip([p["id"] for p in passages], scores)), "reranking_seconds": elapsed})
        print(json.dumps({"phase": "scored", "id": case["id"], "candidates": len(passages),
                          "seconds": round(elapsed, 3), "before": rows[-1]["hybrid_top5"]["span_recall"],
                          "after": rows[-1]["reranked_top5"]["span_recall"]}), flush=True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {"protocol": "verifin-reranking-development-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "independent_gold": False, "annotation_provenance_verified": False,
              "notice": "Development spans: 30 positive variants of only 10 underlying questions; not held-out E3 or end-to-end agent accuracy.",
              "negative_notice": "Metadata-empty controls and semantic-no-answer diagnostic are separate; reranker has no calibrated rejection threshold.",
              "model": reranker.metadata, "corpus_fingerprint": corpus.fingerprint,
              "embedding_sha256": hashlib.sha256(args.embeddings.read_bytes()).hexdigest(),
              "queries_sha256": hashlib.sha256(args.queries.read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "reranking_module_sha256": hashlib.sha256((ROOT / "verifin/reranking.py").read_bytes()).hexdigest(),
              "protocol_controls": {"candidate_generator": "Qwen3-Embedding-0.6B dense + BM25 equal RRF k60",
                                    "candidate_limit": 20, "rrf_component_limit": 100, "candidate_char_budget": 12000,
                                    "final_limit": 5, "final_char_budget": 3000, "chunk_chars": 600, "overlap": 80,
                                    "instruction": "official default; fixed before running; no label tuning"},
              "summary": summarize(rows), "cases": rows,
              "resources": {"cpu_threads": args.threads, "interop_threads": 1,
                            "runtime": {p: importlib.metadata.version(p) for p in ("torch", "transformers", "safetensors")},
                            "model_load_seconds": reranker.load_seconds,
                            "reranking_seconds_total": sum(times),
                            "reranking_seconds_median": statistics.median(times),
                            "reranking_seconds_p95": sorted(times)[math.ceil(.95 * len(times)) - 1],
                            "pairs_scored": len(token_counts), "max_pair_tokens": max(token_counts),
                            "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
                            "latency_scope": "CPU reranking/tokenization only; excludes retrieval, original embedding, loading and one warmup; one timed pass"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"phase": "complete", "summary": report["summary"], "resources": report["resources"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--embeddings", type=Path, default=ROOT / "data/evaluation/embedding_selection_results/qwen3-0.6b-chars600-overlap80.embeddings.json")
    parser.add_argument("--queries", type=Path, default=ROOT / "data/evaluation/embedding_selection_development_queries.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/open_source_results/qwen3-reranker-0.6b.results.json")
    args = parser.parse_args()
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    if args.download:
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL_ID, revision=REVISION, local_dir=args.model_path, token=False, max_workers=3,
                          allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
                                          "merges.txt", "chat_template.jinja", "README.md", "model.safetensors"])
    else:
        run(args)


if __name__ == "__main__":
    main()
