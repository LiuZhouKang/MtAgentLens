# T7-S 参数敏感性：设计、结果与论文写作建议

> 软件学报 · 中文期刊规范 · 93 个 FuxiBench 场景 · **0 次新增付费调用**  
> 本报告服务于审稿意见 6 的回复，以及论文参数敏感性段落的改写。  
> 所有数字可由 `metrics/t7-sensitivity.json` 与 `reproduce/` 复算。

## 0. 结论先行

**部署点 `(safe=0.15, unsafe=0.70, gate=10)` 位于一个宽平台中央，而不是刀尖上。**

- 部署点 PP F1 **0.852**，TPR **0.793**，FPR **0.057**，精确率 **0.920**。
- **8103 点**阈值网格中，F1 范围 **0.769–0.877**；最优仅比部署点高 **+0.025**，最差低 **−0.083**；**6980/8103（86.1%）** 的点与部署点差 ≤0.03。
- 六维权重逐维 ±20%：F1 范围 **0.836–0.852**；门控中心 5–20：**0.830–0.852**。
- **1000 次联合 Latin 超立方采样**：F1 中位数 **0.852**，95% 采样分位 **[0.784, 0.877]**，**91.1%** 的采样 F1 ≥0.80。

一句话：**参数联合扰动没有改变“高精确率、低 FPR、F1 稳定在 0.85 附近”的定性结论。**

## 1. 审稿意见 6 的回应逻辑

审稿人指出：基准、阈值（0.15/0.70）、六维权重、门控参数均由作者确定，建议说明依据，或补充参数敏感性分析。

我们的回应不回避“参数由作者设定”这一事实，而是用三层证据回答“参数是否把结论做成了单点”：

### 第一层：两个部署阈值

- safe 0.08–0.30，步长 0.002，111 值；
- unsafe 0.50–0.86，步长 0.005，73 值；
- gate=10 固定；
- 共 **8103 个精确网格点**。

结论：部署点距最优仅 +0.025；86.1% 的网格点落在部署点 ±0.03 内。  
**这不是“只有 0.15/0.70 好”的单点结果。**

### 第二层：六维权重与门控

- 六维权重逐维 ×0.8 / ×1.2，其余固定；
- 门控中心取 5 / 7.5 / 10 / 15 / 20；
- baseline 93/93 score/verdict 精确重放。

结论：六维权重的影响几乎为零（D3 角色为 −0.016，其余为 0）；门控中心最大影响 −0.022。  
**打分器参数不是 F1 的主要驱动因素。**

### 第三层：联合扰动

- 两阈值、gate、六维权重共 9 维；
- 1000 次 Latin 超立方采样；
- 每次采样重算 score/route，固定已记录 Observer，再算 F1。

结论：F1 中位数 0.852；95% 采样分位 [0.784, 0.877]；91.1% 的采样 F1 ≥0.80。  
**即使多个参数同时偏离部署点，绝大多数配置仍保持可用性能。**

## 2. 实验设计

### 2.1 三个子实验

| 子实验 | 变量 | 取值 | 目的 |
|---|---|---|---|
| S1 阈值地形 | safe × unsafe | 111 × 73 = 8103 | 看二维阈值平台 |
| S2 OAT | 六维权重、gate | 逐维 ×0.8/×1.2；gate 5–20 | 看单个打分器参数 |
| S3 联合采样 | 9 维 | 1000 次 LHS | 看联合扰动分布 |

### 2.2 参数范围

| 参数 | 基准值 | 测试范围 |
|---|---:|---|
| safe threshold | 0.15 | 0.08–0.30（步长 0.002） |
| unsafe threshold | 0.70 | 0.50–0.86（步长 0.005） |
| gate center | 10 | 5, 7.5, 10, 15, 20 |
| D1 边界权重 | 0.28 | 0.224–0.336（±20%） |
| D2 模式权重 | 0.25 | 0.200–0.300（±20%） |
| D3 角色权重 | 0.20 | 0.160–0.240（±20%） |
| D4 重复权重 | 0.10 | 0.080–0.120（±20%） |
| D5 证据权重 | 0.10 | 0.080–0.120（±20%） |
| D6 行为权重 | 0.07 | 0.056–0.084（±20%） |

