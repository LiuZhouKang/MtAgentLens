# MtAgentLens 基准测试环境

基于 eBPF 的 LLM Agent 异常检测基准测试框架。通过 tmux 编排运行真实的 Claude Code 攻击场景，MtAgentLens 采集器捕获内核级事件流，输出结构化检测结果用于评估。

## 快速开始

```bash
# 1. 配置 API 密钥
ln -sf vendors/deepseek/claude.env config/claude.env
# 编辑 config/claude.env，填写 ANTHROPIC_AUTH_TOKEN

# 2. 构建采集器（项目根目录）
make build

# 3. 冒烟测试
make env-smoke

# 4. 运行单个场景
make env-run-oneclick SCENARIO=prompt-injection
```

## 命令参考

| 目标 | 说明 |
|------|------|
| `make env-list-scenarios` | 按模式分组列出所有场景 |
| `make env-smoke` | 冒烟测试：快速运行 prompt-injection |
| `make env-run-fast SCENARIO=<id>` | process 模式运行单个场景（`claude -p`，无 tmux） |
| `make env-run-oneclick SCENARIO=<id>` | tmux-live 模式运行单个场景，完整工件落盘 |
| `make env-verify RUN_DIR=runs/<id>` | 对已完成运行执行评估 |
| `make env-eval RUN_DIR=runs/<id>` | 同上（别名） |

透传额外参数：

```bash
make env-run-fast SCENARIO=network-anomaly ARGS="--collector"
make env-run-oneclick SCENARIO=agent-teams-race-conflict ARGS="--parallel 4"
```

## 执行模式

### process 模式

`claude -p` 单次调用，Claude 输出响应后立即退出。适合单 agent 场景和快速迭代。

```bash
make env-run-fast SCENARIO=prompt-injection
# 等价于: mtagentlens-benchmark run --scenario prompt-injection --mode process
```

### tmux-live 模式

每个 agent 一个 tmux pane，通过 `task_wrapper.sh` 管理生命周期（含 watchdog 超时保护）。适合多 agent 协作场景。

```bash
make env-run-oneclick SCENARIO=agent-teams-race-conflict
# 等价于: mtagentlens-benchmark run --scenario agent-teams-race-conflict --mode tmux-live
```

## 采集器

通过 `--collector` 启用 MtAgentLens eBPF 采集器，捕获 TLS 明文、进程事件、文件操作：

```bash
make env-run-fast SCENARIO=prompt-injection ARGS="--collector"
```

采集器需要 sudo 权限。设置环境变量避免交互式密码提示：

```bash
export MTAGENTLENS_SUDO_PASSWORD='your-password'
```

## 运行工件

每次运行在 `runs/<timestamp>-<uuid>/` 下生成：

```
prompts/           每个 agent 的组装后 prompt
logs/              每个 agent 的标准输出日志
status/            每个 agent 的状态 JSON（running → done/timeout/error）
collector/         采集器输出（trace.log、responsibility.db、stderr）
summary.json       运行结果摘要
.scenario_id       场景标识符
behavior.json      提取的 agent 行为证据
```

## 数据集归档

`run_one.py` 运行单个场景并将结果归档到 FuxiBench：

```bash
sudo python3 env/run_one.py prompt-injection
```

流程：
1. 启动 MtAgentLens eBPF 采集器
2. 执行场景（自动选择 process 或 tmux-live 模式）
3. 质量检查（PBC 边、告警、证据计数）
4. 将 `responsibility.db` 复制到 `compare/Dataset/FuxiBench/traces/<category>/<scenario>/`

## 场景模式

场景 JSON 自声明 `mode`，运行器自动注入对应的 Claude Code 环境变量：

| mode | 环境变量 | 说明 |
|------|----------|------|
| `single_agent` | `CLAUDE_SUBAGENTS_MODE=0` | 单 agent 场景 |
| `agent_teams` | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | 多 agent 并行协作 |
| `subagents` | `CLAUDE_SUBAGENTS_MODE=1` | 子代理 handoff 链 |

## 环境配置

```
config/
├── claude.env → vendors/deepseek/claude.env   # 当前激活的配置（symlink）
├── claude.env.example                         # 配置模板
├── lab.default.json                           # 实验室默认配置
├── observer.toml                              # Observer LLM 配置（symlink）
├── observer.toml.example                      # Observer 配置模板
└── vendors/
    ├── deepseek/claude.env                    # DeepSeek API 配置
    └── claude-aws/claude.env                  # Claude AWS API 配置
```

切换 vendor：`ln -sf vendors/claude-aws/claude.env config/claude.env`

## 目录结构

```
env/
├── README.md                   ← 本文件
├── pyproject.toml              ← mtagentlens-benchmark 包定义
├── Makefile                    ← 便捷命令
├── run_one.py                  ← 单场景运行 + 数据集归档
│
├── src/mtagentlens_benchmark/ ← 基准框架
│   ├── cli.py                  ← CLI 入口
│   ├── engine/                 ← 执行器（tmux 编排、采集器管理）
│   ├── loader/                 ← 场景加载器 + Schema 校验
│   ├── evaluator/              ← 评估器（指标计算、消融）
│   ├── annotator/              ← 标注工具（GT 生成、Kappa）
│   ├── packager/               ← 数据集冻结与打包
│   ├── verifier/               ← 验证器框架
│   └── workspace.py            ← workspace 管理
│
├── docs/                       ← 文档
│   ├── schema.md               ← 场景 JSON Schema 规范
│   ├── methodology.md          ← 基准方法论
│   └── contributing.md         ← 场景贡献指南
│
├── scripts/                    ← 工具脚本
│   ├── task_wrapper.sh         ← tmux agent 生命周期管理
│   ├── legacy/                 ← 一次性迁移/旧评估脚本（历史，保留复现）
│   ├── verifiers/              ← 场景验证器（73 个）
│   └── ...                     ← 数据库检查、数据集冻结等
│
├── scenarios/                  ← 按类别组织的场景定义（以 make env-list-scenarios 为准）
│   └── fixtures/               ← 攻击触发 fixture 文件
├── tests/                      ← 测试（23 个文件）
├── config/                     ← 本地配置（真实凭据 gitignored）
└── runs/                       ← 运行工件（gitignored）
```

## 文档

- [Schema 规范](docs/schema.md) — 场景 JSON Schema 完整字段说明
- [基准方法论](docs/methodology.md) — 设计哲学、分类体系、评估矩阵
- [贡献指南](docs/contributing.md) — 如何贡献新场景、CC-BOS 策略、Verifier 编写规范
