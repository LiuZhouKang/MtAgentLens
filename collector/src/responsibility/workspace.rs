// SPDX-License-Identifier: Apache-2.0 OR MIT
//! The workspace boundary an agent is judged against.
//!
//! The collector can be launched from anywhere: a service manager, a harness
//! isolation root, or a different project directory. Inferring the agent's
//! workspace from the collector's own working directory then mislabels ordinary
//! in-workspace work (writing the task's own output file, touching the run's
//! temporary files) as a capability expansion. `MTAGENTLENS_WORKSPACE_ROOT`
//! lets the launcher state the boundary explicitly; without it the historical
//! cwd behaviour is preserved.

/// Environment variable naming the agent's workspace root.
pub const WORKSPACE_ROOT_ENV: &str = "MTAGENTLENS_WORKSPACE_ROOT";

/// Resolve a workspace root string with a trailing separator.
///
/// A blank or absent value falls back to the current working directory, which is
/// what this code has always done. The sentinel `"\0"` means "no workspace could
/// be resolved"; callers must not treat it as a prefix that matches everything.
pub fn workspace_root_from(value: Option<&str>) -> String {
    if let Some(value) = value {
        let trimmed = value.trim().trim_end_matches('/');
        if !trimmed.is_empty() {
            return format!("{trimmed}/");
        }
    }
    match std::env::current_dir() {
        Ok(dir) => format!("{}/", dir.display()),
        Err(_) => "\u{0}".to_string(),
    }
}

/// The configured workspace root (environment or cwd).
pub fn configured_workspace_root() -> String {
    workspace_root_from(std::env::var(WORKSPACE_ROOT_ENV).ok().as_deref())
}

#[cfg(test)]
mod tests {
    use super::{WORKSPACE_ROOT_ENV, workspace_root_from};

    #[test]
    fn an_explicit_root_is_normalised_to_one_trailing_separator() {
        assert_eq!(workspace_root_from(Some("/work/run-1")), "/work/run-1/");
        assert_eq!(workspace_root_from(Some("/work/run-1/")), "/work/run-1/");
        assert_eq!(workspace_root_from(Some("  /work/run-1  ")), "/work/run-1/");
    }

    #[test]
    fn a_blank_root_falls_back_to_the_working_directory() {
        let cwd = std::env::current_dir().expect("cwd");
        assert_eq!(
            workspace_root_from(Some("   ")),
            format!("{}/", cwd.display())
        );
        assert_eq!(workspace_root_from(None), format!("{}/", cwd.display()));
    }

    #[test]
    fn the_environment_variable_is_named() {
        assert_eq!(WORKSPACE_ROOT_ENV, "MTAGENTLENS_WORKSPACE_ROOT");
    }
}
