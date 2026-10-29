import json,csv,hashlib,statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path(__file__).resolve().parents[4];B=R/'compare/results/overhead';OUT=B/'figures';OUT.mkdir(exist_ok=True)
def load(p):
    # Frozen JSON records historical paths; resolve them without rewriting evidence.
    p=Path(str(p).replace('local/overhead-2026-10-28/study-v4/', 'compare/results/overhead/raw/full-system/'))
    return json.loads(p.read_text())
steady=load(B/'raw/steady/summary.json');assert steady['complete']
s=steady['results'];assert len(s)==3 and all(x['status']=='ok' and x['evidence']['trace_health']['status']=='healthy' for x in s)
raw=load(B/'raw/full-system/report/raw-per-run.json')['runs'];audit=load(B/'data/credential_linkage.json')['rows']
mean=statistics.mean
levels=(1,4,8,16,32)
full=[]
for n in levels:
 a=[x for x in raw if x['profile']=='C3' and x['concurrency']==n];e=[x for x in audit if x['concurrency']==n]
 samples=[]
 for x in a:samples+=load(R/x['source_evidence'])['alert_latency']['samples']
 vals=sorted(x['latency_ms']/1000 for x in samples)
 def quantile(p):
  f=(len(vals)-1)*p;i=int(f);return vals[i]+(vals[min(i+1,len(vals)-1)]-vals[i])*(f-i)
 sources=[v for x in a for k,v in x['layer_counters']['sources'].items() if k.startswith('Process:')]
 full.append(dict(concurrency=n,cpu_seconds_mean=mean(x['collector_cpu_seconds'] for x in a),cpu_percent_mean=mean(x['collector_cpu_mean_percent'] for x in a),rss_peak_mib_mean=mean(x['collector_rss_peak_mib'] for x in a),observer_cost_mean=mean(x['observer_cost_upper_bound_usd'] for x in a),credential_actions=sum(x['credential_successful_actions'] for x in e),credential_linked=sum(x['credential_actions_with_evidence'] for x in e),all_controlled_operations=sum(x['controlled_operations_observed'] for x in a),linked_samples=len(vals),unique_alerts=len(set(x['alert_id'] for x in samples)),pooled_conditional_p95_seconds=quantile(.95),mean_within_run_p95_seconds=mean(x['alert_p95_ms']/1000 for x in a),process_drops_min=min(x['lost'] for x in sources),process_drops_max=max(x['lost'] for x in sources)))
probe=load(B/'raw/probes/tls-probes-final.json')['summary']
numbers=dict(steady=[{k:x[k] for k in ('repeat','active_elapsed_s','active_cpu_seconds','single_core_cpu_percent','steady_pss_mean_mib','steady_rss_mean_mib','controlled_files_missing','ssl_trace_lines')} for x in s],complete_system=full,tls_probe=probe)
(B/'data/numbers.json').write_text(json.dumps(numbers,indent=2)+'\n')
for name,rows in (('steady',numbers['steady']),('complete_system',full)):
 with (B/'data'/({'steady':'steady_runs','complete_system':'complete_system_summary'}[name]+'.csv')).open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,
 'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'svg.fonttype':'none'})
fig,axs=plt.subplots(1,3,figsize=(13,3.8),layout='constrained')
# Use actual cumulative-CPU samples nearest each 5 s boundary; no interpolation.
window_rows=[]
traces=[]
for trial in s:
 rows=[trial['active_before']]+[
  json.loads(line) for line in (B/'raw/steady'/f"r{trial['repeat']}"/'samples.jsonl').read_text().splitlines()
 ]+[trial['active_after']]
 start=trial['active_before']['monotonic'];end=trial['active_after']['monotonic']
 rows=sorted((row for row in rows if start<=row['monotonic']<=end),key=lambda row:row['monotonic'])
 anchors=[rows[0]]+[min(rows,key=lambda row:abs(row['monotonic']-start-second)) for second in range(5,60,5)]+[rows[-1]]
 edges=[row['monotonic']-start for row in anchors]
 rates=[]
 for before,after in zip(anchors,anchors[1:]):
  duration=after['monotonic']-before['monotonic'];cpu=after['cpu_seconds']-before['cpu_seconds']
  assert 4.5<duration<5.5 and cpu>=0
  rates.append(100*cpu/duration)
  window_rows.append(dict(repeat=trial['repeat'],start_s=before['monotonic']-start,end_s=after['monotonic']-start,window_duration_s=duration,cpu_seconds_delta=cpu,cpu_percent=rates[-1]))
 assert len(rates)==12
 assert abs(sum(row['cpu_seconds_delta'] for row in window_rows if row['repeat']==trial['repeat'])-trial['active_cpu_seconds'])<1e-10
 traces.append((trial['repeat'],edges,rates))