### 2.3 固定记录 Observer 的 routing 重算

对每个参数点：

1. 用冻结的六维分量与当前 Rust `fuse_dimensions` 公式精确重算 raw/calibrated score；
2. 由记录 `gate_factor` 反推 gate 输入，按 `sigmoid((gate_input-center)/2)` 重算 gate；
3. 重算 route：`score > unsafe → DirectUnsafe`，`score < safe → DirectSafe`，否则 `ToObserver`；
4. route 与 baseline 相同：保留 production verdict（记录 Observer 固定）；
5. route 变为 DirectSafe/DirectUnsafe：确定性覆盖；hard-rule runs 保留 production verdict。

**baseline 93/93 的 score/verdict 精确重放**；因此这不是重新跑模型，而是对已冻结预测做参数反事实。

## 3. 结果

### 3.1 阈值地形

| 点 | 安全阈值 | 不安全阈值 | TPR | FPR | 精确率 | F1 | 翻转数 |
|---|---|---|---|---|---|---|---|
| 部署点 | 0.15 | 0.70 | 0.793 | 0.057 | 0.920 | **0.852** | 0 |
| 最优网格点 | 0.08 | 0.50 | 0.862 | 0.086 | 0.893 | **0.877** | 7 |
| 最差网格点 | 0.28 | 0.67 | 0.690 | 0.086 | 0.870 | **0.769** | 23 |

- F1 的 2000 次 scenario bootstrap 95% CI：**[0.732, 0.941]**。
- 部署点不在边缘：距最优 +0.025，距最差 −0.083。

### 3.2 OAT

| 参数 | F1 范围 | 翻转数范围 |
|---|---|---|
| D1 边界 | 0.852–0.852 | 1–2 |
| D2 模式 | 0.852–0.852 | 1–2 |
| D3 角色 | 0.836–0.852 | 0–3 |
| D4 重复 | 0.852–0.852 | 1–1 |
| D5 证据 | 0.852–0.852 | 0–1 |
| D6 行为 | 0.852–0.852 | 0–1 |
| 门控中心 | 0.830–0.852 | 0–8 |

**读法**：逐维权重变化几乎不改变 F1；门控中心是影响最大的单参数，但最大降幅也只有 0.022。

### 3.3 联合采样

- 中位数：**0.852**；
- 95% 采样分位：**[0.784, 0.877]**；
- F1 ≥0.80：**91.1%**；
- F1 ≥0.82：**84.2%**；
- 决策翻转率均值 12.8%，p95 30.1%。

**读法**：联合扰动下，大多数配置仍在 0.80 以上；没有出现“参数一变就崩”的区域。

## 4. 主图

![图 8 检测器参数敏感性](figures/fig8_sensitivity.png)

[PDF（矢量）](figures/fig8_sensitivity.pdf) · [SVG](figures/fig8_sensitivity.svg) · [PNG 600 dpi](figures/fig8_sensitivity.png)

**图 8 检测器参数敏感性。**  
(a) 门控中心固定为 10 时，safe × unsafe 的 PP F1 三维地形；底层为 **8103 个精确网格点**；曲面为渲染平滑，表格与指标使用未平滑精确值。空心圆为最优点 `(0.08, 0.50)`，旁注 `F1=0.877`。  
(b) 1000 次联合 Latin 超立方采样的 ΔF1 经验累积分布（ECDF）；图下标注 safe/unsafe/gate/weights 四个采样范围；中位数为 0，95% 采样分位为 `[−0.068, +0.025]`，91.1% 的采样 F1 ≥0.80。95% 是 1000 个采样配置的 2.5–97.5 分位，不是置信区间。  
图中文字为中文，字体 Noto Sans CJK SC，8 pt；600 dpi；PDF/SVG 矢量。

## 5. 论文中可以直接使用的写法

### 5.1 中文段落（建议放 §5.3 或 §7）

