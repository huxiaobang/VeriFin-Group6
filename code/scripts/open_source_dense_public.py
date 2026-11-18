"""Extend the exact frozen public ConvFinQA protocol to cached dense candidates.

Reuses the shared source loader, pure-newline questions, ranking and scorer.
Only local safetensors are loaded; no new download, custom code or API calls.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate",choices=("e5-small","bge-m3"),required=True)
    p.add_argument("--data",type=Path,required=True)
    p.add_argument("--selection",type=Path,required=True)
    p.add_argument("--artifact",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--model-root",type=Path,default=ROOT.parents[1]/"work/embedding-candidates")
    p.add_argument("--batch-size",type=int,default=8)
    p.add_argument("--threads",type=int,default=4)
    args=p.parse_args()
    args.stage="encode"
    if args.artifact.exists() or args.output.exists():
        p.error("Use fresh artifact/output paths")
    os.environ.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    from scripts.benchmark_public_retrieval import load,question,evaluate,write
    from scripts.benchmark_embedding_candidates import CANDIDATES
    from verifin.runtime_embeddings import LocalSentenceEncoder
    manifest,cases=load(args)
    spec=CANDIDATES[args.candidate]
    texts={b["id"]:b["text"] for c in cases for b in c.task_input["source_blocks"]}
    queries=list(dict.fromkeys(question(c,h) for c in cases for h in (False,True)))
    dependencies={name:sha256(ROOT/name) for name in ("scripts/benchmark_public_retrieval.py",
                  "verifin/public_benchmarks.py","verifin/retrieval.py","verifin/runtime_embeddings.py")}
    print(json.dumps({"phase":"load","candidate":args.candidate,"blocks":len(texts),"queries":len(queries)}),flush=True)
    started=time.perf_counter()
    encoder=LocalSentenceEncoder(args.model_root/args.candidate,**spec,batch_size=args.batch_size)
    load_seconds=time.perf_counter()-started
    encoder.encode([queries[0]],"query")
    started=time.perf_counter()
    vectors=encoder.encode(list(texts.values()),"passage")
    passage_seconds=time.perf_counter()-started
    print(json.dumps({"phase":"passages_encoded","candidate":args.candidate,"seconds":passage_seconds}),flush=True)
    started=time.perf_counter()
    qvectors=encoder.encode(queries,"query")
    query_seconds=time.perf_counter()-started
    if len(vectors)!=len(texts) or len(qvectors)!=len(queries):
        raise ValueError("Encoder output count differs from inputs")
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    artifact={"protocol":"public-convfinqa-retrieval-v2","created_at":datetime.now(timezone.utc).isoformat(),
              "selection_sha256":sha256(args.selection),"model":encoder.metadata,
              "chunks":dict(zip(texts,vectors)),"queries":dict(zip(queries,qvectors)),
              "text_sha256":{key:hashlib.sha256(value.encode()).hexdigest() for key,value in texts.items()},
              "passage_seconds":passage_seconds,"query_seconds":query_seconds,
              "model_load_seconds":load_seconds,"passage_operation":"fresh encoding",
              "original_passage_encoding_seconds":passage_seconds,"unique_queries":len(queries),
              "unique_source_blocks":len(texts),"peak_rss_bytes":rss if platform.system()=="Darwin" else rss*1024,
              "script_sha256":sha256(Path(__file__)),"dependency_sha256":dependencies,
              "generative_model_calls":0,"network_requests_during_encoding":0}
    changed=[name for name in dependencies if sha256(ROOT/name)!=dependencies[name]]
    artifact["dependencies_changed_during_encoding"]=changed
    # Encoded inputs and weight identity remain valid if a reporting helper is
    # edited concurrently. Preserve this expensive artifact before rejecting
    # in-process scoring; a clean rescore can then verify the stored vectors.
    write(args.artifact,artifact)
    if changed:
        print(json.dumps({"phase":"artifact_saved_rescore_required","changed_dependencies":changed}),flush=True)
        return
    evaluate(args,manifest,cases,artifact,reranker=None)


if __name__=="__main__":
    main()
