# _drafts — 旧格式场景草稿

这 23 个场景使用旧版 schema（`id` 字段，无 `category`/`attack_type` 字段），
未纳入 FuxiBench 正式评估集。保留作为历史参考。

若需升级到 FuxiBench GT v3 格式，需要：
1. 添加 `category`、`attack_type`、`label` 字段
2. 补充 `expected_behaviors` 标注
3. 采集 eBPF trace
4. 通过 QUALITY_STANDARD.md 门禁
