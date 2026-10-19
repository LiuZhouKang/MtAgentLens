# Collector overhead baseline

机器可读：`docs/evaluation/overhead-baseline.json`（当前为 P1，2026-10-18 实测）
门：`scripts/wire/check_baselines.py`（完整性）+ `collector/tests/overhead_gate.rs`（离线吞吐/RSS）

## 测量方法

- `mtagentlens record -c claude --binary-path <claude ELF> --server-port 7396`
- 固定负载：一次 `claude -p "Reply with exactly: PONG. Do not use tools."`
- 每 1 秒采样**整棵进程树**（Rust 主进程 + 两个 eBPF 程序 + Python 推理服务）的
  `utime+stime+cutime+cstime` 与 `VmRSS`；事件数取日志中以 `{` 开头的行。
- 命令：`./scripts/wire/measure-overhead.sh 45 docs/evaluation/overhead-baseline.json`

## 结果（当前 release，2026-10-18 02:xx，内核 7.0.0-38-generic）

| 指标 | 数值 |
|---|---|
| 平均 CPU | 6.24%（单核百分比） |
| 峰值 RSS（整棵树） | 1,424.8 MiB |
| 事件率 | 19.6 events/s |
| 日志吞吐 | 47.2 KB/s |
| 峰值进程数 | 4 |

### 峰值 RSS 归因（同一采样点）

| 进程 | 峰值 RSS |
|---|---|
| `python3`（DeBERTa ONNX 推理服务） | **1,327.3 MiB** |
| `mtagentlens`（Rust 主进程） | 73.6 MiB |
| `tls_capture`（eBPF） | 83.1 MiB |
| `process`（eBPF） | 4.7 MiB |

## 与 P0.5 的对比

| 指标 | P0.5（2026-10-17） | P1（2026-10-18） | 说明 |
|---|---|---|---|
| 平均 CPU | 8.24% | 6.24% | 单次 45s 采样，受工作负载时序影响；无回归 |
| 峰值 RSS | 1,410.0 MiB | 1,424.8 MiB | 基本不变；ONNX 服务仍占 93% |
| 事件率 | 17.4/s | 19.6/s | 同一量级 |
| 日志吞吐 | 12.5 KB/s | **47.2 KB/s** | 日志体积随响应体大小波动（此前一次为 38.9 KB/s）；原始 `data_b64` 是主要成本 |

日志体积增长是 P1 的明确代价，不是泄漏：同一个响应现在会以三种形态出现——
原始压缩分片（带 `data_b64`）、解码后的合成事件（`decode_status=ok`，含明文 `data_b64`）、
以及 SSE 归一化事件。`--max-log-size` 的轮转参数需要按新体积重新评估。

## 结论

1. **Rust 采集器本体约 75 MiB，解码响应没有显著增加 CPU 或 RSS。**
2. **Layer 3 的 Python 推理服务仍占 1.3 GiB（全部开销的 93%）**，由
   `collector/src/responsibility/judgment/classifier.rs:72-82` 启动。这是 P2/P4 的
   产品决策（量化模型 / 按需加载 / 默认关闭 Layer 3）。
3. 真正的 P1 代价在**磁盘/带宽**，不在 CPU/内存：日志吞吐约 3.1×。

## 边界（诚实声明）

- 单次实测，不是分布；CPU/RSS 每秒采样一次，极短命子进程可能漏计。
- 实时重测需要 sudo 与可用的 LLM endpoint，**不在 `verify.sh` 中**。
  `verify.sh` 覆盖的是：基线完整性与来源字段 + 离线解码的吞吐/峰值 RSS 预算。
- 若未来改变 Layer 3 的加载策略或日志字段，本基线必须重测并更新，而不是静默沿用。

## 2026-10-19 重测尝试（无效，未替换基线）

在活跃交互会话期间执行 `./scripts/wire/measure-overhead.sh 45`，结果被会话污染：
采集到 5,301 事件（基线 883）、平均 CPU 1,836%（基线 6.24%）、RSS 1,627 MiB。
`-c claude` 把本会话的全部工具/子进程一并捕获，因此该次测量**不是** collector
开销，已丢弃并恢复 2026-10-18 基线。干净主机（cgroup/PID 隔离）重测仍是 P1 缺口。

同一次运行暴露了一个真实的持久化缺陷：`FOREIGN KEY constraint failed`。
定位为两处写放大——`dispatch_event` 每事件重写全部临时身份映射，以及父记录被
通道丢弃后子记录仍插入。已在 ADR-0013 记录并修复（映射去重、父 ID 门、批处理
路径 `write_blocking`）；同一 20 秒负载现在 0 丢弃 / 0 写失败。
