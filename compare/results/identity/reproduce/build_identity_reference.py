#!/usr/bin/env python3
"""Merge declared process-lineage anchors with 3-reviewer identity votes."""
import json,glob,sqlite3,os,collections,math
ROOT='/home/lzk/MtAgentLens'
REF=f'{ROOT}/compare/results/identity'
RUNTIME=f'{ROOT}/compare/results/identity/reference'

def load_cases():
    inv=json.load(open(f'{REF}/reference/inventory.json'))
    rt=json.load(open(f'{RUNTIME}/runtime-inventory.json'))
    ledger={r['run_id']:r['ledger'] for r in rt['records']}
    out={}
    for c in inv['cases']:
        b=c['binding']; sid=b['scenario_id']; rid=b['run_id']
        out[c['case_id']]={'scenario_id':sid,'run_id':rid,'ledger':ledger.get(rid)}
    return out

def reviewer_votes():
    votes=collections.defaultdict(lambda: collections.defaultdict(dict))
    for p in glob.glob(f'{REF}/raw/reviews/*-identity-*.jsonl'):
        parts=os.path.basename(p).split('-'); role='-'.join(parts[:2])
        rows=[json.loads(l) for l in open(p) if l.strip()]
        final=[r for r in rows if r.get('type')=='final']
        if not final: continue
        for case in json.loads(final[0]['text']):
            for uid,v in case['identities'].items():
                votes[case['case_id']][uid][role]=v
    return votes

def lineage_map(db):
    parent={}
    if db and os.path.exists(db):
        c=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
        try:
            for pid,st,pp,ps in c.execute('select pid,start_time_ns,parent_pid,parent_start_time_ns from process_events'):
                parent[(pid,st)]=(pp,ps)
        except sqlite3.Error:
            pass
    return parent

def resolve(unit, roots, parent):
    key=(unit.get('pid'),unit.get('process_start_time_ns')); seen=set()
    while key and key not in seen:
        seen.add(key)
        if key in roots: return roots[key],('root' if key==(unit.get('pid'),unit.get('process_start_time_ns')) else 'ancestor')
        key=parent.get(key)
    return None,None

cases=load_cases(); votes=reviewer_votes()
out={'schema':'mtagentlens.identity-reference/1','method':'declared_process_lineage+ai_consensus_3',
     'cases':{},'stats':{}}
tier=collections.Counter(); agree=conflict=0; unit_total=0
for cid,meta in sorted(cases.items()):
    packet=json.load(open(f'{REF}/raw/packets/{cid}.json'))
    roots={}
    for actor,ac in packet['actor_candidates'].items():
        for pid,st in ac.get('processes') or []: roots[(pid,st)]=actor
    db=os.path.join(os.path.dirname(meta['ledger']),'collector','finalized.db') if meta['ledger'] else None
    parent=lineage_map(db)
    units={}
    for u in packet['units']:
        uid=u['unit_id']; unit_total+=1
        det,how=resolve(u,roots,parent)
        vs=votes.get(cid,{}).get(uid,{})
        known=[v['actor'] for v in vs.values() if v.get('actor')]
        consensus=None
        if len(known)>=2:
            counts=collections.Counter(known)
            top,count=counts.most_common(1)[0]
            if count>=2: consensus=top
        actor=None; tier_name='unknown'
        if det:
            actor=det; tier_name='declared_'+ (how or 'process')
            if consensus and consensus==det: agree+=1
            elif consensus and consensus!=det: conflict+=1
        elif consensus:
            actor=consensus; tier_name='ai_consensus'
        tier[tier_name]+=1
        units[uid]={'actor':actor,'tier':tier_name,'pid':u.get('pid'),
                    'process_start_time_ns':u.get('process_start_time_ns'),
                    'ai_votes':{r:(v.get('actor')) for r,v in vs.items()}}
    out['cases'][cid]={'scenario_id':meta['scenario_id'],'run_id':meta['run_id'],'units':units}
out['stats']={'units':unit_total,'tiers':dict(tier),'ai_agree_with_declared':agree,'ai_conflict_with_declared':conflict}
json.dump(out,open(f'{REF}/reference/identity-reference.json','w'),indent=1)
print(json.dumps(out['stats'],indent=1))
