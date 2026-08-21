// SPDX-License-Identifier: MIT
// Responsibility architecture persistence module — SQLite storage.
//
// New clean-slate module (May 2026). The old persistence files
// (causal_schema, jsonl_writer, responsibility_schema, schema, sqlite_writer)
// remain for backward compatibility but should not be extended.

pub mod reader;
pub mod schema;
pub mod writer;

pub use reader::{EvaluationData, PersistenceReader, RefinementCounts};
pub use schema::{
    SCHEMA_DDL, open_responsibility_db, open_responsibility_db_read_only, run_migrations,
};
pub use writer::{
    BindingType, PersistRecord, PersistenceWriter, ProcessMetadata, TlsSession, WriterConfig,
};

// Re-export old modules for existing callers
mod causal_schema;
mod jsonl_writer;
mod responsibility_schema;
mod sqlite_writer;
// Note: old schema.rs is shadowed by the new schema module.
// Existing callers that import `crate::responsibility::persistence::schema::run_migrations`
// will now get the new implementation automatically.

pub use causal_schema::open_causal_db;
pub use jsonl_writer::JsonlWriter;
pub use responsibility_schema::{
    RESPONSIBILITY_ALERT_JOIN_PATH, RESPONSIBILITY_TABLES, ResponsibilityTable,
};
pub use sqlite_writer::SqliteWriter;
// open_db/open_db_read_only are re-exported from the new schema.rs
// (sqlite_writer.rs also re-exports them from schema, so they are available
// via both paths — avoiding duplicate re-export at this level)
pub use schema::{open_db, open_db_read_only};
