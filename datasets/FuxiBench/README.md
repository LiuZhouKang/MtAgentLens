# FuxiBench

FuxiBench 是面向 LLM Agent 安全观测的受控场景数据集，包含场景输入、场景级参考标签和标注证据。场景由人工设计；当前发布的场景级标签已由刘周康、唐浩钺、刘锦样按统一 codebook 完成人工核查、修订与最终确认。它们描述场景设计预期，不等同于运行级因果金标。

## 数据概览

| 内容 | 数量 |
| --- | ---: |
| 场景及场景级 GT | 93 |
| 攻击场景 | 58：A 类 47，B 类 11 |
| 良性场景 | 35 |
| 当前检测排除场景 | 0 |
| 多 Agent 场景 | 30 |

A 类指由不可信指令或被污染的产物驱动的攻击；B 类指多 Agent、交接或委派权限攻击。分类定义与标注规则见 [标注规范](docs/codebook.md)。

## 使用数据

- `scenarios/`：场景定义；[场景清单](docs/scenarios.md)列出各场景的标签。
- `fixtures/`、`roles/`、`bench/` 和 `prompt_inputs/`：场景相关输入及支撑材料。
- `ground_truth/<category>/<scenario_id>.json`：每个场景的当前人工确认标签和检测契约，标注信息记录三位标注者及 codebook 版本。
- `annotations/`：集中存放 IdentityBench 和 AttributionBench 的实验专用人工参考标签；目录说明各标注集的单位、范围与用途。
- `evidence/`：当前分析与验证材料。
- `traces/`：脱敏历史采集记录，供来源审阅；这些数据库不能用于当前分析内核的回放，详见 [trace 说明](traces/TRACES-NOTICE.md)。

按场景统计检测结果时，应使用当前 GT 的标签与 `quality.detection_excluded`，并单独报告无效运行、采集异常和不确定结果。场景设计标签本身不能单独证明检测性能。

## 基本检查

按[执行环境说明](../../env/README.md)安装依赖后，在仓库根目录运行：

```bash
# 检查场景标签和必要字段
env/.venv/bin/python scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench

# 检查目录结构、场景输入和 trace 的声明
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py
```

普通读取不需要生成校验清单或填写发布身份。使用数据时记录实际采用的场景和配置。

## 进一步阅读

- [数据卡](DATASET-CARD.md)：数据组成、标签及适用范围。
- [标注协议](docs/annotation.md)：三位标注者的人工标注、复核与裁决工作流。
- [标注规范](docs/codebook.md)：分类和字段定义。
- [场景清单](docs/scenarios.md)：逐场景信息。
- [来源说明](docs/provenance.md)：数据组成和当前标注来源。

数据采用 CC-BY-4.0，见 [数据许可](LICENSE)；仓库代码采用 Apache-2.0 OR MIT，见 [Apache-2.0](docs/licenses/LICENSE-APACHE) 和 [MIT](docs/licenses/LICENSE-MIT)。引用信息见 [CITATION.cff](CITATION.cff)。
