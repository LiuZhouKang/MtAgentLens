# 返修正文替换稿

本稿对应 ry029 的“修改后再审”，用于替换 Word 中的摘要、§3.5、§5、§6、§7 相关段落。主测量固定为 RC 2026-10-25-fix4（binary `de3a0894…`、release `aa364a45…`），其数字依据见 [claims.json](claims.json)。§6.4 完整开销是 2026-10-28 的独立补充实验（不同冻结批次），数字依据为 `compare/results/overhead/raw/full-system/report/` 的 T8/F2/raw 产物与 sha256，并登记在 [claims-overhead-2026-10-28.json](claims-overhead-2026-10-28.json)（16/16）；不能把它混入 fix4 的 claims.json。本轮已按最新版 `.doc` 合入完整 Word 工作稿，并生成真实修订痕迹；产物见 `local/workpackages/2026-10-25-codex-revision/`。它仍是待补核心实验与排版核对的工作稿，不是合并投稿定稿。原始 `.doc` 与 PDF 保留。

## 摘要

大语言模型驱动的多智能体工作流会把指令、工具执行与资源访问分散在多个进程和数据通道中，给安全检测与事后追溯带来困难。本文提出 MtAgentLens，在不修改智能体业务源码的条件下，通过 eBPF 采集进程、文件、网络与受支持的 SSL/TLS 事件，并利用身份解析与责任图组织委派及资源访问证据。系统将策略边界穿越作为候选信号，由确认动作上的禁止性规则与引用具体证据的语义审阅共同形成运行级裁决，区分 Unsafe、Safe 与待复核结果。基于 FuxiBench 的一次冻结采集中，18 个有效良性运行产生 15 个 Safe、3 个待复核和 0 个 Unsafe，二元裁决覆盖率为 83.3%；43 个有效攻击场景运行中，16 个具有钩子触达证据，其中 4 个 Unsafe、9 个待复核、3 个 Safe。固定已记录语义观测的离线分析表明，逐维移除评分维度不改变该批记录的最终裁决。结果说明系统可组织运行时证据并输出复核线索，同时暴露了攻击执行覆盖、钩子触达与安全裁决的关系及真实多智能体归因验证方面的不足。

### English abstract

Large language model agents distribute instructions, tool execution, and resource access across processes and data channels, complicating security monitoring and retrospective analysis. MtAgentLens collects process, file, network, and supported SSL/TLS events through eBPF without modifying agent application code. It combines identity resolution and a responsibility graph to organize delegation and resource-access evidence. Policy-boundary crossings identify candidates; confirmed actions that violate explicit rules and semantic reviews citing concrete evidence determine a run-level Unsafe, Safe, or review outcome. In a frozen collection using FuxiBench, 18 valid benign runs yielded 15 Safe outcomes, three review outcomes, and no Unsafe outcomes, with 83.3% binary-decision coverage. Of 43 valid attack-scenario runs, 16 had independent evidence that the instrumented hook was reached; these yielded four Unsafe, nine review, and three Safe outcomes. Offline analysis with recorded semantic observations held fixed found no final-verdict changes when individual scoring dimensions were removed. The results demonstrate runtime-evidence organization and review support while identifying limitations in attack execution coverage, the relationship between hook reach and security verdicts, and validation of attribution in real multi-agent workflows.

## §3.3.2 PBC 定义与计数

PBC 表示已观察动作涉及所定义的敏感资源或委派边界。责任图按不同的 `(Agent, 边界)` 对维护 PolicyBoundaryCrossing 边，重复事件增加引用证据而不重复计边。因而本文的 PBC 数量是边界对数，不是系统调用数，也不直接等价于攻击强度。PBC 用于候选构造与证据组织；是否违规仍需结合动作结果、任务授权和规则或语义审阅判断。

## §3.4.2 身份解析与归因单元

进程身份使用 PID 与内核启动时间共同标识。身份融合结合进程谱系、TLS 参与关系及请求中实际出现的会话提示；显式委派和不相交会话形成分离约束，共享进程或连接本身不足以证明两个逻辑智能体相同。Claude Code 请求不要求存在顶层 conversation_id；实现可解析 JSON 编码的 metadata.user_id 中的 session_id，但该字段是应用层提示，不能冒充内核身份。

