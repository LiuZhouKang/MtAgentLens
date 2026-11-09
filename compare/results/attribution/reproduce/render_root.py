#!/usr/bin/env python3
import json, argparse
from pathlib import Path
from collections import Counter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Noto Sans CJK SC','DejaVu Sans'],
 'axes.unicode_minus':False,'font.size':8,'axes.labelsize':8,'axes.titlesize':8,
 'xtick.labelsize':7,'ytick.labelsize':7,'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none'})
ap=argparse.ArgumentParser(); ap.add_argument('--metrics',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
m=json.loads(a.metrics.read_text())
rows=[x for x in m['rows'] if x['known']]
ranks=Counter(x['predicted_roots'].index(x['reference_root'])+1 for x in rows)
vals=[m['attack']['root_at_1'],m['attack']['root_at_2'],m['attack']['root_at_3'],m['attack']['candidate_coverage']]
labels=['Root@1','Root@2','Root@3','候选覆盖']
fig,axes=plt.subplots(1,2,figsize=(7.2,2.9),constrained_layout=True,gridspec_kw={'width_ratios':[1,1.2]})
ax=axes[0]
xs=[1,2,3]; ys=[ranks.get(i,0) for i in xs]
bars=ax.bar(xs,ys,color=['#0072B2','#56B4E9','#009E73'],width=.62)
for b,v in zip(bars,ys): ax.text(b.get_x()+b.get_width()/2,v+.3,str(v),ha='center',fontsize=7)
ax.set_xticks(xs); ax.set_xticklabels(['第 1','第 2','第 3']); ax.set_xlabel('参考根因在候选中的位置')
ax.set_ylabel('场景数'); ax.set_title('(a) 根因候选排名分布',loc='left',fontweight='bold')
ax.set_ylim(0,max(ys)+2); ax.grid(axis='y',color='#EEEEEE',lw=.5); ax.set_axisbelow(True)
for s in ('top','right'): ax.spines[s].set_visible(False)
ax=axes[1]
y=np.arange(len(vals))
ax.barh(y,[v*100 for v in vals],color=['#999999','#56B4E9','#0072B2','#009E73'])
for i,v in enumerate(vals): ax.text(v*100+1,i,f'{v*100:.1f}%',va='center',fontsize=7)
ax.set_yticks(y); ax.set_yticklabels(labels); ax.set_xlim(0,115); ax.set_xlabel('百分比（%）')
ax.set_title('(b) 22 个攻击多智能体场景',loc='left',fontweight='bold')
ax.grid(axis='x',color='#EEEEEE',lw=.5); ax.set_axisbelow(True)
for s in ('top','right'): ax.spines[s].set_visible(False)
for ext in ('png','pdf','svg'): fig.savefig(a.out/'figures'/f't6_root.{ext}',dpi=600,bbox_inches='tight')
print('wrote',a.out/'figures/t6_root.png')
