use serde::{Deserialize, Serialize};

// ═══ ProcessId: Composite key preventing PID reuse ═══

#[derive(Debug, Clone, Copy, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProcessId {
    pub pid: u32,
    pub start_time_ns: u64,
}

impl ProcessId {
    pub fn new(pid: u32, start_time_ns: u64) -> Self {
        Self { pid, start_time_ns }
    }
}

// ═══ Newtype ID macro ═══

macro_rules! newtype_id {
    ($name:ident) => {
        #[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
        pub struct $name(pub String);

        impl $name {
            pub fn new() -> Self {
                Self(uuid::Uuid::new_v4().to_string())
            }
            pub fn from(s: impl Into<String>) -> Self {
                Self(s.into())
            }
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                write!(f, "{}", self.0)
            }
        }

        impl std::ops::Deref for $name {
            type Target = str;
            fn deref(&self) -> &str {
                &self.0
            }
        }
    };
}

newtype_id!(AgentId);
newtype_id!(EventId);
newtype_id!(MessageId);
newtype_id!(TlsSessionId);
newtype_id!(SessionId);
newtype_id!(NodeId);
newtype_id!(EdgeId);
newtype_id!(EvidenceId);
newtype_id!(AlertId);
newtype_id!(TracebackId);
// ClaimId is NOT generated via newtype_id! — it has a custom deterministic
// constructor so that the same logical claim (same subject + policy +
// boundary + type) always maps to the same id across trigger cycles.
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct ClaimId(pub String);

impl ClaimId {
    /// Random ClaimId for cases where determinism isn't needed.
    pub fn new() -> Self {
        Self(uuid::Uuid::new_v4().to_string())
    }
    /// Deterministic ClaimId from the logical composite key.
    /// Same (subject, policy, boundary, claim_type) → same id every time.
    pub fn from_key(parts: &[&str]) -> Self {
        use std::hash::{Hash, Hasher};
        use std::collections::hash_map::DefaultHasher;
        let mut h = DefaultHasher::new();
        for p in parts {
            p.hash(&mut h);
        }
        Self(format!("claim:{:016x}", h.finish()))
    }
    pub fn from(s: impl Into<String>) -> Self {
        Self(s.into())
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for ClaimId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::ops::Deref for ClaimId {
    type Target = str;
    fn deref(&self) -> &str {
        &self.0
    }
}
newtype_id!(PolicyId);
newtype_id!(IntentId);
newtype_id!(ActionId);
newtype_id!(BoundaryId);
