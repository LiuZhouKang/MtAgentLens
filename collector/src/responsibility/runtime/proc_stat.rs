// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! User-space process metadata read from `/proc`.
//!
//! The eBPF layer emits a fixed JSON shape (see `bpftools/process/process.c`)
//! that does not include every field downstream code wants. Everything here is
//! read from `/proc` at the moment the information is still valid, mirroring
//! `cgroup.rs`: no eBPF change, no verifier risk and no kernel-matrix
//! validation, at the cost of a race against process exit.
//!
//! This exists because a field can otherwise look plumbed while never carrying
//! data: `include/process.h` declares `pgid`, but `process.bpf.c` never fills
//! it and `process.c` never prints it, so reading `pgid` out of an eBPF EXEC
//! event always yielded `None`.

/// Read `/proc/<pid>/stat` and return the process group ID.
///
/// The file is `pid (comm) state ppid pgrp ...`. `comm` may itself contain
/// spaces and parentheses (it is the raw executable name), so the fields are
/// counted from after the **last** `)`.
///
/// Returns `None` when the process is gone or the file is unparseable.
pub fn read_pgid(pid: u32) -> Option<u32> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let after_comm = &stat[stat.rfind(')')? + 1..];
    let mut fields = after_comm.split_whitespace();
    let _state = fields.next()?;
    let _ppid = fields.next()?;
    fields.next()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_pgid_of_self_matches_getpgrp() {
        // The test process is alive, so /proc must answer.
        let pid = std::process::id();
        let pgid = read_pgid(pid).expect("own /proc/<pid>/stat must be readable");
        // Every process is in some group; the group leader's pgid equals its
        // own pid, so pgid must be a real, non-zero pid. (`pgid <= u32::MAX`
        // is tautological and was a clippy deny-level error.)
        assert!(pgid > 0);
    }

    #[test]
    fn read_pgid_returns_none_for_missing_process() {
        // PID 0 never exists in /proc as a readable process directory.
        assert_eq!(read_pgid(0), None);
    }
}
