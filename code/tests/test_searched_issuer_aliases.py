"""Exercise alias provenance through frozen-source tool calls and durable receipts."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from verifin.engine import ResearchEngine
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class SearchedIssuerAliasesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.corpus = Path(self.temp.name) / 'corpus'
        self.corpus.mkdir()
        files, documents = {}, []
        companies = [
            {'ticker': 'DIS', 'cik': '0001744489', 'name': 'The Walt Disney Company'},
            {'ticker': 'HD', 'cik': '0000354950', 'name': 'The Home Depot'},
        ]
        for company in companies:
            name = company['ticker'] + '.html'
            raw = ('<html><body>' + company['name'] +
                   ' reported quarterly revenue of 100 million dollars.</body></html>').encode()
            (self.corpus / name).write_bytes(raw)
            text_name = company['ticker'] + '.txt'
            text = (company['name'] + ' reported quarterly revenue of 100 million dollars.').encode()
            (self.corpus / text_name).write_bytes(text)
            files[text_name] = {
                'sha256': hashlib.sha256(text).hexdigest(),
                'url': 'https://example.test/' + name,
                'retrieved_at': '2025-08-20T00:00:00Z',
            }
            files[name] = {
                'sha256': hashlib.sha256(raw).hexdigest(),
                'url': 'https://example.test/' + name,
                'retrieved_at': '2025-08-20T00:00:00Z',
            }
            documents.append({'entity': company['ticker'], 'source': name, 'text': text_name,
                              'period_end': '2025-06-30', 'published_at': '2025-08-01',
                              'publication_precision': 'date_only',
                              'publication_basis': 'Synthetic fixed publication date',
                              'extraction_method': 'synthetic text'})
        (self.corpus / 'manifest.json').write_text(json.dumps({
            'companies': companies, 'files': files, 'documents': documents,
        }))
        self.path = Path(self.temp.name) / 'session.db'
        self.store = SessionStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.sid = self.store.create_session()['id']
        self.tools = ToolRegistry(self.store, self.corpus, allow_network=False, cache_dir=Path(self.temp.name)/'cache')

    def search(self, entity='Disney', cutoff='2025-08-20', query='revenue', scope=None):
        args = dict(entity=entity, query=query, cutoff=cutoff)
        observation = self.tools.execute('search_documents', args, self.sid, scope or {})
        self.store.record_tool_return(self.sid, dict(type='tool_call', episode_id='alias-test', tool='search_documents', arguments=args, ok=observation['ok'], observation=observation))
        return observation

    def test_successful_search_receipt_persists_and_asof_still_filters(self):
        self.assertEqual({}, self.tools.searched_issuer_aliases(self.sid, ['Disney']))
        result = self.search()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['evidence'], result)
        self.assertEqual({'disney':'DIS'}, self.tools.searched_issuer_aliases(self.sid, ['Disney']))
        self.store.close()
        self.store = SessionStore(self.path)
        self.tools = ToolRegistry(self.store, self.corpus, allow_network=False, cache_dir=Path(self.temp.name)/'cache')
        self.assertEqual({'disney':'DIS'}, self.tools.searched_issuer_aliases(self.sid, ['Disney']))
        earlier = self.search(cutoff='2025-01-01', scope={'entities':['Disney']})
        self.assertFalse(earlier['ok'], earlier)
        self.assertFalse(earlier['evidence'], earlier)
        other = self.search(entity='HD', scope={'entities':['Disney']})
        self.assertTrue(other['ok'], other)
        self.assertEqual('HD', other['data']['filters']['entity'])
        self.assertTrue(all(e['entity'] == 'HD' for e in other['evidence']))

    def test_failed_search_cannot_bind(self):
        result = self.search(entity='NotAnIssuer')
        self.assertFalse(result['ok'], result)
        self.assertEqual({}, self.tools.searched_issuer_aliases(self.sid, ['NotAnIssuer']))

    def test_empty_search_cannot_bind(self):
        result = self.search(cutoff='2025-01-01')
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['evidence'], result)
        self.assertEqual({}, self.tools.searched_issuer_aliases(self.sid, ['Disney']))

    def test_ambiguous_receipts_cannot_bind_and_known_issuer_is_never_rewritten(self):
        result = self.search()
        self.assertTrue(result['ok'], result)
        conflicting = copy.deepcopy(result)
        conflicting['data']['filters']['entity'] = 'HD'
        self.store.record_tool_return(self.sid, dict(type='tool_call', episode_id='alias-test', tool='search_documents', arguments={'entity':'Disney'}, ok=True, observation=conflicting))
        self.assertEqual({}, self.tools.searched_issuer_aliases(self.sid, ['Disney']))
        self.assertEqual({}, self.tools.searched_issuer_aliases(self.sid, ['HD', 'DIS', 'The Walt Disney Company']))

    def test_context_keeps_searched_passage_and_respects_scope_and_asof(self):
        result = self.search()
        self.assertTrue(result['ok'], result)
        evidence_id = result['evidence'][0]['id']
        scope = {'entities': ['Disney'], 'cutoff': '2025-08-20'}
        self.store.update_scope(self.sid, scope)
        episode = {'id': 'alias-test', 'request': 'What was Disney revenue?',
                   'active_claim_ids': [], 'recent_tool_results': [], 'issues': []}
        engine = ResearchEngine(self.store, None, self.tools)
        context = engine._context(self.sid, episode)
        visible = next(e for e in context['evidence'] if e['id'] == evidence_id)
        self.assertIn('quarterly revenue', visible.get('text', ''))
        self.store.update_scope(self.sid, {**scope, 'cutoff': '2025-01-01'})
        earlier = engine._context(self.sid, episode)
        self.assertFalse(any(e['id'] == evidence_id and e.get('text') for e in earlier['evidence']))
        self.store.update_scope(self.sid, {**scope, 'entities': ['HD']})
        other = engine._context(self.sid, episode)
        self.assertFalse(any(e['id'] == evidence_id and e.get('text') for e in other['evidence']))
