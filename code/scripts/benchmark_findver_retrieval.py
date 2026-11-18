"""Offline full-report FinDVer BM25/dense/RRF evidence-block diagnostic.

No labels select or encode candidates. Original IDs and gold stay scorer-side.
Length-bucketed CPU FP32 encoding persists actual vectors and refuses truncation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import socket
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verifin.claim_benchmarks import digest, load_findver
from verifin.retrieval import bm25
from verifin.runtime_embeddings import LocalSentenceEncoder

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
QUERY_PREFIX = "Instruct: Given a financial research question, retrieve relevant passages from company financial reports.\nQuery: "


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def metrics(order, blocks, gold_ids, k):
    selected = order[:k]
    found = set(selected) & gold_ids
    first = next((i for i, identity in enumerate(selected, 1) if identity in gold_ids), None)
    return {"evidence_block_recall": len(found) / len(gold_ids),
            "all_evidence": gold_ids <= set(selected), "mrr": 1 / first if first else 0,
            "context_characters": sum(len(blocks[i]["text"]) for i in selected),
            "returned_blocks": len(selected), "passage_ids": selected}


def summarize(rows):
    result = {}
    for subset in ["all"] + sorted({r["subset"] for r in rows}):
        group = [r for r in rows if subset == "all" or r["subset"] == subset]
        out = {"count": len(group), "methods": {}, "theoretical": {}}
        for k in (5, 10):
            out["theoretical"][str(k)] = {
                "all_evidence_upper_bound": statistics.mean(r["gold_count"] <= k for r in group),
                "macro_recall_upper_bound": statistics.mean(min(k, r["gold_count"]) / r["gold_count"] for r in group),
                "claims_with_more_gold_than_k": sum(r["gold_count"] > k for r in group)}
        for mode in ("bm25", "dense", "hybrid"):
            out["methods"][mode] = {}
            for k in (5, 10):
                values = [r["methods"][mode][str(k)] for r in group]
                out["methods"][mode][str(k)] = {
                    "macro_evidence_block_recall": statistics.mean(v["evidence_block_recall"] for v in values),
                    "all_evidence_fraction": statistics.mean(v["all_evidence"] for v in values),
                    "mrr": statistics.mean(v["mrr"] for v in values),
                    "context_characters_mean": statistics.mean(v["context_characters"] for v in values),
                    "context_characters_median": statistics.median(v["context_characters"] for v in values),
                    "context_characters_max": max(v["context_characters"] for v in values)}
        result[subset] = out
    return result


def run(args):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    import numpy as np
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    selection = json.loads(args.selection.read_text())
    cases = load_findver(args.data_dir / "data/testmini.json", args.data_dir / "financial_reports",
                         split="testmini", selected_ids=selection["case_ids"])
    cases.sort(key=lambda c: c.model_case_id)
    blocks = {b["id"]: b for case in cases for b in case.task_input["source_blocks"]}
    ids = sorted(blocks)
    input_hash = digest({"blocks": [{"id": i, "text": blocks[i]["text"]} for i in ids],
                         "queries": [c.input_record() for c in cases]})
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    encoder = LocalSentenceEncoder(args.model_path, model_id=MODEL_ID, revision=REVISION,
                                    query_prefix=QUERY_PREFIX, passage_prefix="", batch_size=8)
    lengths = [len(v) for v in encoder.tokenizer([blocks[i]["text"] for i in ids],
                                                truncation=False, add_special_tokens=True)["input_ids"]]
    if max(lengths) > encoder.max_length:
        raise ValueError("An original block exceeds model context; no truncation permitted")
    identity = digest({"input_hash": input_hash, "encoder": encoder.metadata})
    cache_meta = args.cache_dir / "findver-vector-manifest.json"
    if cache_meta.exists():
        progress = json.loads(cache_meta.read_text())
        if progress["identity"] != identity:
            raise ValueError("Existing vector cache belongs to a different input/model protocol")
        matrix = np.load(args.cache_dir / "findver-block-vectors.npy", mmap_mode="r+")
    else:
        dimension = int(encoder.model.get_sentence_embedding_dimension())
        matrix = np.lib.format.open_memmap(args.cache_dir / "findver-block-vectors.npy", mode="w+",
                                          dtype="float32", shape=(len(ids), dimension))
        progress = {"identity": identity, "input_hash": input_hash, "block_ids": ids,
                    "completed": [], "batches": [], "dimension": dimension, "encoder": encoder.metadata,
                    "source_hashes": {c.group_id: c.provenance["report_sha256"] for c in cases}}
        write_json(cache_meta, progress)
    completed = set(progress["completed"])
    order = sorted((i for i in range(len(ids)) if i not in completed), key=lambda i: (lengths[i], ids[i]))
    deadline = datetime.fromisoformat(args.deadline_utc).timestamp() if args.deadline_utc else float("inf")
    print(json.dumps({"phase": "lengths", "blocks": len(ids), "total_tokens": sum(lengths),
                      "max_tokens": max(lengths), "completed_from_cache": len(completed)}, ensure_ascii=False), flush=True)
    at = 0
    while at < len(order) and time.time() < deadline:
        max_tokens = lengths[order[at]]
        batch_size = 8 if max_tokens <= 512 else 4 if max_tokens <= 1024 else 2 if max_tokens <= 2048 else 1
        indexes = order[at:at + batch_size]
        encoder.batch_size = batch_size
        tick = time.perf_counter()
        vectors = encoder.encode([blocks[ids[i]]["text"] for i in indexes], "passage")
        matrix[indexes] = np.asarray(vectors, dtype="float32")
        matrix.flush()
        elapsed = time.perf_counter() - tick
        completed.update(indexes)
        progress["completed"] = sorted(completed)
        progress["batches"].append({"count": len(indexes), "tokens": [lengths[i] for i in indexes],
                                    "seconds": elapsed, "index_start_in_bucket_order": at})
        write_json(cache_meta, progress)
        at += len(indexes)
        if len(progress["batches"]) % 10 == 0 or at == len(order):
            print(json.dumps({"phase": "encoding", "complete": len(completed), "total": len(ids),
                              "max_batch_tokens": max(lengths[i] for i in indexes),
                              "batch_seconds": round(elapsed, 3),
                              "elapsed_seconds": round(time.perf_counter() - started, 1)}), flush=True)
    common = {"protocol": "findver-full-report-retrieval-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "published_gold": True, "scope": "fixed public testmini development subset; evidence-block retrieval only",
              "no_verification_accuracy_claim": True, "selection": selection, "input_hash": input_hash,
              "model": encoder.metadata, "blocks": len(ids), "claims": len(cases),
              "candidate_policy": "Each claim searches ALL blocks of its dataset-assigned full report; all selected reports are encoded; no gold candidate filtering",
              "ranking_protocol": {"dense": "normalized cosine exact scan", "hybrid": "equal-weight RRF k=60 over full positive BM25 list and full dense list; no candidate cap", "k": [5, 10], "context_budget": None},
              "encoding": {"tokens_total": sum(lengths), "tokens_median": statistics.median(lengths),
                           "tokens_max": max(lengths), "max_sequence_length": encoder.max_length,
                           "truncation": False, "bucket_order": "ascending token length, stable ID",
                           "batch_policy": "8 up to 512 tokens; 4 up to 1024; 2 up to 2048; 1 above 2048; threshold uses first length in contiguous sorted batch",
                           "passage_seconds": sum(b["seconds"] for b in progress["batches"]),
                           "completed_blocks": len(completed), "model_load_seconds": encoder.load_seconds},
              "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "threads": 4,
                              "dtype": "float32", "device": "cpu", "libraries": {n: importlib.metadata.version(n) for n in ["torch", "transformers", "sentence-transformers", "numpy"]}},
              "cache": {"manifest": str(cache_meta.resolve()), "vectors": str((args.cache_dir / "findver-block-vectors.npy").resolve())},
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limits": ["Known source report; discovery is not measured", "Whole blocks vary in size; no common character budget", "Testmini was used for development; not untouched test performance", "No generation, verification verdict, or end-to-end agent score"]}
    if len(completed) != len(ids):
        common.update(status="partial_encoding_no_dense_or_hybrid_metrics", summary=None)
        write_json(args.output, common)
        print(json.dumps({"phase": "partial", "completed": len(completed), "output": str(args.output)}), flush=True)
        return
    encoder.batch_size = 8
    tick = time.perf_counter()
    query_vectors = np.asarray(encoder.encode([c.task_input["statement"] for c in cases], "query"), dtype="float32")
    common["encoding"]["query_seconds"] = time.perf_counter() - tick
    np.save(args.cache_dir / "findver-query-vectors.npy", query_vectors)
    index = {identity: i for i, identity in enumerate(ids)}
    rows = []
    for case, query in zip(cases, query_vectors):
        source_blocks = case.task_input["source_blocks"]
        candidate_ids = [b["id"] for b in source_blocks]
        tick = time.perf_counter()
        lexical = [p["id"] for p, score in bm25(source_blocks, case.task_input["statement"])]
        lexical_ms = (time.perf_counter() - tick) * 1000
        tick = time.perf_counter()
        values = matrix[[index[i] for i in candidate_ids]] @ query
        dense = [i for i, _ in sorted(zip(candidate_ids, values.tolist()), key=lambda p: (-p[1], p[0]))]
        dense_ms = (time.perf_counter() - tick) * 1000
        tick = time.perf_counter()
        fused = Counter()
        for ordering in (lexical, dense):
            for rank, identity in enumerate(ordering, 1):
                fused[identity] += 1 / (60 + rank)
        hybrid = sorted(fused, key=lambda i: (-fused[i], i))
        fusion_ms = (time.perf_counter() - tick) * 1000
        gold = set(case.reference["evidence_source_ids"])
        rows.append({"case_id": case.model_case_id, "official_case_id_scorer_only": case.case_id,
                     "subset": case.subset, "report": case.group_id,
                     "candidate_count": len(candidate_ids), "candidate_characters": sum(len(b["text"]) for b in source_blocks),
                     "gold_count": len(gold), "bm25_positive_candidates": len(lexical),
                     "ranking_ms": {"bm25": lexical_ms, "dense": dense_ms, "rrf_fusion": fusion_ms},
                     "methods": {mode: {str(k): metrics(ordering, blocks, gold, k) for k in (5, 10)}
                                 for mode, ordering in [("bm25", lexical), ("dense", dense), ("hybrid", hybrid)]}})
    common.update(status="completed", summary=summarize(rows), cases=rows)
    common["encoding"]["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    common["wall_seconds_this_run"] = time.perf_counter() - started
    common["cache"]["vectors_sha256"] = hashlib.sha256((args.cache_dir / "findver-block-vectors.npy").read_bytes()).hexdigest()
    write_json(args.output, common)
    print(json.dumps({"phase": "complete", "summary": common["summary"]["all"], "output": str(args.output)}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deadline-utc")
    args = parser.parse_args()
    with patch.object(socket.socket, "connect", side_effect=AssertionError("offline benchmark attempted network")):
        run(args)


if __name__ == "__main__":
    main()
