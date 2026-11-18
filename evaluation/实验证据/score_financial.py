"""Development examiner. Reads the frozen SEC facts after both conditions finish."""
import json
from pathlib import Path

def load(path):
    return json.loads(path.read_text())

def score(root, dest):
    periods={'latest-msft':('MSFT','2024-10-01','2024-12-31'),'prior-msft':('MSFT','2024-07-01','2024-09-30'),'latest-nvda':('NVDA','2024-07-29','2024-10-27')}
    evidence={}
    for name,(symbol,start,end) in periods.items():
        source=root/'code/data/replay/snapshots'/('sec_'+symbol.lower()+'_companyfacts.json')
        tag='Revenues' if symbol=='NVDA' else 'RevenueFromContractWithCustomerExcludingAssessedTax'
        observations=load(source)['facts']['us-gaap'][tag]['units']['USD']
        options=[r for r in observations if r.get('start')==start and r['end']==end and r['filed']<='2025-01-31' and r['form']=='10-Q']
        assert options and len({r['val'] for r in options})==1
        evidence[name]={'entity':symbol,'metric':'revenue','period_start':start,'period_end':end,'value':options[0]['val'],'form_record':options[0],'source':str(source.relative_to(root)),'tag':tag}

    outcomes=[]
    for row in load(dest/'e2-turns.json'):
        key='latest-msft'
        if row['turn']==2:
            key={'04-switch':'latest-nvda','02-previous':'prior-msft'}[row['case']]
        reference=evidence[key]
        def correct(claim):
            dims=claim['constraints']
            return claim.get('asserted_value')==reference['value'] and all(dims.get(k)==reference[k] for k in ('entity','metric','period_start','period_end')) and dims.get('unit')=='USD'
        def matches(record):
            return all(reference[k]==record.get(k) for k in ('entity','metric','period_start','period_end','value'))
        outcomes.append({'case':row['case'],'arm':row['arm'],'turn':row['turn'],'source_key':key,'controller':row['status'],'correct_record_acquired':any(map(matches,row['available_data'])),'first_claims_match':bool(row['first_claims']) and all(map(correct,row['first_claims'])),'final_claims_match':bool(row['claims']) and all(map(correct,row['claims'])),'reported_values':[c['asserted_value'] for c in row['claims']]})
    (dest/'e2-source-grades.json').write_text(json.dumps({'references':evidence,'rows':outcomes},ensure_ascii=False,indent=2)+'\n')
