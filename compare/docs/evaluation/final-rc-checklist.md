# 最终 RC 与正式实验准入

更新：2026-10-26。当前范围为现有93场景、不新增E7场景、AI三会话标注加独立裁决；
没有人工标签。此页区分采集就绪、参考可评分和论文结论就绪，不能相互替代。

## 1. 固定输入和构建

- 唯一当前数据集为 `datasets/FuxiBench`；分类 `fuxibench-2type/1`，A为间接注入，B为多Agent攻击。
- 全部93场景保留。检测队列84＝58 attack（A47/B11）＋26 benign；9 excluded单列。
- 多Agent场景30，检测队列29＝22 attack（A11/B11）＋7 benign；排除handoff-unicode。
- 标注输入以原始case-map/source hashes核验；任务、fixture、角色和prompt必须一致。
  禁止全局替换loopback地址或为检测器表现修改载荷。setup必须使用包内bench。
- 保存release.json及其release_id、collector/helper二进制hash、运行配置hash、kernel和dirty源码状态。
  历史fix4等结果仅按原release/构建/分母复用，不改成新包测量。

```bash
./scripts/verify.sh
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
cd collector && cargo build --release --bin mtagentlens
```

门禁已包含scripts/tests的pytest测试，不再由unittest发现机制遗漏harness回归。
`freeze_rc.freeze()`用于绑定构建与输入；不得把paper_metrics_available=false改成通过。

## 2. 先验证采集，再进入模型工作负载

```bash
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/final_campaign_harness.py status
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/final_campaign_harness.py multi-agent
# 零模型真实内核链：需root；--out必须是新目录
sudo env/.venv/bin/python scripts/evaluation/system_load.py \
  --binary "$PWD/collector/build/release/mtagentlens" --out "$PWD/local/system-load" \
  --concurrency 1,4,8,16,32 --operations 100 --execute
```

system-load覆盖process eBPF→runtime→SQLite，报告原始丢失计数、受控操作缺失比例、
进程树RSS/CPU与持久化可见延迟上界。它不含TLS/Observer，不测语义告警延迟，
只是E9的补充诊断，不代表完整LLM Agent开销；`e9`命令仍仅为parser微基准。

受限真实Agent预演还必须检查TLS/process/persistence健康、finalized DB及release绑定；
模型预检失败或供应商致命错误立即停。预演不挑选结果写入正式指标。
付费执行入口需同时`--execute`与`MTL_ALLOW_PAID=1`，不把dry-run成功当成已跑实验。

执行目录在仓库外，路径保持短；不得在release目录运行fixture或产生__pycache__。
不隐式改写sink主机。loopback是场景原始语义，同机地址不冒充真实跨机外传。
setup启动前后与窗口内收据必须独立验证；marker仅证明钩子触达，不证明攻击成功。

## 3. 每项审稿意见的实验输入与交付

| 项目 | 输入与方法 | 必须产生的证据 / 前置条件 |
|---|---|---|
| E1 判定/数字 | 当前final_decision与原冻结artifact分别复算 | 逐值表、明确Safe/Unsafe/Uncertain/Failed；不混用旧分母 |
| E5 身份微基准 | 原有6拓扑独立生成器 | pairwise/B³及unknown；仅支持合成拓扑结论 |
| E6 真实身份/根因 | 新多Agent运行、外部身份锚点、盲化参考 | **尚未具备逻辑身份/根因参考链路**；不得直接以设计角色或detector身份代替 |
| E8 应用层基线 | 同一release、同一有效运行轨迹 | 每次恰好一个run，A1/A2/A3输入hash、模型版本/用量、canonical DB；按配对比较 |
| E9 完整开销 | 固定 Agent 负载（文件/进程/TLS，触发真实 Observer；成功操作有独立凭据/回执）；单 collector 监测 N 实例；并发 1 C0/C1/C2/C3，并发 4/8/16/32 仅 C0/C3；15 配对区组（3 四臂+12 两臂）、每级 3 次、36 condition runs、372 实例；warmup/pilot/诊断/TLS 微基准另计 | TLS/process/persist 丢失与已知成功操作缺失分别报；CPU/RSS/落盘体积及内存随并发/窗口变化；Observer 延迟/token/费用/失败重试/缺失；可验证触发事件→最终可见告警 p50/p95 与未告警覆盖；C2−C1 不称 graph-only，图更新轻量计时单列；不靠时间邻近配对、无 p99 硬承诺；system-load 仅为补充。**已完成（2026-10-28）：36/36 条件、execution report complete=true、T8/F2 见 `compare/results/overhead/raw/full-system/report/`；结果摘要见[实验总方案 §6.6](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)** |
| E2/E7 PBC混杂 | 当前有效run的全量PBC和独立执行状态 | 全分布、分层n和CI、活动量基线；历史61-run不替代当前分母；不声称已排除全部混杂 |
| E3/E4/E11消融 | 同run checkpoint/原始事件、固定变体 | 明确重放层级；逐维LOO不冒充eBPF/PBC层级拆分 |
| E10 外部协议 | 冻结InjecAgent来源与原输出 | 明确文本降级、构造与指标；不能称OS真实执行 |
| 同级方法 | 固定官方版本、输入适配和输出语义 | 实跑一个可比较实现，或逐方法给官方来源与具体不兼容原因；不把自建规则称官方检测器 |
| 测试独立性 | 回溯家族划分和开发历史 | 披露未有从未见过的独立测试集；不得把划分文件当成独立性证明 |