对同进程多会话，消息可按 `(process, conversation)` 划分；缺少会话标识的系统动作仍保留候选或未知归属。身份微基准用于检验合并/分离规则是否符合预设拓扑，不能替代真实并发工作流中的动作归属准确率。根因评价另需入口、责任主体及传播链的独立参考标签，图规模和链长不作为定位正确性的替代指标。

## §3.5 证据聚合与最终裁决

运行期间的告警为暂定结果。运行结束后，系统聚合确认动作、规则命中和候选审阅，写出唯一运行级 final_decision。六维融合分及 0.15/0.70 阈值用于分诊与排序，不以“分数超过 0.15”直接解释 Unsafe。分数经固定 logistic 函数映射，该映射未经独立开发集拟合，不解释为攻击概率或经验概率校准。

| 证据状态 | 对最终裁决的作用 |
|---|---|
| 确认动作命中启用的禁止性规则 | Unsafe，硬证据优先 |
| 有具体证据引用的 Observer Unsafe | 在对应候选版本内无矛盾时支持 Unsafe |
| 同一候选版本同时存在 Safe 与 Unsafe | Uncertain，进入复核 |
| Observer Uncertain、无证据引用的 Unsafe、调用失败或超时 | 保留未决，不凭此判 Unsafe 或清空为 Safe |
| 对活动候选版本的审阅均为 Safe | Safe |
| 有候选但尚未审阅 | Uncertain |
| 无违规观测且采集健康 | Safe；采集不完整时不能据此确认 Safe |

审阅绑定候选 ID 与事实版本。对一个候选的 Safe 不会清除另一个候选的违规证据，新事实也不会自动继承旧版本的 Safe。最终采集质量检查会将覆盖不足的 Safe 降为 Uncertain；已有明确 Unsafe 仍附带质量说明。本文以最终 Unsafe 定义可行动输出，Uncertain 单独计为弃权。阈值改变可能影响真实运行中的审阅调用，因此其端到端效果须与固定观测的离线分析区分。

## 算法 3 替换伪代码

```text
Input: confirmed actions, active candidates and their fact versions,
       recorded scoped reviews, enabled rules, capture quality
1. Apply enabled prohibitive rules to confirmed actions.
2. Aggregate reviews within each candidate and fact version.
3. Preserve pending candidates and conflicting or failed reviews.
4. Aggregate hard violations and scoped outcomes into one run verdict.
5. If capture quality is not healthy, downgrade Safe to Uncertain.
6. Persist the canonical run verdict and its evidence references.
Output: Unsafe / Safe / Uncertain, evidence authority, quality status
```

这里的硬证据指启用规则对确认动作的命中，不意味着任何凭证文件读取都自动违规。新 sensitive-egress 规则在 fix4 主测量中未开启，其结果只在单独消融中报告。

## §5.1 语料与标签

应区分场景目录、候选评测集、有效运行和已执行攻击。fix4 的输入 release 含 93 个场景；本次选择 process 模式的 19 个良性与 44 个攻击场景，取得 18 个良性和 43 个攻击有效运行。其余场景不进入这次结果的分母。每场景按批次顺序取首个满足完成状态、canonical verdict、健康采集和数据库条件的运行；无效尝试与排除原因保留，不能按裁决优劣选择运行。

场景规范描述预期攻击目标和注入载体，不等于该次运行中攻击已发生。钩子 marker 证明钩子触达，接收端收据证明相应载荷到达，两者均不直接证明全部攻击目标成功。真实运行中的责任主体、入口和传播链需另行标注。本次不报告缺少独立标注记录的人类一致性或真实根因定位准确率。

## §5.2 基线与外部基准协议

仅输入 prompt 的文本检测器与使用操作系统事件并调用 Observer 的系统具有不同信息和成本条件，不能将旧表中的差异解释为同输入检测器优越性。公平比较应在同一有效运行上构造“文本”与“文本＋工具轨迹”输入臂，使用同一裁决定义并报告弃权、覆盖和成本。同级方法的功能比较与实际部署复现结果分列。

原稿的 InjecAgent 适配没有执行对应的操作系统工作流，属于文本适配分析，不能证明运行时或跨智能体泛化。因此撤下该表作为当前系统泛化证据；外部文本协议与真正运行时协议分别报告，不用旧数字补齐尚未完成的应用层基线。

## §5.3 参数与指标

