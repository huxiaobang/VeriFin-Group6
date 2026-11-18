"""Verified replay snapshots and bounded, opt-in SEC JSON access.

No model calls, retries, or hidden follow-up requests belong in this layer.
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import tempfile
import threading
import time
import sqlite3
from contextlib import closing
from datetime import date, datetime, time as day_time, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class DataError(ValueError):
    """Unavailable or unsafe data, suitable for a visible tool failure."""


def stable_id(prefix: str, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return prefix + hashlib.sha256(raw.encode()).hexdigest()[:24]


def parse_day(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DataError("Use an exact date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError("Invalid calendar date.") from exc


def cutoff_time(value: str | None) -> datetime | None:
    """Date-only cutoffs include that whole UTC date; timestamps require a zone."""
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(parse_day(value), day_time.max, timezone.utc)
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("timezone missing")
        return instant.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError) as exc:
        raise DataError("Cutoff must be an ISO date or timezone-aware ISO timestamp.") from exc


def available_by(publication: str | None, cutoff: str | None) -> bool:
    if publication is None:
        return cutoff is None
    # A date-only publication is conservatively unavailable until day's end.
    pub = cutoff_time(publication)
    bound = cutoff_time(cutoff)
    return bound is None or pub <= bound


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DataError("Redirect refused: one tool action permits one HTTP request.")


class SnapshotData:
    _sec_rate_lock = threading.Lock()
    _last_sec_request_at = 0.0

    def __init__(self, data_dir, allow_network=False, user_agent=None, cache_dir=None):
        root = Path(data_dir).resolve()
        self.root = root if (root / "manifest.json").exists() else root / "replay"
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else (self.root / "live").resolve()
        self.allow_network = allow_network
        self.user_agent = user_agent
        self._request_state = threading.local()
        self.manifest = {"companies": [], "documents": [], "files": {}}
        self.manifest_hash = None
        manifest_path = self.root / "manifest.json"
        if manifest_path.exists():
            manifest_bytes = manifest_path.read_bytes()
            self.manifest = json.loads(manifest_bytes.decode("utf-8"))
            self.manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    @property
    def timeout(self):
        return getattr(self._request_state, "timeout", 20.0)

    @timeout.setter
    def timeout(self, value):
        self._request_state.timeout = value

    @property
    def network_requests(self):
        return getattr(self._request_state, "network_requests", 0)

    @network_requests.setter
    def network_requests(self, value):
        self._request_state.network_requests = value

    def raw(self, name: str) -> bytes:
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise DataError("Snapshot path escapes the configured data directory.")
        info = self.manifest["files"].get(name)
        if not info:
            raise DataError("Snapshot missing from the provenance manifest.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise DataError("Snapshot integrity check failed: " + name)
        return data

    def json(self, name: str) -> dict:
        # A fresh object prevents one session from mutating another's cached data.
        return json.loads(self.raw(name))

    def info(self, name: str) -> dict:
        return dict(self.manifest["files"][name], snapshot=name, mode="replay")

    def _get_sec_response(self, url: str, kind: str) -> tuple[bytes, dict]:
        if not self.allow_network:
            raise DataError("Network access is disabled. Select replay data or enable SEC access.")
        if not self.user_agent or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.user_agent):
            raise DataError("SEC access requires an identifying User-Agent with a contact email.")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {"data.sec.gov", "www.sec.gov"}:
            raise DataError("Only HTTPS SEC endpoints are permitted.")
        json_path = bool(re.fullmatch(r"/api/xbrl/companyfacts/CIK\d{10}\.json", parsed.path)
                or re.fullmatch(r"/submissions/CIK\d{10}\.json", parsed.path)
                or re.fullmatch(r"/submissions/CIK\d{10}-submissions-\d{3,6}\.json", parsed.path)
                or parsed.path == "/files/company_tickers.json")
        html_path = parsed.netloc == "www.sec.gov" and bool(re.fullmatch(
            r"/Archives/edgar/data/[1-9]\d{0,9}/\d{18}/[A-Za-z0-9][A-Za-z0-9_.-]*\.html?", parsed.path))
        if parsed.query or parsed.fragment or not (json_path if kind == "json" else html_path):
            raise DataError("Unsupported SEC endpoint.")
        # Bound throughput without sleeping or concealing a queued request.
        with self._sec_rate_lock:
            now = time.monotonic()
            if now - SnapshotData._last_sec_request_at < 0.2:
                raise DataError("SEC request rate limited locally; a later explicit action may retry.")
            SnapshotData._last_sec_request_at = now
            self.network_requests += 1
        accept = "application/json" if kind == "json" else "text/html,application/xhtml+xml"
        max_bytes = 30_000_000 if kind == "json" else 8_000_000
        req = Request(url, headers={"User-Agent": self.user_agent,
                                    "Accept": accept, "Accept-Encoding": "identity"})
        with build_opener(_NoRedirect).open(req, timeout=self.timeout) as response:
            headers = getattr(response, "headers", None)
            content_type = headers.get_content_type() if headers is not None else None
            charset = headers.get_content_charset() if headers is not None else None
            if kind == "html" and content_type not in {"text/html", "application/xhtml+xml"}:
                raise DataError("SEC filing response is not HTML; no content was accepted.")
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise DataError(f"SEC response exceeds the {max_bytes // 1_000_000} MB bound.")
        if kind == "html":
            prefix = raw[:3000].lower()
            if b"<html" not in prefix and b"<!doctype html" not in prefix:
                raise DataError("SEC response lacks an HTML document header.")
            if any(marker in prefix for marker in [b"your request originates from an undeclared automated tool", b"request rate threshold exceeded"]):
                raise DataError("SEC access-denial page is not a filing.")
        sha = hashlib.sha256(raw).hexdigest()
        path = self.save_live_bytes(raw, ".json" if kind == "json" else ".html")
        return raw, {"sha256": sha, "url": url, "content_type": content_type, "charset": charset,
                     "retrieved_at": datetime.now(timezone.utc).isoformat(),
                     "snapshot": str(path), "mode": "live"}

    def save_live_bytes(self, raw: bytes, suffix: str) -> Path:
        if suffix not in {".json", ".html", ".text.txt"}:
            raise DataError("Unsupported snapshot type.")
        sha = hashlib.sha256(raw).hexdigest()
        # Save the exact response for reproducibility; never overwrite replay.
        live = self.cache_dir
        live.mkdir(parents=True, exist_ok=True)
        if live.resolve() != self.cache_dir:
            raise DataError("Live snapshot path changed unexpectedly.")
        path = live / (sha + suffix)
        if not path.resolve().is_relative_to(self.cache_dir):
            raise DataError("Live snapshot file escapes the configured cache directory.")
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise DataError("Existing live snapshot hash mismatch.")
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=live, prefix=".snapshot-", delete=False) as out:
                    temporary = out.name
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        return path

    def get_sec_json(self, url: str) -> tuple[dict, dict]:
        raw, info = self._get_sec_response(url, "json")
        payload = json.loads(raw)
        self._remember_source(info, "json")
        return payload, info

    def get_sec_html(self, url: str) -> tuple[bytes, dict]:
        raw, info = self._get_sec_response(url, "html")
        self._remember_source(info, "html")
        return raw, info

    def read_live_bytes(self, path: str, expected_hash: str) -> bytes:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.cache_dir):
            raise DataError("Cached snapshot path escapes the configured live directory.")
        raw = resolved.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise DataError("Cached live snapshot hash mismatch.")
        return raw

    @property
    def _index_path(self):
        path = self.cache_dir / "sources.sqlite3"
        if not path.resolve().is_relative_to(self.cache_dir):
            raise DataError("Source index path escapes the configured cache directory.")
        return path

    @staticmethod
    def _encode_index(info, kind):
        metadata = {key: info.get(key) for key in ("url", "sha256", "retrieved_at", "content_type", "charset")}
        metadata.update(kind=kind, filename=Path(info["snapshot"]).name)
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return encoded, hashlib.sha256(encoded.encode()).hexdigest()

    def _decode_index(self, url, encoded, digest):
        if hashlib.sha256(encoded.encode()).hexdigest() != digest:
            raise DataError("Persistent source index metadata hash mismatch; explicit live refresh can repair this URL.")
        metadata = json.loads(encoded)
        if not isinstance(metadata, dict):
            raise DataError("Invalid persistent source index metadata object.")
        sha, kind = metadata.get("sha256"), metadata.get("kind")
        if metadata.get("url") != url or kind not in {"json", "html"} or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise DataError("Invalid persistent source index metadata.")
        if metadata.get("filename") != sha + (".json" if kind == "json" else ".html"):
            raise DataError("Invalid persistent source snapshot filename.")
        if "T" not in str(metadata.get("retrieved_at")):
            raise DataError("Persistent source observation needs a precise timestamp.")
        cutoff_time(metadata["retrieved_at"])
        return {"url": url, "sha256": sha, "retrieved_at": metadata["retrieved_at"],
                "snapshot": str(self.cache_dir / metadata["filename"]), "mode": "cached_live",
                "content_type": metadata.get("content_type"), "charset": metadata.get("charset"), "kind": kind}

    def _remember_source(self, info, kind):
        """One short SQLite transaction; old observations cannot downgrade an index."""
        encoded, digest = self._encode_index(info, kind)
        try:
            with closing(sqlite3.connect(self._index_path, timeout=min(self.timeout, 1.0), isolation_level=None)) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute("CREATE TABLE IF NOT EXISTS sources (url TEXT PRIMARY KEY, metadata TEXT NOT NULL, digest TEXT NOT NULL)")
                    row = db.execute("SELECT metadata,digest FROM sources WHERE url=?", (info["url"],)).fetchone()
                    previous = None
                    if row:
                        try:
                            previous = self._decode_index(info["url"], *row)
                        except (DataError, ValueError):
                            # Only an actual successful request reaches this method.
                            # It may repair an invalid metadata row for this same URL.
                            previous = None
                    if previous:
                        before, after = cutoff_time(previous["retrieved_at"]), cutoff_time(info["retrieved_at"])
                        if after < before:
                            db.execute("COMMIT")
                            return
                        if after == before and info["sha256"] != previous["sha256"]:
                            raise DataError("Conflicting source revisions have the same observation time.")
                    db.execute("INSERT INTO sources VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET metadata=excluded.metadata,digest=excluded.digest",
                               (info["url"], encoded, digest))
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise DataError("Persistent source index unavailable or damaged; no silent cache replacement was attempted.") from exc

    def cached_urls(self):
        if not self._index_path.exists():
            return []
        try:
            with closing(sqlite3.connect(self._index_path.as_uri() + "?mode=ro", uri=True, timeout=min(self.timeout, 1.0))) as db:
                return [row[0] for row in db.execute("SELECT url FROM sources ORDER BY url")]
        except sqlite3.Error as exc:
            raise DataError("Persistent source index unavailable or damaged.") from exc

    def _cached_source(self, url, kind):
        if not self._index_path.exists():
            return None
        try:
            with closing(sqlite3.connect(self._index_path.as_uri() + "?mode=ro", uri=True, timeout=min(self.timeout, 1.0))) as db:
                row = db.execute("SELECT metadata,digest FROM sources WHERE url=?", (url,)).fetchone()
        except sqlite3.Error as exc:
            raise DataError("Persistent source index unavailable or damaged.") from exc
        if not row:
            return None
        info = self._decode_index(url, *row)
        if info["kind"] != kind:
            raise DataError("Cached source type does not match the requested source.")
        raw = self.read_live_bytes(info["snapshot"], info["sha256"])
        return raw, info

    def cached_sec_json(self, url):
        cached = self._cached_source(url, "json")
        if cached is None:
            return None
        raw, info = cached
        return json.loads(raw), info

    def cached_sec_html(self, url):
        return self._cached_source(url, "html")
