# 采集修复复核与真实验收

本次仅执行无害临时文件及回环网络 fixture，未执行攻击场景或收费模型任务。
当前内核为 `7.0.0-38-generic`。未验证其他内核或架构。

## 已执行结果

- `make -C bpftools all test`：13 个 C 测试二进制全部通过，日志
  `/tmp/mtagentlens-kernel-c-tests.log`。
- `flock /tmp/mtagentlens-cargo.lock cargo test --lib framework::runners::common::tests`：
  7 passed；非法 stdout 计数/完整排空的新测试先失败后通过，日志
  `/tmp/mtagentlens-kernel-red.log`、`/tmp/mtagentlens-kernel-rust-tests.log`。
- Python 采集器健康/累计计数测试：17 passed；增加真实 trace 路径、健康不依赖
  出现攻击、STDIO 最终损失计数的红绿回归。
- 生产 `process` helper 实际加载内核，fixture 的七项事实检查全部通过：读写 flags
  分离、`/proc/self/environ` 可见、open 失败返回 `-ENOENT`、fork 未 exec 子进程
  父身份、回环 connect 成功、回环 connect `-ECONNREFUSED`、最终统计与 exit 0。
  原始事件/诊断/汇总位于 `/tmp/mtagentlens-kernel-verification-_lgqgy_r/`。

**实际最终统计为 dropped_events=9、rate_limit_drops=0、aggregation_table_full=0。**
检查通过表示指定事实被采到，不代表全程零损失。helper 当前先采集全机事件再做
用户态目标筛选；全机负载和用户地址读取失败均可影响累计损失。不得将这次记录
写成 dropped=0 或据此宣布生产负载零损失。最终运行质量应按非零损失降级。

## 收尾修复

关闭先向 helper 请求 TERM，再持续排空到 EOF 或整体五秒截止；不再使用 50ms
无事件空窗或 5000 条上限提前终止。helper supervisor 记录解析跳过、管道错误、
完整排空和最终计数，缺统计、解析损失、强杀或非零退出均不报告 healthy。
STDIO 先停止生产并排空再输出最终计数；早期 process loader 失败不输出伪最终统计。

benchmark 解析使用真实 trace 文件，不重复把诊断文件当 trace；按来源合并累计
计数，包含 STDIO 的最终 STATS，未知统计不补零。overall_healthy 要求完整且
零损失，出现 PBC 属检测结果，不再作为健康前提。

独立只读审查发现的前端初始化竞态、切 run 残留图/调查上下文/rAF 告警，以及
迁移失败事务清理与 nullable 归档导出边界已交给主审处理。

## 消费端最后复核

独立审查继续发现 routing 对所有 `/sys/kernel/` 路径无条件去噪，抵消了生产者
保留安全相关路径的修复。已按授权删除路径猜测过滤，保留 agent 进程身份边界。
新增真实 `dispatch_event → GraphDeltaBuffer.actions` 回归，确认三个 agent 内核
路径的打开/截断事实可以进入分析链；测试先失败（0 条而非 3 条）后通过。

另修正 connect `-EALREADY`：producer 标记 pending，旧 routing 却改为 failed。
现在保留 pending 和原始 `-114` 返回值，没有把它当成功。

`cargo test --lib responsibility::runtime::routing::tests`：**9 passed**。
红绿日志分别为 `/tmp/mtagentlens-routing-red.log`、`/tmp/mtagentlens-routing-green.log`。
此轮仅以合成事件验证消费者，没有实际写入内核路径。
