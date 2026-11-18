"""Cumulative session resource counts, deduplicated by research episode."""
from __future__ import annotations

import math


def session_usage(episodes):
    # Callers may supply historical checkpoints; keep only each final snapshot.
    latest = {item["id"]: item for item in episodes}
    result = {"episodes": len(latest), "model_calls": 0, "tool_calls": 0, "planner_steps": 0,
              "active_seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    counts = {name: 0 for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
    for episode in latest.values():
        for field in ("model_calls", "tool_calls", "planner_steps", "active_seconds"):
            value = episode.get(field, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                result[field] += value
        usage = episode.get("usage", {})
        for field in counts:
            value = usage.get(field)
            if type(value) is int and value >= 0:
                result[field] += value
            reported = usage.get(field + "_reported_calls", 0)
            if type(reported) is int and reported >= 0:
                counts[field] += min(reported, episode.get("model_calls", 0))
    result["reported_calls"] = counts
    result["planner_steps_complete"] = all("planner_steps" in episode for episode in latest.values())
    result["token_usage_complete"] = all(counts[field] == result["model_calls"] for field in ("prompt_tokens", "completion_tokens"))
    result["token_counts_are_known_lower_bounds"] = not result["token_usage_complete"]
    result["monetary_cost"] = None
    return result
