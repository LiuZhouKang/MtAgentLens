// SPDX-License-Identifier: Apache-2.0 OR MIT
// Scenario vocabulary — optional shared-artifact patterns and sign-off roles.
//
// The compiled default is deliberately neutral: no scenario patterns and no
// authorized roles. Benchmark-corpus knowledge is configuration, never a
// built-in decision. A deployment opts in with `MTAGENTLENS_SCENARIO_PATTERNS`
// or by loading a TOML file explicitly; a configured-but-unreadable file is an
// error, never a silent fallback.

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

impl ScenarioVocabulary {
    /// The neutral vocabulary compiled into the binary.
    ///
    /// It intentionally carries no scenario patterns and no authorized roles.
    /// A deployment that needs either supplies an explicit file.
    pub fn embedded() -> &'static Self {
        static CACHE: OnceLock<ScenarioVocabulary> = OnceLock::new();
        CACHE.get_or_init(ScenarioVocabulary::default)
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

/// The vocabulary analyzers use: the installed one, else the neutral default.
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
    fn startup_installs_only_a_valid_explicit_configuration() {
        const CHILD: &str = "MTL_TEST_VOCABULARY_STARTUP_CHILD";
        if let Some(mode) = std::env::var_os(CHILD) {
            if mode == "invalid" {
                assert!(load_and_install().is_err());
            } else {
                let installed = load_and_install().expect("neutral startup");
                assert!(installed.patterns.is_empty());
                assert!(installed.authorized_roles.is_empty());
                install(ScenarioVocabulary {
                    patterns: vec![],
                    authorized_roles: vec!["later-role".into()],
                });
                assert!(
                    effective().authorized_roles.is_empty(),
                    "startup configuration is immutable"
                );
            }
            return;
        }
        // Isolate OnceLock and the override environment from parallel tests.
        let temporary = tempfile::tempdir().unwrap();
        for mode in ["neutral", "invalid"] {
            let mut command = std::process::Command::new(std::env::current_exe().unwrap());
            command.args(["--exact", "framework::analyzers::scenario_vocabulary::tests::startup_installs_only_a_valid_explicit_configuration"])
                .env(CHILD, mode).env_remove(OVERRIDE_ENV);
            if mode == "invalid" {
                command.env(OVERRIDE_ENV, temporary.path().join("missing.toml"));
            }
            let output = command.output().unwrap();
            assert!(
                output.status.success(),
                "isolated {mode} startup failed: {}",
                String::from_utf8_lossy(&output.stdout)
            );
        }
    }

    #[test]
    fn embedded_and_effective_defaults_are_neutral() {
        let embedded = ScenarioVocabulary::embedded();
        assert!(
            embedded.patterns.is_empty(),
            "the binary's default vocabulary must not carry benchmark scenario patterns"
        );
        assert!(
            embedded.authorized_roles.is_empty(),
            "the binary's default vocabulary must not authorize benchmark roles"
        );
        assert!(effective().patterns.is_empty());
        assert!(effective().authorized_roles.is_empty());
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
    }

    #[test]
    fn malformed_explicit_vocabulary_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("scenarios.toml");
        std::fs::write(&path, "not = [valid").unwrap();
        assert!(
            ScenarioVocabulary::load_from_path(&path.to_string_lossy()).is_err(),
            "a configured-but-malformed file must not fall back silently"
        );
    }

    #[test]
    fn analyzers_share_the_neutral_default() {
        // Two analyzers used to own private copies of a benchmark table. They
        // now read one source, and that source defaults to empty.
        let injected = crate::framework::analyzers::handoff_injection::default_injection_patterns();
        let anomalies =
            crate::framework::analyzers::multi_agent_anomaly::default_shared_artifact_patterns();
        assert!(injected.is_empty());
        assert!(anomalies.is_empty());
    }
}
