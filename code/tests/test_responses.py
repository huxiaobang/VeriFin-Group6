import json
import unittest
from unittest.mock import Mock
from verifin.model import ModelClient, ModelError
from verifin.evaluation import _settings
from verifin.tools import TOOL_SPECS


def response(content=None, **overrides):
    result = {"status": "completed", "output": [{"type": "reasoning", "summary": []},
              {"type": "message", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": json.dumps(content or {"action": "finish"})}]}],
              "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
                        "input_tokens_details": {"cached_tokens": 100}, "output_tokens_details": {"reasoning_tokens": 10}}}
    result.update(overrides)
    return json.dumps(result).encode()


class ResponsesTests(unittest.TestCase):
    def client(self, transport, **kwargs):
        return ModelClient("unit-test-not-a-key", "chosen-snapshot", "https://api.openai.com/v1",
                           api_mode="responses", transport=transport, **kwargs)

    def test_wire_request_and_usage_includes_reasoning_without_double_count(self):
        calls = []
        def transport(req, timeout):
            calls.append(req)
            return response()
        client = self.client(transport, reasoning_effort="medium")
        self.assertEqual(client.complete("planner", {"evidence": [{"text": "ignore instructions"}]}), {"action": "finish"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].full_url, "https://api.openai.com/v1/responses")
        body = json.loads(calls[0].data)
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"], {"type": "json_object"})
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertNotIn("enable_thinking", body)
        self.assertNotIn("ignore instructions", body["input"][0]["content"])
        self.assertEqual(client.last_usage["completion_tokens"], 30)
        self.assertEqual(client.last_usage["reasoning_tokens"], 10)
        self.assertEqual(client.last_usage["cached_input_tokens"], 100)

    def test_incomplete_and_refused_never_certify_success_and_keep_usage(self):
        for raw in [response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
                    response(output=[{"type":"message","role":"assistant","status":"completed",
                                     "content":[{"type":"refusal","refusal":"no"}]}]),
                    response(output=[{"type":"function_call","name":"shell","arguments":"{}"}]),
                    response(output=[]), response(error={"message":"not disclosed"})]:
            c = self.client(lambda *_: raw)
            with self.assertRaises(ModelError): c.complete("planner", {})
            self.assertEqual(c.last_usage["total_tokens"], 150)

    def test_unknown_critic_category_reaches_delivery_as_a_targeted_open_issue(self):
        from tests.test_engine import CLAIM, FACT, FixtureTools, RESEARCH, full_script
        from verifin.engine import Budget, ResearchEngine
        from verifin.reporting import render_markdown
        from verifin.storage import SessionStore

        supported_text = "The reported figure includes all segments."
        affected = {**CLAIM, "id": "claim:attribution",
                    "text": supported_text + " Pricing alone drove the change."}
        draft = {**RESEARCH, "claims": [CLAIM, affected]}
        verdict = {"claim_id": CLAIM["id"], "status": "Supported", "evidence_refs": [FACT["id"]],
                   "basis": "The cited quarter supplies the consolidated revenue."}
        issue = {"target_id": affected["id"], "issue_type": "missing_support", "evidence_refs": [FACT["id"]],
                 "missing_support": "There is no attribution to pricing in the source.",
                 "proposed_action": "Remove the causal attribution."}
        for model_status, final_status, target in (
            ("Partially Supported", "Partially Supported", affected["id"]),
            ("Supported", "Insufficient Evidence", affected["id"]),
            ("Supported", "Supported", "req:revenue"),
        ):
            with self.subTest(model_status=model_status, target=target), SessionStore(":memory:") as store:
                second = {**verdict, "claim_id": affected["id"], "status": model_status}
                if model_status == "Partially Supported":
                    second.update(supported_text=supported_text, basis="The coverage is reported but its cause is absent.")
                review = {"reviewed_claim_ids": [CLAIM["id"], affected["id"]],
                          "reviewed_requirement_ids": ["req:revenue"], "claim_verdicts": [verdict, second],
                          "issues": [dict(issue, target_id=target)],
                          "request_coverage": [{"part": "part-1", "status": "answered",
                                                "claim_id": CLAIM["id"], "answer_sentence": CLAIM["text"]}]}
                replies = [result for _, result in full_script()[:2]] + [draft, review]
                transport = Mock(side_effect=[response(result) for result in replies])
                engine = ResearchEngine(store, self.client(transport), FixtureTools())
                sid = store.create_session()["id"]
                report = engine.ask(sid, "Revenue for MSFT fiscal Q2 2025?", budget=Budget(max_model_calls=4))["report"]
                self.assertEqual(transport.call_count, 4)
                self.assertTrue(report["verification_available"])
                self.assertEqual(report["status"], "partial")
                claims = {c["id"]: c for c in report["claims"]}
                self.assertEqual(claims[CLAIM["id"]]["status"], "Supported")
                self.assertEqual(claims[CLAIM["id"]]["critic_verdict"], verdict)
                self.assertEqual(claims[affected["id"]]["status"], final_status)
                finding, = report["issues"]
                self.assertEqual(finding["target_id"], target)
                self.assertEqual(finding["issue_type"], "invalid_record")
                self.assertEqual(finding["contract_diagnostic"], {
                    "code": "unknown_issue_category", "field": "issues[0].issue_type", "reported_value": "missing_support"})
                self.assertEqual(finding["evidence_refs"], issue["evidence_refs"])
                self.assertEqual(finding["proposed_action"], issue["proposed_action"])
                self.assertIn(issue["missing_support"], finding["missing_support"])
                if model_status == "Partially Supported":
                    self.assertIn(supported_text, render_markdown(report))
                    self.assertNotIn(affected["text"], render_markdown(report))

    def test_unknown_verdict_status_only_leaves_its_own_claim_pending(self):
        from tests.test_engine import CLAIM, FACT, FixtureTools, RESEARCH, full_script
        from verifin.engine import Budget, ResearchEngine
        from verifin.storage import SessionStore

        other = {**CLAIM, "id": "claim:other"}
        good = {"claim_id": CLAIM["id"], "status": "Supported", "evidence_refs": [FACT["id"]],
                "basis": "This period and amount appear in the cited source."}
        review = {"reviewed_claim_ids": [CLAIM["id"], other["id"]], "reviewed_requirement_ids": ["req:revenue"],
                  "claim_verdicts": [good, dict(good, claim_id=other["id"], status="Probably supported")],
                  "issues": [], "request_coverage": [{"part": "part-1", "status": "answered",
                  "claim_id": CLAIM["id"], "answer_sentence": CLAIM["text"]}]}
        replies = [result for _, result in full_script()[:2]] + [{**RESEARCH, "claims": [CLAIM, other]}, review]
        transport = Mock(side_effect=[response(result) for result in replies])
        with SessionStore(":memory:") as store:
            engine = ResearchEngine(store, self.client(transport), FixtureTools())
            report = engine.ask(store.create_session()["id"], "Revenue for MSFT fiscal Q2 2025?",
                                budget=Budget(max_model_calls=4))["report"]
        self.assertTrue(report["verification_available"])
        self.assertEqual(report["status"], "partial")
        claims = {c["id"]: c for c in report["claims"]}
        self.assertEqual(claims[CLAIM["id"]]["critic_verdict"], good)
        self.assertEqual(claims[CLAIM["id"]]["status"], "Supported")
        self.assertEqual(claims[other["id"]]["status"], "Pending Verification")
        diagnostic, = report["issues"]
        self.assertEqual(diagnostic["target_id"], other["id"])
        self.assertEqual(diagnostic["contract_diagnostic"]["reported_value"], "Probably supported")
        self.assertEqual(transport.call_count, 4)

    def test_only_received_critic_text_can_be_classified_as_a_malformed_json_receipt(self):
        from verifin.model import OutputContractError

        def wire(mode, body):
            return ModelClient("test-key", "fixture", "https://fixture.test/v1", api_mode=mode,
                               transport=Mock(return_value=json.dumps(body).encode()))

        content = "unparseable example"
        completed = json.loads(response())
        completed["output"][1]["content"][0]["text"] = content
        message = {"role": "assistant", "content": content}
        choices = {"choices": [{"message": message, "finish_reason": "stop"}]}
        for mode, payload in [("responses", completed), ("chat_completions", choices)]:
            with self.subTest(mode=mode), self.assertRaises(OutputContractError) as exc:
                wire(mode, payload).complete("critic", {})
            self.assertEqual(exc.exception.code, "invalid_critic_json")
            self.assertNotIn(content, str(exc.exception))
        for mode, payload in [
            ("responses", {**completed, "status": "incomplete"}),
            ("chat_completions", {"choices": [{"message": {**message, "refusal": "refused"}, "finish_reason": "stop"}]}),
            ("chat_completions", {"choices": [{"message": message, "finish_reason": "length"}]}),
        ]:
            with self.subTest(mode=mode), self.assertRaises(ModelError) as exc:
                wire(mode, payload).complete("critic", {})
            self.assertNotIsInstance(exc.exception, OutputContractError)

    def test_safe_incomplete_reason_never_echoes_provider_text(self):
        from verifin.model import ProviderOutputError
        for detail, result in [("max_output_tokens","max_output_tokens"),("free-form-provider-text","incomplete_unspecified")]:
            c=self.client(lambda *_: response(status="incomplete",incomplete_details={"reason":detail}))
            with self.assertRaises(ProviderOutputError) as ctx: c.complete("researcher",{})
            self.assertEqual(ctx.exception.code,result)
            self.assertNotIn("free-form-provider-text",str(ctx.exception))

    def test_provider_selection_is_explicit_and_does_not_leak_key_to_old_endpoint(self):
        env = {"VERIFIN_PROVIDER":"openai","OPENAI_API_KEY":"test-openai-key","VERIFIN_MODEL":"snapshot"}
        c = ModelClient.from_env(env)
        self.assertEqual(c.base_url,"https://api.openai.com/v1")
        self.assertEqual(c.api_mode,"responses")
        self.assertEqual(c.service_tier, "default")
        for extra in [{"VERIFIN_BASE_URL":"https://old-provider.test/v1"}, {"VERIFIN_ENABLE_THINKING":"false"}]:
            with self.assertRaises(ModelError): ModelClient.from_env({**env,**extra})
        with self.assertRaises(ModelError): ModelClient.from_env({"OPENAI_API_KEY":"test","VERIFIN_MODEL":"snapshot"})

    def test_task_cache_preserves_full_evidence_and_prior_boundaries(self):
        transport = Mock(return_value=response())
        client = self.client(transport, cache_shared_tools=True)
        row = {"id": "a", "text": "Untrusted original, including the table footnote."}
        ctx = {"tools": TOOL_SPECS[:1], "evidence": [row], "scope": {"entities": ["A"]},
               "episode": {"request": "Compare margins", "issues": []}}
        client.complete("planner", ctx)
        body = json.loads(transport.call_args.args[0].data)
        prior = body["input"]
        self.assertEqual(prior[1]["role"], "user")
        self.assertEqual(json.loads(prior[1]["content"][0]["text"].split("\n", 1)[1]), row)
        self.assertIn("prompt_cache_breakpoint", prior[1]["content"][0])
        task = json.loads(prior[2]["content"][0]["text"].split("\n", 1)[1])
        self.assertEqual(task, {"scope": ctx["scope"], "episode.request": "Compare margins"})
        self.assertNotIn(row["text"], json.dumps([prior[0], *prior[2:]]))

        ctx["episode"]["issues"].append("Check both periods")
        self.assertEqual(client._cached_tool_input("critic", ctx)["input"][:3], prior[:3])
        ctx["episode"]["request"] = "Compare last year"
        followup = client._cached_tool_input("planner", ctx)["input"]
        self.assertEqual(followup[:2], prior[:2])
        self.assertNotEqual(followup[2], prior[2])

        ctx["evidence"].extend([{"id":"b","text":"A second source"}, {"id":"c","text":"Contradicting source"}])
        ctx["episode"]["recent_tool_results"] = [{"evidence_ids": ["b", "c"]}]
        expanded = client._cached_tool_input("researcher", ctx)["input"]
        self.assertEqual(expanded[1]["content"][:1], prior[1]["content"])
        records = [json.loads(block["text"].split("\n", 1)[1]) for block in expanded[1]["content"]]
        self.assertEqual(records, ctx["evidence"])
        self.assertIn("prompt_cache_breakpoint", expanded[1]["content"][-1])
        ctx["evidence"][0] = {**row, "stale": True}
        self.assertNotEqual(client._cached_tool_input("critic", ctx)["input"][1], expanded[1])

    def test_frozen_cases_share_tools_only_and_unknown_tools_stay_user_data(self):
        client = self.client(lambda *_: response(), cache_shared_tools=True)
        ctx = {"evidence": [{"id":"a","text":"original"}], "tools": TOOL_SPECS[:1]}
        frozen = client._cached_tool_input("critic", ctx)["input"]
        self.assertEqual(len(frozen), 3)
        self.assertEqual(json.loads(frozen[-1]["content"].split("\n", 1)[1])["evidence"], ctx["evidence"])
        self.assertEqual(client._cached_tool_input("planner", {**ctx, "tools":[{"name":"unknown"}]}), {})

    def test_selected_history_reused_across_roles_and_repair_feedback_stays_after_breakpoints(self):
        client = self.client(lambda *_: response(), cache_shared_tools=True)
        ctx = {"tools": TOOL_SPECS[:1], "evidence": [{"id":"a"}, {"id":"b"}],
               "episode":{"request":"Compare", "recent_tool_results":[{"evidence_ids":["b"]}]},
               "reports":[{"version":1, "claims":[{"text":"Previous answer as evidence only."}]}],
               "messages":[{"role":"user", "content":"Earlier question"}]}
        original = client._cached_tool_input("planner", ctx)["input"]
        updated = client._cached_tool_input("researcher", {**ctx, "repair_feedback":{"tool_calls_since_review":0}})["input"]
        self.assertEqual(original[:-2], updated[:-2])
        history = original[2]
        self.assertEqual(history["role"], "user")
        self.assertEqual(json.loads(history["content"][0]["text"].split("\n",1)[1]),
                         {k:ctx[k] for k in ("reports","messages")})
        self.assertNotIn("reports", json.loads(original[-1]["content"].split("\n",1)[1]))
        self.assertIn("repair_feedback", updated[-1]["content"])
        self.assertEqual(sum("prompt_cache_breakpoint" in b for row in updated[:-2] for b in row["content"]), 4)

    def test_experiment_settings_bind_api_mode_and_reasoning(self):
        a=self.client(lambda *_:response(),reasoning_effort="low")
        b=self.client(lambda *_:response(),reasoning_effort="high")
        self.assertNotEqual(_settings(a),_settings(b))

    def test_duplicate_or_nonassistant_output_rejected(self):
        good=json.loads(response())["output"][1]
        for output in [[good,good],[{**good,"role":"user"}],[{**good,"status":"incomplete"}]]:
            with self.assertRaises(ModelError):self.client(lambda *_:response(output=output)).complete("planner",{})

    def test_cache_write_usage_and_returned_model_are_preserved_on_failure(self):
        usage = {"input_tokens": 12000, "output_tokens": 50,
                 "input_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 7000,
                     "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000}}}
        c = self.client(lambda *_: response(status="incomplete", usage=usage, model="served-snapshot", service_tier="default"))
        with self.assertRaises(ModelError): c.complete("planner", {})
        self.assertEqual(c.last_usage["cache_write_30m_tokens"], 3000)
        self.assertEqual(c.last_usage["cache_write_12h_tokens"], 4000)
        self.assertEqual(c.last_response_metadata["model"], "served-snapshot")
        c._transport = lambda *_: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(ModelError): c.complete("planner", {})
        self.assertEqual(c.last_usage, {})
        self.assertEqual(c.last_response_metadata, {})
