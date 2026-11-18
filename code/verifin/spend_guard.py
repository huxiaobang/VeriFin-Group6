"""Opt-in, shared reservation ledger for Standard Responses text calls.

Amounts are integer nanodollars. Crashes leave the reservation outstanding.
The ledger stores costs, token counts and prefix hashes, never request text.
"""
from contextlib import closing
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .costs import estimate_call, validate_catalog
from .model import ModelError, SpendLimitReached, _json_loads, _usage


def _nano(usd):
    return int((Decimal(str(usd)) * 10**9).to_integral_value(rounding=ROUND_CEILING))


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


class SpendGuard:
    @classmethod
    def from_env(cls, env, client):
        names = ("VERIFIN_SPEND_LEDGER", "VERIFIN_SPEND_LIMIT_USD", "VERIFIN_PRICE_CATALOG")
        if not all(env.get(k) for k in names):
            raise ModelError("A spend guard requires a ledger, USD limit and explicit price catalog")
        if (client.api_mode, client.base_url, client.service_tier) != ("responses", "https://api.openai.com/v1", "default"):
            raise ModelError("Spend guard requires the OpenAI Standard Responses endpoint")
        return cls(env[names[0]], env[names[1]], json.loads(Path(env[names[2]]).read_text()),
                   client.model, env.get("VERIFIN_RUN_LABEL", "local"))

    def __init__(self, path, limit, catalog, model, label):
        self.path = path
        self.catalog = validate_catalog(catalog)
        self.model, self.label = model, label
        if (catalog["currency"] != "USD" or catalog["base_url"] != "https://api.openai.com/v1"
                or catalog["service_tier"] != "default" or model not in catalog["models"]):
            raise ModelError("Unpriced model, endpoint, currency or tier for spend guard")
        cap = Decimal(str(limit))
        if not cap.is_finite() or cap <= 0 or cap * 10**9 != (cap * 10**9).to_integral_value():
            raise ModelError("Invalid ledger limit")
        self.cap = _nano(cap)
        fingerprint = hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),cap INTEGER,prices TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,label TEXT,role TEXT,hold INTEGER,actual INTEGER,usage TEXT,started TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.execute("INSERT OR IGNORE INTO policy VALUES(1,?,?)", (self.cap, fingerprint))
            if db.execute("SELECT cap,prices FROM policy WHERE id=1").fetchone() != (self.cap, fingerprint):
                raise ModelError("Shared ledger already uses a different budget or catalog")
            db.execute("CREATE TABLE IF NOT EXISTS input_prefixes("
                       "prefix BLOB PRIMARY KEY, input_ceiling INTEGER NOT NULL) WITHOUT ROWID")

    def _request_size(self, request):
        p = json.loads(request.data)
        if (request.full_url != self.catalog["base_url"]+"/responses" or p.get("model") != self.model
                or p.get("service_tier") != "default" or type(p.get("max_output_tokens")) is not int
                or p["max_output_tokens"] < 1 or not isinstance(p.get("input"), list)
                or set(p) - {"model","service_tier","max_output_tokens","input","text","stream","store","reasoning",
                            "prompt_cache_options","prompt_cache_key"}):
            raise ModelError("Unpriced request shape; nothing sent")
        settings = {k:v for k,v in p.items() if k != "input"}
        fingerprint = hashlib.sha256(_encoded(settings))
        prefixes = []
        text_bytes = 0
        framing = len(_encoded(settings)) + 4096
        for msg in p["input"]:
            if not isinstance(msg, dict) or msg.get("role") not in ("user","developer","system"):
                raise ModelError("Spend guard accepts only explicit text inputs")
            content = msg.get("content")
            if isinstance(content, str):
                texts = [content]
                shape = ""
                semantic = msg
            elif isinstance(content, list) and all(isinstance(x,dict) and x.get("type") == "input_text"
                                                  and isinstance(x.get("text"),str) for x in content):
                texts = [block["text"] for block in content]
                shape = [{**block,"text":""} for block in content]
                # A breakpoint changes where a cache write may end. Keep its
                # framing and price every token at the highest eligible rate.
                semantic = {**msg,"content":[{k:v for k,v in block.items() if k != "prompt_cache_breakpoint"}
                                              for block in content]}
                # Evidence is appended as whole input_text blocks in one
                # message. Its older blocks can remain identical even when
                # the whole message no longer matches. Bind each boundary to
                # the complete earlier messages, role, settings and block order.
                blocks = fingerprint.copy()
                blocks.update(b"\0verifin-input-text-prefix-v1\0")
                blocks.update(hashlib.sha256(_encoded({k:v for k,v in msg.items() if k != "content"})).digest())
                length = text_bytes
                for block in semantic["content"]:
                    length += len(block["text"].encode("utf-8"))
                    blocks.update(hashlib.sha256(_encoded(block)).digest())
                    prefixes.append((blocks.digest(), length))
            else:
                raise ModelError("Unpriced content; nothing sent")
            text_bytes += sum(len(t.encode("utf-8")) for t in texts)
            framing += len(_encoded({**msg,"content":shape})) + 128*(len(texts) + 1)
            fingerprint.update(hashlib.sha256(_encoded(semantic)).digest())
            prefixes.append((fingerprint.digest(), text_bytes))
        return p, framing + text_bytes, prefixes

    def _hold(self, db, p, byte_bound, prefixes):
        # A complete, identical prefix consumes at most the *entire* input of
        # its settled witness. Keep all framing and budget the suffix in bytes.
        upper = byte_bound
        for prefix, length in prefixes:
            row = db.execute("SELECT input_ceiling FROM input_prefixes WHERE prefix=?", (prefix,)).fetchone()
            if row is not None:
                upper = min(upper, byte_bound - length + row[0])
        row = self.catalog["models"][self.model]
        bands = [row["short"]] + ([row["long"]] if upper > row["long_context_above"] else [])
        # Price every input byte at the highest applicable input/write rate.
        ttl = p.get("prompt_cache_options", {}).get("ttl")
        if ttl not in (None,"30m","12h"):
            raise ModelError("Unknown cache TTL; nothing sent")
        keys = ["input","cached_input","write_30m"] + (["write_12h"] if ttl != "30m" else [])
        inp = max(Decimal(str(b[k])) for b in bands for k in keys)
        out = max(Decimal(str(b["output"])) for b in bands)
        return _nano((upper*inp + p["max_output_tokens"]*out) / 10**6)

    def reservation(self, request):
        size = self._request_size(request)
        with closing(sqlite3.connect(self.path)) as db:
            return self._hold(db, *size)

    def attempt(self, role, request, timeout, transport):
        p, byte_bound, prefixes = self._request_size(request)
        key = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM attempts WHERE actual>hold LIMIT 1").fetchone():
                raise ModelError("Ledger has a reservation discrepancy; no more requests allowed")
            hold = self._hold(db, p, byte_bound, prefixes)
            spent = db.execute("SELECT COALESCE(SUM(COALESCE(actual,hold)),0) FROM attempts").fetchone()[0]
            if spent + hold > self.cap:
                raise SpendLimitReached("Shared USD budget cannot cover this request; nothing sent")
            db.execute("INSERT INTO attempts(id,label,role,hold) VALUES(?,?,?,?)", (key,self.label,role,hold))
        raw = transport(request, timeout)  # no retries, even with an unknown outcome
        try:
            reply = _json_loads(raw)
            if not isinstance(reply, dict) or (reply.get("model"),reply.get("service_tier")) != (self.model,"default"):
                return raw
            usage = _usage(reply.get("usage"), "responses")
            price = estimate_call({"model":self.model,"base_url":self.catalog["base_url"],
                                   "service_tier":"default","usage":usage}, self.catalog)
            if price["status"] != "estimated":
                return raw
            actual = _nano(price["estimated_cost"])
        except (ValueError, TypeError, UnicodeError):
            return raw
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE attempts SET actual=?, usage=? WHERE id=?", (actual,json.dumps(usage,sort_keys=True),key))
            if (actual <= hold and reply.get("status") == "completed" and not reply.get("error") and not reply.get("incomplete_details")
                    and usage["prompt_tokens"] > 0):
                db.executemany("INSERT INTO input_prefixes(prefix,input_ceiling) VALUES(?,?) "
                               "ON CONFLICT(prefix) DO UPDATE SET input_ceiling=min(input_ceiling,excluded.input_ceiling)",
                               ((prefix, usage["prompt_tokens"]) for prefix, _ in prefixes))
        if actual > hold:
            raise ModelError("Provider receipt exceeded reservation; audit the price policy")
        return raw
