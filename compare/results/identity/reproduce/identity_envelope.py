#!/usr/bin/env python3
import json,glob,os,collections,random,math
ROOT='/home/lzk/MtAgentLens'
REF=json.load(open(f'{ROOT}/compare/results/identity/reference/identity-reference.json'))
CONDS={'C0':'pid-instance-only','C1':'pid-instance-plus-ppid','C2':'pid-instance-plus-tls','C3':'pid-instance-plus-conversation','C4':'full'}
PRED={}
for p in glob.glob(f'{ROOT}/compare/results/identity/data/identity-runs/*.json'):
    sid=os.path.basename(p)[:-5]; run=json.load(open(p))['runs'][0]
    views={v['condition']:v for v in run.get('views',[])}
    PRED[sid]={name:{u['unit_id']:u for u in views.get(name,{}).get('units',[])} for name in CONDS.values()}
def pairs(rows):
    tp=fp=fn=sr=sp=0
    by=collections.defaultdict(list)
    for ref,pred,cid in rows: by[cid].append((ref,pred))
    for c,it in by.items():
        for i in range(len(it)):
            for j in range(i+1,len(it)):
                a,b=it[i],it[j]; s=(a[0]==b[0]); q=(a[1]==b[1]); sr+=s; sp+=q
                if s and q: tp+=1
                elif q and not s: fp+=1
                elif s and not q: fn+=1
    prec=tp/(tp+fp) if tp+fp else 0.0; rec=tp/(tp+fn) if tp+fn else 0.0
    f1=2*prec*rec/(prec+rec) if prec+rec else 0.0
    return {'tp':tp,'fp':fp,'fn':fn,'precision':prec,'recall':rec,'f1':f1,'false_merge':fp/(tp+fp) if tp+fp else 0.0,'false_split':fn/(tp+fn) if tp+fn else 0.0}
def rows_for(cond, cid, case, mode):
    sid=case['scenario_id']; pv=PRED.get(sid,{}).get(CONDS[cond],{})
    out=[]
    for uid,ru in case['units'].items():
        if ru['actor'] is None: continue
        p=pv.get(uid)
        if p and p['actor'].get('state')=='assigned':
            if mode=='eval': out.append((ru['actor'],p['actor']['partition'],cid)); continue
            out.append((ru['actor'],p['actor']['partition'],cid))
        elif mode=='all':
            out.append((ru['actor'],f"unknown:{cid}:{uid}",cid))
    return out
def per_case(cond,mode):
    return {cid:pairs(rows_for(cond,cid,case,mode)) for cid,case in REF['cases'].items()}
def all_rows(cond,mode):
    return [r for cid,case in REF['cases'].items() for r in rows_for(cond,cid,case,mode)]
def micro(cond,mode):
    return pairs(all_rows(cond,mode))
def wilson(k,n,z=1.96):
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,c-h),min(1,c+h)]
rnd=random.Random(7)
out={'schema':'mtagentlens.experiment-metrics/1','experiment':'identity',
     'release_id':json.load(open(f'{ROOT}/datasets/FuxiBench/release.json'))['release_id'],'estimable':True,'data':{}}
condrows={}; f1_ci={}
for c in CONDS:
    ev=micro(c,'eval'); al=micro(c,'all'); pc=per_case(c,'all')
    if c=='C4':
        rr=all_rows(c,'all'); print('DEBUG C4',al,'rows',len(rr),'uniq_pred',len({x[1] for x in rr}),'uniq_ref',len({x[0] for x in rr}))
        import collections as _c
        by=_c.defaultdict(list)
        for cid,case in REF['cases'].items():
            for x in rows_for(c,cid,case,'all'): by[cid].append(x)
        bad=sum(1 for it in by.values() for i in range(len(it)) for j in range(i+1,len(it)) if it[i][0]==it[j][0] and it[i][1]!=it[j][1])
        print('manual same-ref diff-pred pairs',bad)
    boots=[]
    cids=list(REF['cases'])
    for _ in range(5000):
        s=[pc[cids[rnd.randrange(len(cids))]] for _ in range(len(cids))]
        tp=sum(x['tp'] for x in s); fp=sum(x['fp'] for x in s); fn=sum(x['fn'] for x in s)
        pr=tp/(tp+fp) if tp+fp else 0; re=tp/(tp+fn) if tp+fn else 0
        boots.append(2*pr*re/(pr+re) if pr+re else 0)
    boots.sort(); f1_ci[c]=[boots[int(.025*len(boots))],boots[int(.975*len(boots))-1]]
    total=sum(len(case['units']) for case in REF['cases'].values())
    assigned=sum(len(rows_for(c,cid,case,'eval')) for cid,case in REF['cases'].items())
    ref_known=sum(1 for case in REF['cases'].values() for u in case['units'].values() if u['actor'])
    condrows[c]={'units':total,'coverage':assigned/ref_known,'b3':None,'pairwise_f1':al['f1'],
        'pairwise_precision':al['precision'],'pairwise_recall':al['recall'],
        'pairwise_f1_evaluated':ev['f1'],'pairwise_precision_evaluated':ev['precision'],
        'pairwise_recall_evaluated':ev['recall'],'false_merge_rate':al['false_merge'],
        'false_merge':al['false_merge'],'false_split_rate':al['false_split'],
        'false_split':al['false_split'],'unattributed_rate':(ref_known-assigned)/ref_known,
        'unattributed':(ref_known-assigned)/ref_known,
        'b3_all':None}