- E9 条件：C0 Agent-alone、C1 `trace --capture-only`、C2 `trace --disable-responsibility-observer`（runtime+DB 仍运行、Observer off）、C3 默认 `trace`（真实 Observer）；并发 4/8/16/32 仅 C0、C3。

所有待运行项在得到artifact前均为pending，不承诺检测效果。AI同模型fresh context不是统计独立。
现有独立运行标注器的root_actor强制null，因此仅跑完它无法产生RootAgent@1/@3/MRR。
先完成collector之外的native session身份绑定、因果链标注作用域及评分适配，再对这些指标开跑。

## 4. 运行标注与E8接线

```bash
# 命令默认只检查并构造计划，明确指定当前campaign根
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/final_campaign_harness.py \
  --out local/final-experiments runtime-annotations --run-root <campaign-runs>
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/final_campaign_harness.py \
  --out local/final-experiments e8 --results <completed-results.jsonl>
```

标注链为prepare→compact→sanitize→三个text-only fresh reviewer→finalize→独立adjudicate
→finalize→verify。原始证据不改；泄漏内容只从reviewer view移除，并披露部分盲化。
未知保留unknown，不强行二分，不算负例；完整覆盖与会话/packet绑定必须校验。
AI参考一律如实披露，不署真人名字、不报告人工κ/人工gold。

E8先对真实导出做无模型dry-run，再逐run使用real模式。健康、绑定或来源不符的run不得送入付费judge。
resume须核验release、binary、配置snapshot、DB和完成状态；失败/中断不能因文件存在而跳过。

## 5. 评分与提交

- 正式评分读取runs.final_decision，actionable仅为Unsafe。Warning+独立报告。
- 未评完当前候选版本、采集不完整及Observer失败均不构成Safe证明。
- 报告all-attempts、void、unknown、capture-degraded、excluded及每个条件分母；
  不因结果不理想静默剔除，不把重试视为独立场景样本。
- 旧结果复用需逐项匹配输入字节、构建、配置和指标语义；匹配失败只保留历史解释。
- 新数字登记docs/paper/claims.json，运行verify_claims.py，论文/回复信逐值同步；独立开销实验的 T8/F2 数字以 `compare/results/overhead/raw/full-system/report/` 的 sha256 为证据，正式引用前补登记，不能混入 fix4 claims。
- 逻辑身份/运行根因未有参考、同级方法未实跑时，不能宣称全部审稿意见闭环；完整开销已完成正式实测（单主机/单内核/单一负载、3 次描述性重复，有限定）。
- E9 费用按真实 usage 与价格日期记录，缺失写 null；旧预算 flag 不是硬上限。正式 36/36 条件已在独立窗口完成，未终止或改动 DSH 的 93 场景流程。
- E9 探针诊断统计有开销，只在诊断窗口开启并恢复原状态，正式系统计时关闭；TLS 微基准须先用内存 BIO 等完成本地成功握手/读写并检查返回值；零次表示未覆盖，共享 syscall 不按文件 API 拆分，一次性扫描另记。
- E9 范围不含长期泄漏、多主机模型、额外任务家族、逐探针并发矩阵和挂载对照。
- E9 权威协议见 [EXPERIMENT-SUITE-7-REVISED §6](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)（由 lead 更新）。
- 不commit/push，不清理历史用户资产；生成DB/日志留local，不混入代码提交。
