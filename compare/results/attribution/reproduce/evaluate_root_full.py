#!/usr/bin/env python3
import json
from pathlib import Path

out=Path('compare/results/attribution')
repo_root=Path(__file__).resolve().parents[4]
reference=json.loads((repo_root/'datasets/FuxiBench/annotations/attribution-bench/root-reference.json').read_text())
refs=reference['scenarios']
rows=[]
for sid, ref in refs.items():
    predp=out/'predictions'/f'{sid}.json'
    pred=json.loads(predp.read_text()) if predp.exists() else {'root_set':[]}
    ps=pred['root_set']; target=ref['root_agent']
    known=target is not None
    hit=bool(known and target in ps)
    rows.append({'scenario_id':sid,'label':ref['label'],'reference_root':target,
                 'predicted_roots':ps,'known':known,
                 'root_at_1':bool(known and ps and ps[0]==target),
                 'root_at_2':bool(known and target in ps[:2]),
                 'root_at_3':bool(known and target in ps[:3]),
                 'root_in_candidates':hit,
                 'rr':(1/(ps.index(target)+1) if known and target in ps else 0.0) if known else None,
                 'root_set_f1':(2/(1+len(ps)) if hit else 0.0) if known else None,
                 'candidate_count':len(ps)})

def agg(rs):
    known=[x for x in rs if x['known']]
    n=len(rs); k=len(known)
    return {'n':n,'known_n':k,'coverage':k/n if n else None,
            'root_at_1':(sum(x['root_at_1'] for x in known)/k) if k else None,
            'root_at_2':(sum(x['root_at_2'] for x in known)/k) if k else None,
            'root_at_3':(sum(x['root_at_3'] for x in known)/k) if k else None,
            'mrr':(sum(x['rr'] for x in known)/k) if k else None,
            'candidate_coverage':(sum(x['root_in_candidates'] for x in known)/k) if k else None,
            'root_set_f1':(sum(x['root_set_f1'] for x in known)/k) if k else None,
            'mean_candidate_count':(sum(x['candidate_count'] for x in rs)/n) if n else None}
metrics={'schema':'mtagentlens.t6-root/1','cohort':'30 multi-agent scenarios',
         'human_annotators':reference['human_annotators'],
         'reference_method':reference['method'],
         'all':agg(rows),
         'attack':agg([x for x in rows if x['label']=='attack']),
         'benign':agg([x for x in rows if x['label']=='benign']),
         'rows':rows}
(out/'metrics'/'t6-root.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in metrics.items() if k not in ('rows',)},ensure_ascii=False,indent=2))
