"""Run: python3 evaluation/实验证据/reproduce.py EMPTY_OUTPUT_DIRECTORY"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CODE = ROOT / 'code'
sys.dont_write_bytecode = True
sys.path.insert(0, str(CODE))

def reject_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        raise RuntimeError('Offline experiment prohibits network access')
sys.addaudithook(reject_network)

from verifin.evaluation import evaluate_frozen_case
from verifin.evaluation_cli import load_case
from verifin.model import ModelClient
from verifin.planning_evaluation import FrozenPlanningCase, run_planning_pair
from verifin.tools import ToolRegistry
from verifin.retrieval import Corpus, FrozenEmbeddings, corpus_from_snapshots, evaluate
from tests.test_planning import ProtocolModel

def read(path):
    return json.loads(path.read_text())

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()

def compare_inputs(case, focus):
    wires = []
    def capture(request, timeout):
        body = json.loads(request.data)
        user = [m for m in body['input'] if m['role'] == 'user']
        wires.append({'user_sha256':fingerprint(user),'user_chars':sum(len(m['content']) for m in user if isinstance(m['content'],str)),'controls':{k:body.get(k) for k in ('model','reasoning','service_tier','max_output_tokens')}})
        raise RuntimeError('Captured before transmission')
    model = ModelClient.from_env({'VERIFIN_PROVIDER':'openai','VERIFIN_MODEL':'gpt-6.1-sol','OPENAI_API_KEY':'offline','VERIFIN_REASONING_EFFORT':'medium','VERIFIN_MAX_OUTPUT_TOKENS':'8192','VERIFIN_MAX_CONTEXT_CHARS':'200000','VERIFIN_CACHE_SHARED_TOOLS':'1'})
    model.focus_evidence=focus
    model._transport=capture
    run=evaluate_frozen_case(case,model)
    return {'matched':len(wires)==2 and wires[0]==wires[1],'wire':wires,'role_order':run['review_order'],'rule_findings':run['conditions']['rules_only']['feedback']['rule_findings']}

def e1(out):
    trials = {}
    case_files=sorted((HERE.parent/'e1-development/cli-cases').glob('*.json'))
    case_files+=sorted((HERE/'mcd-variants').glob('*.json'))
    for path in case_files:
        case=load_case(path)
        trials[path.stem]={'production_focus':compare_inputs(case,True),'same_full_evidence':compare_inputs(case,False)}
    save(out/'e1-payloads.json',trials)
    # Two forms of presentation remain recognizable. Only condition identifiers are hidden.
    for n, path in enumerate(sorted((HERE/'archive').glob('*/detection.json'))):
        det=read(path); original=read(path.parent/'case.json')
        assert det['context_sha256']==original['context_sha256']
        pair={}
        for slot,method in zip('AB',('structured_critic','generic_review') if n%2 else ('generic_review','structured_critic')):
            result=det['conditions'][method]
            assert result['status']=='ok'
            pair[slot]=result['feedback']
        save(out/'e1-reader'/f'{path.parent.name}.json',{'request':original['context'].get('request'),'claims':original['context']['claims'],'evidence':original['context']['evidence'],'reviews':pair})

class FactsOutage(ToolRegistry):
    evaluation_config={'fault':'financial_facts unavailable in both arms'}
    def execute(self,name,arguments,session_id,scope):
        if name=='financial_facts':
            return {'ok':False,'summary':'Controlled source outage','data':{'network_requests':0},'evidence':[],'calculations':[]}
        return super().execute(name,arguments,session_id,scope)

def e2(out):
    rows=[]
    for i,path in enumerate(sorted((HERE/'e2-cases').glob('*.json'))):
        case=FrozenPlanningCase.from_dict(read(path))
        fault=path.stem=='03-source-outage'
        factory=(lambda s,d,c:FactsOutage(s,d,cache_dir=c)) if fault else None
        order=('fixed','adaptive') if i%2 else ('adaptive','fixed')
        with tempfile.TemporaryDirectory() as tmp:
            run=run_planning_pair(case,ProtocolModel,CODE/'data/replay',output_dir=tmp,condition_order=order,tools_factory=factory)
            hashes={(arm['source_manifest_sha256'],arm['settings_sha256']) for arm in run['conditions'].values()}
            assert len(hashes)==1 and run['model_mode']=='scripted_protocol_fixture'
            for arm,data in run['conditions'].items():
                for index,turn in enumerate(data['turns'],1):
                    report=turn['report'] or {}
                    rows.append({'case':path.stem,'arm':arm,'turn':index,'case_sha':case.context_sha256,'common_hashes':list(next(iter(hashes))),'source_fault':fault,'status':turn['status'],'stop':turn['episode']['stop_reason'],'calls':turn['episode']['model_calls'],'tools':[{'name':e.get('tool'),'ok':e.get('ok')} for e in turn['events'] if e['type']=='tool_call'],'first_claims':[{k:c.get(k) for k in ('text','constraints','asserted_value')} for c in (turn.get('before_verification') or {}).get('claims',[])],'claims':[{k:c.get(k) for k in ('text','constraints','asserted_value','status')} for c in report.get('claims',[])],'available_data':[{k:e.get(k) for k in ('entity','metric','period_start','period_end','value')} for e in report.get('evidence',[]) if e.get('value') is not None]})
    save(out/'e2-turns.json',rows)

def e3(out):
    data=CODE/'data/evaluation'
    corpus=Corpus(corpus_from_snapshots(CODE/'data/replay').documents,max_chars=600,overlap=80)
    queries=read(data/'embedding_selection_development_queries.json')
    artifact=next((data/'embedding_selection_results').glob('qwen*.embeddings.json'))
    vectors=FrozenEmbeddings(artifact,corpus)
    by_id={q['id']:q for q in queries}
    rows=[]
    for k,chars in ((1,3000),(3,3000),(5,3000),(8,3000),(8,4800),(12,7200)):
        result=evaluate(corpus,queries,embeddings=vectors,limit=k,max_context_chars=chars,repeats=1)
        for method,run in result['methods'].items():
            assert run['status']=='evaluated'
            for v in run['cases']:
                q=by_id[v['id']]
                rows.append({'method':method,'k':k,'budget_chars':chars,'id':v['id'],'query':q['query'],'base':q['id'].split('--')[0],'gold':q['relevant'],**v})
    save(out/'e3-queries.json',rows)
    matrix=[]
    for k,chars in ((1,3000),(3,3000),(5,3000),(8,3000),(8,4800),(12,7200)):
        grouped={method:{r['id']:r for r in rows if r['method']==method and r['k']==k and r['budget_chars']==chars and r['recall'] is not None} for method in ('dense','hybrid','bm25')}
        dense,hybrid=grouped['dense'],grouped['hybrid']
        matrix.append({'k':k,'budget_chars':chars,'mean_chars':{name:round(sum(v['context_chars'] for v in g.values())/len(g)) for name,g in grouped.items()},'positive_expressions':len(dense),'hits':{name:sum(x['recall']==1 for x in g.values()) for name,g in grouped.items()},'hybrid_recovers':[i for i in dense if hybrid[i]['recall']>dense[i]['recall']],'hybrid_loses':[i for i in dense if hybrid[i]['recall']<dense[i]['recall']],'all_three_variants':{name:sum(all(x['recall']==1 for x in g.values() if x['base']==base) for base in {x['base'] for x in g.values()}) for name,g in grouped.items()}})
    save(out/'e3-pairs.json',matrix)

if __name__=='__main__':
    dest=Path(sys.argv[1]).resolve()
    if dest.exists() and any(dest.iterdir()):raise SystemExit('Choose a fresh directory')
    dest.mkdir(parents=True,exist_ok=True)
    e1(dest)
    e2(dest)
    e3(dest)
    sys.path.insert(0, str(HERE))
    from score_financial import score
    score(ROOT, dest)
