# FuxiBench 标注数据

本目录集中存放 FuxiBench 场景级 GT 之外、供特定实验评测使用的人工参考标签。
实验结果和预测输出仍分别保存在 `compare/results/`；评测脚本读取本目录中的规范参考集。

| 标注集 | 内容 | 范围 | 规范文件 |
| --- | --- | ---: | --- |
| IdentityBench | 运行中 message、intent、action 单元的身份分区参考 | 30 场景，1,800 单元 | [identity-bench/reference.json](identity-bench/reference.json) |
| AttributionBench | 场景级恶意根因 Agent 标签；证据不足及良性场景为 `null` | 30 场景 | [attribution-bench/root-reference.json](attribution-bench/root-reference.json) |
| AttributionBench pilot | 先导样本的根因参考标签 | 5 场景 | [attribution-bench/root-reference-pilot.json](attribution-bench/root-reference-pilot.json) |

上述参考标签由数据集元数据列出的三位标注者复核、修订并确认。它们是最终评测参考，
不包含逐人独立投票记录；不得据此推算标注者间一致率或 κ。

场景设计级 GT 仍按场景存放于 [`../ground_truth/`](../ground_truth/)，它与这里的运行单元身份
分区及场景根因参考对应不同标注单位，不能互相替代。标注方法见 [`../docs/annotation.md`](../docs/annotation.md)。
