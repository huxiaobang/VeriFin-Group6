"""Offline subprocess transport tests; never invoke a real model or read auth files."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import TestCase
from unittest.mock import patch

from verifin.codex_provider import CodexModelClient, CodexTransportError, child_environment
from verifin.model import ModelClient, ModelError


FAKE = r'''
import json, os, sys, time
args = sys.argv[1:]
with open(LOG, 'a') as f:
    f.write(json.dumps({'args': args, 'env': dict(os.environ), 'cwd': os.getcwd()}) + '\n')
if args == ['login', 'status']:
    print('Logged in using an API key' if MODE == 'api_login' else 'Logged in using ChatGPT')
    sys.exit(0)
if '--help' in args:
    print('--ignore-user-config --ignore-rules --ephemeral --json --strict-config')
    sys.exit(0)
prompt = sys.stdin.read()
if MODE == 'hang':
    time.sleep(10)
if MODE == 'flood':
    print('x' * 100000, flush=True)
    time.sleep(10)
if MODE == 'failed':
    print('PRIVATE_CREDENTIAL_DIAGNOSTIC', file=sys.stderr)
    sys.exit(2)
if MODE == 'tool':
    print(json.dumps({'type':'item.started','item':{'type':'command_execution','command':'read a secret'}}), flush=True)
    time.sleep(10)
if MODE == 'unknown_event':
    print(json.dumps({'type':'future.action'}), flush=True)
    time.sleep(10)
if MODE == 'passive':
    for item_type in ['user_message','error','plan']:
        print(json.dumps({'type':'item.completed','item':{'type':item_type,'text':'PRIVATE_PASSIVE_TEXT'}}), flush=True)
if MODE in ['transient','terminal']:
    print(json.dumps({'type':'error','message':'Reconnecting; HTTP 403 network PRIVATE_DIAGNOSTIC'}), flush=True)
    if MODE == 'terminal':
        print(json.dumps({'type':'turn.failed','error':{'message':'PRIVATE_TERMINAL'}}), flush=True)
        sys.exit(1)
reply = 'not JSON' if MODE == 'invalid_role' else json.dumps({'action':'clarify','question':'Which company?','reason':'Need issuer.'})
print(json.dumps({'type':'thread.started','thread_id':'synthetic-thread'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':reply}}), flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':8,'cached_input_tokens':4}}), flush=True)
if MODE == 'extra_turn':
    print(json.dumps({'type':'turn.started'}), flush=True)
'''


class CodexProviderTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.root / 'codex'
        self.log = self.root / 'calls.jsonl'
        self.receipts = self.root / 'receipts'
        self.context = {'messages':[{'role':'user','content':'What was revenue?'}],
                        'scope':{}, 'requirements':[], 'tools':[], 'evidence':[], 'calculations':[]}

    def client(self, mode='ok', **kwargs):
        self.binary.write_text('#!' + sys.executable + '\nLOG=' + repr(str(self.log))
                               + '\nMODE=' + repr(mode) + '\n' + FAKE)
        self.binary.chmod(0o700)
        return CodexModelClient(binary=str(self.binary), receipts=self.receipts, **kwargs)

    def receipt(self):
        files = list(self.receipts.glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        return json.loads(files[0].read_text())

    def test_factory_uses_subscription_without_api_credentials_or_fallback(self):
        client = ModelClient.from_env({'VERIFIN_PROVIDER':'codex'})
        self.assertIsInstance(client, CodexModelClient)
        self.assertEqual(client.model, 'gpt-6.1-sol')
        self.assertEqual(client.api_mode, 'codex_exec')
        self.assertEqual(client.base_url, 'codex://local')
        self.assertFalse(self.receipts.exists())
        with self.assertRaises(ModelError):
            ModelClient.from_env({'VERIFIN_PROVIDER':'codex','VERIFIN_BASE_URL':'https://example.invalid/v1'})

    def test_success_reuses_role_validation_and_records_unpriced_subscription(self):
        client = self.client()
        with patch.dict(os.environ, {'OPENAI_API_KEY':'PRIVATE_KEY', 'HTTPS_PROXY':'PRIVATE_PROXY'}):
            result = client.complete('planner', self.context)
        self.assertEqual(result['action'], 'clarify')
        receipt = self.receipt()
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(receipt['role_output_validated'])
        self.assertEqual(receipt['billing_state'], 'subscription_unpriced')
        self.assertIsNone(receipt['api_dollars'])
        self.assertIsNone(receipt['served_model'])
        self.assertIsNone(receipt['remote_attempt_count'])
        self.assertFalse(receipt['output_token_cap_enforced'])
        self.assertEqual(receipt['requested_output_tokens'], 16384)
        self.assertEqual(client.last_usage['prompt_tokens'], 20)
        self.assertEqual(client.last_usage['completion_tokens'], 8)
        self.assertEqual(client.last_response_metadata['provider'], 'codex_subscription')
        calls = [json.loads(s) for s in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 3)  # status, capability help, one inference process
        for call in calls:
            self.assertNotIn('OPENAI_API_KEY', call['env'])
            self.assertNotIn('HTTPS_PROXY', call['env'])
            self.assertEqual(call['env'].get('HOME'), os.environ.get('HOME'))
        args = calls[-1]['args']
        self.assertIn('--ignore-user-config', args)
        self.assertIn('--ignore-rules', args)
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_provider="openai"', args)
        self.assertIn('mcp_servers={}', args)
        self.assertIn('project_doc_max_bytes=0', args)
        self.assertIn('features.skip_host_skill_discovery=true', args)
        self.assertIn('read-only', args)
        self.assertIn('shell_tool', args)
        self.assertNotEqual(calls[-1]['cwd'], os.getcwd())

    def test_api_login_is_rejected_without_logging_out_or_dispatching(self):
        client = self.client('api_login')
        with self.assertRaisesRegex(CodexTransportError, 'chatgpt_login_required'):
            client.complete('planner', self.context)
        calls = [json.loads(s) for s in self.log.read_text().splitlines()]
        self.assertEqual([row['args'] for row in calls], [['login','status']])
        self.assertFalse(self.receipt()['dispatched'])
        self.assertEqual(self.receipt()['billing_state'], 'not_dispatched')

    def test_missing_binary_is_clear_and_not_dispatched(self):
        client = CodexModelClient(binary=str(self.root / 'missing'), receipts=self.receipts)
        with self.assertRaisesRegex(CodexTransportError, 'codex_not_found'):
            client.complete('planner', self.context)
        self.assertFalse(self.receipt()['dispatched'])

    def test_invalid_financial_json_preserves_transport_and_rejection_receipt(self):
        client = self.client('invalid_role')
        with self.assertRaises(ModelError):
            client.complete('planner', self.context)
        self.assertEqual(self.receipt()['status'], 'invalid_role_output')
        self.assertFalse(self.receipt()['role_output_validated'])
        self.assertEqual(self.receipt()['billing_state'], 'subscription_unpriced')

    def test_failed_process_never_logs_raw_stderr_or_invents_zero_charge(self):
        client = self.client('failed')
        with self.assertRaises(CodexTransportError):
            client.complete('planner', self.context)
        receipt = self.receipt()
        self.assertEqual(receipt['billing_state'], 'unknown_subscription_usage')
        self.assertNotIn('PRIVATE_CREDENTIAL_DIAGNOSTIC', json.dumps(receipt))

    def test_tool_unknown_event_and_extra_turn_fail_closed(self):
        for mode, code in [('tool','unexpected_external_tool'),('unknown_event','unexpected_event'),
                           ('extra_turn','unexpected_additional_turn')]:
            with self.subTest(mode=mode):
                self.receipts = self.root / mode
                client = self.client(mode)
                with self.assertRaisesRegex(CodexTransportError, code):
                    client.complete('planner', self.context)
                self.assertEqual(self.receipt()['billing_state'], 'unknown_subscription_usage')

    def test_byte_limit_stops_process(self):
        client = self.client('flood', max_output_bytes=1024)
        started = time.monotonic()
        with self.assertRaisesRegex(CodexTransportError, 'output_limit'):
            client.complete('planner', self.context)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.receipt()['status'], 'output_limit')

    def test_timeout_kills_process_and_preserves_unknown_usage(self):
        client = self.client('hang', timeout=1.5)
        started = time.monotonic()
        with self.assertRaisesRegex(CodexTransportError, 'timeout'):
            client.complete('planner', self.context)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.receipt()['billing_state'], 'unknown_subscription_usage')

    def test_subscription_limit_reaches_a_draft_only_report(self):
        from verifin.reporting import report_view
        error = CodexTransportError("cli_turn_failed", ["subscription_limit"])
        report = {"request": {}, "scope": {}, "status": "partial", "stop_reason": "execution_error",
                  "codex_failure": {"role": "critic", "hint": error.user_hint},
                  "claims": [], "evidence": [], "calculations": [], "issues": []}
        self.assertIn("Critic: Your Codex account reported a usage or rate limit", report_view(report)["stage_message"])

    def test_inflight_cancellation_is_distinct_from_timeout(self):
        client = self.client('hang')
        checks = [False, False, True]
        client.cancel_check = lambda: checks.pop(0) if checks else True
        with self.assertRaisesRegex(CodexTransportError, 'cancelled'):
            client.complete('planner', self.context)
        self.assertEqual(self.receipt()['status'], 'cancelled')
        self.assertEqual(self.receipt()['billing_state'], 'unknown_subscription_usage')

    def test_child_environment_does_not_inherit_provider_or_token_overrides(self):
        with patch.dict(os.environ, {'CODEX_API_KEY':'secret','CODEX_ACCESS_TOKEN':'secret',
                'OPENAI_BASE_URL':'https://other.invalid','NODE_OPTIONS':'malicious',
                'PYTHONPATH':'malicious','CODEX_HOME':'own-local-auth-home'}):
            env = child_environment()
        self.assertEqual(env['CODEX_HOME'], 'own-local-auth-home')
        for name in ['CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_BASE_URL','NODE_OPTIONS','PYTHONPATH']:
            self.assertNotIn(name, env)

    def test_passive_protocol_metadata_is_not_mistaken_for_active_tools(self):
        client = self.client('passive')
        self.assertEqual(client.complete('planner', self.context)['action'], 'clarify')
        receipt = self.receipt()
        self.assertEqual(receipt['passive_item_counts'], {'user_message':1,'error':1,'plan':1})
        self.assertNotIn('PRIVATE_PASSIVE_TEXT', json.dumps(receipt))
        self.assertEqual(receipt['last_event_shape']['event_type'], 'turn.completed')

    def test_disallowed_item_records_shape_without_command_content(self):
        client = self.client('tool')
        with self.assertRaises(CodexTransportError):
            client.complete('planner', self.context)
        receipt = self.receipt()
        shape = receipt['rejected_event_shape']
        self.assertEqual(shape['item_type'], 'command_execution')
        self.assertIn('command', shape['item_keys'])
        self.assertNotIn('read a secret', json.dumps(receipt))

    def test_transient_top_level_error_does_not_kill_official_cli_reconnect(self):
        client = self.client('transient')
        self.assertEqual(client.complete('planner', self.context)['action'], 'clarify')
        receipt = self.receipt()
        self.assertEqual(receipt['status'], 'completed')
        self.assertIn('transport_retry', receipt['diagnostics'])
        self.assertIn('http_403', receipt['diagnostics'])
        self.assertNotIn('PRIVATE_DIAGNOSTIC', json.dumps(receipt))
        args = [json.loads(row)['args'] for row in self.log.read_text().splitlines()][-1]
        self.assertFalse(any('chatgpt_base_url' in arg or 'system_proxy_fallback' in arg for arg in args))
        self.assertNotIn('unbounded_connection_retries', args)
        self.assertNotIn('network_proxy', args)

    def test_terminal_failure_is_not_accepted_after_transient_warning(self):
        client = self.client('terminal')
        with self.assertRaisesRegex(CodexTransportError, 'cli_turn_failed.*http_403'):
            client.complete('planner', self.context)
        receipt = self.receipt()
        self.assertEqual(receipt['status'], 'cli_turn_failed')
        self.assertEqual(receipt['billing_state'], 'unknown_subscription_usage')
        self.assertNotIn('PRIVATE_TERMINAL', json.dumps(receipt))