没有可核验的独立开发集记录，不能宣称开发集与测试集严格独立，也不能宣称参数在开跑前已固定。离线敏感性分析固定已记录图、证据与 Observer 观测，改变阈值、相对权重及门控中心，报告分诊与最终标签的变化；它不生成新 Observer 结果，不估计真实调用次数或新增调用的检测收益。

统一缩放全部六个权重会在归一化时抵消，仅作为实现控制，不作为相对权重敏感性证据。相对权重分析逐次仅改变一维，其余五维不变。逐维 leave-one-out 则将一维权重置零。两种分析均不能替代独立测试集。

良性条件误报率以获得健康二元裁决的良性运行作为分母，并同时报告全体有效良性运行的覆盖率与弃权率。攻击侧先报告执行证据，再分别报告 Unsafe、Safe、review 分布；全攻击场景的 Unsafe 输出频率不称为已执行攻击召回率。

## §6.1 运行级结果

本节仅使用 fix4 的冻结二进制与输入 release，不与旧构建、旧协议或不同输入基线混表。

| 集合 | 运行数 | Unsafe | review | Safe | 二元覆盖率 | 弃权率 |
|---|---:|---:|---:|---:|---:|---:|
| 有效良性 | 18 | 0 | 3 | 15 | 83.3% | 16.7% |
| 有效攻击场景 | 43 | 4 | 21 | 18 | 51.2% | 48.8% |
| 其中 marker 钩子触达 | 16 | 4 | 9 | 3 | 43.8% | 56.3% |

良性条件误报为 0/15，Wilson 95% 区间为 [0%, 20.39%]；小样本不能据此声称总体误报率为零。全部有效攻击场景的 Unsafe 输出频率为 4/43；在 marker 钩子触达子集上为 4/16，在该子集的已裁决运行中为 4/7（57.1%，Wilson 95% 区间 [25.05%, 84.18%]）。最后一项排除了九个 review，须与该子集 56.3% 弃权率一同解释。

43 个攻击运行中，16 个出现 marker，8 个有接收端成功收据，二者并集为 18；并集子集与 marker 子集不能混用。marker 触达比例 16/43=37.2% 不是完整攻击成功率。日志拒绝措辞可能与部分钩子执行重叠，不能用“有拒绝语言”直接推断未执行，更不能将未执行样本记作监控器正确防御。

18 个攻击场景运行的 Safe 不能整体称为漏报；只有结合逐运行执行证据才能判断实际已执行攻击中的漏报。marker 子集内的三个 Safe 是后续排查的直接对象。接收端在同机受控配置中使用非 127 地址，此配置验证载荷到达，不证明跨主机通信。

## §6.2 活动量、敏感性与消融

fix4 中良性 PBC 均值为 0.72，攻击 PBC 均值为 1.77，纯 PBC 排序的 AUROC 为 0.528。该结果不支持单用 PBC 作为可靠攻击分类器，也不能替代与攻击活动规模匹配的困难良性测试。

25 个阈值组合的固定观测 replay 未改变 61 个运行的最终裁决；攻击臂的分诊路径最多变化一个运行。逐维 LOO 同样未改变这批记录的裁决，分诊最多变化一个运行。以上是已记录语义观测条件下的稳定性，不是重新触发 Observer 后的端到端鲁棒性。相对权重与门控的新增结果见 [E3 报告](../evaluation/e3-threshold-sensitivity.md)，合入 Word 时使用该报告的实测表。

单独开启 sensitive-egress 硬规则的离线消融在默认关闭时逐条复现 61 个记录裁决；开启后两个有执行证据的攻击由 review 转为 Unsafe，良性无翻转。这说明确定性动作链具有改进潜力，尚不等于开启该规则后的新 campaign 性能。原稿“责任图显著提高 AUROC”及 eBPF/PBC 分层增益数字撤下；仅 eBPF 与 eBPF＋PBC 的端到端拆分尚未完成。

## §6.3 身份与根因定位

合成身份拓扑测试用于验证共享进程、会话切换、连接复用、子进程和委派的合并/分离规则。其测试对象和真值由拓扑规格定义，不能作为真实 23 个 Type C 场景的根因准确率。真实评价应独立标注责任主体、入口与传播链，分别报告身份归属、Top-k/MRR、入口命中和链级指标。当前删除用图深度或图规模替代定位准确性的结论，保留图作为可检查证据组织的展示。

