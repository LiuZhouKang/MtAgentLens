// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/* Kernel-side event enrichment structures for causal attribution */

#ifndef __ENRICHMENT_H__
#define __ENRICHMENT_H__

#include "vmlinux.h"

/* Process hierarchy metadata captured at kernel level */
struct process_hierarchy {
    u32 ppid;
    u64 parent_start_time_ns;
    u32 parent_uid;
    u64 parent_cgroup_id;
};

/* Network socket context for correlation */
struct socket_context {
    u32 saddr;
    u32 daddr;
    u16 sport;
    u16 dport;
    u8 protocol;
};

/* Intent context propagated through syscall chain */
struct intent_context {
    char intent_id[64];
    u64 intent_timestamp_ns;
    u8 intent_class;
};

/* Cgroup hierarchy information */
struct cgroup_context {
    u64 parent_cgroup_id;
    char cgroup_path[256];
};

/* Extended event with all enrichment fields */
struct enriched_event {
    u64 timestamp_ns;
    u64 seq_number;
    u32 pid;
    u32 uid;
    u32 gid;
    char comm[16];
    struct process_hierarchy hierarchy;
    struct socket_context socket;
    struct intent_context intent;
    struct cgroup_context cgroup;
};

#endif /* __ENRICHMENT_H__ */
