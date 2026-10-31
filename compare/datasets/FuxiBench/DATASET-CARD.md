# FuxiBench 数据卡

FuxiBench 用于研究 LLM Agent 安全观测。其场景是人工设计的受控任务，并非生产流量样本；场景级标签是 AI 共识形成的设计参考，不是人工验证的金标准，也不是运行级因果标签。

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

每条 GT 中，`label` 和 `attack_type` 是当前分类；`legacy_label`、`legacy_attack_type` 等字段记录旧分类，不能直接当作当前标签。`expected_behaviors`、`benign_activity` 等字段描述场景检测契约；`label_annotation` 保存标注依据。完整字段与规则见 [标注规范](docs/codebook.md)。

## 标注与证据

场景标签由三个互相隔离的同模型 AI 标注会话产生；无多数的场景以及与旧标签发生变化的字段经过裁决。共接受 279 个标注会话，91 个场景三票一致，2 个场景进入无多数裁决。具体流程和原始记录见 [标注协议](docs/annotation.md) 与 `evidence/v5/`。

这是一套 AI 标注数据：没有人工标签、人工复核、人工金标或人工一致性指标。AI 标注之间的一致性描述标注过程，不能作为标签正确率或人机等效的证明。

此前有 9 个场景被排除；经后续盲标和裁决，它们作为困难良性场景纳入当前 93 场景。旧攻击契约仍作为历史信息保留，不能作为当前攻击标签。迁移和依据见 [来源说明](docs/provenance.md)。

## 使用与解释

检测分析应以当前 GT 为依据，按场景报告标签、排除条件和分母，并记录 void、采集异常及不确定结果；同一场景的重复运行不能视为独立场景。数据集中的场景契约是设计参考，运行时观测结果应通过实际运行单独取得。

`evidence/pbc/` 和 `evidence/validation/` 保存历史分析与验证记录，分别对应其各自的运行和分类口径，不能直接替代当前场景分母。`traces/` 包含脱敏历史数据库，供审阅使用，不可回放到当前分析内核；详情见 [trace 说明](../../../datasets/FuxiBench/traces/TRACES-NOTICE.md)。

本数据集没有人工验证的运行级根因金标，也不单独证明检测性能、真实世界患病率或对所有威胁的覆盖。使用者应结合具体实验设计解释结果。

## 许可和引用

数据采用 CC-BY-4.0，见 [数据许可](../../../datasets/FuxiBench/LICENSE)；仓库代码采用 Apache-2.0 OR MIT，见 [Apache-2.0](../../../datasets/FuxiBench/docs/licenses/LICENSE-APACHE) 和 [MIT](../../../datasets/FuxiBench/docs/licenses/LICENSE-MIT)。引用信息见 [CITATION.cff](../../../datasets/FuxiBench/CITATION.cff)。
