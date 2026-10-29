import json,sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[4]
raw=json.loads((root/'compare/results/overhead/raw/full-system/report/raw-per-run.json').read_text())
rows=[]
for r in raw['runs']:
 if r['profile']!='C3':continue
 db=next((root/r['source_result'].replace('local/overhead-2026-10-28/study-v4/','compare/results/overhead/raw/full-system/')).parent.glob('*.db'))
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
 sql="a.normalized_args_json like '%/tmp/mtloh-%/.aws/credentials%' and a.action_type='FileOpen' and a.outcome='succeeded'"
 total=c.execute(f'select count(distinct a.action_id) from actions a where {sql}').fetchone()[0]
 linked=c.execute(f'select count(distinct a.action_id) from actions a join correlation_evidence e on e.action_id=a.action_id and e.run_id=a.run_id where {sql}').fetchone()[0]
 pids=c.execute('select count(distinct pid) from intents').fetchone()[0]
 rows.append(dict(condition_id=r['condition_id'],concurrency=r['concurrency'],credential_successful_actions=total,credential_actions_with_evidence=linked,intent_pids=pids,alert_linked_operations=r['alert_linked_operations'],controlled_operations=r['controlled_operations_observed']))
 c.close()
p=root/'compare/results/overhead/data/credential_linkage.json';p.write_text(json.dumps(dict(rows=rows,scope='credential actions are not attack-detection ground truth; incomplete linkage cannot be explained away by other irrelevant controlled opens'),indent=2)+'\n')
for n in (1,4,8,16,32):print(n,[ (r['credential_successful_actions'],r['credential_actions_with_evidence'],r['intent_pids']) for r in rows if r['concurrency']==n])
