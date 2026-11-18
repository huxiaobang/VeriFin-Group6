# E3 retrieval implementation and evaluation

`search_documents` now uses real BM25 over deterministic chunks. It can also rank genuine frozen dense vectors by cosine similarity and combine BM25/dense rankings with equal-weight reciprocal rank fusion (RRF). Missing vectors produce an explicit unavailable result; the implementation never relabels lexical results or random vectors as dense retrieval.

## Corpus, filters and evidence

`verifin.retrieval.load_replay_corpus(data_dir)` is the public fixed-corpus loader. It verifies both original source bytes and extracted text against the replay manifest before constructing the corpus. The supplied corpus contains four issuer documents and 55 default chunks from two companies; this is finite fixture coverage, not a company allowlist. `Corpus(documents, max_chars=1800, overlap=250)` also accepts caller-supplied documents for broader frozen corpora. Each document needs a unique `id` and `text`; production provenance should additionally include `entity`, `published_at`, `source_url`, `snapshot_hash`, extraction method, and the text hash. The standard loader provides these fields.

Chunks are exact slices of the extracted Unicode text. They prefer paragraph boundaries, then sentence boundaries, then whitespace; the maximum size is 1,800 characters with approximately 250 characters of overlap. Chunk IDs bind the source identity, raw/text hashes, chunker version and offsets. The corpus fingerprint binds all document metadata and chunk IDs, including publication dates and source URLs. Reordering input documents does not change it. Modified text, provenance, filters or chunking therefore invalidate a frozen vector artifact.

`ToolRegistry.document_corpus()` additionally includes verified, already discovered SEC HTML in that registry's durable cache. It makes no HTTP request, cannot silently discover a new filing, and restores cached history metadata across processes. Fixed E3 evaluation deliberately uses `load_replay_corpus` or an exported frozen corpus so concurrent research sessions cannot silently expand its corpus.

Filters are `entity`, `period_end`, `cutoff`, `forms`, and `document_ids`. They apply before retrieval and BM25 corpus statistics. `period_end` means **document report date**, not the fiscal period of every statement in the document. Returned document evidence leaves financial `period_start` and `period_end` null and retains `document_report_date`. Date-only publication metadata is conservatively unavailable for earlier intraday cutoffs on the same UTC day. Missing publication metadata cannot pass a specified cutoff.

Retrieval is not entailment: a high-scoring passage can be boilerplate, a table fragment, a comparison with another period, or an irrelevant use of a word. The Critic must still assess the actual claim and surrounding text. The inherited web snapshots were captured after the reported events; publication-date filters do not prove that every historical page byte was identical at the cutoff.

## Ranking and budgets

BM25 uses Unicode word tokens, case folding, a small explicit English stop-word list, no stemming, and retained numbers. The implementation has term-frequency saturation, document-length normalization (`k1=1.2`, `b=0.75`) and positive IDF `log(1+(N-df+0.5)/(df+0.5))`. These defaults and IDF match the [Apache Lucene BM25 documentation](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html); the underlying method is discussed by [Robertson and Zaragoza](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf). No exact-query phrase bonus or answer-specific rule is used.

Dense uses normalized vectors and cosine similarity. Hybrid uses `sum(1/(60+rank))` across the two component rankings; a missing lexical match contributes no lexical rank. Ties break by stable chunk ID. The default candidate cap is 100 **per component**; hybrid performs two rankings and is not represented as having identical computational cost to one ranking. The same filtered corpus, final top-k and returned-character budget apply to all methods. Hybrid's extra computation is included in measured ranking latency.

The tool defaults to five passages and at most 9,000 returned characters. Chunks that would exceed the context budget are skipped rather than silently truncated. The standalone `search` API exposes `candidate_limit`, `limit` and `max_context_chars`; the tool schema bounds output to ten passages and 18,000 characters. The returned metadata includes actual context characters, eligible chunk/document counts, scoring definition, corpus fingerprint, vector-artifact hash and ranking latency.

## Frozen vector format and local generation

`FrozenEmbeddings(path_or_dict, corpus, expected_hash=None)` accepts this schema:

```json
{
  "schema_version": 1,
  "corpus_fingerprint": "the actual corpus fingerprint",
  "encoder": {
    "model_id": "the actual encoder identity",
    "revision": "the actual model revision",
    "implementation": "the actual encoder/runtime and pooling method",
    "provenance": "how these vectors were produced"
  },
  "dimension": 384,
  "chunks": {"each_actual_chunk_id": ["actual numeric embedding values"]},
  "queries": {"each exact query string": ["actual numeric embedding values"]}
}
```

