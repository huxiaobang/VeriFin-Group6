"""Offline text-only diagnostic for a pinned native multi-vector retriever.

Uses real model weights; does not modify production retrieval or old releases.
The 34 queries are development diagnostics, never independent human gold.
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
MODEL_ID = "perplexity-ai/pplx-embed-v2-late-0.6b"
REVISION = "7bba8e7cf2075dec747fa8557f41eb90e70361ed"
WEIGHT_SHA256 = "107eaae48d026ea20458b30dd755a0f1475b34fcf5a1895123ad67a2385b9d6b"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_directory(directory):
    modules = json.loads((directory / "modules.json").read_text())
    for module in modules:
        if not module["type"].startswith("sentence_transformers."):
            raise ValueError("Only installed native Sentence Transformers modules are allowed")
        if not (directory / module.get("path", "")).resolve().is_relative_to(directory.resolve()):
            raise ValueError("Module path escapes model directory")
    manifest = []
    for path in sorted(directory.rglob("*")):
        if ".cache" in path.parts or ".range-parts" in path.parts or path.name.endswith(".download.json") or not path.is_file():
            continue
        if not path.resolve().is_relative_to(directory.resolve()) or path.suffix in {".py", ".bin", ".pt", ".pth", ".pickle", ".pkl"}:
            raise ValueError("Experimental loader does not execute downloaded Python or load pickle weights")
        manifest.append({"path": str(path.relative_to(directory)), "bytes": path.stat().st_size, "sha256": sha256(path)})
    if not any(row["path"].endswith(".safetensors") for row in manifest):
        raise ValueError("Local safetensors weights are missing")
    main = next((row for row in manifest if row["path"] == "model.safetensors"), None)
    if main is None or main["bytes"] != 2377321184 or main["sha256"] != WEIGHT_SHA256:
        raise ValueError("Weight file differs from the fixed official PPLX revision")
    return manifest


def summarize(rows, cases):
    result = {}
    by_id = {c["id"]: c for c in cases}
    groups = ["all_positive", "original_english", "chinese_query", "english_paraphrase", "metadata_empty", "semantic_no_answer"]
    for group in groups:
        selected = [r for r in rows if (r["recall"] is not None if group == "all_positive"
                                        else by_id[r["id"]]["diagnostic_group"] == group)]
        positives = [r for r in selected if r["recall"] is not None]
        result[group] = {"count": len(selected),
                         "macro_span_recall_at_5": statistics.mean(r["recall"] for r in positives) if positives else None,
                         "mrr_at_5": statistics.mean(r["reciprocal_rank"] for r in positives) if positives else None,
                         "empty_result_fraction": statistics.mean(not r["passage_ids"] for r in selected) if selected else None}
        if group == "semantic_no_answer":
            result[group]["notice"] = "Return behavior only; uncalibrated top-k retrieval is not an evidence-validity or abstention classifier."
    return result


def score_cases(corpus, cases, passage_arrays, query_arrays):
    import numpy as np
    from sentence_transformers.util.similarity import maxsim
    from verifin.retrieval import bm25
    index = {c["id"]: i for i, c in enumerate(corpus.chunks)}
    methods = {"maxsim": [], "bm25_maxsim_rrf": []}
    per_query_ms = []
    for case, query_array in zip(cases, query_arrays):
        eligible = corpus.eligible(case.get("filters"))
        started = time.perf_counter()
        if eligible:
            scores = maxsim([query_array], [passage_arrays[index[c["id"]]] for c in eligible],
                            device="cpu", length_normalize=False).tolist()[0]
            if not all(math.isfinite(v) for v in scores):
                raise ValueError("Non-finite MaxSim score")
            late = sorted(zip(eligible, scores), key=lambda item: (-item[1], item[0]["id"]))
        else:
            late = []
        per_query_ms.append((time.perf_counter() - started) * 1000)
        lexical = bm25(eligible, case["query"])
        fused = {}
        for ranking in (lexical[:100], late[:100]):
            for rank, (chunk, _) in enumerate(ranking, 1):
                row = fused.setdefault(chunk["id"], [chunk, 0.0])
                row[1] += 1 / (60 + rank)
        hybrid = sorted(fused.values(), key=lambda item: (-item[1], item[0]["id"]))
        for method, ranking in (("maxsim", late), ("bm25_maxsim_rrf", hybrid)):
            top = ranking[:5]
            assert sum(len(c["text"]) for c, _ in top) <= 3000
            covered = [any(c["document_id"] == g["document_id"] and c["start"] <= g["start"] and c["end"] >= g["end"]
                           for c, _ in top) for g in case["relevant"]]
            ranks = [rank for rank, (c, _) in enumerate(top, 1) if any(c["document_id"] == g["document_id"] and
                     c["start"] <= g["start"] and c["end"] >= g["end"] for g in case["relevant"])]
            methods[method].append({"id": case["id"], "recall": sum(covered) / len(covered) if covered else None,
                                    "reciprocal_rank": 1 / min(ranks) if ranks else 0, "eligible_chunks": len(eligible),
                                    "context_chars": sum(len(c["text"]) for c, _ in top),
                                    "passage_ids": [c["id"] for c, _ in top], "scores": [float(s) for _, s in top]})
    return {method: {"groups": summarize(rows, cases), "cases": rows} for method, rows in methods.items()}, per_query_ms


def run(args):
    if args.extra_deps:
        sys.path.insert(0, str(args.extra_deps.resolve()))
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    import numpy as np
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    from sentence_transformers import MultiVectorEncoder
    from verifin.retrieval import Corpus, corpus_from_snapshots, evaluate
    from scripts.benchmark_embedding_candidates import query_suite
    corpus = Corpus(corpus_from_snapshots(ROOT / "data/replay").documents, max_chars=600, overlap=80)
    cases = query_suite()
    # Verify labels, filters, and budget before encoding.
    evaluate(corpus, cases, modes=("bm25",), limit=5, max_context_chars=3000, repeats=1)
    manifest = validate_directory(args.model)
    args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"phase": "load", "model": MODEL_ID, "passages": len(corpus.chunks), "queries": len(cases)}), flush=True)
    started = time.perf_counter()
    model = MultiVectorEncoder(str(args.model), device="cpu", local_files_only=True, trust_remote_code=False, token=False,
                               model_kwargs={"dtype": "float32", "use_safetensors": True})
    load_seconds = time.perf_counter() - started
    if model.similarity_fn_name != "maxsim":
        raise ValueError("Pinned model must use its native MaxSim similarity")
    if model.prompts.get("query") != "[Q] " or model.prompts.get("document") != "[D] ":
        raise ValueError("Pinned query/document marker contract changed")
    transformer = model[0]
    tokenizer = getattr(transformer, "tokenizer", None) or getattr(getattr(transformer, "processor", None), "tokenizer", None)
    if tokenizer is None:
        raise ValueError("Cannot verify text token lengths")
    counts = {}
    for kind, values, limit in (("query", [c["query"] for c in cases], 1024),
                                ("document", [c["text"] for c in corpus.chunks], 4096)):
        lengths = [len(ids) for ids in tokenizer([model.prompts[kind] + t for t in values], truncation=False,
                                                add_special_tokens=True)["input_ids"]]
        if max(lengths) + 32 >= limit:
            raise ValueError("Inputs approach the configured limit; no silent truncation is allowed")
        counts[kind] = {"maximum_raw_tokens_with_marker": max(lengths), "median_raw_tokens_with_marker": statistics.median(lengths),
                        "configured_limit": limit, "reserved_processor_overhead_tokens": 32}
    # One equal warm-up, excluded from query and document encoding times.
    model.encode_query([cases[0]["query"]], batch_size=1, show_progress_bar=False, convert_to_numpy=True)
    started = time.perf_counter()
    passage_arrays = model.encode_document([c["text"] for c in corpus.chunks], batch_size=args.batch_size,
                                           show_progress_bar=False, convert_to_numpy=True)
    passage_seconds = time.perf_counter() - started
    print(json.dumps({"phase": "documents_encoded", "seconds": passage_seconds}), flush=True)
    query_arrays, query_times = [], []
    for case in cases:
        started = time.perf_counter()
        query_arrays.extend(model.encode_query([case["query"]], batch_size=1, show_progress_bar=False, convert_to_numpy=True))
        query_times.append((time.perf_counter() - started) * 1000)
    for row in list(passage_arrays) + query_arrays:
        if row.ndim != 2 or row.shape[1] != 128 or not len(row) or not np.isfinite(row).all():
            raise ValueError("Invalid token embedding array")
        if not np.allclose(np.linalg.norm(row, axis=1), 1, atol=2e-4):
            raise ValueError("Native token vectors must be normalized")
    # Check exported MaxSim scoring against an independent direct implementation.
    official = model.similarity(query_arrays[:2], passage_arrays[:3], length_normalize=False).numpy()
    direct = np.array([[(q @ d.T).max(axis=1).sum() for d in passage_arrays[:3]] for q in query_arrays[:2]])
    if not np.allclose(official, direct, atol=2e-4):
        raise ValueError("Independent MaxSim formula check failed")
    arrays = {f"passage_{i:04d}": np.asarray(v, dtype=np.float32) for i, v in enumerate(passage_arrays)}
    arrays.update({f"query_{i:04d}": np.asarray(v, dtype=np.float32) for i, v in enumerate(query_arrays)})
    artifact = args.output / "pplx-v2-late-0.6b-vectors.npz"
    np.savez_compressed(artifact, **arrays)
    methods, ranking_times = score_cases(corpus, cases, passage_arrays, query_arrays)
    # Reload without pickle and verify every exported tensor exactly.
    with np.load(artifact, allow_pickle=False) as saved:
        if set(saved.files) != set(arrays) or any(not np.array_equal(saved[key], val) for key, val in arrays.items()):
            raise ValueError("Saved vectors did not round trip exactly")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    document_tokens = sum(len(v) for v in passage_arrays)
    result = {"protocol": "verifin-open-source-late-interaction-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "model_id": MODEL_ID, "revision": REVISION, "license": "MIT", "source": "https://huggingface.co/" + MODEL_ID,
              "input_modality": "text_only", "independent_gold": False,
              "notice": "Development diagnostics only: 10 underlying positive questions with three correlated expressions each. Visual-page retrieval was not tested.",
              "runtime": {"device": "cpu", "dtype": str(next(model.parameters()).dtype), "threads": 4, "interop_threads": 1,
                          "batch_size": args.batch_size, "python": platform.python_version(), "torch": torch.__version__,
                          "sentence_transformers": importlib.metadata.version("sentence-transformers"),
                          "transformers": importlib.metadata.version("transformers"), "trust_remote_code": False,
                          "numpy": np.__version__, "pillow": importlib.metadata.version("Pillow"),
                          "torchvision": importlib.metadata.version("torchvision"),
                          "downloaded_python_executed": False, "model_load_seconds": load_seconds},
              "corpus": {"fingerprint": corpus.fingerprint, "documents": len(corpus.documents), "chunks": len(corpus.chunks), "chunk_chars": 600,
                         "overlap": 80, "context_budget_chars": 3000, "top_k": 5},
              "scoring": {"native": "Sum over query token maxima of token-wise normalized dot products (MaxSim)",
                          "pool": "All metadata-eligible chunks; no ANN approximation or reranker candidate restriction",
                          "hybrid": "BM25 + MaxSim equal-weight RRF k=60, 100 candidates per component", "score_is_probability": False},
              "input_contract": {"prompts": model.prompts, "token_lengths": counts, "token_pooling": "none; native Dense+MultiVectorMask+Normalize",
                                 "document_punctuation_mask": json.loads((args.model / "2_MultiVectorMask/config.json").read_text())},
              "resources": {"passage_encoding_seconds": passage_seconds, "passages_per_second": len(corpus.chunks)/passage_seconds,
                            "query_encoding_median_ms": statistics.median(query_times),
                            "query_encoding_p95_ms": sorted(query_times)[math.ceil(.95 * len(query_times))-1],
                            "query_maxsim_median_ms": statistics.median(ranking_times),
                            "peak_process_rss_bytes": rss if platform.system()=="Darwin" else rss*1024,
                            "document_vector_count": document_tokens, "document_vectors_bytes_float32": sum(v.nbytes for v in passage_arrays),
                            "single_1024d_float32_reference_bytes": len(corpus.chunks)*1024*4,
                            "document_vector_count_median": statistics.median(len(v) for v in passage_arrays),
                            "document_vector_count_maximum": max(len(v) for v in passage_arrays),
                            "query_vector_count_median": statistics.median(len(v) for v in query_arrays),
                            "query_vector_count_maximum": max(len(v) for v in query_arrays),
                            "artifact_bytes": artifact.stat().st_size, "model_files_bytes": sum(x["bytes"] for x in manifest)},
              "methods": methods, "artifact_file": artifact.name, "artifact_sha256": sha256(artifact),
              "artifact_mapping": {"passages": [c["id"] for c in corpus.chunks], "queries": [c["id"] for c in cases]},
              "file_manifest": manifest, "script_sha256": sha256(Path(__file__)),
              "checks": {"native_maxsim_matches_direct_formula": True, "vector_export_exact_roundtrip": True,
                         "safetensors_only": True, "native_modules_only": True, "fixed_development_labels": True},
              "generative_model_calls": 0, "network_requests_during_encoding": 0}
    write_json(args.output / "pplx-v2-late-0.6b-results.json", result)
    print(json.dumps({"phase": "complete", "resources": result["resources"],
                      "positive_metrics": {key: value["groups"]["all_positive"] for key,value in methods.items()}}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT.parents[1] / "work/open-source-models/pplx-embed-v2-late-0.6b")
    parser.add_argument("--extra-deps", type=Path, default=ROOT.parents[1] / "work/open-source-models/optional-vision-deps")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/open_source_late_interaction")
    parser.add_argument("--batch-size", type=int, default=8)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
