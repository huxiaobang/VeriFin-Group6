"""Real local query encoding and incremental passage caching; no network access."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import time

from .data import DataError, stable_id
from .retrieval import FrozenEmbeddings, _vector


class LocalSentenceEncoder:
    def __init__(self, model_path, *, model_id, revision, query_prefix="", passage_prefix="", batch_size=16, checkpoint=None):
        check = checkpoint or (lambda: None)
        check()
        if not all(isinstance(x, str) and x.strip() for x in (model_id, revision)):
            raise DataError("Declare the local encoder's model identity and revision.")
        if not isinstance(query_prefix, str) or not isinstance(passage_prefix, str):
            raise DataError("Encoder prefixes must be strings.")
        if type(batch_size) is not int or not 1 <= batch_size <= 128:
            raise DataError("Encoder batch size must be between 1 and 128.")
        directory = Path(model_path).resolve()
        if not directory.is_dir():
            raise DataError("An existing local model directory is required; Hub model IDs are not downloaded.")
        modules_path = directory / "modules.json"
        if not modules_path.is_file():
            raise DataError("A complete local Sentence Transformers model with modules.json is required.")
        modules = json.loads(modules_path.read_text())
        if not isinstance(modules, list) or not modules or any(not isinstance(m, dict) or
                not str(m.get("type", "")).startswith("sentence_transformers.models.") for m in modules):
            raise DataError("Only installed standard sentence_transformers model modules are accepted; custom model code is disabled.")
        if any(not (directory / str(m.get("path", ""))).resolve().is_relative_to(directory) for m in modules):
            raise DataError("Local model module paths cannot escape the model directory.")
        manifest = []
        for path in sorted(directory.rglob("*")):
            check()
            # Hub download locks/metadata are not model inputs and change on a
            # resumed download. Exclude them from the reproducible model identity.
            if ".cache" in path.relative_to(directory).parts or ".git" in path.relative_to(directory).parts:
                continue
            if not path.resolve().is_relative_to(directory):
                raise DataError("Local model files cannot escape the model directory.")
            if not path.is_file():
                continue
            if path.suffix in {".bin", ".pt", ".pth", ".pkl", ".pickle"}:
                raise DataError("Use local safetensors weights; pickle-style model files are not loaded.")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1_048_576), b""):
                    check()
                    digest.update(block)
            manifest.append({"path": str(path.relative_to(directory)), "sha256": digest.hexdigest()})
        if not any(row["path"].endswith(".safetensors") for row in manifest):
            raise DataError("Local safetensors model weights are missing.")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise DataError("Optional sentence-transformers runtime is not installed. No embedding result was generated.") from exc
        started = time.perf_counter()
        self.model = SentenceTransformer(str(directory), device="cpu", local_files_only=True,
                                        trust_remote_code=False, token=False,
                                        model_kwargs={"use_safetensors": True, "dtype": "float32"})
        check()
        self.load_seconds = time.perf_counter() - started
        self.tokenizer = getattr(self.model, "tokenizer", None)
        self.max_length = getattr(self.model, "max_seq_length", None)
        if self.tokenizer is None or type(self.max_length) is not int or self.max_length <= 0:
            raise DataError("Encoder must expose its tokenizer and maximum sequence length.")
        try:
            version = importlib.metadata.version("sentence-transformers")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        self.metadata = {"model_id": model_id, "revision": revision,
                         "implementation": "sentence-transformers " + version + "; CPU float32; cosine normalization",
                         "provenance": "Explicit local safetensors directory; local_files_only=True; trust_remote_code=False; token=False",
                         "query_prefix": query_prefix, "passage_prefix": passage_prefix,
                         "files": manifest, "files_fingerprint": stable_id("model_", manifest)}
        self.batch_size = batch_size

    def encode(self, texts, kind):
        if kind not in {"query", "passage"} or not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise DataError("Encoder requires nonempty text and an explicit query/passage kind.")
        values = [self.metadata[kind + "_prefix"] + t for t in texts]
        tokens = self.tokenizer(values, truncation=False, add_special_tokens=True)
        if any(len(row) > self.max_length for row in tokens["input_ids"]):
            raise DataError("A passage/query exceeds the local encoder token limit; use shorter corpus chunks instead of silently truncating.")
        rows = self.model.encode(values, batch_size=self.batch_size, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=False, prompt="").tolist()
        if len(rows) != len(texts) or not rows:
            raise DataError("Encoder returned the wrong number of vectors.")
        dimension = len(rows[0])
        return [list(_vector(row, dimension)) for row in rows]


def freeze_with_encoder(corpus, queries, encoder):
    if not isinstance(queries, list) or not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise DataError("Embedding queries must be a nonempty list of exact query strings.")
    if not corpus.chunks:
        raise DataError("Cannot encode an empty passage corpus.")
    started = time.perf_counter()
    queries = list(dict.fromkeys(queries))
    chunks = encoder.encode([c["text"] for c in corpus.chunks], "passage")
    questions = encoder.encode(queries, "query")
    artifact = {"schema_version": 1, "corpus_fingerprint": corpus.fingerprint,
                "dimension": len(chunks[0]), "encoder": encoder.metadata,
                "chunks": {c["id"]: v for c, v in zip(corpus.chunks, chunks)},
                "queries": dict(zip(queries, questions)),
                "build": {"model_load_seconds": encoder.load_seconds,
                          "encoding_seconds": time.perf_counter() - started,
                          "chunk_count": len(chunks), "query_count": len(questions), "max_seq_length": encoder.max_length}}
    FrozenEmbeddings(artifact, corpus)
    return artifact


class RuntimeEmbeddings:
    """Cache by model/files/preprocessing/text; bind every result to the live corpus.

    Encoder is injectable for contract tests. A fake encoder is never created by
    production configuration. Each query artifact is immutable and exportable.
    """
    def __init__(self, encoder, cache_dir):
        self.encoder = encoder
        self.cache = Path(cache_dir)
        self.identity = stable_id("encoder_", encoder.metadata)
        self.lock = threading.Lock()
        self.last_stats = {}

    def _vectors(self, texts, kind, check):
        unique = list(dict.fromkeys(texts))
        keys = {t: stable_id("vector_", [self.identity, kind, t]) for t in unique}
        result, missing = {}, []
        for t in unique:
            check()
            path = self.cache / (keys[t] + ".json")
            if path.is_file():
                row = json.loads(path.read_text())
                if row.get("key") != keys[t] or not isinstance(row.get("vector"), list):
                    raise DataError("Embedding cache identity/shape mismatch.")
                _vector(row["vector"], len(row["vector"]))  # Validate, without renormalizing saved floats.
                result[t] = row["vector"]
                self.last_stats["cache_hits"] += 1
            else:
                missing.append(t)
        for offset in range(0, len(missing), self.encoder.batch_size):
            check()
            batch = missing[offset:offset + self.encoder.batch_size]
            vectors = self.encoder.encode(batch, kind)
            check()
            if len(vectors) != len(batch):
                raise DataError("Encoder returned the wrong number of vectors.")
            for t, vector in zip(batch, vectors):
                vector = list(_vector(vector, len(vector)))
                result[t] = vector
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache, delete=False) as f:
                    json.dump({"key": keys[t], "vector": vector}, f, allow_nan=False)
                    temp = f.name
                os.replace(temp, self.cache / (keys[t] + ".json"))
            self.last_stats["encoded_queries" if kind == "query" else "encoded_passages"] += len(batch)
        return [result[t] for t in texts]

    @contextmanager
    def _acquire(self, check):
        # A waiting request must still observe cancellation / its deadline.
        import fcntl
        while not self.lock.acquire(blocking=False):
            check()
            time.sleep(.02)
        try:
            with (self.cache / ".cache.lock").open("a") as lock:
                while True:
                    check()
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(.02)
                yield
        finally:
            self.lock.release()

    def for_query(self, corpus, query, *, checkpoint=None):
        if not isinstance(query, str) or not query.strip() or not corpus.chunks:
            raise DataError("A nonempty query and passage corpus are required.")
        check = checkpoint or (lambda: None)
        check()
        self.cache.mkdir(parents=True, exist_ok=True)
        with self._acquire(check):
            self.last_stats = {"cache_hits": 0, "encoded_passages": 0, "encoded_queries": 0, "network_requests": 0}
            started = time.perf_counter()
            passages = self._vectors([c["text"] for c in corpus.chunks], "passage", check)
            questions = self._vectors([query], "query", check)
            artifact = {"schema_version": 1, "corpus_fingerprint": corpus.fingerprint,
                        "dimension": len(passages[0]), "encoder": self.encoder.metadata,
                        "chunks": {c["id"]: v for c, v in zip(corpus.chunks, passages)},
                        "queries": {query: questions[0]}}
            frozen = FrozenEmbeddings(artifact, corpus)
            check()
            target = self.cache / (frozen.artifact_hash + ".artifact.json")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache, delete=False) as f:
                json.dump(artifact, f, sort_keys=True, allow_nan=False)
                temp = f.name
            os.replace(temp, target)
            self.last_stats.update(encoding_seconds=time.perf_counter() - started,
                                   artifact_path=str(target.resolve()), corpus_fingerprint=corpus.fingerprint)
            return frozen
