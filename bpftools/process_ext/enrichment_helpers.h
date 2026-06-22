// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/* Kernel helpers for event enrichment — included by process.bpf.c
 * after enrichment maps are defined.
 */

#ifndef __ENRICHMENT_HELPERS_H__
#define __ENRICHMENT_HELPERS_H__

#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include "../enrichment.h"

/* AF_INET may not be defined in vmlinux.h context */
#ifndef AF_INET
#define AF_INET 2
#endif

/* Capture parent process metadata from task_struct into process_hierarchy */
static __always_inline int capture_process_hierarchy(
    struct task_struct *task,
    struct process_hierarchy *hier)
{
    struct task_struct *parent;
    u64 parent_start_time;

    if (!task || !hier)
        return -1;

    BPF_CORE_READ_INTO(&parent, task, real_parent);
    if (!parent)
        return -1;

    BPF_CORE_READ_INTO(&hier->ppid, parent, tgid);
    BPF_CORE_READ_INTO(&parent_start_time, parent, start_time);
    hier->parent_start_time_ns = parent_start_time;
    BPF_CORE_READ_INTO(&hier->parent_uid, parent, real_cred, uid.val);
    /* Get parent cgroup ID via task->cgroups->dfl_cgrp->kn->id */
    {
        struct css_set *css;
        struct cgroup *cgrp;
        struct kernfs_node *kn;
        u64 cgid = 0;

        BPF_CORE_READ_INTO(&css, parent, cgroups);
        if (css) {
            BPF_CORE_READ_INTO(&cgrp, css, dfl_cgrp);
            if (cgrp) {
                BPF_CORE_READ_INTO(&kn, cgrp, kn);
                if (kn)
                    BPF_CORE_READ_INTO(&cgid, kn, id);
            }
        }
        hier->parent_cgroup_id = cgid;
    }

    return 0;
}

/* Increment per-process sequence counter (map must be defined by includer) */
static __always_inline u64 get_next_seq_number(void)
{
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 *seq_ptr = bpf_map_lookup_elem(&seq_counter_map, &pid);
    u64 seq = seq_ptr ? *seq_ptr + 1 : 1;
    bpf_map_update_elem(&seq_counter_map, &pid, &seq, BPF_ANY);
    return seq;
}

/* Capture socket context from sock structure into socket_context */
static __always_inline int capture_socket_context(
    struct sock *sk,
    struct socket_context *ctx)
{
    if (!sk || !ctx)
        return -1;

    BPF_CORE_READ_INTO(&ctx->saddr, sk, __sk_common.skc_rcv_saddr);
    BPF_CORE_READ_INTO(&ctx->daddr, sk, __sk_common.skc_daddr);
    BPF_CORE_READ_INTO(&ctx->sport, sk, __sk_common.skc_num);
    BPF_CORE_READ_INTO(&ctx->dport, sk, __sk_common.skc_dport);

    u16 family = 0;
    BPF_CORE_READ_INTO(&family, sk, __sk_common.skc_family);
    ctx->protocol = (family == AF_INET) ? IPPROTO_TCP : 0;
    ctx->state = 0;

    return 0;
}

/* Read argv[0] from task_struct mm->arg_start (snapshot) */
static __always_inline int read_argv0(struct task_struct *task,
				      char *buf, size_t len)
{
    struct mm_struct *mm;
    unsigned long arg_start, arg_end;
    int ret;

    BPF_CORE_READ_INTO(&mm, task, mm);
    if (!mm)
        return -1;

    BPF_CORE_READ_INTO(&arg_start, mm, arg_start);
    BPF_CORE_READ_INTO(&arg_end, mm, arg_end);

    if (arg_start >= arg_end || (arg_end - arg_start) > 4096)
        return -1;

    ret = bpf_probe_read_user_str(buf, len, (void *)arg_start);
    return ret > 0 ? 0 : -1;
}

#endif /* __ENRICHMENT_HELPERS_H__ */
