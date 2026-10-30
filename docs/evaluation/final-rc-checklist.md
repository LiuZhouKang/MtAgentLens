# 实验冻结与验收

本页用于准备新运行和核查已有交付。已有实验是否可引用，由其冻结记录及[当前结果](CURRENT-EVIDENCE.md)决定；此清单不触发新实验。

## 固定输入

- 使用 `datasets/FuxiBench/release.json` 与 `checksums.sha256` 确定输入身份，保留实际场景清单。
- 记录 collector/helper 二进制、源码状态、配置、内核、运行环境和模型/provider。
- 分别固定场景设计标签、运行级参考和评估规则；参考来源见[数据卡](../../datasets/FuxiBench/DATASET-CARD.md)。
- 不以修改 payload、地址、任务或 GT 的方式改善检测结果。需要修订输入时发布新身份，并独立说明适用范围。

## 验证采集

- 核对目标进程身份、配置隔离和采集范围，见[测量隔离](measurement-isolation.md)。
- 保存来源 final stats、受控操作的完成/缺失状态、持久化丢失及退出状态。
- 只在来源结束、drain 与分析 finalize 均有证据后认定运行完成；退化运行明确标记。
- 独立审查 attempt 与 accepted/promoted 的关系，保留失败、超时和恢复记录。

## 验证评估与交付

- 核对 PP/ITT、执行标记、排除条件、覆盖、弃权和统计单位。
- 核对用于计算的输入与冻结清单一致；跨 release 和不同二进制结果分别列示。
- 核对指标可由行级产物复算，图表能追溯到输入，MANIFEST 说明前置条件。
- 明确可直接离线重算、需要本地历史现场、需要重新采集或需要模型服务的操作。

## 离线校验入口

在仓库根目录执行：

```bash
./scripts/verify.sh
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
```

完整数据集包含匹配 release 的历史 trace DB；仅有源码时，需先取得匹配的数据交付包。代码门禁通过说明回归检查通过，不替代实验测量。

各结果的重算与额外前置条件见[复现手册](REPRODUCE.md)。