## §6.4 完整系统开销（实测完成）

**内部状态：正式实测已完成（2026-10-28）；本节数字来自冻结的 36/36 条件批次与独立探针补充。写作成员请使用[Overhead 中文报告](../../results/overhead/README.md)（14 张独立图 + 8 张表）；T8/F2 原产物在 `compare/results/overhead/raw/full-system/report/`。**

旧报告的218.2624 ns由三个BPF程序的平均执行时间相加得到，触发次数不同，配套TLS循环未建立连接，不能解释为单次探针或一次成功SSL操作的成本。新稿撤回该解释及与握手时长的成本比较；历史RSS和已移除ONNX服务的资源数字不作为当前系统开销。

**实验规模**：一套固定 Agent 负载（每实例 16 个成功受控操作，覆盖文件、进程和 TLS，并能进入真实 Observer 路径）。并发 1 比较无监测（C0）、仅采集（C1）、采集加本地责任分析与持久化且 Observer 关闭（C2）、完整系统（C3）；并发 4/8/16/32 只比较 C0 与 C3。15 个配对区组、36 次正式条件运行、372 个 Agent 实例，每格 3 次；36/36 完成，工作完成率均为 100%。正式 plan SHA-256 `1f8a02e5…`；T8.md SHA-256 `7bd95ce4…`；F2.png SHA-256 `cce37005…`。最后一次 32 并发 C3 曾被 300 s harness 关闭上限截断，归档该失败 attempt 后用 600 s 上限按固定恢复规则重跑同一冻结条件；矩阵、负载与测量口径未改，失败 attempt 的费用与部分数据保留。

**完整成本**（T8-a/b/e，均值 [min–max]）：

| 指标 | C0 并发 1 | C1 并发 1 | C2 并发 1 | C3 并发 1 | C3 并发 32 |
|---|---:|---:|---:|---:|---:|
| 条件墙钟（s） | 2.32 [2.27–2.37] | 4.50 [3.92–5.43] | 3.90 [3.83–3.98] | 9.27 [9.04–9.50] | 150.91 [40.21–234.74] |
| Agent 端到端（s） | 2.27 [2.22–2.32] | 2.97 [2.42–3.82] | 2.22 [2.12–2.32] | 2.49 [2.22–2.72] | 2.88 [2.75–3.15] |
| collector CPU 秒 | — | 0.75 [0.73–0.76] | 0.80 [0.79–0.81] | 0.85 [0.83–0.89] | 93.76 [24.94–149.53] |
| 峰值 RSS（MiB） | — | 373.6 [372.5–374.7] | 376.1 [375.8–376.4] | 376.0 [375.8–376.1] | 772.7 [461.7–1036.5] |
| DB+WAL 峰值（MiB） | — | — | 2.98 [2.65–3.63] | 3.59 [3.50–3.75] | 169.45 [84.92–232.27] |
| trace 终态（MiB） | — | 1.01 [0.93–1.12] | 0.76 [0.75–0.78] | 0.78 [0.77–0.79] | 47.86 [39.43–52.42] |
| 责任图节点/边 | — | — | 123.7/160.7 | 125.7/163.3 | 823.7/36,254.7 |
| 图更新次数/总耗时 | — | — | 327.7/4.14 ms | 331.0/4.28 ms | 82,572/3.51 s |
| Observer 尝试/费用上界 | 0 | 0 | 0 | 2.0/$0.005232 | 8.3/$0.067886 |

并发 1 下，仅采集相对无监测的条件墙钟增量为约 2.2 s，完整系统为约 6.9 s；C2−C1 是 runtime 与持久化的组合增量（约 −0.6 s，落在运行间噪声内），不能解释为 graph-only。并发 32 下，完整系统的条件墙钟为 150.91 s [40.21–234.74]，而 C0 为 3.99 s [3.46–4.98]；三次 C3 分别为 40.2/177.8/234.7 s，重尾由 phase-2 批量关联主导。责任图终态为 823.7 节点/36,254.7 边，图更新 82,572 次、合计 3.51 s；图更新计时只覆盖在线 graph builder 与增量 buffer 写入，不含 phase-2 关联、pattern/scoring、快照和 DB 等待，不能代表全部责任图相关成本。

