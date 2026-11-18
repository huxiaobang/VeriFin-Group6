"""Optional local Qwen reranking, kept separate from the released agent policy.

Scores express query/passage relevance, not factual truth or answerability.
The scorer never generates text, downloads weights, or loads custom model code.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time

from .data import DataError, stable_id

MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
WEIGHTS_SHA256 = "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"
INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. '
          'Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def select_reranked(passages, scores, *, limit=5, max_context_chars=3000):
    """Preserve exact evidence identities/offsets and apply the final budget once."""
    if type(limit) is not int or limit < 1 or type(max_context_chars) is not int or max_context_chars < 1:
        raise DataError("Reranking requires positive integer result and context budgets.")
    if not isinstance(passages, list) or not isinstance(scores, list) or len(passages) != len(scores):
        raise DataError("Reranker must return one score per candidate.")
    ids = set()
    for passage, score in zip(passages, scores):
        if (not isinstance(passage, dict) or not isinstance(passage.get("id"), str)
                or not passage["id"] or passage["id"] in ids or not isinstance(passage.get("text"), str)):
            raise DataError("Reranker candidates require unique passage IDs and text.")
        ids.add(passage["id"])
        if type(score) not in (float, int) or not math.isfinite(score):
            raise DataError("Reranker scores must be finite numbers.")
    ranked = sorted(zip(passages, scores), key=lambda row: (-row[1], row[0]["id"]))
    selected, used = [], 0
    for passage, score in ranked:
        if len(selected) == limit:
            break
        if used + len(passage["text"]) > max_context_chars:
            continue
        used += len(passage["text"])
        selected.append(dict(passage, reranker_score=score, reranker_rank=len(selected) + 1))
    return selected


class LocalQwenReranker:
    """Official yes/no logit scoring; CPU float32; full inputs, no truncation."""

    def __init__(self, model_path, *, revision=REVISION, batch_size=4, instruction=INSTRUCTION):
        if type(batch_size) is not int or not 1 <= batch_size <= 64:
            raise DataError("Reranker batch size must be between 1 and 64.")
        if not isinstance(instruction, str) or not instruction.strip() or not isinstance(revision, str) or not revision.strip():
            raise DataError("Declare a nonempty instruction and model revision.")
        if revision != REVISION:
            raise DataError("This scorer is pinned to the audited Qwen3-Reranker-0.6B revision.")
        directory = Path(model_path).resolve()
        if not directory.is_dir() or not (directory / "config.json").is_file():
            raise DataError("An existing complete local Qwen model directory is required.")
        config = json.loads((directory / "config.json").read_text())
        if config.get("model_type") != "qwen3" or config.get("architectures") != ["Qwen3ForCausalLM"]:
            raise DataError("This scorer supports Qwen3ForCausalLM reranker weights only.")
        manifest = []
        for path in sorted(directory.rglob("*")):
            if {".cache", ".git", ".range-parts"}.intersection(path.relative_to(directory).parts) or path.name.endswith(".download.json"):
                continue
            if not path.resolve().is_relative_to(directory):
                raise DataError("Model files cannot escape the model directory.")
            if not path.is_file():
                continue
            if path.suffix in {".bin", ".pt", ".pth", ".pickle", ".pkl"}:
                raise DataError("Only local safetensors weights are accepted.")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1048576), b""):
                    digest.update(block)
            manifest.append({"path": str(path.relative_to(directory)), "sha256": digest.hexdigest()})
        if not any(row["path"].endswith(".safetensors") for row in manifest):
            raise DataError("Local safetensors weights are missing.")
        if {r["path"]: r["sha256"] for r in manifest}.get("model.safetensors") != WEIGHTS_SHA256:
            raise DataError("Weights do not match the official pinned Qwen3-Reranker-0.6B SHA256.")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise DataError("Install optional torch/transformers dependencies to use the reranker.") from exc
        started = time.perf_counter()
        kwargs = dict(local_files_only=True, trust_remote_code=False, token=False)
        self.tokenizer = AutoTokenizer.from_pretrained(str(directory), padding_side="left", **kwargs)
        self.model = AutoModelForCausalLM.from_pretrained(str(directory), use_safetensors=True,
                                                       dtype=torch.float32, **kwargs).to("cpu").eval()
        self.load_seconds = time.perf_counter() - started
        self.max_length = int(self.model.config.max_position_embeddings)
        self.prefix_ids = self.tokenizer.encode(PREFIX, add_special_tokens=False)
        self.suffix_ids = self.tokenizer.encode(SUFFIX, add_special_tokens=False)
        self.no_id = self.tokenizer.encode("no", add_special_tokens=False)
        self.yes_id = self.tokenizer.encode("yes", add_special_tokens=False)
        if len(self.no_id) != 1 or len(self.yes_id) != 1 or self.no_id == self.yes_id:
            raise DataError("Reranker yes/no labels must be distinct single tokens.")
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.batch_size, self.instruction = batch_size, instruction
        self.last_token_counts = []
        self.metadata = {"model_id": MODEL_ID, "revision": revision, "instruction": instruction,
                         "scoring": "softmax over official final-token [no, yes] logits; relevance only; not calibrated",
                         "prompt_sha256": hashlib.sha256((PREFIX + instruction + SUFFIX).encode()).hexdigest(),
                         "compute_dtype": "float32", "device": "cpu", "batch_size": batch_size,
                         "generation": False, "network": False, "truncation": False,
                         "official_weights_sha256_verified": True,
                         "files": manifest, "files_fingerprint": stable_id("reranker_", manifest)}

    def score(self, query, documents):
        if not isinstance(query, str) or not query.strip() or not isinstance(documents, list) or any(
                not isinstance(text, str) or not text.strip() for text in documents):
            raise DataError("Reranking requires a nonempty query and passage strings.")
        if not documents:
            self.last_token_counts = []
            return []
        import torch
        texts = [f"<Instruct>: {self.instruction}\n<Query>: {query}\n<Document>: {text}" for text in documents]
        rows = self.tokenizer(texts, add_special_tokens=False, truncation=False, padding=False)["input_ids"]
        rows = [self.prefix_ids + row + self.suffix_ids for row in rows]
        self.last_token_counts = [len(row) for row in rows]
        if max(self.last_token_counts) > self.max_length:
            raise DataError("Reranker input exceeds model context; refusing silent truncation.")
        scores = []
        with torch.inference_mode():
            for start in range(0, len(rows), self.batch_size):
                batch = self.tokenizer.pad({"input_ids": rows[start:start + self.batch_size]},
                                           padding=True, return_tensors="pt")
                # Only final-position logits are needed; no decoding or KV cache.
                logits = self.model(**batch, use_cache=False, logits_to_keep=1).logits[:, -1, :]
                labels = logits[:, [self.no_id[0], self.yes_id[0]]]
                scores.extend(torch.softmax(labels.float(), dim=-1)[:, 1].tolist())
        if len(scores) != len(documents) or any(not math.isfinite(s) for s in scores):
            raise DataError("Reranker returned invalid scores.")
        return scores
