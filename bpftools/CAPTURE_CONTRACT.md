# 采集事实与关闭契约

文件及网络事件通过 `raw_syscalls/sys_enter` 保存参数，使用对应架构的
`asm/unistd.h` 系统调用编号分派；`raw_syscalls/sys_exit` 补充返回值后才发送。
不对 `/proc`、`/dev` 或库目录进行丢弃式过滤。不同文件数量达到压力阈值时
附加警告，仍保留行为证据。

事件的 `outcome` 为 `succeeded`、`failed`、`pending` 或 `unknown`，
`syscall_ret` 保留原始返回值；无法证明完成的历史/合成事件使用 `null`。
`connect` 的 EINPROGRESS/EALREADY 仅表示等待完成。成功 open 证明成功打开，
不能单凭它声明内容已经读出；成功 connect 也不能证明发送了敏感数据。

进程身份为 TGID 加主线程启动时间，父身份读取父进程主线程。
文件聚合区分进程生命周期、路径、flags 和调用结果；首条事件立即发送，
随后聚合只发送额外次数。聚合表满时直接发送一次，不丢失或重复行为。

关闭时 helper 处理 SIGTERM/SIGINT，先停止生产、排空 ring buffer，再输出最终
统计。Rust supervisor 独立持有 helper，消费流被丢弃时仍请求关闭和排空；
超时强制终止、缺少最终统计和非零退出必须保留为采集质量缺口。
`capture_health_snapshot()` 在未配置探针时返回 `not_configured`，不冒充健康。

无害内核验收：以 root 运行 `python3 bpftools/tests/test_capture_integration.py`。
仅使用临时文件、`/proc/self/status` 和本机回环连接，验证 fork 未 exec、
线程身份、成功/失败返回值和最终统计。加载需要 BPF ring buffer 支持（Linux
5.8 起）；当前实际验证为本机 Linux 7.0.0-38-generic。其他内核/架构没有在
本次运行中验证，不能声称兼容矩阵已经完成。