> 为检验检测器结论是否依赖作者设定的单点参数，我们在 93 个 FuxiBench 场景上进行了三层参数敏感性分析。第一，固定门控中心为 10，对安全阈值（0.08–0.30，步长 0.002）与不安全阈值（0.50–0.86，步长 0.005）做 8103 点网格扫描；部署点 PP F1 为 0.852，最优为 0.877（+0.025），最差为 0.769（−0.083），86.1% 的网格点与部署点差不超过 0.03。第二，对六维权重逐维施加 ±20% 扰动，并将门控中心在 5–20 内变化；权重维度几乎不改变 F1（D3 为 −0.016），门控中心的最大影响为 −0.022。第三，对两阈值、门控中心和六维权重共 9 维做 1000 次 Latin 超立方采样；F1 中位数为 0.852，95% 采样分位为 [0.784, 0.877]，91.1% 的采样 F1 不低于 0.80。上述结果表明，本文结论不依赖单一阈值、权重或门控取值；参数扰动不改变“高精确率、低 FPR”的定性结论。

### 5.2 审稿回复模板

> 感谢审稿人指出参数设定的说明不足。我们补充了覆盖全部作者设定参数的三层敏感性分析：8103 点阈值网格、六维权重/门控的 OAT、以及 1000 次联合 Latin 超立方采样。部署点 PP F1 为 0.852，网格最优仅高 0.025，86.1% 的网格点与部署点差 ≤0.03；权重 ±20% 的 F1 范围为 0.836–0.852，门控中心 5–20 为 0.830–0.852；联合采样中位数为 0.852，95% 采样分位为 [0.784, 0.877]，91.1% 的采样 F1 ≥0.80。上述结果说明结论不依赖单点参数；相关图、表与复现脚本已加入正文/补充材料。

### 5.3 图注英文版（如需要）

> **Figure 8. Parameter sensitivity of the detector.**  
> (a) PP F1 terrain over safe × unsafe thresholds with gate center fixed at 10; the underlying grid contains 8,103 exact points. The surface is smoothed only for rendering; tables and metrics use unsmoothed values. The open circle marks the best grid point `(0.08, 0.50)`, with `F1=0.877`.  
> (b) ECDF of ΔF1 over 1,000 joint Latin-hypercube samples; sampling ranges are shown below the panel. Median ΔF1 = 0; the 95% sampling interval is `[−0.068, +0.025]`; 91.1% of samples have F1 ≥0.80. The 95% interval is a sampling quantile, not a confidence interval.

## 6. 边界与禁止表述

1. **固定记录 Observer**：结论限定在 frozen observations；不是 live Observer 重跑。
2. **hard-rule runs 固定**：阈值/权重扰动不改变其 production verdict。
3. **PP 主口径**：29 条 marker=true 攻击 + 35 benign；ITT 只在 baseline 报告。
4. **8103 点为确定性网格**：不是 8103 个独立样本；bootstrap 只描述 cohort。
5. **gate 外推**：其他 gate center 用同一公式外推；baseline 93/93 精确。
6. **联合采样不是置信区间**：95% 是 1000 个配置的 2.5–97.5 分位。
7. **敏感性不等于因果/最优性证明**：它回答“参数是否稳健”，不回答“参数是否全局最优”。

## 7. 文件

```
compare/results/sensitivity/
  README.md
  plan.json
  data/t7-sensitivity.json      # 8103 点 + OAT + LHS
  metrics/t7-sensitivity.json
  tables/table8_sensitivity.{tex,md}
  tables/table8_oat.{tex,md}
  tables/table8_lhs.tex
  figures/fig8_sensitivity.{png,pdf,svg}
  reproduce/build_sensitivity.py
  reproduce/render_figure8.py
  reproduce/render_assets.py
```

```bash
PYTHONPATH="." python compare/results/sensitivity/reproduce/build_sensitivity.py
PYTHONPATH="env:env/src:." python compare/results/sensitivity/reproduce/render_figure8.py \
  --metrics compare/results/sensitivity/metrics/t7-sensitivity.json \
  --out compare/results/sensitivity
PYTHONPATH="." python compare/results/sensitivity/reproduce/render_assets.py \
  --metrics compare/results/sensitivity/metrics/t7-sensitivity.json \
  --out compare/results/sensitivity
```

## 8. 交接

- 交付清单：[`MANIFEST.md`](MANIFEST.md)
- 原始输入：[`data/raw/`](data/raw/)（93 exports + 生产 verdict + promoted ledger + families；说明见 [`data/README.md`](data/README.md)）
- 派生结果：[`data/t7-sensitivity.json`](data/t7-sensitivity.json)
