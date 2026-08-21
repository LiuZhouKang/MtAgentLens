# FuxiBench（伏羲基准）

首个多 Agent LLM 攻击检测基准数据集。86 个全标注场景，覆盖 14 个攻击类别和 3 种攻击范式（直接注入 / 间接注入 / 多 Agent 攻击）。

命名取自伏羲——上古圣人观天地万象而创八卦，以八种符号分类万物；FuxiBench 以同样的系统性观测精神，通过 OS 级 eBPF 探针将 LLM Agent 攻击面分解为可检测的信号维度。

## 关键数字

| 指标 | 值 |
|------|-----|
| 总场景 | 86（67 攻击 + 19 良性） |
| 攻击类别 | 14 |
| Type A（直接注入） | 10 |
| Type B（间接注入） | 34 |
| Type C（多 Agent） | 23 |
| 标注者 | 3 人独立交叉验证 |
| 标注日期 | 2026-06-11 |
| 内核版本 | Linux 6.17 |

## 目录

```
FuxiBench/
├── README.md              ← 本文件
├── SCENARIOS.md           ← 完整场景目录（86 场景详细描述）
├── QUALITY_STANDARD.md    ← 顶会级数据质量标准
├── metadata.json          ← 机器可读元数据
├── ground_truth/          ← GT JSON 归档（含主评估与扩展/灰区样本）
├── prompt_inputs/         ← 无标签泄露的 prompt 输入包
└── traces/                ← eBPF trace 归档（含主评估与扩展/灰区运行）
```

> 当前 paper 主评估规模以 `compare/results/paper/main_evaluation.json`
> 为准：86 场景，其中 Type A=10、Type B=34、Type C=23、Benign=19。
> `ground_truth/` 与 `traces/` 目录还保留扩展、灰区和历史运行归档，
> 不能直接用文件数量反推主评估规模。

## 快速开始

```bash
# 查看完整场景目录
cat SCENARIOS.md

# 加载元数据
python3 -c "import json; print(json.load(open('metadata.json')))"

# 加载 Ground Truth
python3 -c "
import json, os
for root, dirs, files in os.walk('ground_truth'):
    for f in files:
        if f.endswith('.json'):
            d = json.load(open(os.path.join(root, f)))
            print(f'{d[\"scenario_id\"]}: {d[\"category\"]} ({d[\"attack_type\"]})')
"
```

## 攻击类型

| 类型 | 注入方式 | 检测挑战 |
|------|---------|---------|
| **Type A** | 攻击载荷直接在 task prompt 中 | 意图识别、行为边界约束 |
| **Type B** | 攻击载荷隐藏在 fixture 文件中 | 文件→进程边界跨越检测 |
| **Type C** | 跨进程/跨 Agent 协同攻击 | 进程血缘追踪、身份解析、污点传播 |

## 评估工具

| 工具 | 范式 | 输入 |
|------|------|------|
| MtAgentLens | eBPF 因果行为检测 | eBPF trace + Redis/JWT |
| LLM Guard | Prompt 分类器 | task + fixture 文本 |
| Meta Prompt Guard | Prompt 微调模型 | task + fixture 文本 |
| Agent Sentinel | eBPF LLM 审计 | eBPF trace |

## License

- 场景定义、fixture 和 Ground Truth：CC-BY 4.0
- 冻结 eBPF trace：需签署使用协议
