# MtAgentLens 开销实验：结果、图表与原始数据

> **统一入口 · 审稿意见 5 · 整理日期：2026-10-29**  
> 完整系统实验：2026-10-28 冻结批次；Collector 稳态补测：2026-10-29。两者的负载和测量窗口不同，分别解释。本文及下列文件取代此前分散在 `local/` 和 `docs/evaluation/` 的开销结果入口。

## 1. 先看结论

**固定本地负载下，Collector 稳态单核 CPU 均值为 0.661%，三次均低于 5%。** 这个结果包含 Rust Collector 和两个采集子进程，支持限定采集负载下的低 CPU 占用；不代表完整系统或任意真实 Agent 流量均低于 5%。

- **探针执行较轻**：TLS BPF 执行均值 873.35 ns／成功 SSL 读写操作，三个重复范围 386.68–1133.54 ns。它不等于完整采集延迟。
- **完整系统有真实成本分账**：并发 1 的完整系统每条件消耗 0.85 CPU·s、峰值 RSS 376.04 MiB，真实 Observer 费用上界 $0.005232／条件。
- **高并发仍有边界**：32 并发存在来源丢弃、资源上升和证据关联覆盖不足；不能表述为全并发零丢失或完整系统始终低于 5%。

**推荐阅读顺序：本页 → [综合图](figures/overhead_compact.pdf) → [完整分账表](tables/component_accounting.md) → 按需下载 CSV。**

## 2. 图：稳态成本、完整系统资源与关联覆盖

![MtAgentLens 开销综合图](figures/overhead_compact.png)

[PDF（论文排版）](figures/overhead_compact.pdf) · [SVG（可编辑矢量）](figures/overhead_compact.svg) · [PNG（预览）](figures/overhead_compact.png)

**图注。** A：固定本地负载下三次独立运行的 Collector CPU 轨迹。每约 5 s 由累计 CPU 时间差除以实际窗口时长计算，不插值、不平滑；CPU 100% 表示占满一个逻辑核。图内报告全程平均 CPU 与 5% 平均 CPU 目标。B：真实 Agent＋Observer 完整系统批次的平均单核 CPU 与平均峰值 RSS，包含启动与收尾，每档三个独立重复。C：受控文件捕获覆盖与成功凭据动作的 evidence 关联覆盖，后者标明分子／分母。B、C 的并发档位等距排列。A 与 B 的负载不同，不计算直接性能改善倍数。

**制图规格。** 恢复原来的 13 × 3.8 英寸宽幅布局、彩色稳态轨迹、均值标注与图内图例。DejaVu Sans；PDF 嵌入 TrueType 字体，SVG 保留可编辑文本，PNG 为 300 dpi。

## 3. 表：Collector 稳态与完整系统分账

### 3.1 Collector 稳态

| 独立重复 | 有效窗口 | CPU 时间 | 单核平均 CPU | 平均 PSS／RSS（MiB） | 成功受控文件缺失 | 成功子进程 EXEC 捕获 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 60.000 s | 0.40 s | 0.667% | 111.38／213.32 | 0／600 | 60／60 |
| 2 | 60.000 s | 0.40 s | 0.667% | 111.00／213.02 | 0／600 | 60／60 |
| 3 | 60.000 s | 0.39 s | 0.650% | 111.12／213.05 | 0／600 | 60／60 |
| 三次均值 | 60.000 s | 0.397 s | **0.661%** | **111.17／213.13** | **0／1,800（合计）** | **180／180（合计）** |

**条件与质量。** 每秒 10 次双向 TLS echo（40 次成功 SSL 读写，256 B／次）、10 次唯一文件操作、1 次 `/bin/true` 执行，固定速率持续 60 s。每次独立启动、等待两类探针就绪后计时；包括三个采集进程的用户态、系统态及已回收子进程 CPU，未计入负载程序本身。没有在测量前后额外加入静默时段来稀释 CPU。BPF 累计统计开关为 0，TLS／Process 来源终态丢弃计数均为 0。

**适用范围。** 固定 TLS 载荷不是 HTTP。每次完成 2,400 次 SSL 操作，但 trace 输出为 1,200 个读载荷记录、1,200 个 incomplete 协议诊断及健康记录；不能把来源零丢弃解释为所有原始操作载荷逐一完整留存，更不能据此声称真实 LLM HTTP/SSE 解析完整。未设置无监测配对对照，因此本补测不估算被观测任务减速或全部内核 BPF CPU。

