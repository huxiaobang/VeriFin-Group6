"""Request identity shared by the engine, CLI and local web application."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re

from .data import cutoff_time
from .scope import after_cutoff_prefix, stated_cutoff, validate_scope


def research_cutoff(message, as_of):
    if not isinstance(message, str) or not message.strip():
        raise ValueError("A nonempty research request or clarification is required.")
    stated = stated_cutoff(message)
    day_only_prefix = stated and after_cutoff_prefix(message) != message
    if as_of is not None:
        validate_scope({"cutoff": as_of})
        # A day-only lead-in leaves the timezone and intraday cutoff to the explicit option.
        if stated and cutoff_time(as_of) != cutoff_time(stated) and not (day_only_prefix and as_of[:10] <= stated):
            raise ValueError("Message research time conflicts with the explicit as_of cutoff")
    return stated, day_only_prefix


def lookup_submission(store, session_id, request_id, message=None, as_of=None):
    if request_id is None:
        return None
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
        raise ValueError("Request ID must contain 1–128 letters, digits, periods, underscores, colons or hyphens.")
    submission = store.get_submission(session_id, request_id)
    if submission:
        if message is not None and message != submission["message"]:
            raise ValueError("This request ID is already bound to a different question.")
        if as_of is not None and cutoff_time(as_of) != cutoff_time(submission["as_of"]):
            raise ValueError("This request ID is already bound to a different cutoff.")
    return submission


def saved_response(store, session_id, submission):
    episode = submission["episode"] if submission else None
    if episode is None:
        return None
    result = {"status": episode["status"], "episode": episode}
    if submission["report_version"] is None:
        result["question"] = episode["question"]
    else:
        result["report"] = store.get_report(session_id, submission["report_version"])
    return result


def reserve_submission(store, session_id, request_id, *, message, as_of, budget, max_context_chars,
                       execution_backend, offline, reset_cancel, execution_options=None, model_identity=None):
    """Reserve and initialize cancellation while the caller owns the session lock."""
    if max_context_chars is not None and (type(max_context_chars) is not int or not 1 <= max_context_chars <= 2**63 - 1):
        raise ValueError("Context character limit must be a positive SQLite integer.")
    latest = store.get_submission(session_id)
    if latest and latest["episode_id"] is None:
        raise ValueError(f"Resume pending request {latest['request_id']} before starting another request.")
    with store.atomic_step():
        if request_id is not None:
            store.reserve_submission(session_id, request_id, {
                "message": message, "as_of": as_of, "budget": budget,
                "max_context_chars": max_context_chars, "execution_backend": execution_backend, "offline": offline,
                "execution_options": execution_options or {},
                **({"model_identity": model_identity} if model_identity is not None else {}),
            })
        if reset_cancel:
            store.clear_cancel(session_id)


def submission_status(submission):
    """Return the public receipt without replay inputs or local settings."""
    episode = submission["episode"]
    status = episode["status"] if episode else "running" if submission["episode_id"] else "pending"
    return {
        **{key: submission[key] for key in ("request_id", "episode_id", "report_version")},
        "status": status,
        **({"question": episode["question"]} if episode and episode.get("question") else {}),
        **({"stop_reason": episode["stop_reason"]} if episode and episode.get("stop_reason") else {}),
    }


def model_binding(model):
    """Record routing and response settings without credentials."""
    if model is None:
        return {"mode": "offline_tools_only"}
    fields = ("mode", "model", "api_mode", "reasoning_effort", "critic_reasoning_effort",
              "service_tier", "enable_thinking", "max_output_tokens", "researcher_max_output_tokens",
              "critic_max_output_tokens", "max_context_chars", "method_pack_sha256", "planner_view",
              "focus_evidence", "cache_shared_tools", "openrouter_prompt_cache")
    identity = {field: value for field in fields
                if type(value := getattr(model, field, None)) in {str, int, bool}}
    endpoint = getattr(model, "base_url", None)
    if isinstance(endpoint, str):
        identity["endpoint_sha256"] = sha256(endpoint.encode()).hexdigest()
    spend_guard = getattr(model, "spend_guard", None)
    ledger, cap = getattr(spend_guard, "path", None), getattr(spend_guard, "cap", None)
    if isinstance(ledger, (str, Path)) and type(cap) is int:
        identity["spend_policy"] = {"ledger_sha256": sha256(str(Path(ledger).resolve()).encode()).hexdigest(),
                                    "cap_nanodollars": cap}
    return identity


def check_model_binding(submission, model):
    if submission["offline"] != (model is None):
        raise ValueError("Resume requires the original online or offline research mode.")
    if "model_identity" in submission and model_binding(model) != submission["model_identity"]:
        raise ValueError("Resume requires the original model, provider route and response settings.")
