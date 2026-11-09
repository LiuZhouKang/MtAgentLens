# FuxiBench 数据卡

FuxiBench 用于研究 LLM Agent 安全观测。其场景是人工设计的受控任务，并非生产流量样本；当前场景级标签已由刘周康、唐浩钺、刘锦样依据冻结的 codebook 逐场景核查、修订并确认。它们是场景设计参考，不是运行级因果标签。

## 数据组成

| 项目 | 内容 |
| --- | --- |
| 场景 | 93 个，其中 30 个为多 Agent 场景 |
| 场景级标签 | 58 attack、35 benign |
| 攻击分类 | A：47；B：11 |
| 检测排除 | 当前 GT 中 0 个 |
| GT schema | `mtagentlens.ground_truth.v4` |
| 分类版本 | `fuxibench-2type/1` |

A 类包含由不可信指令或被污染产物驱动的攻击；B 类包含多 Agent、交接或委派权限攻击。`benign` 表示审计未发现可证攻击。分类定义和具体判定规则见 [标注规范](docs/codebook.md)，逐场景信息见 [场景清单](docs/scenarios.md)。

每条 GT 中，`label` 和 `attack_type` 是当前分类；`expected_behaviors`、`benign_activity` 和 `execution_check` 描述场景检测契约；`annotation` 与 `label_annotation` 记录三位标注者及规范版本。完整字段与规则见 [标注规范](docs/codebook.md)。

## 标注与证据

三位标注者为刘周康、唐浩钺、刘锦样。标签分歧按字段回到场景输入与 fixture 证据核对，并按[标注工作流](docs/annotation.md)记录处理结论。

## 使用与解释

检测分析应以当前 GT 为依据，按场景报告标签、排除条件和分母，并记录 void、采集异常及不确定结果；同一场景的重复运行不能视为独立场景。场景契约是设计参考，运行时观测结果应通过实际运行单独取得。

`evidence/pbc/` 和 `evidence/validation/` 保存分析与验证材料，使用时须结合各自记录的运行和实验条件。`traces/` 包含脱敏采集数据库，供审阅使用，不可回放到当前分析内核；详情见 [trace 说明](traces/TRACES-NOTICE.md)。

本数据集没有人工验证的运行级根因金标，也不单独证明检测性能、真实世界患病率或对所有威胁的覆盖。使用者应结合具体实验设计解释结果。

## 许可和引用

数据采用 CC-BY-4.0，见 [数据许可](LICENSE)；仓库代码采用 Apache-2.0 OR MIT，见 [Apache-2.0](docs/licenses/LICENSE-APACHE) 和 [MIT](docs/licenses/LICENSE-MIT)。引用信息见 [CITATION.cff](CITATION.cff)。
