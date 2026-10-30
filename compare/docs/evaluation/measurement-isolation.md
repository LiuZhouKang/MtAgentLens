# 测量隔离

测量需同时隔离 Agent 配置与采集目标。此机制用于控制实验环境，不是容器或安全沙箱。当前测量结果及其环境见[实验状态](CURRENT-EVIDENCE.md)。

## 配置隔离

[clean_profile.py](../../../env/src/mtagentlens_benchmark/engine/clean_profile.py)为每次运行生成独立 settings、MCP 配置和 manifest，限制继承的环境与扩展。Claude Code 的启动参数关闭交互环境中的插件、hooks、MCP、skills 和自动记忆来源；实际采用的参数以每次 manifest 和命令行为准。

Benchmark CLI 提供 `--clean-profile`；也可通过 `MTAGENTLENS_CLEAN_PROFILE=1` 启用对应路径。不要根据操作者的交互会话配置推断受测进程的配置。

## 采集目标隔离

同名交互 Agent 可能同时产生 TLS 与系统事件。针对原生 Claude ELF，实验工具可建立独立副本，使 uprobe 绑定和进程 comm 与外层会话区分。`--agent-binary` 与 `--agent-comm` 指定该目标；comm 不超过 Linux 的 15 字节限制。

运行记录应包含 binary 路径、comm、clean profile 与实际进程信息。仅设置 `-c claude` 可能包含其他同名进程，不能据此声称实验目标已隔离。

受控良性运行入口为 [run-benign-fpr.sh](../../../scripts/wire/run-benign-fpr.sh)。该脚本会执行工作负载，需按当前预算、权限和测量目标选择运行；它不是单纯离线校验命令。

## 其他运行时

Agent 边界可由 `MTAGENTLENS_AGENT_COMMS` 与 `MTAGENTLENS_AGENT_EXE` 扩展，具体行为见[能力清单](../../../docs/capabilities.json)。`MTAGENTLENS_AGENT_CGROUP` 不提供已验证的 cgroup 名称解析，不能作为有效隔离机制。

OpenClaw 的结构化工具意图来自只读的 out-of-band 来源；进程观测边界仍需明确。先于采集器启动的长驻 gateway 应显式选择目标 PID，并核对工具子进程是否进入追踪，不能只依赖新进程 comm 过滤。

## 测量时记录什么

- Agent 实际配置、binary/comm/PID 与采集目标。
- 主机后台活动、CPU/RSS 口径和系统级噪声。
- 各来源 final stats、退出、drain 和持久化丢失。
- 清理顺序：先结束受测进程及会话，再移除临时副本。

配置隔离不会消除整机噪声，也不证明全局零丢失。结果解释应对应各冻结实验实际记录的控制条件。
