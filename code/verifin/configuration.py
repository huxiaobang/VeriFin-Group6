"""Explicit optional data inputs shared by CLI, UI and offline experiments."""
from __future__ import annotations

import os
from pathlib import Path


def add_source_arguments(parser, *, live_adapter=True):
    parser.add_argument("--market-data", help="Validated provider price snapshot JSON; no network required")
    parser.add_argument("--retrieval-vectors", help="Frozen embeddings matched to the document corpus")
    parser.add_argument("--retrieval-encoder", help="Local encoder configuration for new queries and incremental document vectors")
    if live_adapter:
        parser.add_argument("--market-config", help="Alpha Vantage listing/configuration JSON; key comes only from ALPHAVANTAGE_API_KEY")


def source_flags(args):
    """Keep original source paths for restart. Provider keys stay in the environment."""
    return {key: str(Path(value).resolve())
            for key in ("market_data", "market_config", "retrieval_vectors", "retrieval_encoder")
            if (value := getattr(args, key, None))}


def tool_options(args):
    """Validate configuration before starting a user turn; performs no I/O to providers."""
    from .offline_cli import read_json
    result = {}
    market = getattr(args, "market_data", None)
    if market:
        from .market import PriceSnapshot
        path = Path(market).resolve()
        PriceSnapshot.from_path(path)
        result["market_data"] = path
    vectors = getattr(args, "retrieval_vectors", None)
    encoder = getattr(args, "retrieval_encoder", None)
    if vectors and encoder:
        raise ValueError("Choose --retrieval-vectors or --retrieval-encoder, not both")
    if encoder:
        config_path = Path(encoder).resolve()
        config = read_json(config_path)
        allowed = {"model_path", "model_id", "revision", "query_prefix", "passage_prefix", "batch_size", "chunk_chars", "chunk_overlap"}
        if not isinstance(config, dict) or set(config) - allowed or not {"model_path", "model_id", "revision"} <= set(config):
            raise ValueError("Encoder config requires model_path/model_id/revision and explicit preprocessing only")
        for field in ("model_path", "model_id", "revision"):
            if not isinstance(config[field], str) or not config[field].strip():
                raise ValueError("Encoder model_path/model_id/revision must be nonempty strings")
        if any(not isinstance(config.get(k, ""), str) for k in ("query_prefix", "passage_prefix")):
            raise ValueError("Encoder prefixes must be strings")
        if type(config.get("batch_size", 16)) is not int or not 1 <= config.get("batch_size", 16) <= 128:
            raise ValueError("Encoder batch size must be between 1 and 128")
        config["model_path"] = str((config_path.parent / config["model_path"]).resolve())
        if not Path(config["model_path"]).is_dir():
            raise ValueError("Encoder weights directory does not exist")
        chars, overlap = config.get("chunk_chars", 600), config.get("chunk_overlap", 80)
        if type(chars) is not int or type(overlap) is not int or not 200 <= chars <= 6000 or not 0 <= overlap < chars // 2:
            raise ValueError("Invalid encoder chunk size/overlap")
        config.update(chunk_chars=chars, chunk_overlap=overlap)
        result["retrieval_encoder"] = config
    if vectors:
        path = Path(vectors).resolve()
        if not isinstance(read_json(path), dict):
            raise ValueError("Frozen embeddings must be a JSON object")
        # Corpus binding is checked by FrozenEmbeddings at retrieval time.
        result["retrieval_vectors"] = path
    config_path = getattr(args, "market_config", None)
    if config_path:
        from .market import AlphaVantageDailyAdapter
        config = read_json(config_path)
        if not isinstance(config, dict) or set(config) - {"instruments", "outputsize", "allow_premium"}:
            raise ValueError("Market config accepts only instruments, outputsize and allow_premium; credentials belong in the environment")
        if "instruments" not in config or not isinstance(config["instruments"], list) or not config["instruments"]:
            raise ValueError("Market config requires explicit listing identities")
        if type(config.get("allow_premium", False)) is not bool:
            raise ValueError("allow_premium must be true or false")
        result["market_adapter"] = AlphaVantageDailyAdapter(
            api_key=os.environ.get("ALPHAVANTAGE_API_KEY"), allow_network=bool(getattr(args, "live", False)), **config)
    return result
