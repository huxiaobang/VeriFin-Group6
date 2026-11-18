"""Pinned PPLX MaxSim retrieval on the same pre-frozen public ConvFinQA subset.

This evaluates retrieval within the provided report excerpts, not full Agent QA.
No production code or old release is modified. No model API is called.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
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
from scripts.open_source_late_interaction import MODEL_ID, REVISION, validate_directory, write_json, sha256


def run(args):
    if (args.output/"pplx-v2-late-0.6b-convfinqa-results.json").exists() or (args.output/"pplx-v2-late-0.6b-convfinqa-vectors.npz").exists():
        raise ValueError("Use a fresh experiment output directory; completed artifacts are not overwritten")
    if args.extra_deps:
        sys.path.insert(0, str(args.extra_deps.resolve()))
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    import numpy as np
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    from sentence_transformers import MultiVectorEncoder
    from sentence_transformers.util.similarity import maxsim
    from scripts.benchmark_public_retrieval import load, question, measure, aggregate
    from verifin.retrieval import bm25
    dependency_paths = {"scorer":ROOT/"scripts/benchmark_public_retrieval.py",
                        "public_adapter":ROOT/"verifin/public_benchmarks.py",
                        "bm25_retrieval":ROOT/"verifin/retrieval.py",
                        "model_validation_helper":ROOT/"scripts/open_source_late_interaction.py"}
    dependency_hashes = {name:sha256(path) for name,path in dependency_paths.items()}
    selection, cases = load(args)
    frozen_protocol = args.output/"protocol.json"
    if frozen_protocol.exists():
        protocol = json.loads(frozen_protocol.read_text())
        if protocol["revision"] != REVISION or protocol["selection_sha256"] != sha256(args.selection) or protocol["passage_batch_size"] != args.batch_size:
            raise ValueError("Arguments differ from the pre-inference protocol snapshot")
    blocks = {b["id"]: b for c in cases for b in c.task_input["source_blocks"]}
    block_ids = list(blocks)
    queries = list(dict.fromkeys(question(c, history) for c in cases for history in (False, True)))
    manifest = validate_directory(args.model)
    args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"phase": "load", "cases": len(cases), "blocks": len(blocks), "queries": len(queries)}), flush=True)
    started = time.perf_counter()
    model = MultiVectorEncoder(str(args.model), device="cpu", local_files_only=True, token=False, trust_remote_code=False,
                               model_kwargs={"dtype": "float32", "use_safetensors": True})
    load_seconds = time.perf_counter()-started
    if model.similarity_fn_name != "maxsim" or model.prompts.get("query") != "[Q] " or model.prompts.get("document") != "[D] ":
        raise ValueError("Native model scoring/prefix contract changed")
    first = model[0]
    tokenizer = getattr(first, "tokenizer", None) or getattr(getattr(first, "processor", None), "tokenizer", None)
    if tokenizer is None:
        raise ValueError("Cannot verify tokenizer limits")
    input_lengths = {}
    for kind, texts, limit in (("query", queries, 1024), ("document", [blocks[i]["text"] for i in block_ids], 4096)):
        lengths = [len(t) for t in tokenizer([model.prompts[kind]+text for text in texts], truncation=False, add_special_tokens=True)["input_ids"]]
        if max(lengths)+32 >= limit:
            raise ValueError("Public input exceeds the native configured budget; refusing silent truncation")
        input_lengths[kind] = {"maximum_raw_tokens_with_marker": max(lengths), "configured_limit": limit,
                               "reserved_processor_overhead_tokens": 32}
    model.encode_query([queries[0]], batch_size=1, show_progress_bar=False, convert_to_numpy=True)
    started = time.perf_counter()
    passage_arrays = model.encode_document([blocks[i]["text"] for i in block_ids], batch_size=args.batch_size,
                                           show_progress_bar=False, convert_to_numpy=True)
    passage_seconds = time.perf_counter()-started
    if len(passage_arrays) != len(block_ids):
        raise ValueError("Encoder returned an unexpected number of document arrays")
    print(json.dumps({"phase": "blocks_encoded", "count": len(blocks), "seconds": passage_seconds}), flush=True)
    query_arrays, query_ms = [], []
    for query_number, text in enumerate(queries,1):
        started = time.perf_counter()
        query_arrays.extend(model.encode_query([text], batch_size=1, show_progress_bar=False, convert_to_numpy=True))
        query_ms.append((time.perf_counter()-started)*1000)
        if query_number % 32 == 0:
            print(json.dumps({"phase":"queries_encoded","count":query_number,"total":len(queries)}),flush=True)
    if len(query_arrays) != len(queries):
        raise ValueError("Encoder returned an unexpected number of query arrays")
    started = time.perf_counter()
    batch_query_arrays = model.encode_query(queries,batch_size=args.batch_size,show_progress_bar=False,convert_to_numpy=True)
    batch_query_seconds = time.perf_counter()-started
    if len(batch_query_arrays) != len(query_arrays) or any(a.shape != b.shape or not np.allclose(a,b,atol=3e-4,rtol=3e-4)
                                                         for a,b in zip(query_arrays,batch_query_arrays)):
        raise ValueError("Single and batch query token embeddings disagree beyond FP32 tolerance")
    batch_max_abs_difference = max(float(np.abs(a-b).max()) for a,b in zip(query_arrays,batch_query_arrays))
    del batch_query_arrays
    for a in list(passage_arrays)+query_arrays:
        if a.ndim != 2 or a.shape[1] != 128 or not len(a) or not np.isfinite(a).all() or not np.allclose(np.linalg.norm(a,axis=1),1,atol=2e-4):
            raise ValueError("Native token embeddings failed shape/finite/norm validation")
    independent = np.asarray([[(q@d.T).max(axis=1).sum() for d in passage_arrays[:3]] for q in query_arrays[:2]])
    official = model.similarity(query_arrays[:2],passage_arrays[:3],length_normalize=False).numpy()
    if not np.allclose(independent,official,atol=2e-4):
        raise ValueError("Independent MaxSim formula disagrees")
    passage_map, query_map = dict(zip(block_ids,passage_arrays)), dict(zip(queries,query_arrays))
    rows, ranking_ms = [], []
    for case in cases:
        source_blocks = case.task_input["source_blocks"]
        lookup = {b["id"]: b for b in source_blocks}
        gold = case.reference["source_ids"]
        if not gold or case.reference["unmapped_source_locators"]:
            raise ValueError("Official reference has unmapped source evidence")
        methods = {}
        for history in (False,True):
            prefix = "history_questions" if history else "current_question"
            query = question(case,history)
            started = time.perf_counter()
            scores = maxsim([query_map[query]],[passage_map[b["id"]] for b in source_blocks],
                            device="cpu",length_normalize=False).tolist()[0]
            late = sorted(zip(source_blocks,scores),key=lambda item:(-item[1],item[0]["id"]))
            ranking_ms.append((time.perf_counter()-started)*1000)
            lexical = bm25(source_blocks,query)
            fused = {}
            for ranking in (late[:100],lexical[:100]):
                for i,(b,_) in enumerate(ranking,1):
                    fused[b["id"]]=fused.get(b["id"],0)+1/(60+i)
            hybrid = [lookup[i] for i in sorted(fused,key=lambda i:(-fused[i],i))]
            methods[prefix+"_maxsim_top5"]=measure([b for b,_ in late],gold,5)
            methods[prefix+"_bm25_maxsim_rrf_top5"]=measure(hybrid,gold,5)
        rows.append({"case_id":case.case_id,"group_id":case.group_id,"dialogue_id":case.dialogue_id,
                     "turn_index":case.turn_index,"candidate_count":len(source_blocks),"methods":methods})
    arrays={f"passage_{i:04d}":np.asarray(a,dtype=np.float32) for i,a in enumerate(passage_arrays)}
    arrays.update({f"query_{i:04d}":np.asarray(a,dtype=np.float32) for i,a in enumerate(query_arrays)})
    artifact=args.output/"pplx-v2-late-0.6b-convfinqa-vectors.npz"
    np.savez_compressed(artifact,**arrays)
    with np.load(artifact,allow_pickle=False) as saved:
        if set(saved.files)!=set(arrays) or any(not np.array_equal(saved[k],v) for k,v in arrays.items()):
            raise ValueError("Vector export did not round trip exactly")
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if dependency_hashes != {name:sha256(path) for name,path in dependency_paths.items()}:
        raise ValueError("Shared scorer/adapter code changed during this experiment")
    result={"protocol":"public-convfinqa-late-interaction-v1","created_at":datetime.now(timezone.utc).isoformat(),
            "pre_inference_protocol_sha256":sha256(frozen_protocol) if frozen_protocol.exists() else None,
            "selection":selection,"selection_sha256":sha256(args.selection),"source":cases[0].source,
            "model":{"model_id":MODEL_ID,"revision":REVISION,"license":"MIT","input_modality":"text_only",
                     "prompts":model.prompts,"input_lengths":input_lengths,"file_manifest":manifest},
            "runtime":{"device":"cpu","dtype":str(next(model.parameters()).dtype),"threads":4,"interop_threads":1,
                       "batch_size":args.batch_size,"torch":torch.__version__,"sentence_transformers":importlib.metadata.version("sentence-transformers"),
                       "transformers":importlib.metadata.version("transformers"),"numpy":np.__version__,
                       "pillow":importlib.metadata.version("Pillow"),"torchvision":importlib.metadata.version("torchvision"),
                       "trust_remote_code":False,"model_load_seconds":load_seconds},
            "summary":aggregate(rows),"cases":rows,
            "resources":{"unique_source_blocks":len(block_ids),"unique_query_texts":len(queries),"passage_encoding_seconds":passage_seconds,
                         "eligible_candidate_count_minimum":min(len(c.task_input["source_blocks"]) for c in cases),
                         "eligible_candidate_count_median":statistics.median(len(c.task_input["source_blocks"]) for c in cases),
                         "eligible_candidate_count_maximum":max(len(c.task_input["source_blocks"]) for c in cases),
                         "query_encoding_seconds":sum(query_ms)/1000,"query_encoding_median_ms":statistics.median(query_ms),
                         "query_batch_encoding_seconds":batch_query_seconds,"query_batch_encoding_batch_size":args.batch_size,
                         "single_vs_batch_max_abs_difference":batch_max_abs_difference,
                         "query_encoding_p95_ms":sorted(query_ms)[math.ceil(.95*len(query_ms))-1],
                         "query_encoding_batch_size":1,"ranking_maxsim_median_ms":statistics.median(ranking_ms),
                         "peak_process_rss_bytes":rss if platform.system()=="Darwin" else rss*1024,
                         "document_vector_count":sum(len(a) for a in passage_arrays),
                         "document_vectors_bytes_float32":sum(a.nbytes for a in passage_arrays),
                         "document_token_vector_median":statistics.median(len(a) for a in passage_arrays),
                         "document_token_vector_maximum":max(len(a) for a in passage_arrays),
                         "query_token_vector_median":statistics.median(len(a) for a in query_arrays),
                         "query_token_vector_maximum":max(len(a) for a in query_arrays),
                         "single_1024d_float32_reference_bytes":len(block_ids)*1024*4,"artifact_bytes":artifact.stat().st_size},
            "controls":{"candidate_pool":"All original supplied context rows and sentences, no reference-based filtering",
                        "query_history":"Previous user questions only; no reference answers or future turns",
                        "scoring":"Normalized per-token dot product Sum-MaxSim; not single-vector cosine",
                        "hybrid":"BM25 + MaxSim equal-weight RRF k=60; top100 per component",
                        "top_k":5,"context_char_budget":None,"source_discovery_measured":False,"visual_input_measured":False},
            "artifact_file":artifact.name,"artifact_sha256":sha256(artifact),
            "artifact_mapping":{"passages":block_ids,"query_texts":queries},
            "source_text_sha256":{identity:sha256_text(b["text"]) for identity,b in blocks.items()},
            "script_sha256":sha256(Path(__file__)),
            "dependency_sha256":dependency_hashes,
            "checks":{"native_maxsim_matches_independent_formula":True,"export_exact_roundtrip":True,"same_frozen_selection":True,
                      "single_query_matches_batch_within_fp32_tolerance":True},
            "limits":["Public dev provided-context evidence retrieval, not full Agent accuracy or unseen-model test.",
                      "100 correlated turns from 18 source-page groups, not 100 independent reports.",
                      "No generated answers are supplied; previous user questions alone may not resolve every follow-up.",
                      "Different source-block lengths; compare paired source IDs, not fixed-character E3 metrics.",
                      "Single-query latency here should not be equated to the baseline script's batch encoding throughput."],
            "generative_model_calls":0,"network_requests_during_encoding":0}
    write_json(args.output/"pplx-v2-late-0.6b-convfinqa-results.json",result)
    print(json.dumps({"phase":"complete","summary":result["summary"],"resources":result["resources"]}),flush=True)


def sha256_text(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model",type=Path,default=ROOT.parents[1]/"work/open-source-models/pplx-embed-v2-late-0.6b")
    p.add_argument("--extra-deps",type=Path,default=ROOT.parents[1]/"work/open-source-models/optional-vision-deps")
    p.add_argument("--data",type=Path,required=True)
    p.add_argument("--selection",type=Path,required=True)
    p.add_argument("--output",type=Path,default=ROOT/"data/evaluation/open_source_late_public")
    p.add_argument("--batch-size",type=int,default=8)
    run(p.parse_args())


if __name__=="__main__":
    main()