with (B/'data/steady_cpu_windows.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=window_rows[0]);w.writeheader();w.writerows(window_rows)
a=axs[0];v=[x['single_core_cpu_percent'] for x in s]
colors=('#0072B2','#D55E00','#009E73');styles=('-', '--', ':')
for (repeat,edges,rates),color,style in zip(traces,colors,styles):
 a.stairs(rates,edges,baseline=None,color=color,linestyle=style,linewidth=1.55,label=f'Run {repeat}',zorder=3)
a.set(xlim=(0,60),ylim=(0,1.65),xticks=[0,15,30,45,60],yticks=[0,.4,.8,1.2,1.6],xlabel='Time since workload start (s)',ylabel='CPU (% of one logical core)',title='A  Collector CPU over time')
a.text(.025,.96,f'Mean {mean(v):.3f}%',transform=a.transAxes,va='top',fontsize=11,fontweight='bold',color='#176b91')
a.text(.975,.96,'Mean CPU target: 5%',transform=a.transAxes,ha='right',va='top',fontsize=8,color='#555555')
a.text(.025,.86,'Synthetic workload · 5 s windows · 3 runs',transform=a.transAxes,va='top',fontsize=7.5,color='#555555')
a.legend(frameon=False,loc='lower center',bbox_to_anchor=(.5,.015),ncol=3,fontsize=7.5,handlelength=2.2,columnspacing=1.3)
a=axs[1];ix=list(range(5))
a.plot(ix,[x['cpu_percent_mean'] for x in full],'o-',color='#176b91',label='CPU')
a.set(xlabel='Concurrent Agent instances',xticks=ix,xticklabels=levels,ylabel='CPU (% of one logical core)',title='B  Complete system (cold batch)')
t=a.twinx()
t.plot(ix,[x['rss_peak_mib_mean'] for x in full],'s--',color='#ab7431')
t.set_ylabel('Mean peak RSS (MiB)',color='#ab7431')
t.spines['right'].set_visible(True)
a.tick_params(axis='y',labelcolor='#176b91')
a.text(.04,.93,'Blue: CPU; ochre: RSS',transform=a.transAxes,fontsize=8)
a=axs[2]
file_capture=[100*(1-mean(row['raw_missing_fraction'] for row in raw if row['profile']=='C3' and row['concurrency']==n)) for n in levels]
a.plot(ix,file_capture,'o-',color='#176b91',label='Controlled file capture')
a.plot(ix,[x['credential_linked']/x['credential_actions']*100 for x in full],'s-',color='#9b594e',label='Credential actions with evidence')
a.set(xlabel='Concurrent Agent instances',xticks=ix,xticklabels=levels,ylabel='Observed coverage (%)',ylim=(-5,112),title='C  Capture and evidence linkage')
a.legend(frameon=False,fontsize=7,loc='center left')
for i,x in enumerate(full):
 a.annotate(f"{x['credential_linked']}/{x['credential_actions']}",(i,x['credential_linked']/x['credential_actions']*100),xytext=(0,-13),textcoords='offset points',ha='center',fontsize=7)
for a in axs:a.grid(axis='y',alpha=.18);a.set_axisbelow(True)
for ext in ('pdf','svg','png'):
 fig.savefig(OUT/f'overhead_compact.{ext}',dpi=300,bbox_inches='tight')
plt.close(fig)
text=['# 意见 5：精简分账表（2026-10-29 复核）','','## A. Collector 稳态：固定合成负载','','| 重复 | 单核平均 CPU | CPU 秒 | 平均 PSS / RSS（MiB） | 受控文件缺失 | TLS 来源丢弃 |','|---|---:|---:|---:|---:|---:|']
for x in s:text.append(f"| {x['repeat']} | {x['single_core_cpu_percent']:.3f}% | {x['active_cpu_seconds']:.2f} | {x['steady_pss_mean_mib']:.2f} / {x['steady_rss_mean_mib']:.2f} | {x['controlled_files_missing']}/600 | 0 |")
text+=['','每次 60 秒；40 次成功 SSL 读写/秒、256 B/次，10 次受控文件操作/秒、1 次子进程执行/秒。包含 Rust 和两个采集辅助进程，100% 为一个逻辑核。OpenSSL 本地内存 BIO；不等价于原生 Agent/BoringSSL 负载。PSS 不含未映射的其他内核 map 内存。未设置无监测配对对照，故本补测不估算任务减速或全部内核执行 CPU。每次完成 2,400 次 SSL 读写，trace 保留 1,200 个读载荷记录和 1,200 个 incomplete 协议诊断；固定载荷不是 HTTP，来源丢弃计数为零不等于所有操作的原始载荷逐一留存，也不支持真实 LLM HTTP/SSE 完整性声明。','','## B. 完整系统：已有冻结 Agent 批次','','| 并发 | CPU 秒均值 | 含冷启动单核 CPU 均值 | 峰值 RSS 均值 MiB | Observer 费用上界/条件 | 凭据动作有 evidence | 条件延迟 pooled p95 秒（样本/独立告警） | Process 来源丢弃范围 |','|---|---:|---:|---:|---:|---:|---:|---:|']
for x in full:text.append(f"| {x['concurrency']} | {x['cpu_seconds_mean']:.2f} | {x['cpu_percent_mean']:.2f}% | {x['rss_peak_mib_mean']:.1f} | ${x['observer_cost_mean']:.6f} | {x['credential_linked']}/{x['credential_actions']} | {x['pooled_conditional_p95_seconds']:.2f}（{x['linked_samples']}/{x['unique_alerts']}） | {x['process_drops_min']}–{x['process_drops_max']} |")
text+=['','各档三个独立运行；延迟是已关联操作的描述性 pooled p95，操作共享告警，不是独立重复，也不代表全部操作或未来尾延迟。原报告 145.17 s 是并发 32 的运行内 p95 均值；本表 pooled p95 使用不同汇总口径。受控文件操作 raw/persisted 均未缺失，但并不证明其他事件无丢失或全部动作可归因。并发 32 中 23/96 个成功凭据动作具有关联 evidence，因此低关联覆盖不能仅归因于其他无关文件操作。原始失败 300 s 尝试保留；恢复运行不证明恢复规则预先登记。','','## C. 组件与探针','','| 项目 | 结果 | 范围 |','|---|---|---|','| 并发 1 C1 / C2 / C3 CPU 秒 | 0.75 / 0.80 / 0.85 | 已有短批次，含启动、退出；C2−C1 不是 graph-only |','| 并发 1 C3 图更新总计时 | 4.28 ms/条件 | 不含全部分析、快照、DB 与 phase-2 |','| 并发 1 C3 Observer | 2 次，12,706 输入＋1,176 输出 token，$0.005232/条件 | 真实调用 usage，峰时费率上界，非账单 |',f"| TLS BPF 执行 | {probe['ns_per_successful_ssl_operation']['mean']:.2f} ns/成功读写操作；范围 {probe['ns_per_successful_ssl_operation']['min']:.2f}–{probe['ns_per_successful_ssl_operation']['max']:.2f} | 复用三个零丢弃微基准；非完整采集延迟 |",'| Process syscall entry / completion | 69.0 / 88.7 ns/次 | 复用三个短诊断窗口，包含过滤返回 |','| Process exec / exit | 3.885 / 1.605 μs/次 | 同上，不等于有效事件端到端成本 |','','图注：A 为新稳态固定合成负载的 CPU 时间轨迹，每 5 秒分界取最近真实样本，由累计 CPU 时间差／实际窗口时长计算阶梯值，不平滑、不插值；5% 仅是全程平均 CPU 目标，三次全程均值未变。B/C 为既有完整系统真实 Agent 批次。不同窗口与负载不作直接组件差值。图 C 的两条曲线使用不同分母，均明确标注；关联覆盖不属于攻击漏报率。三个重复只支持描述性结果。']
(B/'tables/component_accounting.md').write_text('\n'.join(text)+'\n')
inputs=[B/'raw/steady/summary.json',B/'raw/steady/plan.json',B/'data/credential_linkage.json',B/'raw/steady/independent-validation.json',Path(__file__),B/'raw/full-system/report/raw-per-run.json',B/'raw/probes/tls-probes-final.json']+[B/'raw/steady'/f'r{i}'/'samples.jsonl' for i in (1,2,3)]
(B/'data/figure_inputs_sha256.json').write_text(json.dumps({str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},indent=2)+'\n')
print(json.dumps({'steady_cpu':v,'pooled_c32_p95':full[-1]['pooled_conditional_p95_seconds'],'figure':str(OUT/'overhead_compact.pdf')}))