PSS 为进程树 `smaps_rollup` 比例分摊内存之和。ring-buffer 的双重虚拟映射使 RSS 高于实际物理映射负担；PSS 未覆盖其他全部未映射内核 BPF map，不重复加上已计入映射的 ring 容量。

### 3.2 原完整系统实验：保留真实部署成本

| 并发 Agent 数 | CPU 时间均值（CPU·s） | 含启动平均单核 CPU | 峰值 RSS 均值（MiB） | Observer 费用上界／条件 | 成功凭据动作有 evidence |
|---|---:|---:|---:|---:|---:|
| 1 | 0.85 | 9.15% | 376.04 | $0.005232 | 3／3 |
| 4 | 2.00 | 13.27% | 396.36 | $0.011752 | 3／12 |
| 8 | 4.48 | 27.70% | 411.57 | $0.013960 | 5／24 |
| 16 | 15.08 | 38.34% | 434.56 | $0.027504 | 11／48 |
| 32 | 93.76 | 63.93% | 772.66 | $0.067886 | 23／96 |

每档三个独立条件运行；原实验为 36 条件、372 个 Agent 实例。并发 1 使用 C0 无监测／C1 仅采集／C2 采集＋分析＋DB／C3 完整真实 Observer；并发 4/8/16/32 仅 C0/C3。C2−C1 包含分析与持久化，不能称为纯图维护成本。

并发 1：C1／C2／C3 CPU 时间分别为 0.75／0.80／0.85 CPU·s；C3 图更新累计 4.28 ms／条件（不含全部分析、phase-2、快照与 DB），Observer 2 次／条件、12,706 输入＋1,176 输出 token。费用按已记录实际 usage 与实验日期峰时费率估算上界，非供应商账单。

原 C1 短批次平均 CPU 为 17.11%，首秒已消耗约 91%–96% 的累计 CPU，包含原生 Agent 大 ELF 的 BoringSSL 检测启动成本。新补测使用 OpenSSL；其就绪时间约 0.204 s、启动 CPU 约 0.073 s，不能拿来替代原生 Agent 冷启动结果或声称产品代码已得到同等优化。

### 3.3 探针补充：复用已有短诊断

| 项目 | 三次窗口均值 | 解释 |
|---|---:|---|
| TLS BPF 执行／成功 SSL 操作 | **873.35 ns** | 256 B 载荷；三个重复均无来源丢弃；非端到端延迟 |
| Process syscall entry／completion | 69.0／88.7 ns／调用 | 包含过滤返回，不代表有效事件完整处理成本 |
| Process exec／exit | 3.885／1.605 μs／调用 | 同上；没有逐 Hook 新增大矩阵 |

TLS 的不同程序平均时间不能简单相加。未触发程序标为未覆盖，不作为零开销；程序次数、累计执行时间和覆盖状态见 [probe_programs.csv](data/probe_programs.csv)。主性能计时未开启 BPF 统计；探针诊断的统计开关原值为 0，结束后均恢复。原 218 ns 的“单次操作成本”解释已撤回。

## 4. 审稿回复必须保留的限定

1. **捕获不等于完整归因。** 32 并发的受控操作 raw／persisted 缺失均为 0／1,536，但 Process 来源丢弃为 13,439–16,442；二者分母不同，不能合并为单一丢失率。TLS 来源零丢弃也不代表协议解析没有截断或不完整。
2. **关联覆盖不足客观存在。** 32 并发的 96 个成功凭据动作均进入 actions，只有 23 个具有关联 evidence，不能仅以其他无关文件操作解释。该良性审计 fixture 不是攻击真值，不能据此估算攻击漏报率。
3. **延迟依赖成功关联样本。** 原 145.17 s 是三个运行内 p95 的均值；23 个已关联操作合并后的描述性 p95 为 228.16 s，只共享 3 个最终 alert。不能把 23 条样本当作独立重复，也不能推广到未关联操作。终点为 Collector 广播，不是浏览器接收。
4. **恢复尝试保留。** 32 并发第三次原尝试在 300 s 被截断，恢复运行改为 600 s。原计划中未核实到预登记该恢复规则的证据，不称作已证明的预注册重试。失败日志、DB 与费用在历史归档中。
5. **环境与版本限定。** 单主机、内核 `7.0.0-38-generic`、三次描述性重复，没有长期泄漏或跨环境结论。未人为终止其他任务，不能称为独占主机。冻结 Collector ELF SHA-256 为 `a0aba182b45a00a7a3959da03d2ae5f373d12e3ce6d4ca6be115eb520734c5b5`；旧 `ff621e25…` 是 sudo 包装器的哈希。

