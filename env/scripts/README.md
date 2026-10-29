# env/scripts/

基准环境的辅助脚本。日常入口是 `mtagentlens-benchmark`（见 `env/README.md`）；
本目录只放框架之外的验证、归档与迁移工具。

| 脚本 | 用途 | 状态 |
|------|------|------|
| `task_wrapper.sh` | tmux pane 内启动 agent、写状态文件 | 框架调用 |
| `compact_db.py` | DB 压缩；`verify.sh` 的门禁用例 | 活跃（有测试） |
| `accountability_evidence.py` | 证据契约唯一实际校验器 | 活跃 |
| `gen_fixtures.py` | 生成场景 fixtures | 活跃 |
| `check_alerts_db.py` | 直接检查 alert DB 行 | 调试 |
| `summarize_responsibility_run.py` | 汇总单次 run | 调试/报告 |
| `verify_pipeline.py` | 管线完整性检查 | 调试 |
| `audit_trace.sh` / `audit_trace.py` | 按已配置采集源检查完整性、丢弃与观测覆盖 | 活跃（有测试） |
| `legacy/evaluate_frozen_v2.py` | 旧版冻结集评估 | 历史，保留复现 |
| `legacy/freeze_10.py` | 一次性冻结 10 个场景 | 历史，保留复现 |
| `legacy/fix_indirect_injection.py` | 一次性场景修复 | 历史，保留复现 |
| `legacy/upgrade_indirect_injection.py` | 一次性场景升级 | 历史，保留复现 |
| `legacy/add_accountability_ground_truth.py` | 一次性 GT 补充 | 历史，保留复现 |
| `legacy/generate_expected_alerts_summary.py` | 旧评估摘要（其入口已删除） | 历史，仅存档 |

## 采集质量审计

```bash
env/scripts/audit_trace.sh runs/<run-id> <scenario-id>
# 无 collector_health.json 配置记录的旧运行，需要显式声明预期来源：
env/scripts/audit_trace.sh runs/<run-id> <scenario-id> --sources tls,process,stdio
```

审计只读 SQLite，并复用运行器的计数解析器。预期来源以运行时配置为准，
不能通过减少 `--sources` 隐藏已开启的来源。缺失最终统计、无效计数、丢弃事件、
来源没有观测或配置冲突均阻断；零告警本身不阻断。返回码：通过 0、阻断 2、
无效调用 3。通过只表示采集质量检查通过，场景是否执行和数据集是否准入另行验证。
