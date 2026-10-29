# 标注数据集 release 的外部消费校验（2026-10-25）

> 验收目标（P0-2）："把 agent 标注试点扩展为可复现 dataset release……**确认外部工具
> 可消费**"。此前 `release-verify` 是仓库内工具；本文件记录一个**不 import
> `tools.annotator`** 的独立消费者，证明"只拿 release 目录也能验证"。

## 1. 是什么

`scripts/evaluation/annotation_release_consumer.py`（stdlib + `jsonschema`）：

1. **内容寻址**：重算 `release_id = sha256(canonical_json(manifest − release_id))`；
2. **字节清单**：`files` 里每个相对路径重算 sha256；并拒绝**未登记的多余文件**
   （`release.json`/`reviewers.json` 是设计上的两个例外）；
3. **case 绑定**：每个 `cases/<id>/bundle/bundle.json` 的 `evidence_bundle_hash` 必须
   等于其内容哈希、且等于该 case 的 `task_id`；每个 source 文件重算 sha256；
4. **标注行校验**：`annotations.jsonl` 每行信封键集合固定；`annotation` 载荷用
   release 自带的 JSON Schema 校验；case id / codebook / method 一致性检查；
5. **计数可复算**：annotations/annotators/methods/model_proposals/human_annotations/
   cases/splits 全部由行与 case 重算并与 manifest 对比；`splits` 映射与 case 记录互相印证；
6. **证据与划分**：验证事件字节锚点、身份和根因引用属于相应 bundle；同 scenario 不得跨 dev/test。
7. **诚信断言**：`paper_metrics_available` 恒为 `false`；消费者不认证人工标注质量。

退出码：0 = 可被外部消费；1 = 列出全部问题。

## 2. 在真实 release 上的结果

```bash
env/.venv/bin/python scripts/evaluation/annotation_release_consumer.py \
  --root local/workpackages/2026-10-24-pilot-codebook-v1/release
# release: e05c0ed213b3338c39c2af9ff77a4136179e5569125777e90a3b192c6876cadb
# cases=2 annotations=6 paper_metrics_available=False
# VERIFIED for external consumption   (exit 0)
```

过程中消费者**抓到了两处真实的可消费性缺口**（已修正消费者实现，不是放宽断言）：

- `schema/annotation.schema.json` 描述的是**标注载荷**（`attempted/executed/…`），
  不是 JSONL 信封；消费者现在分别校验"信封键集合"和"载荷 schema"；
- 顶层 `splits` 是 `case_id → split` **映射**，`counts.splits` 是聚合计数；两者都会与
  case 记录交叉核对。

## 3. 门禁

`compare/tests/test_annotation_release_consumer.py`：有效 release 通过；
篡改文件 / 改 `release_id` / 多余文件 / 载荷 schema 违约 / 计数不可复算 /
无人标注却声称指标 / bundle 内容被改 —— 全部被拒。

## 4. 边界

- 消费者只证明 **release 内部一致性与可解析性**，不证明标注质量、不产生 κ，也不代表
  有真人参与（当前 `human_annotations=0`、`attestations=0`，release 已如实声明
  `paper_metrics_available=false`）。
- 消费者需要 `jsonschema`（本仓库 `env/.venv` 已安装；无网络、无第三方服务）。
