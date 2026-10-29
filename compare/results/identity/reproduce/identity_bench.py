#!/usr/bin/env python3
import json,glob,os,collections,random,math
ROOT='/home/lzk/MtAgentLens'
ref=json.load(open(f'{ROOT}/compare/results/identity/reference/identity-reference.json'))
CONDS=['pid-instance-only','pid-instance-plus-ppid','pid-instance-plus-tls','pid-instance-plus-conversation','full']
# load predictions per scenario
pred={}
for p in glob.glob(f'{ROOT}/compare/results/identity/data/identity-runs/*.json'):
    sid=os.path.basename(p)[:-5]
    run=json.load(open(p))['runs'][0]
    views={v['condition']:v for v in run.get('views',[])}
    pred[sid]={c:{u['unit_id']:u for u in views.get(c,{}).get('units',[])} for c in CONDS}
def pairwise(rows):
    # rows: (ref, pred, case)
    tp=fp=fn=0; same_ref=same_pred=0
    bycase=collections.defaultdict(list)
    for r,p,c in rows: bycase[c].append((r,p))
    for c,items in bycase.items():
        for i in range(len(items)):
            for j in range(i+1,len(items)):
                r1,p1=items[i]; r2,p2=items[j]
                sr=r1==r2; sp=p1==p2
                same_ref+=sr; same_pred+=sp
                if sr and sp: tp+=1
                elif sp and not sr: fp+=1
                elif sr and not sp: fn+=1
    prec=tp/(tp+fp) if tp+fp else None
    rec=tp/(tp+fn) if tp+fn else None
    f1=2*prec*rec/(prec+rec) if prec and rec else None
    return {'tp':tp,'fp':fp,'fn':fn,'precision':prec,'recall':rec,'f1':f1,'same_ref_pairs':same_ref,'same_pred_pairs':same_pred}
def b3(rows_by_case):
    ps=[]; rs=[]
    for c,items in rows_by_case.items():
        ref_clusters=collections.defaultdict(set); pred_clusters=collections.defaultdict(set)
        for uid,r,p in items:
            ref_clusters[r].add(uid); pred_clusters[p].add(uid)
        for uid,r,p in items:
            rc=ref_clusters[r]; pc=pred_clusters[p]
            ps.append(len(rc & pc)/len(pc)); rs.append(len(rc & pc)/len(rc))
    return sum(ps)/len(ps), sum(rs)/len(rs)
result={'schema':'mtagentlens.identity-bench/1','conditions':{},'reference':ref['stats']}
for cond in CONDS:
    rows=[]; bycase=collections.defaultdict(list); assigned=total=0
    for cid,case in ref['cases'].items():
        sid=case['scenario_id']; pv=pred.get(sid,{}).get(cond,{})
        for uid,ru in case['units'].items():
            total+=1
            p=pv.get(uid)
            if p and p.get('actor',{}).get('state')=='assigned':
                assigned+=1
                if ru['actor'] is not None:
                    rows.append((ru['actor'],p['actor']['partition'],cid)); bycase[cid].append((uid,ru['actor'],p['actor']['partition']))
    pm=pairwise(rows)
    bp,br=b3(bycase)
    evaluated=len(rows); ref_known=sum(1 for c in ref['cases'].values() for u in c['units'].values() if u['actor'] is not None)
    # false merge/split over evaluated pairs
    fm=pm['fp']/ (pm['tp']+pm['fp']) if pm['tp']+pm['fp'] else None
    fs=pm['fn']/ (pm['tp']+pm['fn']) if pm['tp']+pm['fn'] else None
    # Fair comparison: treat each unknown prediction as a singleton, so
    # abstention shows up as false splits instead of vanishing from the metric.
    allrows=[]; allby=collections.defaultdict(list)
    for cid,case in ref['cases'].items():
        sid=case['scenario_id']; pv=pred.get(sid,{}).get(cond,{})
        for uid,ru in case['units'].items():
            if ru['actor'] is None: continue
            p=pv.get(uid)
            part=(p['actor']['partition'] if p and p['actor'].get('state')=='assigned' else f'unknown:{cid}:{uid}')
            allrows.append((ru['actor'],part,cid)); allby[cid].append((uid,ru['actor'],part))
    pm_all=pairwise(allrows); bp_all,br_all=b3(allby)
    result['conditions'][cond]={'units':total,'ref_known':ref_known,'evaluated':evaluated,
        'pairwise_all':pm_all,'b3_all':2*bp_all*br_all/(bp_all+br_all) if bp_all+br_all else None,
        'false_split_all':pm_all['fn']/(pm_all['tp']+pm_all['fn']) if pm_all['tp']+pm_all['fn'] else None,
        'coverage':assigned/total if total else None,'pairwise':{**pm,'f1':pm['f1'] if pm['f1'] is not None else 0.0},
        'b3_precision':bp,'b3_recall':br,'b3_f1':2*bp*br/(bp+br) if bp+br else None,
        'false_merge_rate':fm,'false_split_rate':fs,
        'unattributed_rate':(ref_known-evaluated)/ref_known if ref_known else None}
# bootstrap micro pairwise F1 by case
rnd=random.Random(7)
for cond in CONDS:
    cases=list(ref['cases'].keys()); boots=[]
    rows_by_case=collections.defaultdict(list)
    for cid,case in ref['cases'].items():
        sid=case['scenario_id']; pv=pred.get(sid,{}).get(cond,{})
        for uid,ru in case['units'].items():
            p=pv.get(uid)
            if ru['actor'] is not None and p and p['actor'].get('state')=='assigned':
                rows_by_case[cid].append((ru['actor'],p['actor']['partition'],cid))
    for _ in range(5000):
        sample=[rows_by_case[cases[rnd.randrange(len(cases))]] for _ in range(len(cases))]
        flat=[x for b in sample for x in b]
        pm=pairwise(flat); boots.append(pm['f1'] or 0.0)
    boots.sort(); result['conditions'][cond]['pairwise_f1_ci']=[boots[int(.025*len(boots))],boots[int(.975*len(boots))-1]]
json.dump(result,open(f'{ROOT}/compare/results/identity/report/identity-bench.json','w'),indent=1)
for c,v in result['conditions'].items():
    print(c, 'coverage',round(v['coverage'],3),'pairF1',round(v['pairwise']['f1'],3),'ci',[round(x,3) for x in v['pairwise_f1_ci']],'b3',round(v['b3_f1'],3),'FM',round(v['false_merge_rate'],3),'FS',round(v['false_split_rate'],3),'unattr',round(v['unattributed_rate'],3))