The strings inside the arrays above are placeholders, not valid embeddings. Every chunk must be covered exactly; every vector must have the declared dimension, finite values and nonzero norm. An unknown query fails rather than silently inventing an embedding. Provenance declarations are recorded, but merely naming a model cannot authenticate an artifact's origin. Use a trusted build procedure and preserve its file hash.

`freeze_local_embeddings(corpus, queries, model_path, *, model_id, revision, query_prefix='', passage_prefix='', batch_size=16)` produces a real artifact using an explicitly supplied local Sentence Transformers model. It requires separately installed `sentence-transformers` and complete local safetensors weights. It loads on CPU with `local_files_only=True`, `trust_remote_code=False`, `token=False`, and `use_safetensors=True`; it does not download weights or call an embedding/LLM API. Custom modules, pickle-style weights and paths escaping the supplied directory are refused. Model files are hashed; prefixes, runtime version, load/encoding time and maximum sequence length are recorded. Overlong inputs fail instead of being silently truncated. These controls follow the [official Sentence Transformers API](https://sbert.net/docs/package_reference/sentence_transformer/model.html), checked on 2026-10-15.

No neural runtime or model weights were available during the implementation session. The local-builder tests mock the encoder only to verify configuration, shape, provenance and refusal behavior. They are **not** a claim of neural retrieval quality. Dense and hybrid remain unavailable until a genuine artifact is supplied or a real local build succeeds.

## CLI and Python use

From the project directory, with the project's Python runtime:

```bash
python -m verifin --data-dir data retrieval corpus --output /tmp/verifin-corpus.json
python -m verifin --data-dir data retrieval search --query "Microsoft Cloud gross margin" --mode bm25
python -m verifin --data-dir data retrieval evaluate \
  --cases data/evaluation/e3_development_queries.json \
  --output /tmp/verifin-e3-development.json
python -m verifin --data-dir data retrieval build-embeddings \
  --corpus /tmp/verifin-corpus.json --queries /path/to/query-strings.json \
  --model-path /path/to/local-sentence-transformer \
  --model-id actual-model-id --revision actual-revision \
  --output /tmp/verifin-embeddings.json
```

`--queries` is a JSON list of exact strings. The search/evaluate commands accept `--embeddings`, `--corpus`, `--limit` and `--max-context-chars`. Search accepts a `--filters` JSON file. `ToolRegistry(..., retrieval_vectors=path)` and the research CLI's `--retrieval-vectors` expose the same artifact to the agent. An artifact built for the fixed replay corpus will intentionally fail if the active tool corpus also contains newly cached filing documents; freeze that expanded corpus or use a separate replay cache for an evaluation.

Python evaluation is `evaluate(corpus, cases, modes=('bm25','dense','hybrid'), embeddings=None, limit=5, max_context_chars=9000, repeats=3)`. Each case has `id`, `query`, optional `filters`, and `relevant=[{'document_id': ..., 'start': ..., 'end': ...}]`. Gold spans must exist, pass the filters, and be fully coverable by a chunk. A retrieved passage counts as a hit only if it fully contains the annotated span. The evaluator reports macro span recall, MRR, per-case passage IDs, context usage, median/p95 ranking latency, and empty-result accuracy for explicitly labelled negatives. Unavailable methods get a reason, not a score of zero or a fabricated result.

The same case list, filters, corpus fingerprint, top-k and character budget are used across methods. Query/corpus fingerprints accompany the output. Ranking latency includes filtering and ranking; it excludes disk I/O, corpus construction, model loading and embedding generation. Build timing is reported separately. This is not end-to-end agent latency, answer faithfulness or a statistical significance test. No warm-up exclusion or paired bootstrap analysis is silently applied.

## Development fixture and formal E3 boundary

`data/evaluation/e3_development_queries.json` contains 10 manually selected positive source spans and four engineering negatives (publication cutoff, report-date mismatch, unrelated terms). Every case is labelled `development_example_not_independent_gold`. Some alternative relevant passages are not exhaustively annotated. It validates the executable pipeline and filters; it is not a held-out corpus, independently adjudicated gold standard, or publishable evidence that BM25 is better than dense/hybrid.

Before a formal E3 run, freeze an independently annotated query/relevance set and the corpus, record the actual encoder and preprocessing choices, and run all available conditions under the same output budgets. Do not replace missing dense measurements with the synthetic unit-test vectors. Tests cover BM25's numerical formula, filters, source offsets, hash mismatches, provenance fingerprints, cosine/RRF mechanics, context limits and safe local-builder failures.
