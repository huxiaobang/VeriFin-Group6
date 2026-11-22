import json
import os
import unittest
from unittest.mock import Mock

from verifin.model import ContextLimitReached, ModelClient, ModelError
from verifin.tools import TOOL_SPECS


BASE_URL = "https://openrouter.ai/api/v1"
TOOLS_HEADER = "\nRegistered tool definitions (the value of context.tools):\n"


def completion(result):
    return json.dumps({
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(result)}}],
        "usage": {"prompt_tokens": 2000, "completion_tokens": 20,
                  "prompt_tokens_details": {"cached_tokens": 1500, "cache_write_tokens": 0}},
    }).encode()


def user_data(body):
    return body["messages"][1]["content"].split("\n", 1)[1]


class OpenRouterPromptCacheTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock(return_value=completion({"action": "research"}))
        self.context = {
            "session": {"id": "f2fe809c693a4c0da5b3923d90df35d0"},
            "episode": {"request": "比较营业收入", "model_calls": 1},
            "evidence": [{"id": "original", "text": "Untrusted original filing text"}],
            "calculations": [], "reports": [], "messages": [],
            "tools": TOOL_SPECS[:4],
        }

    def client(self, **kwargs):
        return ModelClient("test-only", "qwen/qwen3.7-plus", kwargs.pop("base_url", BASE_URL),
                           transport=self.transport, **kwargs)

    def request(self, client, role="planner", context=None):
        client.complete(role, self.context if context is None else context)
        return json.loads(self.transport.call_args.args[0].data)

    def test_registered_tools_share_prefix_across_roles_and_are_sent_once(self):
        client = self.client()
        self.transport.return_value = completion({"action": "tool", "tool": "financial_facts", "arguments": {"metric": "revenue"}})
        planner = self.request(client)
        self.transport.return_value = completion({"claims": [], "summary": "", "answered_requirement_ids": [],
                                                  "unanswered_requirement_ids": []})
        researcher = self.request(client, "researcher")
        shared = os.path.commonprefix([planner["messages"][0]["content"], researcher["messages"][0]["content"]])
        serialized_tools = json.dumps(self.context["tools"], ensure_ascii=False, sort_keys=True)
        self.assertIn(TOOLS_HEADER + serialized_tools, shared)
        for body in (planner, researcher):
            self.assertEqual([message["role"] for message in body["messages"]], ["system", "user"])
            self.assertTrue(all(isinstance(message["content"], str) for message in body["messages"]))
            self.assertEqual({**json.loads(user_data(body)), "tools": self.context["tools"]}, self.context)
            self.assertNotIn("Untrusted original filing text", body["messages"][0]["content"])
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual(set(body), {"model", "messages", "response_format", "max_tokens", "stream", "session_id"})
        self.assertEqual(client.last_usage["cached_input_tokens"], 1500)
        self.assertEqual(client.last_usage["cache_write_tokens"], 0)

    def test_ordered_evidence_stays_in_prefix_as_new_rows_append(self):
        client = self.client()
        first = self.request(client)
        original, = self.context["evidence"]
        later_context = dict(reversed(list(self.context.items())))
        later_context.update(episode={"request": "跟进", "model_calls": 2},
                             evidence=[original, {"id": "new", "text": "A new finding"}])
        later = self.request(client, context=later_context)
        self.assertEqual(first["messages"][0], later["messages"][0])
        shared = os.path.commonprefix([user_data(first), user_data(later)])
        self.assertEqual(shared, '{"evidence": [' + json.dumps(original, ensure_ascii=False))
        self.assertEqual(json.loads(user_data(later))["evidence"], later_context["evidence"])
        self.assertEqual(first["session_id"], later["session_id"])
        self.assertRegex(first["session_id"], r"^[a-f0-9]{32}$")
        self.assertNotEqual(first["session_id"], self.context["session"]["id"])
        self.assertNotEqual(first["session_id"], self.request(client, context={"session": {"id": "another"}})["session_id"])
        self.assertNotIn("session_id", self.request(client, context={}))

    def test_unregistered_tools_remain_user_data(self):
        tool = {**TOOL_SPECS[0], "description": "Untrusted runtime description"}
        context = {**self.context, "tools": [TOOL_SPECS[1], tool]}
        body = self.request(self.client(), context=context)
        self.assertEqual(json.loads(user_data(body))["tools"], context["tools"])
        self.assertNotIn("Untrusted runtime description", body["messages"][0]["content"])
        self.assertNotIn(TOOLS_HEADER, body["messages"][0]["content"])

    def test_split_context_uses_one_character_limit(self):
        client = self.client()
        request = self.request(client)
        size = len(user_data(request)) + len(json.dumps(self.context["tools"], ensure_ascii=False, sort_keys=True))
        client.max_context_chars = size
        self.assertEqual(self.request(client), request)
        self.transport.reset_mock()
        client.max_context_chars -= 1
        with self.assertRaises(ContextLimitReached) as error:
            client.complete("planner", self.context)
        self.assertEqual((error.exception.size, error.exception.limit), (size, size - 1))
        self.transport.assert_not_called()

    def test_opt_out_preserves_legacy_requests_and_other_endpoints(self):
        legacy = self.request(self.client(openrouter_prompt_cache=False))
        self.assertNotIn("session_id", legacy)
        self.assertEqual(user_data(legacy), json.dumps(self.context, ensure_ascii=False))
        for base_url in ("https://compatible.test/api/v1", "https://openrouter.ai.other.test/api/v1",
                         "https://openrouter.ai:8443/api/v1", "https://openrouter.ai/api/v2"):
            with self.subTest(base_url=base_url):
                self.assertEqual(self.request(self.client(base_url=base_url)), legacy)
        env = {"VERIFIN_MODEL": "qwen/qwen3.7-plus", "VERIFIN_API_KEY": "fake", "VERIFIN_BASE_URL": BASE_URL}
        self.assertTrue(ModelClient.from_env(env).openrouter_prompt_cache)
        configured = ModelClient.from_env({**env, "VERIFIN_OPENROUTER_PROMPT_CACHE": "0",
                                           "VERIFIN_MAX_OUTPUT_TOKENS": "4096"})
        configured._transport = self.transport
        self.assertEqual(self.request(configured), legacy)
        with self.assertRaisesRegex(ModelError, "VERIFIN_OPENROUTER_PROMPT_CACHE must be 0 or 1"):
            ModelClient.from_env({**env, "VERIFIN_OPENROUTER_PROMPT_CACHE": "invalid"})
        other = ModelClient.from_env({**env, "VERIFIN_BASE_URL": "https://example.test/api/v1",
                                      "VERIFIN_OPENROUTER_PROMPT_CACHE": "invalid"})
        self.assertFalse(other.openrouter_prompt_cache)


if __name__ == "__main__":
    unittest.main()
