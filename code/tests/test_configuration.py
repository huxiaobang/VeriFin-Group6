import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verifin.configuration import tool_options
from tests.test_market import mapping, sample


class ConfigurationTests(unittest.TestCase):
    def test_local_snapshot_and_adapter_configuration_never_contact_provider(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "market.json").write_text(json.dumps(sample()))
            (path / "adapter.json").write_text(json.dumps({"instruments": [mapping()]}))
            args = argparse.Namespace(market_data=path / "market.json", market_config=path / "adapter.json", live=False)
            with patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": "unit-test-secret"}), patch("urllib.request.urlopen", side_effect=AssertionError("No request permitted")):
                result = tool_options(args)
            self.assertEqual((path / "market.json").resolve(), result["market_data"])
            self.assertFalse(result["market_adapter"].allow_network)

    def test_config_rejects_embedded_credentials_and_truthy_string_flags(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            args = argparse.Namespace(market_config=path, live=False)
            for value in ({"instruments": [mapping()], "api_key": "secret"},
                          {"instruments": [mapping()], "allow_premium": "false"}):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    tool_options(args)