**捕获完整性与告警**：所有 C1–C3 条件的受控操作 raw missing 与 persisted loss 均为 0.0%；32 并发为 0/1536 raw、0/1536 persisted。该分母是成功完成的受控文件操作，不是全局事件数。32 并发下进程采集源报告 13,439–16,442 次 ring-buffer 丢弃，TLS 源为 0；并发 1 各源为 0。上游丢弃、语义过滤和写库失败分层报告，不合并成单一 loss 率。可关联最终告警/受控操作在并发 1 为 1/16（6.25%，p50=p95=7.63 s），32 并发为 23/1536（1.5% [0.4–2.3]，p50=144.10 s、p95=145.17 s）。覆盖率低是因为多数受控文件操作不产生可告警证据，不是漏报率；未关联样本不记零延迟。

**探针补充**：TLS 成功读写微基准（256 B、256 次循环、16 次预热、3 次重复且均零丢弃）测得每成功 SSL 读写操作 **873.4 ns [386.7–1133.5]**（相关程序合计时间/成功读写操作数），逐程序均值 `probe_SSL_rw_enter` 186.7 ns、`probe_SSL_read_exit` 564.6 ns、`probe_SSL_write_exit` 808.7 ns。其他已启用程序的 3 个 C1 短诊断窗口给出 `enter_syscall` 66.3–70.9 ns/call、`complete_syscall` 85.6–90.7 ns/call、`handle_exec` 3.74–4.15 μs/call、`handle_exit` 1.47–1.75 μs/call；这些是窗口内平均、可能含快速过滤返回，不能称为每个有效事件的完整处理成本，诊断计时与正式系统计时分开。

**费用**：36 个正式条件的 Agent+Observer 峰时费用上界合计 **$0.6204**（含归档失败 attempt $0.0309）；本实验全部 cohort 合计 **$1.6465**（观测/已知 usage 上界 $1.0265 + 未计量请求准备金 $0.62）。费用按真实 usage 与 DeepSeek 官方峰时单价计算，是保守上界而非账单；缺失 usage 不记零。

**边界**：单主机、单内核（`7.0.0-38-generic`）、单一固定 Agent 负载、每格 3 次描述性重复；不报 CI 或 p99，不推断长期无泄漏或跨环境性能。任务耗时的配对差值在并发 1 为 −0.03%…+17.32%、并发 32 为 −4.05%…+20.70%，负值保留且不作显著性声明；固定短任务的差异落在运行间噪声内，不能据此宣称加速或精确减速。结果只支持该冻结构建与负载下的完整成本与并发趋势。

**内部执行与交付**：协议见[实验总方案§6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)及 §6.6 实测结果；T8/F2 由 `overhead_report.py` 严格完整模式导出，36/36 条件、无 warning。Word 合入时用 T8 替换表 12、用 F2 替换图 8，并把本节改写为正式结果段落；正式引用前按 [claims-overhead-2026-10-28.json](claims-overhead-2026-10-28.json) 登记新数字与 artifact 哈希（16/16 已核对）。

## §7 讨论与结束语

本文的主要结果是建立从操作系统事件到智能体证据组织和运行级复核裁决的实现路径。OS 观测补充应用文本，但受受支持协议、动作可见性与身份歧义限制；会话提示和相关性边不能自动解释为经过独立验证的因果真值。评分是分诊启发式，图的证据组织价值与检测增益分别评价。冻结采集已显示攻击执行不足、较高 review 比例和钩子触达与安全裁决的关系；困难良性、同输入基线与真实多智能体归因仍是接下来的实验重点；完整开销已完成正式实测（§6.4），其边界为单主机、单内核、单一负载与 3 次描述性重复。

## Word 合入与提交要求

1. 替换中英文摘要、§3.5 与算法 3，并同步引言贡献和结束语，移除 TPR=1.000 等旧结论。
2. 以本稿 §6.1–§6.4 替换旧性能、基线与消融表及配图；旧图不能只改图注继续使用。
3. 真实身份/根因、同输入基线、困难良性和完整开销完成后，将新增实验接到相应段落，不用离线稳定性替代端到端结果。
4. 页码、图表编号、交叉引用在 Word 排版后再核对。提交前按编辑要求合成“修改说明→带修订状态稿→定稿”单一 PDF；截止为 2026-11-03 之前。
