#!/usr/bin/env python3
import json
from pathlib import Path

out=Path('compare/results/attribution')
repo_root=Path(__file__).resolve().parents[4]
reference=json.loads((repo_root/'datasets/FuxiBench/annotations/attribution-bench/root-reference-pilot.json').read_text())
refs=reference['scenarios']
rows=[]
for sid, ref in refs.items():
    pred=json.loads((out/'predictions'/f'{sid}.json').read_text())
    ps=pred['root_set']; target=ref['root_agent']
    rows.append({'scenario_id':sid,'reference_root':target,
                 'predicted_roots':ps,'root_at_1':bool(ps and ps[0]==target),
                 'root_at_3':target in ps[:3],
                 'root_in_candidates':target in ps,
                 'candidate_count':len(ps)})
n=len(rows)
metrics={'schema':'mtagentlens.t6-root-pilot/1','cohort':'5 multi-agent pilot',
         'human_annotators':reference['human_annotators'],
         'reference_method':reference['method'],
         'n':n,
         'root_at_1':sum(x['root_at_1'] for x in rows)/n,
         'root_at_3':sum(x['root_at_3'] for x in rows)/n,
         'candidate_coverage':sum(x['root_in_candidates'] for x in rows)/n,
         'mean_candidate_count':sum(x['candidate_count'] for x in rows)/n,
         'rows':rows}
(out/'metrics').mkdir(exist_ok=True)
(out/'metrics'/'t6-root-pilot.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in metrics.items() if k!='rows'},ensure_ascii=False,indent=2))
for x in rows: print(x['scenario_id'],x['root_at_1'],x['root_at_3'],x['candidate_count'])
