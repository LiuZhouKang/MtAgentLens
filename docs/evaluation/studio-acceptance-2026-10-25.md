# 单页 studio 验收实测（2026-10-25，只读探针）

> 目的：用**真实服务器**验证"标注 + 复核同页、独立标注默认不拉取 AI 草稿、
> 导出仅 curator 可用"。探针只 GET，不写任何标注/复核记录；使用 store 的
> **临时副本**（`/tmp/studio-probe.sqlite`），测完删除。**没有任何真人标签被写入。**

## 1. 启动

```bash
cp local/workpackages/2026-10-24-pilot-codebook-v1/records.sqlite /tmp/studio-probe.sqlite
python3 -m tools.annotator serve --store /tmp/studio-probe.sqlite \
  --rater "刘周康" --method human --mode studio --codebook-version pilot/1 --port 7433
# -> http://127.0.0.1:7433/#token=<capability>
```

## 2. 探针结果与断言

| 探针 | 结果 | 断言 |
|---|---|---|
| `GET /`（带 Bearer token） | 页面同时含"标注"（22 次）与"复核"（23 次）pane 标记 | 单页双 pane ✅ |
| `GET /api/tasks` | `{"tasks":[{"id":"task-0001","has_annotation":false}, …]}` | 独立队列**只暴露 id/has_annotation**，无草稿元数据 ✅ |
| `GET /api/tasks/task-0001`（标注视角） | keys = actors, codebook_version, comparisons, differences, events, history, id, own, review_stale, reviewed_revisions；`model_proposal / proposal / proposed` **全部不出现** | 标注视角看不到 AI 草稿 ✅ |
| `GET /api/tasks/task-0001?review=1`（复核视角） | 出现 `attestations` 与 model proposal 字段 | 复核视角才可见草稿 ✅ |
| `GET /api/export`（studio 模式） | **403**（`curator_required`） | 导出仅裁决模式 ✅ |

服务端其余保护（代码与单测已覆盖，本次未重复攻击）：Host/Origin 校验、Bearer
capability、`no-store`、CSP 带 nonce、独立模式禁未脱敏 raw 事件、请求体 ≤1 MiB。

## 3. 与门禁的关系

这些断言的**自动化版本**在门禁中：
`tools/annotator/tests/test_studio_ux.py`（独立 pane 永不见草稿元数据/正文；复核队列可见
proposal）与 `frontend/e2e/annotation-workbench.spec.ts`（双 pane、键盘、axe 0 violations）。
本次实测是端到端 sanity check，不替代那些测试。

## 4. 边界

- 只验证了 **studio 模式 + human rater 名称**的读取路径；没有产生人工修订、复核声明或
  attestation——这些仍需真人实际操作（见 `docs/HANDOFF-NEXT-SESSION.md` §4）。
- 探针使用 store 副本，仓库内权威 store 未被修改。

## 5. 权威 store 未被修改（证据）

探针结束后对权威 store 计数：`bundle_tasks=2`、`bundle_revisions=6`
（全部为既有 `model_proposal`）、`review_attestations=0`；临时副本已删除。

