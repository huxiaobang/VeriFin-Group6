import json
import unittest

from verifin.model import ModelClient, ModelError, OutputContractError, ProviderOutputError


class QwenOutputTest(unittest.TestCase):
    def client(self, model='qwen/qwen3.7-plus', cap='8192'):
        return ModelClient.from_env({'VERIFIN_MODEL':model, 'VERIFIN_BASE_URL':'https://example.test/v1',
                                     'VERIFIN_API_KEY':'mock', 'VERIFIN_QWEN_RESEARCHER_MAX_OUTPUT_TOKENS':cap})

    def test_role_and_model_limits_on_the_wire(self):
        for model in ('qwen/qwen3.7-plus','another/model'):
            client = self.client(model,'12000')
            for role in ('planner','researcher','critic'):
                limits = []
                def transport(request, timeout):
                    limits.append(json.loads(request.data)['max_tokens'])
                    return b'{"choices":[{"finish_reason":"length"}]}'
                client._transport = transport
                with self.assertRaises(ProviderOutputError) as err:
                    client.complete(role,{})
                self.assertEqual(err.exception.code,'max_output_tokens')
                expected = 12000 if role == 'researcher' else 16384
                self.assertEqual(limits, [expected if model.startswith('qwen/') else 4096])
                self.assertEqual(client.last_response_metadata['max_output_tokens'],limits[0])
        for cap in ('0','16385','free'):
            with self.assertRaises(ModelError):
                self.client(cap=cap)

    def test_default_role_limit_respects_explicit_global_limit(self):
        env = {"VERIFIN_MODEL": "qwen/qwen3.7-plus", "VERIFIN_BASE_URL": "https://example.test/v1",
               "VERIFIN_API_KEY": "mock"}
        for settings, limit in ((env, 16384), ({**env, "VERIFIN_MAX_OUTPUT_TOKENS": "2048"}, 2048)):
            client = ModelClient.from_env(settings)
            def transport(request, timeout):
                self.assertEqual(json.loads(request.data)["max_tokens"], limit)
                return b'{"choices":[{"finish_reason":"length"}]}'
            client._transport = transport
            for role in ("critic", "researcher"):
                with self.assertRaises(ProviderOutputError):
                    client.complete(role, {})

    def test_openrouter_critic_configuration_on_the_wire(self):
        env = {"VERIFIN_MODEL": "qwen/qwen3.7-plus", "VERIFIN_BASE_URL": "https://openrouter.ai/api/v1",
               "VERIFIN_API_KEY": "mock", "VERIFIN_MAX_OUTPUT_TOKENS": "4096",
               "VERIFIN_QWEN_CRITIC_MAX_OUTPUT_TOKENS": "12000"}
        for extra, role, limit, reasoning in [
            ({}, "critic", 12000, {"reasoning": {"effort": "low"}}),
            ({}, "researcher", 4096, {}),
            ({}, "planner", 4096, {}),
            ({"VERIFIN_REASONING_EFFORT": "medium"}, "critic", 12000, {"reasoning_effort": "medium"}),
            ({"VERIFIN_ENABLE_THINKING": "false"}, "critic", 12000, {"enable_thinking": False}),
            ({"VERIFIN_BASE_URL": "https://other.test/v1"}, "critic", 12000, {}),
            ({"VERIFIN_MODEL": "another/model"}, "critic", 4096, {}),
        ]:
            with self.subTest(extra=extra, role=role):
                client = ModelClient.from_env({**env, **extra})
                def transport(request, timeout):
                    body = json.loads(request.data)
                    self.assertEqual({k: body[k] for k in ("reasoning", "reasoning_effort", "enable_thinking")
                                      if k in body}, reasoning)
                    field = "max_completion_tokens" if "reasoning_effort" in reasoning else "max_tokens"
                    self.assertEqual(body[field], limit)
                    return b'{"choices":[{"finish_reason":"length"}]}'
                client._transport = transport
                with self.assertRaises(ProviderOutputError):
                    client.complete(role, {})
                self.assertEqual(client.last_response_metadata["max_output_tokens"], limit)
        for invalid in ("0", "16385", "bad"):
            with self.assertRaises(ModelError):
                ModelClient.from_env({**env, "VERIFIN_QWEN_CRITIC_MAX_OUTPUT_TOKENS": invalid})

    def test_finish_signal_overrules_token_proximity_and_usage_survives(self):
        client = self.client()
        for finish, content, code in [
            ('length','{}','max_output_tokens'), ('stop','{','invalid_researcher_json'),
            ('stop','', 'empty_researcher_output'), ('stop','{}','missing_or_invalid_claims'),
            ('content_filter','', 'content_filter'), (None,'{}','not_completed')]:
            payload = {'choices':[{'finish_reason':finish,'native_finish_reason':'private-provider-text',
                                   'message':{'role':'assistant','content':content}}],
                       'usage':{'completion_tokens':4094,'completion_tokens_details':{'reasoning_tokens':3325}}}
            client._transport = lambda *_:json.dumps(payload).encode()
            with self.subTest(finish=finish,content=content), self.assertRaises((OutputContractError,ProviderOutputError)) as err:
                client.complete('researcher',{})
            self.assertEqual(err.exception.code,code)
            self.assertEqual(client.last_usage['completion_tokens'],4094)
            self.assertEqual(client.last_usage['reasoning_tokens'],3325)
            self.assertEqual(client.last_response_metadata['finish_reason'],finish or 'missing')
            self.assertEqual(client.last_response_metadata['native_finish_reason'],'other')
