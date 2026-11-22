"""Default role limits are applied to actual outbound payloads, with local budgeting."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from verifin.model import ModelClient, ProviderOutputError, SpendLimitReached


class ResearchOutputDefaultsTest(unittest.TestCase):
    def wire(self, env):
        client = ModelClient.from_env({"VERIFIN_API_KEY": "mock", **env})
        requests = []

        def transport(request, timeout):
            body = json.loads(request.data)
            field = "max_output_tokens" if request.full_url.endswith("/responses") else "max_tokens"
            requests.append(body[field])
            if field == "max_output_tokens":
                return b'{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}'
            return b'{"choices":[{"finish_reason":"length"}]}'

        client._transport = transport
        for role in ("planner", "researcher", "critic"):
            with self.assertRaises(ProviderOutputError) as caught:
                client.complete(role, {})
            self.assertEqual(caught.exception.code, "max_output_tokens")
        return tuple(requests)

    def test_supported_default_and_nonmatching_model(self):
        default = (16384, 16384, 16384)
        for model, provider, expected in (
                ("gpt-6.1-sol", "openai", default),
                ("openai/gpt-6.1-sol", "compatible", default),
                ("qwen/qwen3.7-plus", "compatible", default),
                ("qwen/qwen3.7-plus-2026-11-22", "compatible", default),
                ("deepseek/deepseek-v4.1-flash-1017", "compatible", default),
                ("qwen/qwen3-32b", "compatible", (4096, 4096, 4096)),
                ("deepseek/deepseek-v4.1-flash", "compatible", (4096, 4096, 4096))):
            with self.subTest(model=model):
                env = {"VERIFIN_PROVIDER": provider, "VERIFIN_MODEL": model}
                if provider == "compatible":
                    env["VERIFIN_BASE_URL"] = "https://openrouter.ai/api/v1"
                self.assertEqual(self.wire(env), expected)

    def test_explicit_overrides_include_higher_global_and_qwen_roles(self):
        env = {"VERIFIN_BASE_URL": "https://openrouter.ai/api/v1", "VERIFIN_MODEL": "qwen/qwen3.7-plus",
               "VERIFIN_MAX_OUTPUT_TOKENS": "24576", "VERIFIN_QWEN_RESEARCHER_MAX_OUTPUT_TOKENS": "13000",
               "VERIFIN_QWEN_CRITIC_MAX_OUTPUT_TOKENS": "15000"}
        self.assertEqual(self.wire(env), (24576, 13000, 15000))
        env["VERIFIN_MODEL"] = "deepseek/deepseek-v4.1-flash-1017"
        self.assertEqual(self.wire(env), (24576, 24576, 24576))

    def test_larger_token_default_respects_existing_usd_admission(self):
        rates = dict(input=0, output=100, cached_input=0, write_30m=0, write_12h=0)
        catalog = {"schema_version": 1, "currency": "USD", "as_of": "2026-11-22",
                   "source_url": "https://example.test/price-fixture", "base_url": "https://api.openai.com/v1",
                   "service_tier": "default",
                   "models": {"gpt-6.1-sol": dict(short=rates, long=rates, long_context_above=100000)}}
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            price_path = directory / "prices.json"
            price_path.write_text(json.dumps(catalog), encoding="utf-8")
            client = ModelClient.from_env({"VERIFIN_PROVIDER": "openai", "VERIFIN_MODEL": "gpt-6.1-sol",
                "VERIFIN_API_KEY": "mock", "VERIFIN_PRICE_CATALOG": str(price_path),
                "VERIFIN_SPEND_LIMIT_USD": "1.00", "VERIFIN_SPEND_LEDGER": str(directory / "spend.sqlite")})
            def not_sent(*_args):
                self.fail("A larger token estimate exceeded the existing USD cap")
            client._transport = not_sent
            with self.assertRaises(SpendLimitReached):
                client.complete("researcher", {})
