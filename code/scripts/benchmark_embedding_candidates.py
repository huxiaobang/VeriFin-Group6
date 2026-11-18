"""Reproducible, non-generative local embedding development diagnostics.

Downloads are explicit and unauthenticated. Running an evaluation uses only
local safetensors models, fixed source snapshots and existing development spans.
These diagnostics are NOT held-out E3 results or independent human gold.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CANDIDATES = {
    "e5-small": {"model_id": "intfloat/multilingual-e5-small", "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3", "query_prefix": "query: ", "passage_prefix": "passage: "},
    "bge-m3": {"model_id": "BAAI/bge-m3", "revision": "5617a9f61b028005a4858fdac845db406aefb181", "query_prefix": "", "passage_prefix": ""},
    "qwen3-0.6b": {"model_id": "Qwen/Qwen3-Embedding-0.6B", "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3", "query_prefix": "Instruct: Given a financial research question, retrieve relevant passages from company financial reports.\nQuery: ", "passage_prefix": ""},
}
VARIANTS = {
    "msft-cloud-margin": ("微软云业务毛利率下降的原因是什么？", "What compressed profitability as a percentage of sales in Microsoft's cloud business?"),
    "msft-operating-expenses": ("微软营业费用增长主要由哪些因素推动？", "Which spending categories were responsible for the rise in Microsoft's operating costs?"),
    "msft-shareholder-return": ("微软通过股息和股票回购向股东返还了多少资金？", "How much cash was distributed to Microsoft's owners by buying back stock and paying dividends?"),
    "msft-azure-growth": ("Azure及其他云服务收入增长了多少？", "What was the year-over-year sales increase for Azure together with other cloud services?"),
    "msft-constant-currency": ("微软如何计算固定汇率口径下的比较数据？", "What method removes foreign-exchange movements from Microsoft's comparisons across reporting periods?"),
    "nvda-blackwell": ("Blackwell架构带来了多少季度收入？", "What sales did NVIDIA's new Blackwell platform contribute during the quarter?"),
    "nvda-gaming-supply": ("英伟达游戏业务收入为何环比下降？", "What caused NVIDIA's gaming business to sell less than in the preceding quarter?"),
    "nvda-gross-margin": ("英伟达本季度毛利率下滑的原因是什么？", "Which factors squeezed NVIDIA's quarterly gross profit as a proportion of sales?"),
    "nvda-dividend": ("英伟达本次宣布的季度股息的派发日和登记日分别是什么？", "On which dates must NVIDIA stockholders be on the register and receive the declared cash distribution?"),
    "nvda-datacenter-year": ("英伟达整个财政年度的数据中心收入是多少？", "How much did NVIDIA's Data Center business earn in sales over the entire fiscal year?"),
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def host_metadata():
    result = {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(), "ram_gib": None}
    if platform.system() == "Darwin":
        for key, field in (("machdep.cpu.brand_string", "processor"), ("hw.memsize", "ram_gib")):
            try:
                value = subprocess.run(["/usr/sbin/sysctl", "-n", key], check=True, capture_output=True, text=True, timeout=3).stdout.strip()
                result[field] = int(value) / 1024 ** 3 if field == "ram_gib" else value
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    result["measurement_note"] = "Fields unavailable under OS sandbox restrictions remain unspecified; no assumed hardware values."
    return result


def query_suite():
    original = json.loads((ROOT / "data/evaluation/e3_development_queries.json").read_text())
    cases = []
    for supplied in original:
        case = copy.deepcopy(supplied)
        case["diagnostic_group"] = "original_english" if case["relevant"] else (
            "semantic_no_answer" if case["id"] == "unrelated-lexical" else "metadata_empty")
        cases.append(case)
        if case["id"] not in VARIANTS:
            continue
        for group, question in zip(("chinese_query", "english_paraphrase"), VARIANTS[case["id"]]):
            variant = copy.deepcopy(case)
            variant.update(id=case["id"] + "--" + group, query=question, diagnostic_group=group,
                           annotation_status="agent_authored_development_variant_not_independent_gold",
                           annotation_note="Agent-authored query variant inherits the original development source span; no additional human relevance adjudication.")
            cases.append(variant)
    return cases


def download(candidate, base):
    # No private model credentials are sent. Pin once and retain the revision.
    from huggingface_hub import hf_hub_download, snapshot_download
    spec = CANDIDATES[candidate]
    base.mkdir(parents=True, exist_ok=True)
    provenance_file = base / (candidate + ".provenance.json")
    if provenance_file.exists():
        provenance = json.loads(provenance_file.read_text())
        if provenance["model_id"] != spec["model_id"] or provenance["revision"] != spec["revision"]:
            raise ValueError("Existing provenance has a different model identity")
        revision = provenance["revision"]
    else:
        revision = spec["revision"]
        provenance = dict(spec, revision=revision, source="https://huggingface.co/" + spec["model_id"],
                          download_authentication=False, trust_remote_code=False)
        write_json(provenance_file, provenance)
    target = base / candidate
    print(json.dumps({"phase": "download", "model_id": spec["model_id"], "revision": revision}), flush=True)
    snapshot_download(spec["model_id"], revision=revision, local_dir=target, token=False,
                      allow_patterns=["config.json", "config_sentence_transformers.json", "modules.json", "sentence_bert_config.json",
                                      "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "vocab.json", "merges.txt",
                                      "sentencepiece.bpe.model", "model.safetensors", "model.safetensors.index.json", "model-*.safetensors",
                                      "1_Pooling/config.json", "2_Normalize/config.json", "README.md", "LICENSE"], max_workers=3)
    if not list(target.glob("*.safetensors")):
        if candidate != "bge-m3":
            raise ValueError("The pinned model has no downloaded safetensors weights")
        # BGE-M3's official pinned revision distributes a PyTorch state_dict.
        # Keep it outside the production model directory; conversion is explicit.
        source_dir = base / "_source" / candidate
        path = hf_hub_download(spec["model_id"], "pytorch_model.bin", revision=revision,
                               local_dir=source_dir, token=False)
        provenance.update(model_path=str(target.resolve()), download_complete=False,
                          conversion_required=True, source_weight_path=str(Path(path).resolve()))
        write_json(provenance_file, provenance)
        print(json.dumps({"phase": "download_complete_conversion_required", "model": candidate}), flush=True)
        return
    provenance.update(model_path=str(target.resolve()), download_complete=True)
    write_json(provenance_file, provenance)
    print(json.dumps({"phase": "download_complete", "model": candidate}), flush=True)


def convert_bge(base):
    """Convert the official tensor state_dict without unrestricted unpickling."""
    import torch
    from safetensors.torch import save_file
    provenance_file = base / "bge-m3.provenance.json"
    provenance = json.loads(provenance_file.read_text())
    source = Path(provenance["source_weight_path"])
    if source.resolve().parent != (base / "_source/bge-m3").resolve():
        raise ValueError("The source weight file must remain in the dedicated experiment source directory")
    # Do not use weights_only=False or allowlist custom pickle globals.
    tensors = torch.load(source, map_location="cpu", weights_only=True)
    if not isinstance(tensors, dict) or not tensors or any(not isinstance(k, str) or not isinstance(v, torch.Tensor) for k, v in tensors.items()):
        raise ValueError("Expected a tensor-only state dictionary")
    target = base / "bge-m3/model.safetensors"
    save_file({key: value.contiguous() for key, value in tensors.items()}, target, metadata={"format": "pt"})
    def digest(path):
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1048576), b""):
                sha.update(block)
        return sha.hexdigest()
    provenance.update(download_complete=True, conversion_required=False,
                      conversion={"method": "torch.load(weights_only=True, map_location=cpu) -> safetensors.torch.save_file",
                                  "torch": torch.__version__, "safetensors": importlib.metadata.version("safetensors"),
                                  "source_sha256": digest(source), "safetensors_sha256": digest(target),
                                  "tensor_count": len(tensors), "parameter_count": sum(value.numel() for value in tensors.values()),
                                  "custom_pickle_globals": False, "unrestricted_pickle": False})
    write_json(provenance_file, provenance)
    print(json.dumps({"phase": "conversion_complete", "model": "bge-m3", "conversion": provenance["conversion"]}), flush=True)


def grouped_results(evaluation, cases):
    groups = {}
    for group in sorted({c["diagnostic_group"] for c in cases}):
        ids = {c["id"] for c in cases if c["diagnostic_group"] == group}
        groups[group] = {"case_count": len(ids), "methods": {}}
        for mode, result in evaluation["methods"].items():
            if result["status"] != "evaluated":
                groups[group]["methods"][mode] = result
                continue
            rows = [r for r in result["cases"] if r["id"] in ids]
            positives = [r for r in rows if r["recall"] is not None]
            value = {"macro_span_recall_at_5": statistics.mean(r["recall"] for r in positives) if positives else None,
                     "mrr_at_5": statistics.mean(r["reciprocal_rank"] for r in positives) if positives else None,
                     "empty_result_fraction": statistics.mean(not r["passage_ids"] for r in rows),
                     "case_ids": [r["id"] for r in rows]}
            if group == "semantic_no_answer":
                value["interpretation"] = "Diagnostic behavior only: cosine top-k has no calibrated rejection threshold; nonempty results are not a measure of encoder quality."
            groups[group]["methods"][mode] = value
    return groups


def run(candidate, base, output, chunks, overlap, threads, batch_size):
    # Explicitly offline at evaluation time. A model download is a separate step.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import torch
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    from verifin.retrieval import Corpus, FrozenEmbeddings, corpus_from_snapshots, evaluate
    from verifin.runtime_embeddings import LocalSentenceEncoder
    provenance = json.loads((base / (candidate + ".provenance.json")).read_text())
    if not provenance.get("download_complete"):
        raise ValueError("Complete the pinned model download before evaluation")
    spec = CANDIDATES[candidate]
    fixed = corpus_from_snapshots(ROOT / "data/replay")
    corpus = Corpus(fixed.documents, max_chars=chunks, overlap=overlap)
    cases = query_suite()
    write_json(ROOT / "data/evaluation/embedding_selection_development_queries.json", cases)
    # Validate source spans before expensive encoding; no new label construction.
    baseline = evaluate(corpus, cases, modes=("bm25",), limit=5, max_context_chars=5 * chunks, repeats=3)
    print(json.dumps({"phase": "load", "candidate": candidate, "passages": len(corpus.chunks), "queries": len(cases)}), flush=True)
    started = time.perf_counter()
    encoder = LocalSentenceEncoder(base / candidate, model_id=spec["model_id"], revision=provenance["revision"],
                                   query_prefix=spec["query_prefix"], passage_prefix=spec["passage_prefix"], batch_size=batch_size)
    total_loader_seconds = time.perf_counter() - started
    source_load_dtype = str(next(encoder.model.parameters()).dtype)
    # Match CPU compute precision across architectures. BF16 can be emulated and
    # extremely slow on this host; its performance is not a model-size effect.
    started = time.perf_counter()
    encoder.model.float()
    float32_conversion_seconds = time.perf_counter() - started
    encoder.metadata["benchmark_compute_dtype"] = "torch.float32"
    token_lengths = {}
    for kind, values in (("passage", [c["text"] for c in corpus.chunks]), ("query", [c["query"] for c in cases])):
        counts = [len(row) for row in encoder.tokenizer([spec[kind + "_prefix"] + text for text in values],
                                                      truncation=False, add_special_tokens=True)["input_ids"]]
        token_lengths[kind] = {"maximum": max(counts), "median": statistics.median(counts), "count": len(counts)}
    # One equal, explicitly excluded warm-up per candidate.
    encoder.encode([cases[0]["query"]], "query")
    started = time.perf_counter()
    vectors = encoder.encode([c["text"] for c in corpus.chunks], "passage")
    passage_seconds = time.perf_counter() - started
    print(json.dumps({"phase": "passages_encoded", "candidate": candidate, "seconds": passage_seconds}), flush=True)
    query_vectors, query_times = {}, []
    for case in cases:
        started = time.perf_counter()
        query_vectors[case["query"]] = encoder.encode([case["query"]], "query")[0]
        query_times.append(time.perf_counter() - started)
    artifact = {"schema_version": 1, "corpus_fingerprint": corpus.fingerprint, "dimension": len(vectors[0]),
                "encoder": encoder.metadata, "chunks": {c["id"]: v for c, v in zip(corpus.chunks, vectors)}, "queries": query_vectors}
    frozen = FrozenEmbeddings(artifact, corpus)
    evaluation = evaluate(corpus, cases, embeddings=frozen, limit=5, max_context_chars=5 * chunks, repeats=3)
    model_files = [p for p in (base / candidate).rglob("*") if p.is_file() and ".cache" not in p.parts]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = rss if platform.system() == "Darwin" else rss * 1024
    run_name = candidate + f"-chars{chunks}-overlap{overlap}"
    artifact_path = output / (run_name + ".embeddings.json")
    write_json(artifact_path, artifact)
    result = {"protocol": "verifin-embedding-development-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "candidate": candidate, "model_id": spec["model_id"],
              "revision": provenance["revision"], "model_files_fingerprint": encoder.metadata["files_fingerprint"],
              "source": provenance["source"], "weight_conversion": provenance.get("conversion"),
              "annotation_provenance_verified": False, "independent_gold": False,
              "notice": "Small development diagnostics only. Query variants and span labels are not independent human gold. No generative model or paid API was called.",
              "hardware": host_metadata(),
              "runtime": {"python": sys.version.split()[0], "device": "cpu", "torch_threads": threads, "torch_interop_threads": 1, "batch_size": batch_size,
                          "torch": torch.__version__, "sentence_transformers": importlib.metadata.version("sentence-transformers"),
                          "transformers": importlib.metadata.version("transformers"), "tokenizer_truncation": False,
                          "dtype": str(next(encoder.model.parameters()).dtype), "source_load_dtype": source_load_dtype, "max_seq_length": encoder.max_length,
                          "parameter_count": sum(p.numel() for p in encoder.model.parameters()),
                          "pooling": encoder.model[1].get_config_dict() if hasattr(encoder.model[1], "get_config_dict") else str(encoder.model[1])},
              "preprocessing": {"chunk_chars": chunks, "chunk_overlap": overlap, "query_prefix": spec["query_prefix"], "passage_prefix": spec["passage_prefix"],
                                "token_lengths": token_lengths},
              "resources": {"model_files_bytes": sum(p.stat().st_size for p in model_files), "peak_process_rss_bytes": peak_rss_bytes,
                            "peak_rss_scope": "Whole evaluation process; includes Python, torch, tokenizer and weights. Not model-only RAM, not total machine memory.",
                            "loader_including_hash_seconds": total_loader_seconds, "model_load_seconds": encoder.load_seconds,
                            "float32_conversion_seconds": float32_conversion_seconds,
                            "passage_encoding_seconds": passage_seconds, "passages_per_second": len(corpus.chunks) / passage_seconds,
                            "query_encoding_median_ms": statistics.median(query_times) * 1000,
                            "query_encoding_p95_ms": sorted(query_times)[max(0, int(.95 * len(query_times) + .999999) - 1)] * 1000,
                            "query_encoding_scope": "Single-query tokenize + forward + normalize; one warm-up excluded; one measurement per distinct query, no cache."},
              "corpus": {"documents": len(corpus.documents), "characters": sum(len(c["text"]) for c in corpus.documents), "chunks": len(corpus.chunks), "fingerprint": corpus.fingerprint},
              "groups": grouped_results(evaluation, cases), "evaluation": evaluation,
              "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "artifact_file": artifact_path.name,
              "network_requests_during_encoding": 0, "generative_model_calls": 0}
    write_json(output / (run_name + ".results.json"), result)
    print(json.dumps({"phase": "complete", "candidate": candidate, "result": str(output / (run_name + ".results.json")), "resources": result["resources"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "convert-bge", "run", "queries"))
    parser.add_argument("--candidate", choices=CANDIDATES)
    parser.add_argument("--models", type=Path, default=ROOT.parents[1] / "work/embedding-candidates")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/embedding_selection_results")
    parser.add_argument("--chunk-chars", type=int, default=600)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    if args.action == "queries":
        write_json(ROOT / "data/evaluation/embedding_selection_development_queries.json", query_suite())
    elif args.action == "convert-bge":
        convert_bge(args.models)
    elif not args.candidate:
        parser.error("--candidate is required for download/run")
    elif args.action == "download":
        download(args.candidate, args.models)
    else:
        run(args.candidate, args.models, args.output, args.chunk_chars, args.chunk_overlap, args.threads, args.batch_size)


if __name__ == "__main__":
    main()
