"""Small localhost UI, with background work and a separate cancellation path."""
from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlparse

from .engine import Budget, ResearchEngine, SessionBusy, session_lock
from .model import ModelClient
from .reporting import report_view, render_csv, render_markdown
from .storage import SessionStore
from .submissions import check_model_binding, lookup_submission, model_binding, research_cutoff, reserve_submission, submission_status
from .tools import ToolRegistry
from .charts import chart_data, render_chart_svg
from .scope import edit_scope
from .usage import session_usage


ASSETS = Path(__file__).resolve().parent / "web_assets"
PAGE = (ASSETS / "page.html").read_text(encoding="utf-8")


class SubmissionConfigurationConflict(ValueError):
    pass


def web_submission_status(submission):
    if submission is None:
        return None
    return {**submission_status(submission), "as_of": submission["as_of"]}


class Application:
    def __init__(self, db_path, data_dir, allow_network=False, *, tool_options=None, offline=False, source_flags=None):
        self.db_path, self.data_dir = str(Path(db_path).resolve()), Path(data_dir)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        SessionStore(self.db_path).close()
        self.allow_network = allow_network
        self.offline = offline
        self.tool_options = dict(tool_options or {})
        self.execution_options = {"data_dir": str(self.data_dir.resolve()), "live": bool(allow_network and not offline),
                                  "source_flags": dict(source_flags or {})}
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.errors = {}
        self.lock = threading.Lock()

    def start(self, sid, message, budget, *, max_context_chars=None, as_of=None, request_id=None):
        with self.lock, SessionStore(self.db_path) as store:
            submission = lookup_submission(store, sid, request_id, message, as_of)
            if submission and submission["episode"]:
                return {"running": False, **web_submission_status(submission)}
            if self.jobs.get(sid):
                if submission and self.jobs[sid] == request_id:
                    return {"running": True, **web_submission_status(submission)}
                raise SessionBusy("This session is already running.")
            try:
                # Reserve before HTTP acknowledges the request. A restart may
                # occur before the background worker enters the engine.
                with session_lock(store, sid):
                    submission = lookup_submission(store, sid, request_id, message, as_of)
                    if submission and submission["episode"]:
                        return {"running": False, **web_submission_status(submission)}
                    if submission:
                        max_context_chars = submission["max_context_chars"]
                        if self.offline and not submission["offline"]:
                            raise SubmissionConfigurationConflict("Open VeriFin with model access to resume this request.")
                        options = submission.get("execution_options", {})
                        if options.get("method_pack") or options.get("capture_case") or any(options.get("engine_flags", {}).values()):
                            raise SubmissionConfigurationConflict("Resume this request in the CLI to use its original research settings.")
                        if any(key in options and options[key] != value for key, value in self.execution_options.items()):
                            raise SubmissionConfigurationConflict("Resume this request with its original source settings.")
                    else:
                        if request_id is not None and message is None:
                            raise ValueError("No submission with that request ID was found in this session.")
                        if not isinstance(message, str) or not message.strip() or len(message) > 20000:
                            raise ValueError("A research question of 1–20000 characters is required.")
                        if not isinstance(budget, Budget):
                            budget = Budget(**budget)
                        if (budget.max_model_calls > 100 or budget.max_tool_calls > 100
                                or budget.max_seconds > 1800 or budget.max_planner_steps > 500):
                            raise ValueError("UI allowance exceeds its limits (100 model/tool calls, 500 planner steps, 1800 seconds).")
                        research_cutoff(message, as_of)
                    offline = self.offline or bool(submission and submission["offline"])
                    model = None if offline else ModelClient.from_env(max_context_chars=max_context_chars)
                    if submission:
                        try:
                            check_model_binding(submission, model)
                        except ValueError as exc:
                            raise SubmissionConfigurationConflict(str(exc)) from exc
                    else:
                        reserve_submission(store, sid, request_id, message=message, as_of=as_of,
                                           budget=asdict(budget), max_context_chars=max_context_chars,
                                           execution_backend="python-staged", offline=offline, reset_cancel=True,
                                           execution_options=self.execution_options, model_identity=model_binding(model))
                        if request_id is not None:
                            submission = store.get_submission(sid, request_id)
            except SessionBusy:
                submission = lookup_submission(store, sid, request_id, message, as_of)
                if submission:
                    return {"running": submission["episode"] is None, **web_submission_status(submission)}
                raise
            self.jobs[sid] = request_id or True
            self.errors.pop(sid, None)

        def work():
            store = None
            try:
                store = SessionStore(self.db_path)
                tools = ToolRegistry(store, self.data_dir, allow_network=self.allow_network and not offline,
                                     user_agent=os.environ.get("SEC_USER_AGENT"),
                                     cache_dir=Path(self.db_path).parent / "snapshots", **self.tool_options)
                engine = ResearchEngine(store, model, tools)
                if request_id is not None:
                    result = engine.ask(sid, request_id=request_id, reset_cancel=False)
                else:
                    result = engine.ask(sid, message, budget=budget, reset_cancel=False, as_of=as_of,
                                        max_context_chars=max_context_chars)
                if result["episode"].get("stop_reason") == "execution_error":
                    self.errors[sid] = "Research stopped on " + result["episode"].get("error_type", "an execution error") + ". See the saved report and activity log."
            except SessionBusy:
                if request_id is None:
                    self.errors[sid] = "This session is already running."
            except Exception as exc:
                self.errors[sid] = "Research could not run: " + type(exc).__name__
            finally:
                if store:
                    store.close()
                with self.lock:
                    self.jobs[sid] = False

        try:
            threading.Thread(target=work, daemon=True, name="verifin-research").start()
        except Exception:
            with self.lock:
                self.jobs[sid] = False
            raise
        return {"running": True, **(web_submission_status(submission) or {})}


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def valid_host(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host", "") in allowed

        def log_message(self, format, *args):
            pass  # Requests may contain private research identifiers.

        def send(self, status, value, content_type="application/json; charset=utf-8", filename=None):
            body = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith("application/json") else value.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                self.send(403, {"error": "This app only serves its local listener hostname"})
                return
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self.send(200, PAGE.replace("__TOKEN__", app.token).replace("__LIVE_LABEL__", "+ live SEC enabled" if app.allow_network else "only"), "text/html; charset=utf-8")
                return
            if path in {"/assets/app.js", "/assets/style.css"}:
                self.send(200, (ASSETS / path.rsplit("/", 1)[1]).read_text(encoding="utf-8"),
                          "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
                return
            store = SessionStore(app.db_path)
            try:
                if path == "/api/config":
                    if app.offline:
                        self.send(200, {"configured":True,"offline":True})
                        return
                    try:
                        model = ModelClient.from_env()
                        self.send(200, {"configured": True, "model": model.model, "max_context_chars":model.max_context_chars})
                    except Exception:
                        self.send(200, {"configured": False})
                elif path == "/api/sessions":
                    self.send(200, store.list_sessions())
                elif path.startswith("/api/session/"):
                    parts = path.split("/")
                    sid = parts[3]
                    if len(parts) == 4:
                        # A worker may finish during the read. Observe activity first so a
                        # response with an older snapshot still asks the client to poll.
                        running = bool(app.jobs.get(sid))
                        if not running:
                            try:
                                with session_lock(store, sid):
                                    pass
                            except ValueError:
                                running = True
                        with store.read_snapshot():
                            context = store.get_context(sid, report_mode="summaries")
                            events = store.events(sid, limit=30, exclude_types=["episode_checkpoint"])
                            checkpoint = store.latest_event(sid, "episode_checkpoint")
                            usage = session_usage(store.list_episode_summaries(sid))
                            latest_submission = web_submission_status(store.get_submission(sid))
                            requested_id = parse_qs(parsed.query, keep_blank_values=True).get("request_id", [])
                            if len(requested_id) > 1:
                                raise ValueError("Select one request ID.")
                            requested_submission = web_submission_status(lookup_submission(store, sid, requested_id[0])) if requested_id else None
                        episode = checkpoint["episode"] if checkpoint else None
                        if episode and episode.get("summary"):
                            episode = {**episode, "summary_kind":"unverified_researcher_draft"}
                        if episode and episode.get("status") == "running" and not running:
                            episode = {**episode, "status": "interrupted"}
                        latest = context["reports"][-1] if context["reports"] else None
                        current_version = None
                        if latest and not running and (episode is None or (
                                episode.get("status") in {"complete", "partial"}
                                and latest.get("episode_id") == episode["id"])):
                            current_version = latest["version"]
                        context.update(events=[e for e in events if e.get("type") != "episode_checkpoint"][-30:], episode=episode, running=running, error=app.errors.get(sid),
                                       session_usage=usage, current_report_version=current_version,
                                       latest_submission=latest_submission, requested_submission=requested_submission)
                        self.send(200, context)
                    elif len(parts) == 5 and parts[4] in {"report", "report.json", "report.md", "evidence.csv", "calculations.csv", "charts.json", "chart.svg"}:
                        query = parse_qs(parsed.query, keep_blank_values=True)
                        if "version" in query:
                            values = query["version"]
                            if len(values) != 1 or not values[0].isascii() or not values[0].isdigit() or int(values[0]) < 1:
                                raise ValueError("Report version must be one positive integer")
                            version = int(values[0])
                        else:
                            summaries = store.list_report_summaries(sid, limit=1)
                            if not summaries:
                                raise KeyError("No report")
                            version = summaries[-1]["version"]
                        report = store.get_report(sid, version)
                        name = parts[4]
                        if name in {"report", "report.md"}:
                            report = store.report_with_stage(sid, report)
                        if name == "charts.json":
                            self.send(200, chart_data(report))
                        elif name == "chart.svg":
                            values = query.get("group", [])
                            if len(values) != 1:
                                raise ValueError("Select exactly one chart group")
                            group = next((g for g in chart_data(report)["groups"] if g["id"] == values[0]), None)
                            if group is None:
                                raise KeyError("Unknown chart group")
                            self.send(200, render_chart_svg(group), "image/svg+xml; charset=utf-8")
                        elif name in {"report", "report.json"}:
                            if name == "report":
                                report = report_view(report)
                            self.send(200, report, filename="report.json" if name == "report.json" else None)
                        else:
                            content = render_markdown(report) if name == "report.md" else render_csv(report[name.split(".")[0]])
                            self.send(200, content, "text/markdown; charset=utf-8" if name.endswith(".md") else "text/csv; charset=utf-8", name)
                    else:
                        self.send(404, {"error": "Unknown route"})
                else:
                    self.send(404, {"error": "Unknown route"})
            except KeyError:
                self.send(404, {"error": "Session or report not found"})
            except ValueError as exc:
                self.send(400, {"error": str(exc)})
            finally:
                store.close()

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.valid_host() or (origin is not None and origin != "http://" + self.headers.get("Host", "")):
                self.send(403, {"error": "This app only accepts same-origin local requests"})
                return
            if self.headers.get("X-VeriFin-Token") != app.token:
                self.send(403, {"error": "Invalid local application token"})
                return
            store = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 65536:
                    raise ValueError("Request body must be between 1 and 65536 bytes")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                path = urlparse(self.path).path
                store = SessionStore(app.db_path)
                if path == "/api/sessions":
                    self.send(201, store.create_session(body.get("title", "Untitled")))
                elif path.startswith("/api/session/") and len(path.split("/")) == 5:
                    parts = path.split("/")
                    sid, action = parts[3:]
                    store.get_session(sid)
                    if action == "cancel":
                        store.request_cancel(sid)
                        self.send(200, {"cancel_requested": True})
                    elif action == "scope":
                        with app.lock:
                            if app.jobs.get(sid):
                                raise ValueError("Stop or finish the active research before editing scope")
                            updated = edit_scope(store, sid, body.get("scope"), expected_version=body.get("expected_version"))
                        self.send(200, updated)
                    elif action == "ask":
                        request_id = body.get("request_id")
                        try:
                            result = app.start(sid, body.get("message"), body.get("budget", {}),
                                               max_context_chars=body.get("max_context_chars"), as_of=body.get("as_of"),
                                               request_id=request_id)
                        except (SessionBusy, SubmissionConfigurationConflict) as exc:
                            self.send(409 if request_id is not None else 400, {"error": str(exc)})
                        else:
                            self.send(202 if result["running"] else 200, result)
                    else:
                        self.send(404, {"error": "Unknown route"})
                else:
                    self.send(404, {"error": "Unknown route"})
            except KeyError:
                self.send(404, {"error": "Session not found"})
            except (ValueError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception as exc:
                self.send(503, {"error": "Unable to start research: " + type(exc).__name__ + ". Check model configuration."})
            finally:
                if store:
                    store.close()

    return Handler


def serve(db_path, data_dir, *, port=8765, allow_network=False, tool_options=None, offline=False, source_flags=None):
    app = Application(db_path, data_dir, allow_network, tool_options=tool_options, offline=offline, source_flags=source_flags)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(app))
    print(f"VeriFin is available at http://127.0.0.1:{server.server_port}", flush=True)
    print(("离线模式。可查看已保存的真实回答，新问题只读本地材料。" if offline else
           "This local UI uses configured model credentials.") + " Ctrl+C stops the server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