## 5. 可直接使用的审稿回复草稿

> 感谢指出局部探针延迟和 RSS 不足以代表完整系统成本。我们撤回旧 218 ns 的单次操作解释，补充采集、分析与持久化、责任图维护计时、真实 Observer 调用及外部模型费用的分账结果。冻结构建下，完整系统并发 1 的 CPU 时间为 0.85 CPU·s、峰值 RSS 为 376.04 MiB，Observer 费用上界为 $0.005232／条件；同时报告不同并发下的资源、来源丢弃、条件告警延迟与关联覆盖。为区分冷启动和持续采集成本，我们另用固定本地负载完成三次 60 s 测量，Collector 进程树单核平均 CPU 为 0.667%、0.667%、0.650%，平均 PSS 为 111.17 MiB；这一结果限定于所列合成负载。TLS BPF 执行成本为 873.35 ns／成功读写操作，其他程序短诊断单列。高并发丢弃和关联覆盖不足如实保留；未关联操作不进入延迟分位数，也不记为零。

该草稿仍需与最终论文表号、图号及实际合入段落一致，不把实验执行完成写成高并发完整归因已获验证。

## 6. 文件导航：需要什么就打开什么

```text
overhead/
├── README.md       本报告：结论、方法、图表、审稿回复与数据入口
├── figures/        唯一当前综合图，PDF／SVG／PNG
├── tables/         当前分账表、冷启动组件表、探针汇总表
├── data/           CSV、数值 JSON、文件指纹与迁移索引
├── raw/            正式完整系统、三次稳态、探针原始证据
├── reproduce/      现有证据的审计与图表生成脚本
└── archives/       早期批次、失败／预跑、旧图表，按主题压缩
```

### CSV 数据及单位

| 文件 | 数据层级／用途 | 行数 |
|---|---|---:|
| [steady_runs.csv](data/steady_runs.csv) | 三次稳态汇总；CPU 为单逻辑核百分比，内存 MiB | 3 |
| [steady_cpu_windows.csv](data/steady_cpu_windows.csv) | 图 A 的绘图数据；每次 12 个约 5 s 窗口，含实际边界、CPU 秒差及单核占用百分比 | 36 |
| [steady_samples.csv](data/steady_samples.csv) | 进程树时间序列；CPU 为累计秒，RSS／PSS 为 KiB；单调时钟为秒 | 891 |
| [steady_process_samples.csv](data/steady_process_samples.csv) | 逐进程样本；utime／stime／cutime／cstime 为系统时钟 tick | 2,673 |
| [full_system_runs.csv](data/full_system_runs.csv) | 原正式 36 条件，含 C0/C1/C2/C3；包含全部单次结果 | 36 |
| [full_system_samples.csv](data/full_system_samples.csv) | 原 result 中保存的资源时间序列（含原导出抽样）；CPU 秒／百分比、内存 KiB、存储 bytes | 3,665 |
| [complete_system_summary.csv](data/complete_system_summary.csv) | C3 每并发档汇总；含关联分母及不同延迟统计口径 | 5 |
| [controlled_operations.csv](data/controlled_operations.csv) | 5,952 条正式操作回执＋1,800 条稳态成功文件事件；纳秒时间缺失处留空 | 7,752 |
| [observer_attempts.csv](data/observer_attempts.csv) | 真实模型调用；elapsed_ms 为毫秒，epoch_ns 为纳秒；usage 展开列及原始 JSON | 62 |
| [probe_programs.csv](data/probe_programs.csv) | 程序级次数差值、执行时间差值 ns、ns／调用及覆盖标记 | 69 |
| [credential_linkage.csv](data/credential_linkage.csv) | 每个 C3 条件的成功凭据动作、关联 evidence 和 intent PID 数 | 15 |
| [original_files.csv](data/original_files.csv) | 整理前文件的路径、大小、SHA-256 或符号链接目标 | 20,112 |
| [migration.csv](data/migration.csv) | 旧路径到现路径／压缩包成员的对应关系及整理状态 | 见文件 |
| [package_files.csv](data/package_files.csv) | 整理后文件清单与 SHA-256；清单自身除外 | 见文件 |

