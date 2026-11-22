"""Deterministic, auditable passage retrieval, without a generative model.

BM25 statistics are computed after metadata filtering. Dense retrieval requires
explicit, genuine encoder output; missing embeddings never become random vectors
or a lexical result labelled "dense". Offsets refer to the original Unicode text.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import time
import importlib.metadata
from collections import Counter
from pathlib import Path

from .data import DataError, SnapshotData, available_by, parse_day, stable_id

CHUNK_VERSION = "boundary-chars-v1"
DOCUMENT_READ_LIMIT = 4500
STOPWORDS = frozenset("a an and are as at be by for from how in is it of on or that the this to was were what when which why with".split())


def corpus_from_snapshots(data_dir):
    """Public fixed-corpus loader for CLI/evaluation; verifies original and text hashes.

    Accepts a path or existing SnapshotData. Only manifest documents are included,
    so another session's live cache cannot silently change a fixed E3 corpus.
    """
    data = data_dir if isinstance(data_dir, SnapshotData) else SnapshotData(data_dir)
    return Corpus([snapshot_document(data, doc) for doc in data.manifest["documents"]])


def snapshot_document(data, doc):
    """Load one original and its text with the same verified provenance as the corpus."""
    data.raw(doc["source"])
    text = data.raw(doc["text"]).decode("utf-8")
    info, derived = data.info(doc["source"]), data.info(doc["text"])
    return {"id": stable_id("source_doc_", info["url"]), "text": text,
            "entity": doc["entity"], "document_report_date": doc.get("period_end"),
            "document_period_start": doc.get("period_start"), "form": doc.get("form"),
            "published_at": doc["published_at"], "publication_precision": doc["publication_precision"],
            "source_url": info["url"], "snapshot_hash": info["sha256"], "snapshot_path": doc["source"],
            "text_snapshot_path": doc["text"], "text_snapshot_hash": derived["sha256"],
            "retrieved_at": info["retrieved_at"], "extraction_method": doc["extraction_method"], "mode": "replay"}


# Stable CLI name; keep the descriptive Python alias for existing callers.
load_replay_corpus = corpus_from_snapshots


def tokenize(text):
    """Unicode word tokens, case folded; no stemming, numbers are retained."""
    return [word for word in re.findall(r"[^\W_]+", text.casefold(), re.UNICODE) if word not in STOPWORDS]


def chunk_spans(text, max_chars=1800, overlap=250):
    if not isinstance(text, str):
        raise DataError("Document text must be a string.")
    if not isinstance(max_chars, int) or not 200 <= max_chars <= 6000 or not 0 <= overlap < max_chars // 2:
        raise DataError("Invalid passage size or overlap.")
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            lower = start + max_chars // 2
            # Prefer a paragraph, then a sentence, then a word boundary.
            choices = [m.end() for m in re.finditer(r"\n\s*\n", text[lower:end])]
            if not choices:
                choices = [m.end() for m in re.finditer(r"[.!?]\s+", text[lower:end])]
            if not choices:
                choices = [m.end() for m in re.finditer(r"\s+", text[lower:end])]
            if choices:
                end = lower + choices[-1]
        if text[start:end].strip():
            yield start, end
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        # Don't start halfway through a word; retain all uncovered content.
        if next_start > start and next_start < end and text[next_start - 1].isalnum():
            boundary = re.search(r"\s+", text[next_start:end])
            if boundary:
                next_start += boundary.end()
        start = min(next_start, end)


class Corpus:
    """Each document has id, text, entity, publication and provenance metadata."""
    def __init__(self, documents, max_chars=1800, overlap=250):
        self.documents = []
        self.chunks = []
        ids = set()
        for supplied in documents:
            doc = dict(supplied)
            if not isinstance(doc.get("id"), str) or doc["id"] in ids or not isinstance(doc.get("text"), str):
                raise DataError("Corpus requires unique document IDs and string text.")
            ids.add(doc["id"])
            digest = hashlib.sha256(doc["text"].encode()).hexdigest()
            if doc.get("text_snapshot_hash") and digest != doc["text_snapshot_hash"]:
                raise DataError("Corpus document text hash does not match provenance.")
            doc["text_snapshot_hash"] = digest
            self.documents.append(doc)
            for start, end in chunk_spans(doc["text"], max_chars, overlap):
                chunk = dict(doc, text=doc["text"][start:end], document_id=doc["id"], start=start, end=end)
                chunk["id"] = stable_id("chunk_", [doc["id"], doc.get("snapshot_hash"), digest, CHUNK_VERSION, start, end])
                self.chunks.append(chunk)
        self.chunks.sort(key=lambda row: row["id"])
        # Bind source URLs, publication/filter fields and extraction provenance,
        # not just passage text; exported corpora must detect metadata tampering.
        metadata = [{key: value for key, value in doc.items() if key != "text"}
                    for doc in sorted(self.documents, key=lambda doc: doc["id"])]
        self.fingerprint = stable_id("corpus_", [CHUNK_VERSION, max_chars, overlap, metadata,
                                               [c["id"] for c in self.chunks]])
        self.max_chars, self.overlap = max_chars, overlap

    def eligible(self, filters=None):
        filters = filters or {}
        allowed = {"entity", "period_end", "cutoff", "forms", "document_ids"}
        if set(filters) - allowed:
            raise DataError("Unsupported retrieval metadata filter.")
        if filters.get("period_end"):
            parse_day(filters["period_end"])
        return [c for c in self.chunks
                if (not filters.get("entity") or c.get("entity") == filters["entity"])
                and (not filters.get("period_end") or c.get("document_report_date") == filters["period_end"])
                and (not filters.get("forms") or c.get("form") in filters["forms"])
                and (not filters.get("document_ids") or c["document_id"] in filters["document_ids"])
                and available_by(c.get("published_at"), filters.get("cutoff"))]


def bm25(chunks, query, k1=1.2, b=0.75):
    if not (math.isfinite(k1) and k1 > 0 and math.isfinite(b) and 0 <= b <= 1):
        raise DataError("Invalid BM25 parameters.")
    # A set's randomized iteration can perturb near-tied floating-point sums
    # across Python processes. Fix accumulation order as well as final ID ties.
    terms = sorted(set(tokenize(query)))
    if not terms:
        raise DataError("Query contains no searchable terms.")
    counts = [Counter(tokenize(chunk["text"])) for chunk in chunks]
    lengths = [sum(row.values()) for row in counts]
    n = len(chunks)
    if not n:
        return []
    avg = sum(lengths) / n or 1.0
    df = Counter(term for row in counts for term in row)
    ranked = []
    for chunk, frequencies, length in zip(chunks, counts, lengths):
        score = 0.0
        for term in terms:
            tf = frequencies.get(term, 0)
            if tf:
                # Positive Robertson/Sparck Jones IDF, as used in Lucene BM25.
                idf = math.log1p((n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        if score > 0:
            ranked.append((chunk, score))
    return sorted(ranked, key=lambda row: (-row[1], row[0]["id"]))


def _vector(values, dimension):
    if not isinstance(values, list) or len(values) != dimension or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise DataError("Embedding has an invalid dimension or non-finite value.")
    norm = math.sqrt(sum(x * x for x in values))
    if not math.isfinite(norm) or norm == 0:
        raise DataError("Embedding has zero or invalid norm.")
    return tuple(x / norm for x in values)


class FrozenEmbeddings:
    """Read declared encoder output, bound to the exact corpus and query texts.

    Provenance is recorded, not magically authenticated: callers must establish
    how an artifact was produced. This class cannot certify a claimed model name.
    """
    def __init__(self, artifact, corpus, expected_hash=None):
        if isinstance(artifact, (str, Path)):
            if Path(artifact).stat().st_size > 100_000_000:
                raise DataError("Embedding artifact exceeds 100 MB.")
            raw = Path(artifact).read_bytes()
            if len(raw) > 100_000_000:
                raise DataError("Embedding artifact exceeds 100 MB.")
            self.artifact_hash = hashlib.sha256(raw).hexdigest()
            if expected_hash and self.artifact_hash != expected_hash:
                raise DataError("Embedding artifact hash mismatch.")
            artifact = json.loads(raw)
        else:
            self.artifact_hash = hashlib.sha256(json.dumps(artifact, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if expected_hash and self.artifact_hash != expected_hash:
                raise DataError("Embedding artifact hash mismatch.")
        if not isinstance(artifact, dict):
            raise DataError("Embedding artifact must be a JSON object.")
        if artifact.get("schema_version") != 1 or artifact.get("corpus_fingerprint") != corpus.fingerprint:
            raise DataError("Embedding artifact belongs to a different corpus or schema.")
        self.model = artifact.get("encoder", {})
        if not isinstance(self.model, dict) or not all(isinstance(self.model.get(k), str) and self.model[k].strip() for k in ("model_id", "revision", "implementation", "provenance")):
            raise DataError("Embedding artifact must declare encoder identity, revision, implementation and provenance.")
        dimension = artifact.get("dimension")
        if isinstance(dimension, bool) or not isinstance(dimension, int) or not 2 <= dimension <= 65536:
            raise DataError("Invalid embedding dimension.")
        rows = artifact.get("chunks", {})
        if not isinstance(rows, dict) or set(rows) != {c["id"] for c in corpus.chunks}:
            raise DataError("Embedding artifact must cover the exact corpus chunks.")
        self.vectors = {key: _vector(value, dimension) for key, value in rows.items()}
        queries = artifact.get("queries", {})
        if not isinstance(queries, dict) or any(not isinstance(q, str) or not q for q in queries):
            raise DataError("Embedding queries must map exact query strings to vectors.")
        self.queries = {key: _vector(value, dimension) for key, value in queries.items()}

    def rank(self, chunks, query):
        if query not in self.queries:
            raise DataError("No genuine frozen embedding for this exact query; dense/hybrid retrieval is unavailable.")
        vector = self.queries[query]
        return sorted([(chunk, sum(a * b for a, b in zip(vector, self.vectors[chunk["id"]]))) for chunk in chunks],
                      key=lambda row: (-row[1], row[0]["id"]))


def complete_passage_starts(passages, documents, lookback, remaining):
    """Extend ranked chunks inside the same source without displacing any selected hit."""
    sources = {doc["id"]: doc for doc in documents}
    used = 0
    for row in passages:
        doc = sources[row["document_id"]]
        start = row["start"]
        row["source_window"] = {"ranked_start": start, "ranked_end": row["end"],
                                "more_text_before": start > 0, "more_text_after": row["end"] < len(doc["text"])}
        floor = max(0, start - min(remaining - used, lookback))
        if floor == start:
            continue
        # Search the preceding text only. A budget boundary is not evidence
        # of a new sentence or bullet.
        boundary = 0 if floor == 0 else None
        for match in re.finditer(r"\n\s*\n|[.!?]\s+|[。！？•‣]\s*", doc["text"][floor:start]):
            boundary = floor + match.end()
        if boundary is None or not doc["text"][boundary:start].strip():
            continue
        row["retrieved_chunk_id"] = row["id"]
        row["id"] = stable_id("chunk_", [doc["id"], doc.get("snapshot_hash"), doc["text_snapshot_hash"],
                                         CHUNK_VERSION, boundary, row["end"]])
        row["start"] = boundary
        row["source_window"]["more_text_before"] = boundary > 0
        row["text"] = doc["text"][boundary:row["end"]]
        used += start - boundary
    return used


def search(corpus, query, filters=None, mode="bm25", limit=5, embeddings=None, max_context_chars=9000, candidate_limit=100, *, complete_starts=False):
    if mode not in {"bm25", "dense", "hybrid"}:
        raise DataError("Unsupported retrieval mode.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise DataError("Invalid retrieval limit.")
    if not isinstance(max_context_chars, int) or not corpus.max_chars <= max_context_chars <= 600_000:
        raise DataError("Context budget must accommodate at least one full chunk.")
    if not isinstance(candidate_limit, int) or not limit <= candidate_limit <= 10_000:
        raise DataError("Candidate budget must be at least the result limit.")
    started = time.perf_counter()
    chunks = corpus.eligible(filters)
    if mode in {"dense", "hybrid"} and embeddings is None:
        raise DataError("Dense/hybrid requires genuine frozen embeddings for this corpus and query; none are configured.")
    lexical = bm25(chunks, query)[:candidate_limit] if mode in {"bm25", "hybrid"} else []
    dense = embeddings.rank(chunks, query)[:candidate_limit] if mode in {"dense", "hybrid"} else []
    if mode == "hybrid":
        # Equal-weight reciprocal rank fusion, k=60, no score scale assumptions.
        fused = {}
        for ranking in (lexical, dense):
            for rank, (chunk, _) in enumerate(ranking, 1):
                record = fused.setdefault(chunk["id"], [chunk, 0.0])
                record[1] += 1 / (60 + rank)
        ranked = sorted(fused.values(), key=lambda row: (-row[1], row[0]["id"]))
    else:
        ranked = lexical if mode == "bm25" else dense
    result, chars = [], 0
    for chunk, score in ranked:
        if len(result) == limit:
            break
        if chars + len(chunk["text"]) > max_context_chars:
            continue
        chars += len(chunk["text"])
        result.append(dict(chunk, retrieval_score=score, retrieval_method=mode))
    if complete_starts:
        chars += complete_passage_starts(result, corpus.documents, corpus.overlap, max_context_chars - chars)
    return {"passages": result, "mode": mode, "query": query, "filters": filters or {},
            "corpus_fingerprint": corpus.fingerprint, "eligible_chunks": len(chunks),
            "eligible_document_ids": sorted({c["document_id"] for c in chunks}),
            "ranking_latency_ms": (time.perf_counter() - started) * 1000,
            "context_chars": chars, "max_context_chars": max_context_chars,
            "limit": limit, "candidate_limit": candidate_limit,
            "scoring": "BM25 k1=1.2 b=0.75, filtered corpus statistics" if mode == "bm25" else
                       "cosine similarity" if mode == "dense" else "equal-weight reciprocal rank fusion k=60",
            "embedding_artifact_hash": embeddings.artifact_hash if embeddings else None}


def evaluate(corpus, cases, modes=("bm25", "dense", "hybrid"), embeddings=None, limit=5, max_context_chars=9000, repeats=3):
    """Fixed queries/filters/budgets. Gold spans must be contained, not guessed.

    Each case: id, query, filters, relevant=[{document_id,start,end}]. Recall is
    fraction of gold spans fully covered by a returned passage; MRR is first hit.
    Labelled negatives have relevant=[] and report empty-result diagnostics.
    Dense top-k is a candidate generator, not a relevance/answerability decision.
    """
    if type(repeats) is not int or not 1 <= repeats <= 30:
        raise DataError("Evaluation repeats must be between 1 and 30.")
    if not isinstance(cases, list) or not cases:
        raise DataError("Retrieval evaluation requires a nonempty case list.")
    if not isinstance(modes, (list, tuple)) or not modes or any(not isinstance(m, str) or m not in {"bm25", "dense", "hybrid"} for m in modes) or len(set(modes)) != len(modes):
        raise DataError("Choose distinct retrieval methods from bm25, dense and hybrid.")
    doc_map = {d["id"]: d for d in corpus.documents}
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"].strip() or case["id"] in ids:
            raise DataError("Evaluation cases require unique IDs.")
        ids.add(case["id"])
        if not isinstance(case.get("query"), str) or not case["query"].strip() or not isinstance(case.get("relevant"), list):
            raise DataError("Each evaluation case needs a nonempty query and a relevant-span list.")
        for gold in case["relevant"]:
            if (not isinstance(gold, dict) or not isinstance(gold.get("document_id"), str)
                    or gold["document_id"] not in doc_map or type(gold.get("start")) is not int
                    or type(gold.get("end")) is not int
                    or not 0 <= gold["start"] < gold["end"] <= len(doc_map[gold["document_id"]]["text"])):
                raise DataError("Gold span does not exist in the corpus.")
            if not any(c["document_id"] == gold["document_id"] and c["start"] <= gold["start"] and c["end"] >= gold["end"] for c in corpus.eligible(case.get("filters"))):
                raise DataError("Gold span is excluded by filters or not coverable by this chunking configuration.")
    result = {"corpus_fingerprint": corpus.fingerprint, "case_count": len(cases),
              "case_fingerprint": stable_id("queries_", cases), "limit": limit,
              "annotation_statuses": sorted({str(c.get("annotation_status", "unspecified")) for c in cases}),
              "annotation_provenance_verified": False,
              "notice": "Metrics use the supplied relevance labels; the evaluator does not establish their independence or correctness.",
              "negative_metric_notice": "negative_empty_accuracy is a legacy diagnostic name, not answer accuracy. Dense/hybrid rank eligible chunks without a calibrated relevance threshold; separate metadata-empty controls from semantic no-answer queries.",
              "max_context_chars": max_context_chars, "repeats": repeats,
              "latency_scope": "ranking and filtering only; corpus construction, vector encoding/loading and disk I/O excluded", "methods": {}}
    for mode in modes:
        rows, latencies = [], []
        try:
            for case in cases:
                for _ in range(repeats):
                    retrieved = search(corpus, case["query"], case.get("filters"), mode, limit, embeddings, max_context_chars)
                    latencies.append(retrieved["ranking_latency_ms"])
                passages = retrieved["passages"]
                covered = [any(p["document_id"] == g["document_id"] and p["start"] <= g["start"] and p["end"] >= g["end"] for p in passages) for g in case["relevant"]]
                hit_ranks = [i for i, p in enumerate(passages, 1) if any(p["document_id"] == g["document_id"] and p["start"] <= g["start"] and p["end"] >= g["end"] for g in case["relevant"])]
                rows.append({"id": case["id"], "recall": sum(covered) / len(covered) if covered else None,
                             "reciprocal_rank": 1 / min(hit_ranks) if hit_ranks else 0,
                             "negative_empty_correct": not passages if not covered else None,
                             "negative_kind": ("metadata_empty" if retrieved["eligible_chunks"] == 0 else "semantic_no_answer") if not covered else None,
                             "passage_ids": [p["id"] for p in passages], "context_chars": retrieved["context_chars"]})
            positives = [r for r in rows if r["recall"] is not None]
            negatives = [r for r in rows if r["negative_empty_correct"] is not None]
            result["methods"][mode] = {"status": "evaluated", "cases": rows,
                "macro_span_recall": statistics.mean(r["recall"] for r in positives) if positives else None,
                "mrr": statistics.mean(r["reciprocal_rank"] for r in positives) if positives else None,
                "negative_empty_accuracy": statistics.mean(r["negative_empty_correct"] for r in negatives) if negatives else None,
                "metadata_empty_return_rate": statistics.mean(r["negative_empty_correct"] for r in negatives if r["negative_kind"] == "metadata_empty") if any(r["negative_kind"] == "metadata_empty" for r in negatives) else None,
                "semantic_no_answer_empty_rate": statistics.mean(r["negative_empty_correct"] for r in negatives if r["negative_kind"] == "semantic_no_answer") if any(r["negative_kind"] == "semantic_no_answer" for r in negatives) else None,
                "latency_median_ms": statistics.median(latencies) if latencies else None,
                "latency_p95_ms": sorted(latencies)[max(0, math.ceil(.95 * len(latencies)) - 1)] if latencies else None}
        except DataError as exc:
            result["methods"][mode] = {"status": "unavailable", "reason": str(exc)}
    return result


def freeze_local_embeddings(corpus, queries, model_path, *, model_id, revision,
                            query_prefix="", passage_prefix="", batch_size=16):
    """Encode real local safetensors weights; no download or silent truncation."""
    from .runtime_embeddings import LocalSentenceEncoder, freeze_with_encoder
    encoder = LocalSentenceEncoder(model_path, model_id=model_id, revision=revision,
                                   query_prefix=query_prefix, passage_prefix=passage_prefix, batch_size=batch_size)
    return freeze_with_encoder(corpus, queries, encoder)
