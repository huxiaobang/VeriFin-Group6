"""Usage: python3 audit/offline_check.py NEW_OUTPUT_DIR ; stdlib only, blocks outgoing sockets."""
from pathlib import Path
import contextlib,io,json,os,sys,hashlib,shutil,sqlite3,unittest,datetime
from collections import Counter
root=Path(__file__).resolve().parents[1]; code=root/'code'
output=Path(sys.argv[1]).resolve()
if output.exists() and any(output.iterdir()): raise SystemExit('Choose a new output directory.')
output.mkdir(parents=True,exist_ok=True);sys.dont_write_bytecode=True
for k in list(os.environ):
 if k.endswith('API_KEY'):os.environ.pop(k)
def guard(event,args):
 if event in ('socket.connect','socket.getaddrinfo','socket.sendto'): raise RuntimeError('offline audit blocked network')
sys.addaudithook(guard);sys.path.insert(0,str(code));os.chdir(code)
from verifin.cli import main
from scripts.demo_replay import run_demo
from verifin.evaluation import FrozenCase
from verifin.verification import check_rules
from verifin.retrieval import Corpus,FrozenEmbeddings,corpus_from_snapshots,evaluate
def read(f):return json.loads(Path(f).read_text())
def sha(f):return hashlib.sha256(Path(f).read_bytes()).hexdigest()
def cli(args):
 buf=io.StringIO()
 with contextlib.redirect_stdout(buf):rc=main(list(map(str,args)))
 if rc not in (None,0):raise RuntimeError((args,rc,buf.getvalue()[-500:]))
 return buf.getvalue()
res={'python':sys.version.split()[0],'new_project_api_calls':0,'network':'outbound sockets and DNS disabled','run_utc':str(datetime.datetime.now(datetime.timezone.utc))}
def save(): (output/'summary.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
demo=run_demo(output/'scripted',code/'data/replay')
res['scripted']={k:v for k,v in demo.items() if k not in ('exports','session_id')};save()
real={}
for sym,n in [('MCD',5),('PEP',3),('AXP',3)]:
 d=root/'demo'/sym; src=d/'run/session.sqlite3'; db=output/(sym+'.sqlite3');shutil.copyfile(src,db)
 sid=read(d/'run/sid.json'); manifest=read(d/'corpus/manifest.json')
 for f,h in manifest['files'].items():assert sha(d/'corpus'/f)==h['sha256'],f
 for i in range(1,n+1):
  path=output/sym/str(i)
  cli(['--db',db,'--data-dir',d/'corpus','export',sid,path,'--version',str(i)])
  orig=read(d/f'run/turn-{i}/report.json'); restored=read(path/'report.json')
  assert orig==restored,(sym,i,'archive report mismatch')
 real[sym]={'versions_exported_equal':n,'snapshot_hashes_ok':len(manifest['files'])}
res['real_saved_reports']=real;save()
task=read(code/'docs/planning_case.example.json')['turns'][0]['task']
(output/'task.json').write_text(json.dumps(task))
cli(['--db',output/'preview.sqlite3','planning','preview','--task',output/'task.json','--output',output/'planning'])
prev=read(output/'planning/preview.json')
res['planning_preview']={k:v for k,v in prev.items() if isinstance(v,(str,int,bool))};save()
f=root/'evaluation/e1-development';proto=read(f/'protocol.json');frozen={}
for k,m in proto['cases'].items():
 case=FrozenCase.from_context('draft-'+k,read(f/'cases'/f'{k}.json'))
 assert case.context_sha256==m['context_hash']; frozen[k]=len(check_rules(case.context()))
res['e1_frozen_rules_only']=frozen
dev=[json.loads(line) for line in (root/'evaluation/development18/pilot_agent_inputs.jsonl').read_text().splitlines() if line.strip()]
assert len(set(x['question_id'] for x in dev))==18
slots=read(code/'examples/study/heldout-slots.template.json')
assert len(slots['slots'])==12 and not any(s['gold_completed'] for s in slots['slots'])
res['split']={'development_by_prefix':dict(Counter(x['question_id'][0] for x in dev)),'reserved_empty_slots':12};save()
data=code/'data/evaluation'; corpus=Corpus(corpus_from_snapshots(code/'data/replay').documents,max_chars=600,overlap=80)
queries=read(data/'embedding_selection_development_queries.json')
res['e3']={}
for file in sorted((data/'embedding_selection_results').glob('*.embeddings.json')):
 vectors=FrozenEmbeddings(file,corpus); r=evaluate(corpus,queries,embeddings=vectors,limit=5,max_context_chars=3000,repeats=1)
 assert all(g['status']=='evaluated' for g in r['methods'].values())
 res['e3'][file.name.split('-chars')[0]]={m:{k:g[k] for k in ('macro_span_recall','mrr','latency_median_ms','latency_p95_ms','metadata_empty_return_rate','semantic_no_answer_empty_rate')} for m,g in r['methods'].items()}
 (output/(file.stem+'.ranks.json')).write_text(json.dumps(r))
res['e3_corpus']={'chunks':len(corpus.chunks),'queries':len(queries),'positive':sum(bool(q['relevant']) for q in queries),'fingerprint':corpus.fingerprint};save()
suites='test_sessions_e2e test_history test_scope_amendments test_bounded_delivery test_stop_handoffs test_calculation_operands test_market test_verification test_evaluation test_revision test_planning_evaluation test_study test_retrieval'.split()
suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.'+s for s in suites])
with (output/'regressions.txt').open('w') as log: result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
res['regressions']={'modules':suites,'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),'skipped':len(result.skipped)}
save();print(json.dumps(res,ensure_ascii=False,indent=2));sys.exit(not result.wasSuccessful())
