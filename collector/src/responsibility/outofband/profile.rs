// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Profile-driven configuration for out-of-band sources.
//!
//! Out-of-band reading is inherently platform-specific, so it is never enabled
//! implicitly: an operator declares which agent's on-disk data may be read, and
//! which schema version was verified against. A version mismatch disables the
//! source rather than risking misparsed data.

use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum OutOfBandKind {
    OpenClawTrajectory,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct OutOfBandProfile {
    /// Agent label this profile applies to (matches the runtime profile id).
    pub agent: String,
    pub kind: OutOfBandKind,
    /// Root directory holding the agent's state (e.g. `~/.openclaw`).
    pub state_dir: PathBuf,
    /// Schema version verified against.
    pub schema_version: u32,
}

#[derive(Debug)]
pub enum ProfileError {
    Io(std::io::Error),
    Toml(toml::de::Error),
}

impl std::fmt::Display for ProfileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "out-of-band profile io error: {e}"),
            Self::Toml(e) => write!(f, "out-of-band profile parse error: {e}"),
        }
    }
}

impl std::error::Error for ProfileError {}

/// Load every `*.toml` in `dir`.
///
/// A missing directory yields an empty list — out-of-band reading is optional,
/// never a hard requirement.
pub fn load_profiles(dir: &Path) -> Result<Vec<OutOfBandProfile>, ProfileError> {
    if !dir.exists() {
        return Ok(Vec::new());
    }
    let mut out: Vec<OutOfBandProfile> = Vec::new();
    for entry in std::fs::read_dir(dir).map_err(ProfileError::Io)? {
        let path = entry.map_err(ProfileError::Io)?.path();
        if path.extension().and_then(|e| e.to_str()) != Some("toml") {
            continue;
        }
        let text = std::fs::read_to_string(&path).map_err(ProfileError::Io)?;
        out.push(toml::from_str(&text).map_err(ProfileError::Toml)?);
    }
    out.sort_by(|a, b| a.agent.cmp(&b.agent));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_directory_yields_no_profiles() {
        let got = load_profiles(Path::new("/nonexistent/definitely/not/here")).unwrap();
        assert!(got.is_empty());
    }

    #[test]
    fn parses_a_profile() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("openclaw.toml"),
            r#"
agent = "openclaw"
kind = "open-claw-trajectory"
state_dir = "/home/testuser/.openclaw"
schema_version = 1
"#,
        )
        .unwrap();

        let got = load_profiles(dir.path()).unwrap();
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].agent, "openclaw");
        assert_eq!(got[0].kind, OutOfBandKind::OpenClawTrajectory);
        assert_eq!(got[0].schema_version, 1);
    }

    #[test]
    fn ignores_non_toml_files() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("README.md"), "not a profile").unwrap();
        assert!(load_profiles(dir.path()).unwrap().is_empty());
    }
}
