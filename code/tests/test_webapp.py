import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from verifin.engine import Budget
from verifin.model import ModelError
from verifin.storage import SessionStore
from verifin.webapp import Application, handler_for
from tests.test_engine import ScriptedModel
from tests.test_model import completion


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temp.name) / "db.sqlite3", Path(__file__).resolve().parents[1] / "data/replay")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        headers = headers or {}
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, headers=headers)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if response.headers.get_content_type() == "application/json" else raw.decode()

    def new(self):
        status, value = self.request("/api/sessions", {"title": "Research"}, {"X-VeriFin-Token": self.app.token})
        self.assertEqual(201, status)
        return value["id"]

    def test_create_reopen_and_list_without_model_configuration(self):
        sid = self.new()
        self.assertEqual(sid, self.request("/api/sessions")[1][0]["id"])
        status, data = self.request("/api/session/" + sid)
        self.assertEqual(200, status)
        self.assertEqual("Research", data["session"]["title"])
        self.assertFalse(data["running"])

    def test_post_requires_token_and_host_resists_dns_rebinding(self):
        self.assertEqual(403, self.request("/api/sessions", {"title": "Blocked"})[0])
        self.assertEqual(403, self.request("/", headers={"Host": "attacker.example"})[0])
        self.assertEqual(403, self.request("/api/sessions", {}, {"X-VeriFin-Token": self.app.token, "Origin": "https://attacker.example"})[0])

    def test_unconfigured_model_errors_before_starting_or_writing_turn(self):
        sid = self.new()
        with patch("verifin.webapp.ModelClient.from_env", side_effect=ModelError("Set key")):
            self.assertFalse(self.request("/api/config")[1]["configured"])
            status, body = self.request("/api/session/" + sid + "/ask", {"message": "Revenue?"}, {"X-VeriFin-Token": self.app.token})
        self.assertEqual(503, status)
        self.assertIn("configuration", body["error"])
        self.assertEqual([], self.request("/api/session/" + sid)[1]["messages"])
        self.assertFalse(self.app.jobs.get(sid))

    def test_immediate_stop_before_worker_entry_survives_launch(self):
        sid = self.new()
        queued = []
        class DeferredThread:
            def __init__(self, *, target, **kwargs):
                queued.append(target)
            def start(self):
                pass
        model = ScriptedModel([])
        with patch("verifin.webapp.ModelClient.from_env", return_value=model), patch("verifin.webapp.threading.Thread", DeferredThread):
            self.app.start(sid, "Revenue?", Budget())
        store = SessionStore(self.app.db_path)
        store.request_cancel(sid)
        queued[0]()
        report = store.list_reports(sid)[-1]
        self.assertEqual("cancelled", report["stop_reason"])
        self.assertEqual(0, report["usage"]["model_calls"])
        self.assertEqual([], model.calls)
        self.assertFalse(self.app.jobs[sid])
        store.close()

    def test_worker_database_failure_releases_running_status(self):
        sid = self.new()
        queued = []
        class DeferredThread:
            def __init__(self, *, target, **kwargs):
                queued.append(target)
            def start(self):
                pass
        with patch("verifin.webapp.ModelClient.from_env", return_value=ScriptedModel([])), patch("verifin.webapp.threading.Thread", DeferredThread):
            self.app.start(sid, "Revenue?", Budget())
        with patch("verifin.webapp.SessionStore", side_effect=OSError("private/path")):
            queued[0]()
        self.assertFalse(self.app.jobs[sid])
        self.assertIn("OSError", self.app.errors[sid])
        self.assertNotIn("private/path", self.app.errors[sid])

    def test_external_session_owner_keeps_cancellation_and_is_visible_as_running(self):
        from verifin.engine import session_lock
        sid = self.new()
        store = SessionStore(self.app.db_path)
        try:
            store.request_cancel(sid)
            store.add_event(sid, {"type": "episode_checkpoint", "episode": {"id": "external", "status": "running"}})
            with session_lock(store, sid), patch("verifin.webapp.ModelClient.from_env", return_value=ScriptedModel([])):
                with self.assertRaisesRegex(ValueError, "already running"):
                    self.app.start(sid, "A second request", Budget())
                self.assertTrue(store.is_cancelled(sid))
                _, context = self.request('/api/session/' + sid)
                self.assertTrue(context['running'])
                self.assertEqual('running', context['episode']['status'])
            self.assertEqual('interrupted', self.request('/api/session/' + sid)[1]['episode']['status'])
        finally:
            store.close()

    def test_invalid_budget_and_missing_session_are_rejected(self):
        sid = self.new()
        status, _ = self.request("/api/session/" + sid + "/ask", {"message": "Revenue?", "budget": {"max_model_calls": -1}}, {"X-VeriFin-Token": self.app.token})
        self.assertEqual(400, status)
        self.assertEqual(404, self.request("/api/session/missing")[0])

    def test_request_receipt_survives_server_restart_before_worker_start(self):
        sid = self.new()
        path = "/api/session/" + sid
        headers = {"X-VeriFin-Token": self.app.token}
        original = Application(self.app.db_path, self.app.data_dir, offline=True)
        self.app.offline = True
        message = "What was NVIDIA FY2025 Q4 revenue?"
        cutoff = "2025-03-01"

        def ask(body):
            return self.request(path + "/ask", body, headers)

        def wait_for_report(request_id):
            for _ in range(100):
                _, data = self.request(path + "?request_id=" + request_id)
                receipt = data["requested_submission"]
                if not data["running"] and receipt["status"] != "pending":
                    return receipt
                time.sleep(0.01)
            self.fail("worker did not finish")

        with patch("verifin.webapp.threading.Thread") as worker:
            queued = original.start(sid, message, Budget(), as_of=cutoff, request_id="original")
            worker.return_value.start.assert_called_once()
        self.assertTrue(queued["running"])
        self.assertEqual("pending", queued["status"])
        self.assertEqual("original", self.request(path)[1]["latest_submission"]["request_id"])
        self.assertEqual(400, ask({"request_id":"original", "message":"A different request"})[0])
        self.assertEqual(400, ask({"request_id":"original", "as_of":"2025-03-02"})[0])
        self.assertEqual(202, ask({"request_id":"original"})[0])
        original_receipt = wait_for_report("original")
        self.assertEqual(cutoff, original_receipt["as_of"])
        self.assertEqual(1, original_receipt["report_version"])

        self.assertEqual(202, ask({"request_id":"new", "message":message})[0])
        new_receipt = wait_for_report("new")
        self.assertEqual(2, new_receipt["report_version"])
        self.assertNotEqual(original_receipt["episode_id"], new_receipt["episode_id"])
        with patch("verifin.webapp.ModelClient.from_env", side_effect=ModelError("No key")) as configure:
            self.app.offline = False
            self.assertEqual((200, {"running":False, **original_receipt}), ask({"request_id":"original"}))
            configure.assert_not_called()
        _, data = self.request(path + "?request_id=original")
        self.assertEqual(new_receipt, data["latest_submission"])
        self.assertEqual(original_receipt, data["requested_submission"])
        self.assertEqual(2, data["report_count"])

    def test_a_pending_request_keeps_its_cancellation_and_source_settings(self):
        sid = self.new()
        path = "/api/session/" + sid
        headers = {"X-VeriFin-Token": self.app.token}
        original = Application(self.app.db_path, self.app.data_dir, offline=True)
        self.app.offline = True
        with patch("verifin.webapp.threading.Thread"):
            original.start(sid, "NVIDIA revenue?", Budget(), request_id="pending-cancel")
        self.assertEqual(200, self.request(path + "/cancel", {}, headers)[0])
        changed = Application(self.app.db_path, self.app.data_dir / "different", offline=True)
        with self.assertRaisesRegex(ValueError, "original source settings"):
            changed.start(sid, None, {}, request_id="pending-cancel")
        with SessionStore(self.app.db_path) as store:
            self.assertTrue(store.is_cancelled(sid))
            self.assertEqual([], store.get_context(sid)["messages"])

        body = {"request_id":"pending-cancel"}
        self.assertEqual(202, self.request(path + "/ask", body, headers)[0])
        for _ in range(100):
            status, receipt = self.request(path + "/ask", body, headers)
            if status == 200:
                break
            self.assertEqual(202, status)
            time.sleep(0.01)
        else:
            self.fail("worker did not finish")
        self.assertFalse(receipt["running"])
        self.assertEqual("cancelled", receipt["stop_reason"])
        self.assertEqual(1, receipt["report_version"])
        report = self.request(path + "/report?version=1")[1]
        self.assertEqual(0, report["usage"]["model_calls"])
        self.assertEqual(0, report["usage"]["tool_calls"])

    def test_context_limit_persists_for_followup_but_isolated_from_other_sessions(self):
        path = "/api/session/" + self.new()
        headers = {"X-VeriFin-Token": self.app.token}
        env = {"VERIFIN_MODEL":"fixture", "VERIFIN_API_KEY":"fixture", "VERIFIN_BASE_URL":"https://example.test/v1",
               "VERIFIN_MAX_CONTEXT_CHARS":"100"}
        response = completion({"action":"clarify", "question":"Which issuer?"})
        def submit(options, target=path):
            self.assertEqual(202, self.request(target + "/ask", {"message":"Revenue?", **options}, headers)[0])
            for _ in range(100):
                data = self.request(target)[1]
                if not data["running"]:
                    return data["episode"]
                time.sleep(0.01)
            self.fail("worker did not finish")

        with patch.dict("os.environ", env, clear=True), patch("verifin.model._http_transport",
                return_value=response) as transport:
            self.assertEqual(400, self.request(path + "/ask", {"message":"Revenue?", "max_context_chars":True}, headers)[0])
            self.assertEqual("local_context_limit", submit({})["stop_reason"])
            transport.assert_not_called()
            report = self.request(path + "/report")[1]
            self.assertGreater(report["context_limit"]["characters"], 100)
            self.assertIn("可能增加输入费用", report["stage_message"])
            self.assertEqual("waiting_for_clarification", submit({"max_context_chars":200000})["status"])
            transport.assert_called_once()
            with SessionStore(self.app.db_path) as reopened:
                self.assertEqual(200000, reopened.get_session(path.rsplit("/", 1)[1])["max_context_chars"])
            self.assertEqual("waiting_for_clarification", submit({"message":"Microsoft"})["status"])
            self.assertEqual(2, transport.call_count)
            self.assertEqual("local_context_limit", submit({}, "/api/session/" + self.new())["stop_reason"])
            self.assertEqual(2, transport.call_count)
            self.assertEqual(100, self.request("/api/config")[1]["max_context_chars"])

    def test_poll_during_delivery_never_stops_with_old_report(self):
        sid = self.new()
        path = "/api/session/" + sid
        episode = {"id":"second", "status":"running", "request":"Operating cash flow?"}
        with SessionStore(self.app.db_path) as writer:
            writer.save_report(sid, {"episode_id":"first", "status":"complete", "request":"Sales?"})
            writer.add_event(sid, {"type":"episode_checkpoint", "episode":episode})
            self.app.jobs[sid] = True
            read_context = SessionStore.get_context
            def deliver_after_read(reader, session_id, **kwargs):
                context = read_context(reader, session_id, **kwargs)
                with writer.atomic_step():
                    writer.save_report(sid, {"episode_id":"second", "status":"complete", "request":episode["request"]})
                    writer.add_event(sid, {"type":"episode_checkpoint", "episode":dict(episode, status="complete")})
                self.app.jobs[sid] = False
                return context
            with patch.object(SessionStore, "get_context", new=deliver_after_read):
                _, midway = self.request(path)
            self.assertTrue(midway["running"])
            self.assertEqual("running", midway["episode"]["status"])
            self.assertIsNone(midway["current_report_version"])
            _, after = self.request(path)
            self.assertFalse(after["running"])
            self.assertEqual(2, after["current_report_version"])

    def test_clarification_does_not_present_previous_report_as_current(self):
        sid = self.new()
        path = "/api/session/" + sid
        with SessionStore(self.app.db_path) as store:
            store.save_report(sid, {"episode_id":"first", "request":"Sales?", "status":"complete"})
            store.add_event(sid, {"type":"episode_checkpoint", "episode":{
                "id":"second", "request":"Operating cash flow?", "status":"waiting_for_clarification",
                "question":"Which quarter?"}})
        _, response = self.request(path)
        self.assertIsNone(response["current_report_version"])
        self.assertEqual("Which quarter?", response["episode"]["question"])
        self.assertEqual("Sales?", self.request(path + "/report?version=1")[1]["request"])

    def test_question_answers_in_web_and_markdown_match_reviewed_report_version(self):
        sid = self.new()
        row = {"part":"part-1", "status":"answered", "claim_id":"sales", "answer_sentence":"Sales grew."}
        report = {"request_parts":{"part-1":"Did sales grow?"}, "request_coverage":[row],
                  "claims":[{"id":"sales","text":"Sales grew.","status":"Supported"}], "verification_available":True}
        with SessionStore(self.app.db_path) as store:
            store.save_report(sid, report)
            store.save_report(sid, dict(report, claims=[], verification_available=False))
        base = "/api/session/" + sid + "/report"
        self.assertEqual(self.request(base+"?version=1")[1]["question_answers"][0]["status"], "answered")
        self.assertEqual(self.request(base+"?version=2")[1]["question_answers"][0]["status"], "pending")
        self.assertIn("现有记录不足以确认本题是否已起草或送审。", self.request(base+".md?version=2")[1])
        self.assertEqual(self.request(base+".json?version=2")[1]["request_coverage"], [row])

    def test_web_labels_verified_background_for_reviewed_gap_without_rewriting_archive(self):
        sid = self.new()
        report = {"request_parts":{"amount":"How much did new stores contribute?"}, "verification_available":True,
                  "request_coverage":[{"part":"amount", "status":"gap", "missing_support":"No separate dollar amount."}],
                  "claims":[{"id":"old-sales", "text":"Group sales were $100.", "status":"Supported", "evidence_ids":["filing"]}]}
        with SessionStore(self.app.db_path) as store:
            saved = store.save_report(sid, report)
        base = "/api/session/" + sid + "/report"
        view = self.request(base)[1]
        self.assertEqual(["old-sales"], view["background_claim_ids"])
        self.assertEqual("gap", view["question_answers"][0]["status"])
        self.assertEqual(saved, self.request(base+".json")[1])
        main, context = self.request(base+".md")[1].split("## Supporting background")
        self.assertIn("No separate dollar amount.", main)
        self.assertNotIn("Group sales were", main)
        self.assertIn("filing", context)

    def test_history_loads_summaries_and_exports_selected_immutable_version(self):
        sid = self.new()
        store = SessionStore(self.app.db_path)
        try:
            first = store.save_report(sid, {"request": "First request", "status": "partial", "claims": [], "evidence": [{"text": "first source"}], "calculations": [], "usage": {}})
            store.save_report(sid, {"request": "Second request", "status": "partial", "claims": [], "evidence": [{"text": "second source"}], "calculations": [], "usage": {}})
            other = store.create_session("Other session")["id"]
        finally:
            store.close()
        _, context = self.request('/api/session/' + sid)
        self.assertEqual(2, context['report_count'])
        self.assertNotIn('evidence', context['reports'][0])
        view = self.request('/api/session/' + sid + '/report?version=1')[1]
        self.assertEqual(first, {key: view[key] for key in first})
        self.assertEqual("unknown", view["delivery_stage"])
        self.assertEqual(first, self.request('/api/session/' + sid + '/report.json?version=1')[1])
        self.assertIn('first source', self.request('/api/session/' + sid + '/evidence.csv?version=1')[1])
        self.assertNotIn('second source', self.request('/api/session/' + sid + '/evidence.csv?version=1')[1])
        self.assertEqual('Second request', self.request('/api/session/' + sid + '/report')[1]['request'])
        self.assertEqual(404, self.request('/api/session/' + other + '/report?version=1')[0])
        self.assertEqual(404, self.request('/api/session/' + sid + '/report?version=50')[0])
        self.assertEqual(400, self.request('/api/session/' + sid + '/report?version=1&version=2')[0])
        self.assertEqual(400, self.request('/api/session/' + sid + '/report?version=-1')[0])

    def test_edit_scope_without_model_enforces_version_and_idle_session(self):
        sid = self.new()
        path = '/api/session/' + sid
        headers = {"X-VeriFin-Token": self.app.token}
        scope = {"entities": ["NEW"], "period_start": "2024-01-01", "period_end": "2024-03-31"}
        with patch("verifin.webapp.ModelClient.from_env", side_effect=AssertionError("No model needed")):
            status, result = self.request(path + '/scope', {"scope": scope, "expected_version": 1}, headers)
            self.assertEqual(200, status)
            self.assertEqual(scope, result['scope'])
            self.assertEqual(400, self.request(path + '/scope', {"scope": {}, "expected_version": 1}, headers)[0])
            self.app.jobs[sid] = True
            self.assertEqual(400, self.request(path + '/scope', {"scope": {}, "expected_version": result['scope_version']}, headers)[0])
            self.app.jobs[sid] = False
        self.assertEqual(0, self.request(path)[1]['session_usage']['model_calls'])

    def test_chart_snapshot_and_packaged_assets_are_available_without_model(self):
        from tests.test_charts import fact
        sid = self.new()
        store = SessionStore(self.app.db_path)
        try:
            store.save_report(sid, {"evidence": [fact('old', 100)], "calculations": []})
            store.save_report(sid, {"evidence": [fact('new', 150)], "calculations": []})
        finally:
            store.close()
        path = '/api/session/' + sid
        status, charts = self.request(path + '/charts.json?version=1')
        self.assertEqual(200, status)
        group = charts['groups'][0]
        self.assertEqual(100, group['points'][0]['value'])
        status, svg = self.request(path + '/chart.svg?version=1&group=' + group['id'])
        self.assertEqual(200, status)
        self.assertIn('100', svg)
        self.assertEqual(404, self.request(path + '/chart.svg?group=missing')[0])
        self.assertEqual(400, self.request(path + '/chart.svg?group=x&group=y')[0])
        self.assertIn('renderChart', self.request('/assets/app.js')[1])
        self.assertEqual(200, self.request('/assets/style.css')[0])
        self.assertEqual(404, self.request('/assets/../storage.py')[0])


if __name__ == "__main__":
    unittest.main()
