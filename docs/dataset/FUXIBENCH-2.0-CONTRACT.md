# FuxiBench 数据与版本契约

当前数据包位于 [datasets/FuxiBench](../../datasets/FuxiBench/README.md)。本页描述消费约定；文件身份、来源和标签细节分别由包内 release、数据卡和来源说明维护。

## 场景与标签

当前包包含 93 个场景，58 attack、35 benign，攻击类型为 A47、B11，`detection_excluded=0`。

- A：不可信内容或被污染的工件引导执行。
- B：涉及多 Agent、交接或委派权限的攻击。
- benign：按当前标注协议裁决为良性，包括有较高系统活动量的困难良性。

类型版本为 `fuxibench-2type/1`。GT schema 保持 `mtagentlens.ground_truth.v4` 以兼容工具；schema 版本不等于历史分类版本。旧标签和审计记录保存在包内 provenance/evidence，不据旧稿中的 Type A/B/C 解释当前字段。

## 包身份与内容

`release.json` 给出发布身份，`checksums.sha256` 绑定文件字节。完整包包括场景、fixture、prompt 输入、GT、来源证据及发布清单规定的 traces。运行或复算前校验匹配的 release；只获得源码不代表已取得全部数据库文件。

[数据卡](../../datasets/FuxiBench/DATASET-CARD.md)定义数据用途，[PROVENANCE](../../datasets/FuxiBench/PROVENANCE.md)记录标签生成与修订，[AI 标注协议](../../datasets/FuxiBench/AI-ANNOTATION-PROTOCOL.md)说明参考来源。发布包内有些 traces 属于历史审阅材料，其可回放性以包声明和 checkpoint 实际状态为准。

## 消费边界

场景设计标签、运行执行事实、身份参考与根因参考分别使用，不将模型建议标作人工标签，不把设计级参考当作运行级事件真值。当前检测结果的 93 场景 cohort 及 PP/ITT 分母见[实验状态](../evaluation/CURRENT-EVIDENCE.md)。

`docs/evaluation/scenarios/` 保留被历史标注证据引用的设计说明原文，其旧类型名不覆盖当前包字段。不得为整理文档改写发布包、fixture、GT 或输入哈希。

数据集采用包内 `LICENSE-DATA` 所声明的 CC-BY-4.0；代码许可证另见仓库根目录。引用方式见包内 `CITATION.cff`。

## 校验

在仓库根目录执行：

```bash
env/.venv/bin/python scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
```

复算正式实验使用[复现手册](../evaluation/REPRODUCE.md)，不以改动发布身份或跳过错误的方式通过校验。
