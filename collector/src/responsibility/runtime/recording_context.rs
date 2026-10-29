// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Read-only snapshot of the collector's own procfs identity.
//!
//! Only `/proc/self` paths are read, never `/proc/<numeric-pid>`. Values that
//! cannot be measured remain null; a pid-space conflict is reported rather
//! than resolved by guessing a host/kernel namespace mapping.

use serde_json::{Value, json};
use std::fs;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

const SCHEMA: &str = "mtagentlens.collector-process/v1";
const MAX_FILE_BYTES: u64 = 1024 * 1024;
const NANOS_PER_SEC: i64 = 1_000_000_000;

pub(super) fn capture() -> Value {
    capture_from(Path::new("/proc"), std::process::id())
}

fn capture_from(root: &Path, pid: u32) -> Value {
    let self_pid = read_self_pid(root);
    let stat = read_bounded(&root.join("self/stat"))
        .and_then(|raw| parse_stat(&raw))
        .filter(|(stat_pid, _)| Some(*stat_pid) == self_pid);
    let nspid = read_bounded(&root.join("self/status")).and_then(|raw| parse_nspid(&raw));

    let pid_space_status = match (self_pid, stat, nspid.as_ref()) {
        (Some(procfs_pid), Some((stat_pid, start_ticks)), Some(nspid)) if start_ticks > 0 => {
            if procfs_pid == pid && stat_pid == procfs_pid && nspid == &[pid] {
                "observed"
            } else {
                "pid_space_mismatch"
            }
        }
        _ => "unavailable",
    };

    let boot_id = read_bounded(&root.join("sys/kernel/random/boot_id"))
        .and_then(|raw| uuid::Uuid::parse_str(raw.trim()).ok())
        .map(|id| id.to_string());

    let (monotonic, boottime) = read_bounded(&root.join("self/timens_offsets"))
        .map(|raw| parse_time_offsets(&raw))
        .unwrap_or((None, None));

    json!({
        "schema": SCHEMA,
        "pid": pid,
        "procfs_pid": self_pid,
        "procfs_start_ticks": stat.map(|(_, start_ticks)| start_ticks),
        "nspid": nspid,
        "boot_id": boot_id,
        "namespaces": {
            "pid": namespace_meta(root, "pid"),
            "pid_for_children": namespace_meta(root, "pid_for_children"),
            "time": namespace_meta(root, "time"),
            "time_for_children": namespace_meta(root, "time_for_children"),
            "mnt": namespace_meta(root, "mnt"),
        },
        "time_offsets_ns": {
            "monotonic": monotonic,
            "boottime": boottime,
        },
        "pid_space_status": pid_space_status,
    })
}

fn read_bounded(path: &Path) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    if file.metadata().ok()?.len() > MAX_FILE_BYTES {
        return None;
    }
    let mut bytes = Vec::new();
    file.take(MAX_FILE_BYTES + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > MAX_FILE_BYTES {
        return None;
    }
    String::from_utf8(bytes).ok()
}

fn read_self_pid(root: &Path) -> Option<u32> {
    let target = fs::read_link(root.join("self")).ok()?;
    let pid = target.file_name()?.to_str()?.parse::<u32>().ok()?;
    (pid > 0).then_some(pid)
}

// Returns (leading pid, field 22 starttime). Field 22 must be positive.
fn parse_stat(raw: &str) -> Option<(u32, u64)> {
    let open = raw.find('(')?;
    let close = raw.rfind(')')?;
    if close <= open {
        return None;
    }
    let pid = raw[..open].trim().parse::<u32>().ok()?;
    let start_ticks = raw[close + 1..]
        .split_whitespace()
        .nth(19)?
        .parse::<u64>()
        .ok()?;
    (pid > 0 && start_ticks > 0).then_some((pid, start_ticks))
}

fn parse_nspid(raw: &str) -> Option<Vec<u32>> {
    let mut matches = raw.lines().filter_map(|line| line.strip_prefix("NSpid:"));
    let line = matches.next()?;
    if matches.next().is_some() {
        return None;
    }
    let nspid = line
        .split_whitespace()
        .map(|value| value.parse::<u32>().ok())
        .collect::<Option<Vec<_>>>()?;
    (!nspid.is_empty() && nspid.iter().all(|&pid| pid > 0)).then_some(nspid)
}

// Each clock is independent: a malformed or duplicate entry only makes that
// clock unknown. Nanoseconds are validated before checked arithmetic.
fn parse_time_offsets(raw: &str) -> (Option<i64>, Option<i64>) {
    let mut monotonic = None;
    let mut boottime = None;
    let mut monotonic_seen = false;
    let mut boottime_seen = false;

    for line in raw.lines() {
        let mut fields = line.split_whitespace();
        let Some(clock) = fields.next() else {
            continue;
        };
        let parsed = (|| {
            let secs = fields.next()?.parse::<i64>().ok()?;
            let nanos = fields.next()?.parse::<i64>().ok()?;
            if !(0..NANOS_PER_SEC).contains(&nanos) || fields.next().is_some() {
                return None;
            }
            secs.checked_mul(NANOS_PER_SEC)?.checked_add(nanos)
        })();

        match clock {
            "monotonic" => {
                monotonic = if monotonic_seen { None } else { parsed };
                monotonic_seen = true;
            }
            "boottime" => {
                boottime = if boottime_seen { None } else { parsed };
                boottime_seen = true;
            }
            _ => {}
        }
    }

    (monotonic, boottime)
}

fn namespace_meta(root: &Path, name: &str) -> Value {
    let Ok(meta) = fs::metadata(root.join("self/ns").join(name)) else {
        return Value::Null;
    };
    let device = meta.dev();
    let inode = meta.ino();
    if inode > 0 {
        json!({"device": device, "inode": inode})
    } else {
        Value::Null
    }
}

#[cfg(test)]
mod tests;
