"""Validated, explicit scope edits. Does not interpret a natural-language request."""
from __future__ import annotations

from datetime import date, datetime
import json
import re


_LEADING_CUTOFF = re.compile(
    r"^\s*(?:研究时点(?:固定)?为|研究截止时间为|信息截止时间为|"
    r"(?:research\s+)?as\s+of\s+)"
    r"\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2}))"
    r"(?=$|[\s,，。；;（(])", re.IGNORECASE)

_AMENDED_CUTOFF = re.compile(
    r"(?:研究|信息|资料)(?:的)?(?:截止(?:日(?:期)?|时点|时间)?|时点)\s*(?:改(?:为|到)|调整为|设为|固定为|为|[:：])\s*"
    r"|\b(?:change|set|move)\s+(?:the\s+)?(?:research |information )?(?:cutoff|as[- ]of date)\s+to\s+", re.I)
_CUTOFF_VALUE = re.compile(r"(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2}))?)"
                           r"(?=$|[\s,，。；;（(])", re.I)
_AS_OF_DAY = re.compile(r"^\s*截至\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*[,，。；;]\s*")


def after_cutoff_prefix(message):
    return _AS_OF_DAY.sub("", message, count=1)

def stated_cutoff(message):
    """Recognize a stated information cutoff separately from a financial reporting period."""
    chinese_day = _AS_OF_DAY.match(message)
    if chinese_day:
        return date(*(int(v) for v in chinese_day.groups())).isoformat()
    match = _LEADING_CUTOFF.match(message)
    if match:
        return validate_scope({"cutoff":match.group(1)})["cutoff"]
    explicit = _AMENDED_CUTOFF.search(message)
    if explicit and (match := _CUTOFF_VALUE.match(message[explicit.end():])):
        return validate_scope({"cutoff":match.group(1)})["cutoff"]
    return None


def validate_scope(scope):
    if not isinstance(scope, dict):
        raise ValueError("Scope must be a JSON object")
    # Round trip also excludes non-JSON values and nonfinite numbers.
    clean = json.loads(json.dumps(scope, ensure_ascii=False, allow_nan=False))
    if len(json.dumps(clean, ensure_ascii=False)) > 30000:
        raise ValueError("Scope is too large")
    for field in ("entities", "metrics"):
        values = clean.get(field, [])
        if not isinstance(values, list) or len(values) > 30 or any(
                not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values):
            raise ValueError(f"{field} must contain up to 30 nonempty strings")
        if len(set(values)) != len(values):
            raise ValueError(f"{field} must not contain duplicates")
    for field in ("period_start", "period_end"):
        value = clean.get(field)
        if value is not None:
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError(f"{field} must be an exact YYYY-MM-DD date or null")
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{field} is not a valid date") from None
            if parsed.isoformat() != value:
                raise ValueError(f"{field} must be an exact YYYY-MM-DD date")
    if clean.get("period_start") and clean.get("period_end") and clean["period_start"] > clean["period_end"]:
        raise ValueError("Period start must not be after period end")
    cutoff = clean.get("cutoff")
    if cutoff is not None:
        if not isinstance(cutoff, str):
            raise ValueError("Cutoff must be a date or timezone-aware ISO timestamp")
        try:
            if len(cutoff) == 10:
                if date.fromisoformat(cutoff).isoformat() != cutoff:
                    raise ValueError()
            elif datetime.fromisoformat(cutoff.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValueError("Cutoff must be a date or timezone-aware ISO timestamp") from None
    requirements = clean.get("requirements", [])
    if not isinstance(requirements, list) or len(requirements) > 100:
        raise ValueError("Requirements must be a list of up to 100 items")
    seen = set()
    for item in requirements:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ("id", "text")):
            raise ValueError("Each required-answer item needs a nonempty id and text")
        if len(item["id"]) > 200 or len(item["text"]) > 2000 or item["id"] in seen:
            raise ValueError("Requirement IDs must be unique and text must fit the limits")
        seen.add(item["id"])
    return clean


def edit_scope(store, session_id, scope, *, expected_version):
    """Serialize with research and reject stale browser edits before any writes."""
    from .engine import session_lock
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("An expected positive scope version is required")
    scope = validate_scope(scope)
    with session_lock(store, session_id):
        before = store.get_session(session_id)
        if before["scope_version"] != expected_version:
            raise ValueError("Scope changed since this form was opened; reload the current scope before saving")
        if before["scope"] == scope:
            return before
        updated = store.update_scope(session_id, scope)
        store.add_event(session_id, {"type": "scope_edited", "origin": "explicit_user_edit",
                                    "previous_scope_version": expected_version,
                                    "scope_version": updated["scope_version"], "scope": scope})
        return updated
