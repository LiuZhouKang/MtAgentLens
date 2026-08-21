/* SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause */
/* Minimal process context enrichment for causal attribution */

#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include "../enrichment.h"

/* Process context map: (pid, start_time_ns) -> process_context */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 4096);
	__type(key, u64);  /* (pid << 32) | start_time_ns_hi */
	__type(value, struct process_context);
} proc_context_map SEC(".maps");

/* Per-process syscall sequence counter */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 4096);
	__type(key, u32);  /* pid */
	__type(value, u64);  /* seq_counter */
} seq_counter_map SEC(".maps");

/* Helper: Build composite key from pid and start_time_ns */
static __always_inline u64 make_proc_key(u32 pid, u64 start_time_ns)
{
	return ((u64)pid << 32) | (start_time_ns >> 32);
}

/* Helper: Read argv[0] from task_struct */
static __always_inline int read_argv0(struct task_struct *task, char *buf, size_t len)
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

/* Helper: Get parent process info */
static __always_inline int get_parent_info(struct task_struct *task,
					   u32 *ppid, u64 *parent_start_time_ns)
{
	struct task_struct *parent;
	u64 start_time;

	BPF_CORE_READ_INTO(&parent, task, real_parent);
	if (!parent)
		return -1;

	*ppid = BPF_CORE_READ(parent, tgid);
	start_time = BPF_CORE_READ(parent, start_time);
	*parent_start_time_ns = start_time;

	return 0;
}

/* Update process context on exec */
int enrich_on_exec(struct task_struct *task, u64 timestamp_ns)
{
	struct process_context ctx = {};
	u32 pid, ppid;
	u64 start_time_ns, parent_start_time_ns;
	u64 cgroup_id;
	u32 uid, gid;
	u64 proc_key;

	pid = bpf_get_current_pid_tgid() >> 32;
	start_time_ns = BPF_CORE_READ(task, start_time);
	cgroup_id = bpf_get_current_cgroup_id();
	uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;
	gid = (bpf_get_current_uid_gid() >> 32) & 0xFFFFFFFF;

	/* Get parent info */
	if (get_parent_info(task, &ppid, &parent_start_time_ns) < 0) {
		ppid = 0;
		parent_start_time_ns = 0;
	}

	/* Read argv[0] */
	read_argv0(task, ctx.argv0, sizeof(ctx.argv0));

	/* Build context */
	ctx.ppid = ppid;
	ctx.parent_start_time_ns = parent_start_time_ns;
	ctx.cgroup_id = cgroup_id;
	ctx.uid = uid;
	ctx.gid = gid;
	ctx.seq_counter = 0;

	/* Store in map */
	proc_key = make_proc_key(pid, start_time_ns);
	bpf_map_update_elem(&proc_context_map, &proc_key, &ctx, BPF_ANY);

	return 0;
}

/* Get next sequence number for syscall */
static __always_inline u64 get_seq_number(u32 pid)
{
	u64 *seq = bpf_map_lookup_elem(&seq_counter_map, &pid);
	u64 current_seq = seq ? *seq : 0;
	bpf_map_update_elem(&seq_counter_map, &pid, &current_seq + 1, BPF_ANY);
	return current_seq;
}

/* Enrich event with process context */
int enrich_event(struct enriched_event *event, struct task_struct *task)
{
	struct process_context *ctx;
	u64 proc_key;

	if (!event || !task)
		return -1;

	/* Lookup process context */
	proc_key = make_proc_key(event->pid, event->start_time_ns);
	ctx = bpf_map_lookup_elem(&proc_context_map, &proc_key);

	if (ctx) {
		event->ppid = ctx->ppid;
		event->parent_start_time_ns = ctx->parent_start_time_ns;
		event->cgroup_id = ctx->cgroup_id;
		event->uid = ctx->uid;
		event->gid = ctx->gid;
		__builtin_memcpy(event->argv0, ctx->argv0, sizeof(event->argv0));
	}

	/* Add sequence number */
	event->seq_number = get_seq_number(event->pid);

	return 0;
}

#endif