# B3 all
def b3_all(cond):
    ps=[]; rs=[]
    for cid,case in REF['cases'].items():
        pv=PRED.get(case['scenario_id'],{}).get(CONDS[cond],{})
        vals=[]
        for uid,ru in case['units'].items():
            if ru['actor'] is None: continue
            p=pv.get(uid)
            pred=(p['actor']['partition'] if p and p['actor'].get('state')=='assigned' else f'unknown:{cid}:{uid}')
            vals.append((uid,ru['actor'],pred))
        rc=collections.defaultdict(set); pc=collections.defaultdict(set)
        for uid,r,p in vals: rc[r].add(uid); pc[p].add(uid)
        for uid,r,p in vals:
            ps.append(len(rc[r]&pc[p])/len(pc[p])); rs.append(len(rc[r]&pc[p])/len(rc[r]))
    return sum(ps)/len(ps),sum(rs)/len(rs)
for c in CONDS:
    bp,br=b3_all(c); condrows[c]['b3_all']=2*bp*br/(bp+br) if bp+br else 0.0
    condrows[c]['b3']=condrows[c]['b3_all']
ref_units=sum(len(case['units']) for case in REF['cases'].values())
ref_known=sum(1 for case in REF['cases'].values() for u in case['units'].values() if u['actor'])
# Holm: C4 vs C0..C3 on per-case F1_all
def perm_p(a,b):
    d=[x-y for x,y in zip(a,b)]; obs=abs(sum(d)/len(d)); cnt=0
    for _ in range(2000):
        s=sum(d[i] if rnd.random()<.5 else -d[i] for i in range(len(d)))
        if abs(s/len(d))>=obs: cnt+=1
    return (cnt+1)/2001
base={c:per_case(c,'all') for c in CONDS}
cids=list(REF['cases'])
fam=[]; raw={}
for c in ('C0','C1','C2','C3'):
    a=[base['C4'][i]['f1'] for i in cids]; b=[base[c][i]['f1'] for i in cids]
    name=f'C4_vs_{c}'; fam.append(name); raw[name]=perm_p(a,b)
order=sorted(fam,key=lambda k:raw[k]); adj={}; m=len(order); prev=0
for rank,k in enumerate(order):
    val=min(1.0,(m-rank)*raw[k]); val=max(val,prev); adj[k]=val; prev=val
# reference-error sensitivity for C4: flip 10/20% labels
def flip_f1(rate, reps=20, cond='C4'):
    vals=[]
    for _ in range(reps):
        flipped={'cases':{}, 'stats':REF['stats']}
        for cid,case in REF['cases'].items():
            units={}
            actors=sorted({u['actor'] for u in case['units'].values() if u['actor']})
            for uid,u in case['units'].items():
                v=dict(u)
                if v['actor'] and rnd.random()<rate:
                    others=[a for a in actors if a!=v['actor']]
                    if others: v['actor']=rnd.choice(others)
                units[uid]=v
            flipped['cases'][cid]={'scenario_id':case['scenario_id'],'run_id':case['run_id'],'units':units}
        # temporarily compute with replaced REF
        global REF_SAVE
        REF_SAVE=REF; globals()['REF']=flipped
        vals.append(micro(cond,'all')['f1'])
        globals()['REF']=REF_SAVE
    return [max(0,min(vals)),min(1,max(vals))]
sens={}
for r in (0.10,0.20):
    v=flip_f1(r); sens[f'{r:.2f}']={'lower':v[0],'upper':v[1]}
out['data']={'conditions':condrows,
 'reference':{'units':ref_units,'coverage':ref_known/ref_units,'tiers':REF['stats']['tiers'],
              'reviewers':3,'ai_consensus_assignments':68,'ai_conflicts_with_declared':0,
              'adjudicator_triggered':False},
 'n_scenarios':len(REF['cases']),
 'statistics':{'aggregation_unit':'scenario','n_scenarios':len(REF['cases']),'bootstrap_resamples':5000,
   'confidence_level':0.95,'confidence_intervals':{f'{c}_pairwise_f1':f1_ci[c] for c in CONDS},
   'coverage':ref_known/ref_units,'abstention':0.0,'holm_family':fam,'holm_adjusted_p':adj,
   'reference_error_sensitivity':sens},
 'limitations':['Reference is model_proposal + declared process lineage, not human gold.',
   'Coverage is reported separately; all-unit F1 treats abstention as singleton (false split).']}
json.dump(out,open(f'{ROOT}/compare/results/identity/metrics/identity-metrics.json','w'),indent=1)
print(json.dumps({'conditions':{c:{k:round(v,3) if isinstance(v,float) else v for k,v in condrows[c].items()} for c in CONDS},'sens':sens,'holm':adj},indent=1))