空值保持为空，不能将其解释为零。CSV 的 `source_json` 是当前路径，`original_source_json` 保留测量时路径。冻结 JSON、计划、日志和 DB 内容未为迁移而改写，因此内部仍可能出现旧路径；通过 `migration.csv` 追溯。PID、绝对单调时间和文件路径均只在原测量环境内解释。

### 原始证据与历史归档

- [完整系统正式批次](raw/full-system/)：冻结计划、构建指纹、36 条件的 result／measurement-evidence／回执／原生 Agent 输出、trace、SQLite DB、原 T8 与数值导出。
- [Collector 稳态](raw/steady/)：三次 result、逐进程 JSONL 样本、工作量、trace、健康证据、冻结计划与原测量脚本／目标。
- [探针](raw/probes/)：TLS 微基准及三次 Process 程序短诊断。
- [早期实验](archives/earlier-cohorts.tar.gz)：被替代的早期 cohort、校准、旧构建及原始费用线索。
- [预跑与截断尝试](archives/formal-pilot-and-censored.tar.gz)：原正式批次 pilot 和 300 s 截断尝试。
- [诊断与失败补测](archives/diagnostic-attempts.tar.gz)：空闲检查、费用上界 $0.001679292 的三个旧真实 Agent 任务、重复挂载的无效测量和短校验。无效测量未纳入 0.661% 的结果。
- [任务工作目录](archives/workload-workdirs.tar.gz)：372 个 Agent 的 profile／fixture 和三次稳态 fixture，已压缩归档，避免数千个合成工作文件干扰浏览；原生任务输出、操作回执与 trace 仍可直接查看。
- [更早的 218 ns 性能材料](archives/legacy-218ns-performance.tar.gz)：原 `compare/results/perf/` 的旧 JSON、图和表；该数字解释已撤回，文件校验见 `data/legacy_probe_files.csv`，不作为当前实验结果。
- [旧版报告与图表](archives/previous-report-and-figures.tar.gz)：原 14 图、8 表和历史长报告；仅供追溯，本页限定优先。

本地正式补测及短校验 API 费用为 $0。原完整实验全部 cohort 的既有费用上界为 $1.6465（含 $0.62 未计量请求准备金）；不与本地补测混为同一费用分母。

## 7. 从已有证据重新生成与核对

在仓库根执行以下命令，**不启动新实验、不调用 API**：

```bash
# 从原 DB 复核凭据动作关联覆盖
env/.venv/bin/python compare/results/overhead/reproduce/audit_existing.py

# 更新唯一综合图、分账表、两张汇总 CSV 及输入哈希
env/.venv/bin/python compare/results/overhead/reproduce/make_compact.py

# 核对原完整系统的 16 条已登记数字
env/.venv/bin/python scripts/evaluation/verify_claims.py \
  --manifest docs/paper/claims-overhead-2026-10-28.json
```

已验证：三次正式稳态结果与输入指纹一致；1,800 条成功文件事件、180 个成功子进程执行均可核查；探针调用次数和执行时间可计算对应 ns／调用；CSV 行数与来源一致。归档在清理源目录前完整读取校验，失败证据没有因结果不理想而丢弃。没有重新运行完整测试套件或付费矩阵。

`raw/steady/run.py` 是保留原哈希的历史执行脚本，包含测量时旧路径，不能直接作为迁移后的新实验命令执行。重新测量需要新输出目录和新冻结计划，不覆盖本包。`.db`、日志、二进制和历史归档属于本地实验产物，不自动纳入 Git。

## 交接

- 交付清单：[`MANIFEST.md`](MANIFEST.md)
- 原始数据：[`raw/`](raw/)（2952 文件，1.1 GB）
- 派生数据：[`data/`](data/)；说明见 [`data/README.md`](data/README.md)。
