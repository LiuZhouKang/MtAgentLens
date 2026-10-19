// SPDX-License-Identifier: Apache-2.0 OR MIT
// Scenario vocabulary — the shared-artifact patterns the analyzers recognise.
//
// These defaults are shaped by the benchmark corpus, not by universal
// properties of agent traffic. They live in `patterns/scenarios.toml` so a
// deployment can replace them without patching detection code, and so the
// analyzers that consume them cannot drift apart (the same six-entry table
// used to be duplicated in `handoff_injection` and `multi_agent_anomaly`).
//
// The embedded copy is the default. `MTAGENTLENS_SCENARIO_PATTERNS` overrides
// it; a configured-but-unreadable file is an error, never a silent fallback.

use serde::Deserialize;
use std::sync::OnceLock;

/// One shared-artifact scenario: a handoff file, a probe file that confirms
/// propagation, and the marker text that shows a subagent accepted it.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct SharedArtifactPattern {
    pub scenario_id: String,
    /// Path suffix of the shared handoff file.
    pub shared_suffix: String,
    /// Path suffix of the probe file.
    pub probe_suffix: String,
    /// Text that indicates the subagent accepted and acted on the artifact.
    pub acceptance_marker: String,
}

impl SharedArtifactPattern {
    /// Construct a pattern programmatically (tests and custom vocabularies).
    pub fn new(
        shared_suffix: impl Into<String>,
        probe_suffix: impl Into<String>,
        acceptance_marker: impl Into<String>,
        scenario_id: impl Into<String>,
    ) -> Self {
        Self {
            scenario_id: scenario_id.into(),
            shared_suffix: shared_suffix.into(),
            probe_suffix: probe_suffix.into(),
            acceptance_marker: acceptance_marker.into(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct ScenarioVocabulary {
    #[serde(default)]
    pub patterns: Vec<SharedArtifactPattern>,
    /// Roles whose sign-off is authoritative for the security analyzer.
    #[serde(default)]
    pub authorized_roles: Vec<String>,
}

/// Environment variable naming an alternative vocabulary file.
pub const OVERRIDE_ENV: &str = "MTAGENTLENS_SCENARIO_PATTERNS";

const EMBEDDED: &str = include_str!("../../../patterns/scenarios.toml");

impl ScenarioVocabulary {
    /// The vocabulary compiled into the binary.
    ///
    /// A parse failure is a build-time test failure, not a runtime condition:
    /// the file is part of this crate.
    pub fn embedded() -> &'static Self {
        static CACHE: OnceLock<ScenarioVocabulary> = OnceLock::new();
        CACHE.get_or_init(|| {
            toml::from_str(EMBEDDED).expect("embedded scenario vocabulary must be valid TOML")
        })
    }

    /// Load the effective vocabulary, honouring [`OVERRIDE_ENV`].
    pub fn load() -> Result<Self, String> {
        match std::env::var(OVERRIDE_ENV) {
            Ok(path) if !path.is_empty() => Self::load_from_path(&path),
            _ => Ok(Self::embedded().clone()),
        }
    }

    /// Load a vocabulary from an explicit file. Used by [`Self::load`] and by
    /// embedders that own their configuration.
    pub fn load_from_path(path: &str) -> Result<Self, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|err| format!("cannot read scenario vocabulary {path}: {err}"))?;
        toml::from_str(&text)
            .map_err(|err| format!("cannot parse scenario vocabulary {path}: {err}"))
    }
}

/// The process-wide vocabulary installed at startup.
static EFFECTIVE: OnceLock<ScenarioVocabulary> = OnceLock::new();

/// Install the vocabulary for this process. The first install wins; later
/// calls are ignored, so analyzers always see one immutable table.
pub fn install(vocabulary: ScenarioVocabulary) {
    let _ = EFFECTIVE.set(vocabulary);
}

/// The vocabulary analyzers use: the installed one, else the embedded default.
pub fn effective() -> &'static ScenarioVocabulary {
    EFFECTIVE
        .get()
        .unwrap_or_else(|| ScenarioVocabulary::embedded())
}

/// Load (honouring [`OVERRIDE_ENV`]) and install the process vocabulary.
/// This is the startup entry point for the collector binary.
pub fn load_and_install() -> Result<&'static ScenarioVocabulary, String> {
    install(ScenarioVocabulary::load()?);
    Ok(effective())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_vocabulary_parses_and_carries_the_known_scenarios() {
        let vocabulary = ScenarioVocabulary::embedded();
        let ids: Vec<&str> = vocabulary
            .patterns
            .iter()
            .map(|pattern| pattern.scenario_id.as_str())
            .collect();
        assert_eq!(
            ids,
            vec![
                "subagents-context-loss",
                "subagents-identity-confusion",
                "subagents-handoff-injection",
                "agent-teams-role-hijack",
                "subagents-privilege-leak",
                "agent-teams-context-pollution",
            ],
            "the embedded table must stay in step with the benchmark corpus"
        );
        assert!(
            vocabulary
                .authorized_roles
                .iter()
                .any(|role| role == "security-performance-auditor"),
            "the security analyzer's authoritative role must come from the vocabulary"
        );
        for pattern in &vocabulary.patterns {
            assert!(pattern.shared_suffix.starts_with('/'));
            assert!(pattern.probe_suffix.starts_with('/'));
            assert!(!pattern.acceptance_marker.is_empty());
        }
    }

    #[test]
    fn startup_install_path_yields_the_embedded_table_by_default() {
        let installed = load_and_install().expect("default vocabulary must load");
        assert!(!installed.patterns.is_empty());
        // Later installs are ignored: analyzers see one immutable table.
        install(ScenarioVocabulary::default());
        assert!(!effective().patterns.is_empty());
    }

    #[test]
    fn explicit_vocabulary_file_replaces_the_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("scenarios.toml");
        std::fs::write(
            &path,
            r#"
authorized_roles = ["custom-auditor"]
[[patterns]]
scenario_id = "custom"
shared_suffix = "/custom/shared.md"
probe_suffix = "/custom/probe.txt"
acceptance_marker = "ACCEPTED"
"#,
        )
        .unwrap();
        let vocabulary = ScenarioVocabulary::load_from_path(&path.to_string_lossy()).unwrap();
        assert_eq!(vocabulary.patterns.len(), 1);
        assert_eq!(vocabulary.patterns[0].scenario_id, "custom");
        assert_eq!(vocabulary.authorized_roles, vec!["custom-auditor"]);

        // A configured file that cannot be parsed is an error, never a silent
        // fallback to the built-in table.
        std::fs::write(&path, "not = [valid").unwrap();
        assert!(ScenarioVocabulary::load_from_path(&path.to_string_lossy()).is_err());
    }

    #[test]
    fn every_analyzer_sees_the_same_default_table() {
        // Two analyzers used to own private copies of this table. This asserts
        // they now read one source, so a change cannot land in only one.
        let injected = crate::framework::analyzers::handoff_injection::default_injection_patterns();
        let anomalies =
            crate::framework::analyzers::multi_agent_anomaly::default_shared_artifact_patterns();
        assert_eq!(injected.len(), anomalies.len());
        for (left, right) in injected.iter().zip(anomalies.iter()) {
            assert_eq!(left.scenario_id, right.scenario_id);
            assert_eq!(left.shared_suffix, right.shared_suffix);
            assert_eq!(left.probe_suffix, right.probe_suffix);
            assert_eq!(left.acceptance_marker, right.acceptance_marker);
        }
    }
}
