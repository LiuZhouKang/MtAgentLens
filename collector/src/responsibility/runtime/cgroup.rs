// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! User-space cgroup resolution.
//!
//! The eBPF layer does NOT collect cgroup: `process/process.h` declares
//! `cgroup_id`/`parent_cgroup_id` but `process.bpf.c` never fills them
//! (only `process_ext/enrichment.bpf.c`, which is not wired into the
//! collector's process program, does). Verified by running `process` and
//! inspecting its JSON: no cgroup field is emitted.
//!
//! So we read `/proc/<pid>/cgroup` in user space instead. That keeps this
//! entirely out of the BPF programs (no verifier risk, no kernel matrix
//! validation), at the cost of a race: `/proc` disappears once a process
//! exits. `CgroupCache` mitigates this by capturing the value the moment an
//! EXEC event arrives.

use std::collections::HashMap;

/// Extract the systemd unit (last path component) from `/proc/<pid>/cgroup`.
///
/// Input looks like:
/// `0::/user.slice/user-1000.slice/user@1000.service/app.slice/openclaw-gateway.service`
///
/// Returns `Some("openclaw-gateway.service")`. Returns `None` for the root
/// cgroup (`0::/`) or anything unparseable.
pub fn parse_unit(content: &str) -> Option<String> {
    let line = content.lines().next()?;
    // cgroup v2 format: "<hierarchy-id>::<path>"; take everything after the
    // final ':'. rsplit is used because the path itself never contains ':'.
    let path = line.rsplit(':').next()?;
    let last = path.rsplit('/').next()?;
    if last.is_empty() {
        return None;
    }
    Some(last.to_string())
}

/// Read a live process's cgroup unit. Returns `None` if the process is gone
/// or `/proc` is unreadable.
pub fn read_unit(pid: u32) -> Option<String> {
    let content = std::fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
    parse_unit(&content)
}

/// `pid -> cgroup unit` cache.
///
/// A long-lived process (e.g. a systemd service) stays readable for its whole
/// life, but a short-lived CLI can exit before we would otherwise look it up.
/// Capturing on EXEC keeps the signal available afterwards.
#[derive(Debug, Default)]
pub struct CgroupCache {
    units: HashMap<u32, String>,
}

impl CgroupCache {
    /// Capture the unit for `pid` if not already known. Cheap no-op on repeat.
    pub fn observe(&mut self, pid: u32) {
        if self.units.contains_key(&pid) {
            return;
        }
        if let Some(unit) = read_unit(pid) {
            self.units.insert(pid, unit);
        }
    }

    /// Look up a previously captured unit.
    pub fn get(&self, pid: u32) -> Option<&str> {
        self.units.get(&pid).map(String::as_str)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_systemd_service_unit() {
        let c =
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/openclaw-gateway.service\n";
        assert_eq!(parse_unit(c).as_deref(), Some("openclaw-gateway.service"));
    }

    #[test]
    fn parses_terminal_scope_unit() {
        let c = "0::/user.slice/user-1000.slice/app.slice/app-org.gnome.Terminal.slice/vte-spawn-abc.scope\n";
        assert_eq!(parse_unit(c).as_deref(), Some("vte-spawn-abc.scope"));
    }

    #[test]
    fn returns_none_for_root_cgroup() {
        assert_eq!(parse_unit("0::/\n"), None);
    }

    #[test]
    fn returns_none_for_empty_input() {
        assert_eq!(parse_unit(""), None);
    }

    #[test]
    fn returns_none_when_path_has_no_component() {
        assert_eq!(parse_unit("0::"), None);
    }

    #[test]
    fn observes_and_caches_self() {
        let mut cache = CgroupCache::default();
        let pid = std::process::id();
        cache.observe(pid); // this test process certainly exists
        let first = cache.get(pid).map(String::from);
        cache.observe(pid); // second call must be a no-op
        assert_eq!(cache.get(pid).map(String::from), first);
    }

    #[test]
    fn read_unit_returns_none_for_dead_pid() {
        // PID 0 is never a user process.
        assert_eq!(read_unit(0), None);
    }
}
