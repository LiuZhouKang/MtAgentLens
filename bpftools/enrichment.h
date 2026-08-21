/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
/* Kernel-side event enrichment for causal attribution */

#ifndef __ENRICHMENT_H
#define __ENRICHMENT_H

#include "process.h"

/* ========== Process hierarchy (parent metadata) ========== */
struct process_hierarchy {
	u32 ppid;
	u64 parent_start_time_ns;
	u32 parent_uid;
	u64 parent_cgroup_id;
};

/* ========== Process context (per-process enriched state) ========== */
struct process_context {
	u32 ppid;
	u64 parent_start_time_ns;
	u64 cgroup_id;
	u32 uid;
	u32 gid;
	u64 seq_counter;
	char argv0[64];
};

/* ========== Socket context (for network enrichment) ========== */
struct socket_context {
	u32 saddr;
	u32 daddr;
	u16 sport;
	u16 dport;
	u8 protocol;
	u8 state;
};

/* ========== File descriptor tracking ========== */
struct fd_info {
	u64 inode;
	u32 flags;
	u32 peer_pid;
	u64 peer_start_time_ns;
};

/* ========== Enriched event structure (full output) ========== */
struct enriched_event {
	/* Base event */
	u64 timestamp_ns;
	u32 pid;
	u64 start_time_ns;
	char comm[TASK_COMM_LEN];

	/* Process context */
	u32 ppid;
	u64 parent_start_time_ns;
	u64 cgroup_id;
	u32 uid;
	u32 gid;

	/* Parent context (causal chain) */
	u32 parent_uid;
	u64 parent_cgroup_id;

	/* Syscall sequencing */
	u64 seq_number;

	/* Argument extraction */
	char argv0[64];

	/* Event type and enrichment */
	u32 event_type;
	union {
		struct {
			u32 fd;
			u64 inode;
			u32 flags;
		} file_op;
		struct socket_context network;
	} enrichment;
};

#endif /* __ENRICHMENT_H */
