import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verifin.cli import main
from verifin.engine import ResearchEngine
from verifin.evaluation import FrozenCase
from verifin.evaluation_cli import capture_callback, case_record, load_case, run_evaluation, write_new_json
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from tests.test_evaluation import ReviewFixture
from tests.test_repair_loop import TwoRepairFixture
from tests.test_verification import fixture


class EvaluationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def case(self):
        case = FrozenCase.from_context('fixture-case', fixture())
        path = self.root / 'case.json'
        write_new_json(path, {**case_record(case), 'generator': {'model_mode': 'scripted_fixture'}})
        return path

    def test_capture_precedes_first_review_and_does_not_replace_with_repaired_answer(self):
        path = self.root / 'capture.json'
        with SessionStore(self.root / 'sessions.sqlite3') as store:
            sid = store.create_session('Capture integration fixture')['id']
            model = TwoRepairFixture()
            tools = ToolRegistry(store, Path(__file__).resolve().parents[1] / 'data/replay')
            result = ResearchEngine(store, model, tools, candidate_callback=capture_callback(path)).ask(sid, 'MSFT FY2025 Q2 revenue?')
        self.assertEqual('complete', result['status'])
        self.assertEqual(3, model.drafts)
        case = load_case(path)
        self.assertEqual(65_585_000_000, case.context()['claims'][0]['asserted_value'])
        self.assertEqual(69_632_000_000, result['report']['claims'][0]['asserted_value'])
        self.assertNotIn('status', case.context()['claims'][0])
        self.assertNotIn('issues', case.context())
        self.assertEqual('scripted_fixture', json.loads(path.read_text())['generator']['model_mode'])
        with self.assertRaises(ValueError):
            capture_callback(path)

    def test_cli_detection_saves_both_reviews_and_original_case_without_database(self):
        path = self.case()
        output = self.root / 'run'
        model = ReviewFixture()
        with patch('verifin.cli.ModelClient.from_env', return_value=model), contextlib.redirect_stdout(io.StringIO()):
            code = main(['--db', str(self.root / 'must-not-exist.sqlite3'), 'evaluate', '--case', str(path), '--output', str(output)])
        self.assertEqual(0, code)
        self.assertEqual(2, len(model.calls))
        self.assertFalse((self.root / 'must-not-exist.sqlite3').exists())
        run = json.loads((output / 'detection.json').read_text())
        self.assertEqual(run['conditions']['generic_review']['review_input_sha256'], run['conditions']['structured_critic']['review_input_sha256'])
        self.assertFalse(json.loads((output / 'summary.json').read_text())['truth_scored'])
        self.assertEqual('scripted_fixture', json.loads((output/'case-source.json').read_text())['generator']['model_mode'])
        with self.assertRaises(ValueError):
            run_evaluation(path, output, model)
        self.assertEqual(2, len(model.calls))

    def test_tampered_or_duplicate_key_case_rejected_before_model_call(self):
        path = self.case()
        payload = json.loads(path.read_text())
        payload['context']['evidence'][0]['value'] = 999
        path.write_text(json.dumps(payload))
        model = ReviewFixture()
        with self.assertRaises(ValueError):
            run_evaluation(path, self.root/'run', model)
        self.assertEqual([], model.calls)
        path.write_text('{"case_id":"a","case_id":"b"}')
        with self.assertRaises(ValueError):
            load_case(path)

    def test_cli_shared_revision_runs_both_feedbacks_without_extra_critic(self):
        class RevisionFixture(ReviewFixture):
            def complete(self, role, context):
                if role not in {'revision_planner', 'researcher'}:
                    return super().complete(role, context)
                self.calls.append((role, context))
                self.last_usage = {'total_tokens': 10}
                if role == 'revision_planner':
                    return {'action': 'research' if context['claims'][0]['asserted_value'] != context['evidence'][0]['value'] else 'finish', 'reason': 'Scripted correction protocol'}
                original = context['claims'][0]
                claim = {key: original[key] for key in ('id','evidence_ids','calculation_ids','requirement_ids','constraints','asserted_unit')}
                value = context['evidence'][0]['value']
                claim.update(text=f'Quarterly revenue was USD {value}.', asserted_value=value)
                return {'claims':[claim], 'answered_requirement_ids':['r1'], 'unanswered_requirement_ids':[], 'summary':'SCRIPTED FIXTURE'}
        context = fixture()
        context['claims'][0].update(text='Quarterly revenue was USD 110.', asserted_value=110)
        with SessionStore(self.root/'specs.sqlite3') as store:
            context['tools'] = ToolRegistry(store, Path(__file__).resolve().parents[1]/'data/replay').list_tools()
        path = self.root/'revision-case.json'
        write_new_json(path, case_record(FrozenCase.from_context('revision-fixture', context)))
        model = RevisionFixture()
        output = self.root/'revised'
        with patch('verifin.cli.ModelClient.from_env', return_value=model), contextlib.redirect_stdout(io.StringIO()):
            code = main(['evaluate','--case',str(path),'--output',str(output),'--revise','--model-calls','4'])
        self.assertEqual(0, code)
        revisions = json.loads((output/'revisions.json').read_text())
        self.assertEqual({'generic_review','structured_critic'}, set(revisions['conditions']))
        for result in revisions['conditions'].values():
            self.assertEqual('candidate', result['status'], result)
            self.assertEqual(100, result['claims'][0]['asserted_value'])
            self.assertEqual('Pending Verification', result['claims'][0]['status'])
            self.assertFalse(result['truth_scored'])
            self.assertEqual(3, result['usage']['model_calls'])
        self.assertEqual(1, sum(role=='critic' for role,_ in model.calls))


if __name__ == '__main__':
    unittest.main()
