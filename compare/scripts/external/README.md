# InjecAgent external baseline

这四个脚本复跑 InjecAgent 外部数据集基线，产物对应
`compare/results/external/InjecAgent/` 中已冻结的 JSON。

## 输入

- `compare/Dataset/external/InjecAgent/`：2,108 条攻击用例、良性对照和
  `tools.json`；来源与字段见该目录的 `README.md`。
- `compare/models/prompt-guard-86m/`：`rerun_mpg_injecagent.py` 使用的本地
  Meta Prompt Guard 权重。
- API 配置：`env/config/claude.env` 或
  `env/config/vendors/deepseek/claude.env`，供 Observer 和 Agent
  Sentinel-style 调用。

## 脚本

| 脚本 | 作用 |
|---|---|
| `run_injecagent_benchmark.py` | MtAgentLens + Agent Sentinel-style 联合评估 |
| `run_mtap_injecagent_full.py` | 仅 MtAgentLens 全量评估 |
| `run_as_injecagent_full.py` | 仅 Agent Sentinel-style 全量评估 |
| `rerun_mpg_injecagent.py` | 仅 Meta Prompt Guard 本地权重复评 |

## 运行

在仓库根目录执行，例如：

```bash
PYTHONPATH="env:env/src:." python3 compare/scripts/external/run_injecagent_benchmark.py
```

这些脚本不属于当前 93 场景论文主流程；输出是冻结外部基线证据。重新运行会
覆盖 `compare/results/external/InjecAgent/`，应先确认批次和备份。
