# FuxiBench 数据与版本契约

当前数据包位于 [datasets/FuxiBench](../../datasets/FuxiBench/README.md)。本页描述消费约定；场景、标签和来源以数据卡及来源说明为入口。

## 场景与标签

当前包包含 93 个场景，58 attack、35 benign，攻击类型为 A47、B11，`detection_excluded=0`。

- A：不可信内容或被污染的工件引导执行。
- B：涉及多 Agent、交接或委派权限的攻击。
- benign：按当前标注协议裁决为良性，包括有较高系统活动量的困难良性。

类型版本为 `fuxibench-2type/1`。GT schema 保持 `mtagentlens.ground_truth.v4` 以兼容工具；schema 版本不等于历史分类版本。旧标签和审计记录保存在包内 provenance/evidence，不据旧稿中的 Type A/B/C 解释当前字段。

## 数据内容

数据集包括场景、fixture、prompt 输入、GT、来源证据与历史 traces。[数据卡](../../datasets/FuxiBench/DATASET-CARD.md)说明数据用途，[来源说明](../../datasets/FuxiBench/docs/provenance.md)记录标签生成与修订，[标注协议](../../datasets/FuxiBench/docs/annotation.md)说明参考来源。历史 traces 供审阅，不支持当前分析内核回放。

普通加载和结构校验不要求 release 清单或逐文件校验。历史实验的输入身份按其原始记录追溯，不将当前目录的修改冒充为当时的数据快照。

## 消费边界

场景设计标签、运行执行事实、身份参考与根因参考分别使用，不将模型建议标作人工标签，不把设计级参考当作运行级事件真值。当前检测结果的 93 场景 cohort 及 PP/ITT 分母见[实验状态](../evaluation/CURRENT-EVIDENCE.md)。

`docs/evaluation/scenarios/` 保留被历史标注证据引用的设计说明原文，其旧类型名不覆盖当前包字段。不得为整理文档改写发布包、fixture、GT 或输入哈希。

数据集采用包内 `LICENSE` 所声明的 CC-BY-4.0；代码许可证另见仓库根目录。引用方式见包内 `CITATION.cff`。

## 校验

在仓库根目录执行：

```bash
env/.venv/bin/python scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
```

复算正式实验使用[复现手册](../evaluation/REPRODUCE.md)，历史实验的计算范围以对应冻结记录为准。
