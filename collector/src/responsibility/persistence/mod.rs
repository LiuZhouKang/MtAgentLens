// SPDX-License-Identifier: Apache-2.0 OR MIT
// Responsibility architecture persistence module — SQLite storage.
//
// One schema, one opener (`schema.rs`), one writer and one reader. The legacy
// causal schema and its writers were deleted with the single-schema migration.

pub mod codec;
pub mod reader;
pub mod schema;
pub mod writer;

pub use reader::{EvaluationData, PersistenceReader, RefinementCounts};
pub use schema::{
    SCHEMA_DDL, open_responsibility_db, open_responsibility_db_read_only, run_migrations,
};
pub use writer::{
    BindingType, IdentityBeliefLevel, PersistRecord, PersistenceWriter, ProcessMetadata,
    TlsSession, WriterConfig,
};

pub use schema::open_db_read_only;
