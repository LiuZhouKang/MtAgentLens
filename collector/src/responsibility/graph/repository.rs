// SPDX-License-Identifier: MIT
//
// repository.rs — GraphRepository: specialized SQLite queries for the
// responsibility graph that PersistenceReader does not provide.
//
// PersistenceWriter handles all writes (nodes, edges, snapshots).
// GraphRepository provides graph-specific read queries:
//   - find_nodes_for_agent   — all graph nodes belonging to an agent
//   - find_subgraph           — BFS subgraph around a center node
//   - find_edges_in_window    — edges of a type within a time range

use crate::responsibility::types::{
    AgentId, EdgeId, GraphSnapshot, NodeId, ResponsibilityEdge, ResponsibilityEdgeType,
    ResponsibilityNode, ResponsibilityNodeType,
};
use rusqlite::{Connection, params};
use std::path::Path;
use std::sync::Mutex;

// ─── GraphRepository ──────────────────────────────────────────────────────

/// SQLite-backed graph query layer for the responsibility graph.
///
/// Provides read-optimized queries for graph traversal and analysis.
/// All writes go through PersistenceWriter; this repository is for
/// specialized graph queries that go beyond PersistenceReader's generic API.
pub struct GraphRepository {
    conn: Mutex<Connection>,
}

impl GraphRepository {
    /// Open (or create) the repository at `path`. Enables WAL mode for
    /// concurrent read access alongside PersistenceWriter.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, rusqlite::Error> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    // ── Node queries ────────────────────────────────────────────────────

    /// Find all graph nodes belonging to an agent.
    pub fn find_nodes_for_agent(
        &self,
        agent_id: &AgentId,
    ) -> Result<Vec<ResponsibilityNode>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT node_id, node_type, agent_id, resource_json, evidence_refs_json,
                    timestamp_ns, phase
             FROM graph_nodes WHERE agent_id = ?1",
        )?;
        let rows = stmt.query_map(params![agent_id.to_string()], |row| {
            Ok(ResponsibilityNode {
                node_id: NodeId::from(row.get::<_, String>(0)?),
                node_type: serde_json::from_str(&row.get::<_, String>(1)?)
                    .unwrap_or(ResponsibilityNodeType::Agent),
                agent_id: row.get::<_, Option<String>>(2)?.map(AgentId::from),
                resource: row
                    .get::<_, Option<String>>(3)?
                    .and_then(|s| serde_json::from_str(&s).ok()),
                evidence_refs: row
                    .get::<_, String>(4)
                    .map_or(Vec::new(), |s| serde_json::from_str(&s).unwrap_or_default()),
                timestamp_ns: row.get::<_, i64>(5)? as u64,
                phase: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or(
                    crate::responsibility::types::PhaseIndicator::Provisional { timestamp_ns: 0 },
                ),
            })
        })?;
        rows.collect()
    }

    // ── Edge queries ────────────────────────────────────────────────────

    /// Find edges of a specific type within a time window.
    pub fn find_edges_in_window(
        &self,
        edge_type: &ResponsibilityEdgeType,
        start_ns: u64,
        end_ns: u64,
    ) -> Result<Vec<ResponsibilityEdge>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let type_str = format!("{:?}", edge_type);
        let mut stmt = conn.prepare(
            "SELECT edge_id, edge_type, source_node_id, target_node_id,
                    evidence_refs_json, confidence, phase
             FROM graph_edges
             WHERE edge_type = ?1
               AND CAST(json_extract(phase, '$.timestamp_ns') AS INTEGER) BETWEEN ?2 AND ?3",
        )?;
        let rows = stmt.query_map(params![type_str, start_ns as i64, end_ns as i64], |row| {
            Ok(ResponsibilityEdge {
                edge_id: EdgeId::from(row.get::<_, String>(0)?),
                edge_type: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(1)?))
                    .unwrap_or(ResponsibilityEdgeType::IntentCausesAction),
                source: NodeId::from(row.get::<_, String>(2)?),
                target: NodeId::from(row.get::<_, String>(3)?),
                evidence_refs: row
                    .get::<_, String>(4)
                    .map_or(Vec::new(), |s| serde_json::from_str(&s).unwrap_or_default()),
                confidence: row.get::<_, f32>(5)?,
                phase: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or(
                    crate::responsibility::types::PhaseIndicator::Provisional { timestamp_ns: 0 },
                ),
                signal: None,
            })
        })?;
        rows.collect()
    }

    // ── Snapshot queries ────────────────────────────────────────────────

    /// Load a graph snapshot by ID.
    pub fn load_snapshot(
        &self,
        snapshot_id: &str,
    ) -> Result<Option<GraphSnapshot>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json
             FROM graph_snapshots WHERE snapshot_id = ?1",
        )?;
        let mut rows = stmt.query_map(params![snapshot_id], |row| {
            Ok(GraphSnapshot {
                snapshot_id: row.get(0)?,
                timestamp_ns: row.get::<_, i64>(1)? as u64,
                nodes: serde_json::from_str(&row.get::<_, String>(2)?).unwrap_or_default(),
                edges: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
                metrics: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
            })
        })?;
        match rows.next() {
            Some(r) => Ok(Some(r?)),
            None => Ok(None),
        }
    }

    /// Load all snapshots within a time window, ordered by timestamp.
    pub fn load_snapshots_in_window(
        &self,
        start_ns: u64,
        end_ns: u64,
    ) -> Result<Vec<GraphSnapshot>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json
             FROM graph_snapshots
             WHERE timestamp_ns BETWEEN ?1 AND ?2
             ORDER BY timestamp_ns",
        )?;
        let rows = stmt.query_map(params![start_ns as i64, end_ns as i64], |row| {
            Ok(GraphSnapshot {
                snapshot_id: row.get(0)?,
                timestamp_ns: row.get::<_, i64>(1)? as u64,
                nodes: serde_json::from_str(&row.get::<_, String>(2)?).unwrap_or_default(),
                edges: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
                metrics: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
            })
        })?;
        rows.collect()
    }
}
