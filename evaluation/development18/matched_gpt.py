"""Direct OpenAI Responses transport for the matched development pilot."""
from contextlib import closing
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPRedirectHandler
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from time import monotonic
from uuid import uuid4

from verifin.model import ModelClient, ModelError, SpendLimitReached
from verifin.spend_guard import SpendGuard


MODEL = "gpt-6.1-sol"
URL = "https://api.openai.com/v1"
DEFAULT_PRICES = Path(__file__).resolve().parents[2] / "code/data/pricing/openai-standard-2026-11-06.json"


def dedicated_key():
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        for service in ("VeriFin.OpenAI", "VeriFin-OpenAI"):
            result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
                                    capture_output=True, check=False, timeout=15)
            if result.returncode == 0:
                key = result.stdout.decode("ascii").strip()
                break
    if not key or not key.startswith("sk-") or key.startswith("sk-or-"):
        raise ModelError("Official OpenAI project credential unavailable")
    return key


def append_event(path, event):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_official(req, seconds):
    """Honor system HTTPS proxy settings and keep credentials on the official endpoint."""
    if req.full_url != URL + "/responses" or req.get_method() != "POST":
        raise ModelError("Unexpected OpenAI endpoint or method")
    try:
        return build_opener(_NoRedirect()).open(req, timeout=seconds)
    except HTTPError as exc:
        return exc


class OpenAITransport:
    def __init__(self, directory):
        self.directory = directory
        self.role = None

    def __call__(self, req, seconds):
        attempt = uuid4().hex
        path = self.directory / "provider.jsonl"
        append_event(path, {"attempt_id": attempt, "role": self.role, "status": "started",
                            "request_bytes": len(req.data)})
        start = monotonic()
        with open_official(req, seconds) as res:
            body = res.read(4_000_001)
            event = {"attempt_id": attempt, "role": self.role, "http_status": res.status,
                     "request_id": res.getheader("x-request-id"), "elapsed_seconds": round(monotonic()-start, 3)}
            if res.status != 200 or len(body) > 4_000_000:
                append_event(path, {**event, "status": "http_rejected_or_oversize"})
                raise ModelError(f"OpenAI response status {res.status} or exceeds size limit")
            try:
                response = json.loads(body)
                if not isinstance(response, dict):
                    raise ValueError
            except (ValueError, UnicodeError):
                append_event(path, {**event, "status": "invalid_provider_json"})
                raise ModelError("Invalid OpenAI response envelope") from None
            (self.directory / f"provider-output-{attempt}.json").write_bytes(body)
            append_event(path, {**event, "status": "provider_response_received", "model": response.get("model"),
                                "response_status": response.get("status"), "usage": response.get("usage")})
            return body


class CaseGuard:
    """The runner owns each case exclusively. The shared ledger caps all cases atomically."""
    def __init__(self, ledger, limit, recorder):
        self.ledger = ledger
        self.case_cap = Decimal(str(limit)) * 10**9
        self.recorder = recorder

    def attempt(self, role, request, timeout, transport):
        with closing(sqlite3.connect(self.ledger.path)) as db:
            used, = db.execute("SELECT COALESCE(SUM(COALESCE(actual,hold)),0) FROM attempts WHERE label=?",
                               (self.ledger.label,)).fetchone()
        if used + self.ledger.reservation(request) > self.case_cap:
            raise SpendLimitReached("Case USD cap cannot cover this request; nothing sent")

        self.recorder.role = role
        return self.ledger.attempt(role, request, timeout, transport)


def make_model_factory(args):
    catalog = json.loads(Path(args.price_catalog or DEFAULT_PRICES).read_text())
    key = dedicated_key()
    case_limit = Decimal(str(args.case_limit_usd or args.spend_limit_usd))
    if not case_limit.is_finite() or case_limit <= 0:
        raise ValueError("case limit must be positive and finite")

    def factory(case, directory):
        ledger = SpendGuard(Path(args.output) / "openai-cost-estimates.sqlite3", args.spend_limit_usd,
                            catalog, MODEL, case.question_id)
        recorder = OpenAITransport(directory)
        client = ModelClient(key, MODEL, URL, api_mode="responses", service_tier="default",
                             reasoning_effort="medium", cache_shared_tools=True, timeout=60,
                             max_context_chars=180_000, max_output_tokens=getattr(args, "decision_output_tokens", 16384), transport=recorder)
        client.researcher_max_output_tokens = 8192
        client.spend_guard = CaseGuard(ledger, case_limit, recorder)
        return client
    return factory
