# MtAgentLens DB 持久化体积优化设计
**日期**: 2026-06-03
**状态**: 待审查
**目标**: 将 10 分钟 session 的 DB 体积从 440MB 降至 < 20MB

---

## 1. 问题诊断

### 实测数据（subagents-handoff-injection-classical, 10min）

| 表 | 行数 | 实际体积 | 根因 |
|----|------|---------|------|
| `graph_snapshots` | 1,638 | ~270KB/snapshot ≈ 440MB | 每个 trigger cycle 存一份完整图 JSON，且无变更也写 |
| `non_link_diagnostics` | 42,731 | ~50MB | 每个 intent-action 匹配失败写一行独立 diagnostic |
| `actions` | 45,381 | ~30MB | 每个文件操作都记录，大量 action 无匹配 evidence |
| `tls_sessions` | 23,878 | 合理 | 正常 SSL 会话记录 |

### 根因

**`graph_snapshots` 三重浪费**：
1. 无变更也写入（图未变化时 90% 的 snapshot 是冗余的）
2. 全量 JSON 副本（1,400 个图元素每次完整序列化）
3. 写入频率不受控（trigger 和 debounce 被 bypass）

**`non_link_diagnostics` + `actions`**：逐行写入无聚合，每毫秒级事件生成一行。

---

## 2. 方案：三层渐进修复

### 第一层：Snapshot 写入门控（核心，减少 95%）

在 `persistence/writer.rs` 中，写入 snapshot 前检查图是否实际变更。

#### 2.1 图内容哈希

```rust
// graph/snapshot.rs — 新增快捷哈希
impl ResponsibilityGraph {
    /// 轻量内容哈希：基于节点数 + 边数 + 最新边 ID
    pub fn content_hash(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.node_count().hash(&mut h);
        self.edge_count().hash(&mut h);
        // 最新边的 edge_id 作为变化指示器
        if let Some(last_edge) = self.all_edges().last() {
            last_edge.edge_id.as_str().hash(&mut h);
        }
        if let Some(last_node) = self.all_nodes().last() {
            last_node.node_id.as_str().hash(&mut h);
        }
        h.finish()
    }
}
```

#### 2.2 Writer 变更检测

```rust
// persistence/writer.rs
pub struct PersistenceWriter {
    // ... 现有字段 ...
    last_snapshot_hash: u64,
    snapshots_written: u64,
    snapshots_skipped: u64,
}

impl PersistenceWriter {
    fn write_snapshot_if_changed(&mut self, graph: &ResponsibilityGraph) -> Result<()> {
        let hash = graph.content_hash();
        if hash == self.last_snapshot_hash {
            self.snapshots_skipped += 1;
            return Ok(());
        }
        self.last_snapshot_hash = hash;
        self.snapshots_written += 1;
        self.write_full_snapshot(graph) // 现有逻辑
    }
}
```

**预期**：1,638 → ~80 snapshots（仅实际变更时写入）。

### 第二层：Diagnostic 和 Action 聚合写入（减少 95%）

#### 2.3 Diagnostic 计数器

```rust
// persistence/writer.rs
pub struct PersistenceWriter {
    // ... 
    pending_diagnostics: HashMap<DiagnosticKey, u64>,
    pending_action_counts: HashMap<String, u64>,
}

#[derive(Hash, PartialEq, Eq)]
struct DiagnosticKey {
    category: String,     // "no_matching_action", "correlation_weak", etc.
    detail_hash: u64,     // 相同内容的 diagnostic 合并
}

impl PersistenceWriter {
    /// 聚合写入：accumulate 同类 diagnostic，定时 flush
    fn write_diagnostic_aggregated(&mut self, diag: NonLinkDiagnostic) {
        let key = DiagnosticKey {
            category: diag.reason_category(),
            detail_hash: hash_str(&diag.detail),
        };
        *self.pending_diagnostics.entry(key).or_default() += 1;
    }

    fn flush_diagnostics(&mut self) -> Result<()> {
        for (key, count) in self.pending_diagnostics.drain() {
            self.conn.execute(
                "INSERT OR REPLACE INTO non_link_diagnostics (category, detail, count, last_seen_ns)
                 VALUES (?1, ?2, ?3, ?4)",
                (key.category, key.detail_hash.to_string(), count, current_time_ns()),
            )?;
        }
        Ok(())
    }
}
```

**注意**：`non_link_diagnostics` 表需要新增 `count` 和 `last_seen_ns` 列，或直接改为 replace 语义。若不想改 schema，可改为每个 trigger cycle 最多写 3 条 aggregated diagnostic。

#### 2.4 Action 过滤

```rust
// 只持久化产生了 correlation_evidence 的 action
fn write_action_if_matched(&mut self, action: &Action, has_evidence: bool) {
    if !has_evidence {
        return; // 90% 的 action 无匹配 → 跳过
    }
    self.write_action(action);
}
```

**预期**：`non_link_diagnostics` 42,731 → ~30，`actions` 45,381 → ~500。

### 第三层：Snapshot 增量存储（长期架构优化）

在 `graph_snapshots` 表增加 delta 存储能力：

```rust
// graph_snapshots 新增列:
//   base_snapshot_id: Option<String> — 基于哪个 keyframe
//   delta_json: Option<String> — 增量 JSON

struct SnapshotDelta {
    added_nodes: Vec<ResponsibilityNode>,
    removed_node_ids: Vec<NodeId>,
    added_edges: Vec<ResponsibilityEdge>,
    removed_edge_ids: Vec<EdgeId>,
}
```

策略：每 20 个 snapshot 存一次 keyframe（全量），其余 19 次只存 delta。读取时从最近 keyframe + 后续 delta 列表重建当前图。

**本次不实现**（作为后续优化），第一层+第二层已足够将 440MB 降至 < 20MB。

---

## 3. 实施计划

### Phase 1：Snapshot 写入门控（1 小时）

1. `ResponsibilityGraph` 新增 `content_hash()` 方法
2. `PersistenceWriter` 新增 `last_snapshot_hash` 字段 + `write_snapshot_if_changed()`
3. `runtime.rs` trigger() 中调用 `write_snapshot_if_changed` 替代直接 `write_snapshot`
4. 编译 + 测试

### Phase 2：Diagnostic 聚合 + Action 过滤（2 小时）

1. `PersistenceWriter` 新增 `pending_diagnostics` HashMap
2. `write_diagnostic` 改为聚合模式（按 category 合并计数）
3. `flush_diagnostics()` 在每个 trigger 末尾 flush
4. `write_action` 增加 `has_evidence` 过滤
5. 编译 + 测试

### Phase 3：E2E 验证（30 分钟）

1. 构建 release binary
2. 运行 shadow-agent 10min → 对比 DB 体积
3. 目标：< 20MB

---

## 4. 兼容性

- `graph_snapshots` 表 schema 不变（只减写入次数）
- `non_link_diagnostics` schema 不变（或可选增加 count 列，向后兼容）
- `actions` schema 不变（只减写入次数）

---

## 5. 预期效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| `graph_snapshots` | 1,638 行 / 440MB | ~80 行 / ~20MB |
| `non_link_diagnostics` | 42,731 行 / ~50MB | ~30 行 / ~100KB |
| `actions` | 45,381 行 / ~30MB | ~500 行 / ~2MB |
| **总 DB** | **~520MB** | **< 25MB** |
