// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/* Copyright (c) 2020 Facebook */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_endian.h>
#include "process.h"
#include <asm/unistd.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 8192);
	__type(key, pid_t);
	__type(value, u64);
} exec_start SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	/* 1MiB -> 8MiB -> 32MiB: 8MiB still overflowed while the single consumer
	 * stalls in the inline Observer LLM call (~12.3k events of ~680B). */
	__uint(max_entries, 32 * 1024 * 1024);
} rb SEC(".maps");

/* Every loss is counted at its cause (enum drop_reason lives in process.h,
 * shared with the userspace reader). A single total cannot distinguish a full
 * ring buffer from a failed userspace read; keep the sum for compatibility and
 * emit the per-reason split. */
struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, DROP_REASON_MAX);
	__type(key, __u32);
	__type(value, __u64);
} dropped_events SEC(".maps");

const volatile unsigned long long min_duration_ns = 0;
/* nsfs device measured by the loader, never inferred from a PID number. */
const volatile unsigned long long pid_namespace_device = 0;
const volatile unsigned long long pid_namespace_inode = 0;

/* Iterator output is a fixed binary event, not a ring action or EXEC. */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct event);
} snapshot_scratch SEC(".maps");

static __always_inline void snapshot_monitor_pid(struct event *e,
                                                 struct task_struct *task)
{
    struct pid *pid = BPF_CORE_READ(task, thread_pid);
    unsigned int level = BPF_CORE_READ(pid, level);
    e->pid_namespace.error = PID_NAMESPACE_UNAVAILABLE;
    if (!pid || level > 32 || !pid_namespace_device || !pid_namespace_inode)
        return;
    /* PID namespace levels are kernel-bounded. Read each upid through CO-RE
     * offsets/sizes; never copy a variable kernel layout onto a C stack type. */
    for (unsigned int i = 0; i < 33; i++) {
        if (i > level)
            break;
        const char *entry = (const char *)pid + bpf_core_field_offset(struct pid, numbers)
                          + i * bpf_core_type_size(struct upid);
        struct pid_namespace *ns = NULL;
        int nr = 0;
        if (bpf_probe_read_kernel(&ns, sizeof(ns), entry + bpf_core_field_offset(struct upid, ns)) || !ns)
            continue;
        if (BPF_CORE_READ(ns, ns.inum) != pid_namespace_inode)
            continue;
        if (bpf_probe_read_kernel(&nr, sizeof(nr), entry + bpf_core_field_offset(struct upid, nr)) || nr <= 0)
            return;
        e->pid_namespace.device = pid_namespace_device;
        e->pid_namespace.inode = pid_namespace_inode;
        e->pid_namespace.tgid = nr;
        e->pid_namespace.error = 0;
        return;
    }
}

SEC("iter/task")
int snapshot_tasks(struct bpf_iter__task *ctx)
{
    struct task_struct *task = ctx->task;
    struct event *e;
    __u32 key = 0;
    int pid, tgid;
    if (!task)
        return 0;
    pid = BPF_CORE_READ(task, pid);
    tgid = BPF_CORE_READ(task, tgid);
    if (pid <= 0 || pid != tgid || !BPF_CORE_READ(task, mm) || BPF_CORE_READ(task, exit_state))
        return 0;
    e = bpf_map_lookup_elem(&snapshot_scratch, &key);
    if (!e)
        return 0;
    __builtin_memset(e, 0, sizeof(*e));
    e->type = EVENT_TYPE_PROCESS_SNAPSHOT;
    e->pid = tgid;
    e->ppid = BPF_CORE_READ(task, real_parent, tgid);
    e->start_time_ns = BPF_CORE_READ(task, start_time);
    e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
    if (!e->start_time_ns)
        return 0;
    e->timestamp_ns = bpf_ktime_get_ns();
    bpf_core_read_str(e->comm, sizeof(e->comm), &task->comm);
    struct pid *pgrp = BPF_CORE_READ(task, signal, pids[PIDTYPE_PGID]);
    if (pgrp)
        e->pgid = BPF_CORE_READ(pgrp, numbers[0].nr);
    /* This is the iterated task, not current (the iterator reader). */
    e->cgroup_id = BPF_CORE_READ(task, cgroups, dfl_cgrp, kn, id);
    snapshot_monitor_pid(e, task);
    bpf_seq_write(ctx->meta->seq, e, sizeof(*e));
    return 0;
}


static __always_inline void fill_exec_pid_namespace(struct event *e,
                                                   struct task_struct *task)
{
    struct bpf_pidns_info mapped = {};
    unsigned long long inode = 0;
    long error;

    e->pid_namespace.error = PID_NAMESPACE_UNAVAILABLE;
    if (!pid_namespace_device)
        return;
    if (BPF_CORE_READ_INTO(&inode, task, nsproxy, pid_ns_for_children, ns.inum)
        || !inode)
        return;
    /* for_children can differ from the active namespace after unshare().
     * The helper validates dev/inode against the active namespace; failure
     * must stay unknown rather than claiming the future child namespace. */
    error = bpf_get_ns_current_pid_tgid(pid_namespace_device, inode,
                                      &mapped, sizeof(mapped));
    if (error) {
        e->pid_namespace.error = error;
        return;
    }
    if (!mapped.tgid)
        return;
    e->pid_namespace.error = 0;
    e->pid_namespace.device = pid_namespace_device;
    e->pid_namespace.inode = inode;
    e->pid_namespace.tgid = mapped.tgid;
}


static __always_inline void inc_dropped(enum drop_reason reason)
{
	__u32 key = (__u32)reason;
	__u64 *cnt = bpf_map_lookup_elem(&dropped_events, &key);

	if (cnt)
		__sync_fetch_and_add(cnt, 1);
}

/* Failed reads need their original process context before userspace filtering.
 * A missing notice remains in the global counter and therefore unattributed. */
static __always_inline void report_user_read_failed(int syscall_id, const void *src)
{
    struct event *e;
    struct task_struct *task;

    inc_dropped(DROP_USER_READ_FAILED);
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e)
        return;
    __builtin_memset(e, 0, sizeof(*e));
    e->type = EVENT_TYPE_CAPTURE_LOSS;
    e->capture_loss_reason = DROP_USER_READ_FAILED;
    e->pid = bpf_get_current_pid_tgid() >> 32;
    e->timestamp_ns = bpf_ktime_get_ns();
    task = (struct task_struct *)bpf_get_current_task();
    e->ppid = BPF_CORE_READ(task, real_parent, tgid);
    e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
    e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
    e->file_op.flags = syscall_id;
    e->syscall_ret = (long long)(unsigned long long)src;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_ringbuf_submit(e, 0);
}

/* Read a user path string without dropping the event when the string is
 * longer than the destination buffer: bpf_probe_read_user_str() returns
 * -ENAMETOOLONG instead of a prefix. Fall back to a bounded byte read and
 * NUL-terminate, so an over-long path truncates instead of becoming a lost
 * action (real Claude Code runs produced 92 such notices in 140 s). */
static __always_inline int read_user_path(char *dst, size_t size, const char *src)
{
    if (bpf_probe_read_user_str(dst, size, src) >= 0)
        return 0;
    if (!size)
        return -1;
    /* Reading a fixed 127-byte prefix from a pointer near a page boundary
     * faults into the next (possibly unmapped) page. Bound the fallback to the
     * current page: a truncated prefix is recoverable, a lost action is not. */
    /* Use only constant-length reads so the verifier can bound the stack write.
     * A 127-byte read that would cross the page boundary is shortened to a
     * small constant prefix; the action survives with a truncated path. */
    unsigned long offset = (unsigned long)src & 4095UL;
    size_t take = size - 1;
    if (offset > 4096UL - take) {
        take = 32;
        if (offset > 4096UL - take)
            take = 1;
    }
    if (bpf_probe_read_user(dst, take, src) < 0) {
        /* Final diagnostic/robustness step: if even one byte is unreadable the
         * pointer is genuinely invalid; otherwise keep the action with a
         * minimal prefix rather than reporting it as lost. */
        if (bpf_probe_read_user(dst, 1, src) < 0)
            return -1;
        dst[1] = '\0';
        return 0;
    }
    dst[take] = '\0';
    return 0;
}

/* Pending entry arguments are keyed by TID until raw_syscalls/sys_exit.
 * A failed allocation is a measured capture loss, never a successful action. */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 8192);
    __type(key, __u32);
    __type(value, struct event);
} pending_syscalls SEC(".maps");

static __always_inline int stage_syscall(struct event *e)
{
    __u32 tid = (__u32)bpf_get_current_pid_tgid();
    if (bpf_map_update_elem(&pending_syscalls, &tid, e, BPF_ANY))
        inc_dropped(DROP_PENDING_TABLE_FULL);
    bpf_ringbuf_discard(e, 0);
    return 0;
}

SEC("tp/raw_syscalls/sys_exit")
int complete_syscall(struct trace_event_raw_sys_exit *ctx)
{
    __u32 tid = (__u32)bpf_get_current_pid_tgid();
    struct event *pending = bpf_map_lookup_elem(&pending_syscalls, &tid);
    if (!pending)
        return 0;
    struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) {
        inc_dropped(DROP_RINGBUF_FULL);
        bpf_map_delete_elem(&pending_syscalls, &tid);
        return 0;
    }
    __builtin_memcpy(e, pending, sizeof(*e));
    /* raw_syscalls/sys_enter sees the path page before the syscall faults it
     * in; bpf_probe_read_user cannot fault, so the enter read fails for a
     * not-yet-resident page even though openat() itself succeeds. Retry after
     * the syscall, when getname() has made the bytes resident. */
    if (e->type == EVENT_TYPE_FILE_OPERATION && e->file_op.op == FILE_OP_OPEN &&
        e->file_op.filepath[0] == '\0' && e->syscall_ret != 0) {
        unsigned long long retry = (unsigned long long)e->syscall_ret;
        char path[MAX_FILENAME_LEN] = {};
        if (read_user_path(path, sizeof(path), (const char *)(unsigned long)retry) < 0) {
            report_user_read_failed((int)ctx->id, (const void *)(unsigned long)retry);
            bpf_ringbuf_discard(e, 0);
            bpf_map_delete_elem(&pending_syscalls, &tid);
            return 0;
        }
        __builtin_memcpy(e->file_op.filepath, path, sizeof(path));
    }
    e->syscall_ret = ctx->ret;
    e->syscall_completed = true;
    if (e->type == EVENT_TYPE_FILE_OPERATION && e->file_op.op == FILE_OP_OPEN)
        e->file_op.fd = ctx->ret >= 0 ? ctx->ret : -1;
    bpf_map_delete_elem(&pending_syscalls, &tid);
    bpf_ringbuf_submit(e, 0);
    return 0;
}

/*
 * Bash readline uretprobe handler.
 *
 * The section name is a bare label: it must NOT carry an absolute path.
 * `SEC("uretprobe//usr/bin/bash:readline")` made libbpf resolve the binary at
 * load time, and libbpf aborts the entire attach sequence on the first failure
 * while this program is `progs[0]` — so on a system without /usr/bin/bash
 * (NixOS, musl-only images) `process_bpf__attach` failed and none of the
 * exec/exit/open programs were attached either. The collector's userspace
 * resolves the real bash path and attaches this probe by hand (see
 * `attach_bash_readline` in process.c).
 */
SEC("uretprobe/readline")
int BPF_URETPROBE(bash_readline, const void *ret)
{
	struct event *e;
	struct task_struct *task;
	char comm[TASK_COMM_LEN];
	u32 pid;

	if (!ret)
		return 0;

	/* Check if this is actually bash */
	bpf_get_current_comm(&comm, sizeof(comm));
	if (comm[0] != 'b' || comm[1] != 'a' || comm[2] != 's' || comm[3] != 'h' || comm[4] != 0)
		return 0;

	pid = bpf_get_current_pid_tgid() >> 32;

	/* Reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	/* Fill out the sample with bash readline data */
	e->type = EVENT_TYPE_BASH_READLINE;
	e->pid = pid;
	e->ppid = 0; /* Not relevant for bash commands */
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	/* pid-reuse protection. These must be populated on EVERY event type:
	 * a reused ring-buffer slot otherwise leaks whatever the previous
	 * occupant wrote, and the consumer takes that value as the process's
	 * real start time — corrupting ProcessId identity downstream. */
	task = (struct task_struct *)bpf_get_current_task();
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));
	bpf_probe_read_user_str(&e->command, sizeof(e->command), ret);

	/* Submit to user-space for post-processing */
	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tp/sched/sched_process_exec")
int handle_exec(struct trace_event_raw_sched_process_exec *ctx)
{
	struct task_struct *task;
	unsigned fname_off;
	struct event *e;
	pid_t pid;
	u64 ts;

	/* Get process info */
	pid = bpf_get_current_pid_tgid() >> 32;
	task = (struct task_struct *)bpf_get_current_task();

	/* remember time exec() was executed for this PID */
	ts = bpf_ktime_get_ns();
	bpf_map_update_elem(&exec_start, &pid, &ts, BPF_ANY);

	/* don't emit exec events when minimum duration is specified */
	if (min_duration_ns)
		return 0;

	/* reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	/* fill out the sample with data */
	e->type = EVENT_TYPE_PROCESS;
	e->exit_event = false;
	e->syscall_completed = true;
	e->syscall_ret = 0;
	e->pid = pid;
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->timestamp_ns = ts;
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	fname_off = ctx->__data_loc_filename & 0xFFFF;
	bpf_probe_read_str(&e->filename, sizeof(e->filename), (void *)ctx + fname_off);

	/* Capture full command line with arguments from mm->arg_start */
	struct mm_struct *mm = BPF_CORE_READ(task, mm);
	unsigned long arg_start = BPF_CORE_READ(mm, arg_start);
	unsigned long arg_end = BPF_CORE_READ(mm, arg_end);
	unsigned long arg_len = arg_end - arg_start;

	/* Limit to buffer size */
	if (arg_len > MAX_COMMAND_LEN - 1)
		arg_len = MAX_COMMAND_LEN - 1;

	/* Read command line from userspace memory.
	 *
	 * `bpf_probe_read_user_str` stops at the first NUL, which is the end of
	 * argv[0]: every event carried only the program name and the argument
	 * list — the part that identifies the action — was lost. Read the raw
	 * argv block instead and turn the separators into spaces.
	 */
	if (arg_len > 0) {
		unsigned long read_len = arg_len < MAX_COMMAND_LEN - 1 ? arg_len : MAX_COMMAND_LEN - 1;
		long ret = bpf_probe_read_user(e->full_command, read_len, (void *)arg_start);
		if (ret < 0) {
			/* Fallback to just comm if we can't read cmdline */
			bpf_probe_read_kernel_str(&e->full_command, sizeof(e->full_command), e->comm);
		} else {
			/* Replace null bytes with spaces for readability */
			for (int i = 0; i < MAX_COMMAND_LEN - 1 && i < read_len; i++) {
				if (e->full_command[i] == '\0')
					e->full_command[i] = ' ';
			}
			e->full_command[read_len] = '\0';
		}
	} else {
		/* No arguments, use comm */
		bpf_probe_read_kernel_str(&e->full_command, sizeof(e->full_command), e->comm);
	}

    /* Read process-group and cgroup identity at exec time. Both are in the
     * initial/kernel PID namespace, matching the event's pid/ppid. Failed
     * reads leave the zero from the memset above, which the emitter reports
     * as null rather than inventing a value. */
    struct pid *pgid_pointer = BPF_CORE_READ(task, signal, pids[PIDTYPE_PGID]);
    if (pgid_pointer)
        e->pgid = BPF_CORE_READ(pgid_pointer, numbers[0].nr);
    e->cgroup_id = bpf_get_current_cgroup_id();

    fill_exec_pid_namespace(e, task);
	/* successfully submit it to user-space for post-processing */
	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tp/sched/sched_process_exit")
int handle_exit(struct trace_event_raw_sched_process_template* ctx)
{
	struct task_struct *task;
	struct event *e;
	pid_t pid, tid;
	u64 id, ts, *start_ts, duration_ns = 0;

	/* get PID and TID of exiting thread/process */
	id = bpf_get_current_pid_tgid();
	pid = id >> 32;
	tid = (u32)id;
    bpf_map_delete_elem(&pending_syscalls, &tid);

	/* ignore thread exits */
	if (pid != tid)
		return 0;

	/* if we recorded start of the process, calculate lifetime duration */
	start_ts = bpf_map_lookup_elem(&exec_start, &pid);
	ts = bpf_ktime_get_ns();
	if (start_ts)
		duration_ns = ts - *start_ts;
	else if (min_duration_ns)
		return 0;
	bpf_map_delete_elem(&exec_start, &pid);

	/* if process didn't live long enough, return early */
	if (min_duration_ns && duration_ns < min_duration_ns)
		return 0;

	/* reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	/* fill out the sample with data */
	task = (struct task_struct *)bpf_get_current_task();

	e->type = EVENT_TYPE_PROCESS;
	e->exit_event = true;
	e->duration_ns = duration_ns;
	e->pid = pid;
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->timestamp_ns = ts;
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	e->exit_code = (BPF_CORE_READ(task, exit_code) >> 8) & 0xff;
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	/* send data to user-space for post-processing */
	bpf_ringbuf_submit(e, 0);
	return 0;
}

/* Syscall tracepoint for openat */
static __always_inline int trace_openat(struct trace_event_raw_sys_enter *ctx, int syscall_id)
{
	struct event *e;
	pid_t pid;
	struct task_struct *task;
	char filepath[MAX_FILENAME_LEN] = {};
	int dfd, flags;
	const char *filename;

	pid = bpf_get_current_pid_tgid() >> 32;

	/* Get syscall arguments */
	dfd = (int)ctx->args[0];
	filename = (const char *)ctx->args[1];
	flags = (int)ctx->args[2];

	/* Read filename from user space; a path-not-resident read is retried
	 * at syscall exit, after the kernel copied the path from user memory. */
	const char *retry_path = 0;
	if (read_user_path(filepath, sizeof(filepath), filename) < 0)
		retry_path = filename;

	/* Reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	/* Fill out the event */
	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	/* pid-reuse protection. These must be populated on EVERY event type:
	 * a reused ring-buffer slot otherwise leaks whatever the previous
	 * occupant wrote, and the consumer takes that value as the process's
	 * real start time — corrupting ProcessId identity downstream. */
	task = (struct task_struct *)bpf_get_current_task();
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	/* Copy filepath and set file open details */
	__builtin_memcpy(e->file_op.filepath, filepath, sizeof(filepath));
	e->file_op.fd = -1; /* Will be set on return if needed */
	e->file_op.flags = flags;
	e->file_op.is_open = true;
	e->file_op.op = FILE_OP_OPEN;
	e->file_op.filepath2[0] = '\0';

	/* Submit to user-space; a path-not-resident read is retried at exit. */
	e->syscall_ret = (long long)(unsigned long long)retry_path;
	return stage_syscall(e);
}

/* Syscall tracepoint for open */
static __always_inline int trace_open(struct trace_event_raw_sys_enter *ctx, int syscall_id)
{
	struct event *e;
	pid_t pid;
	struct task_struct *task;
	char filepath[MAX_FILENAME_LEN] = {};
	int flags;
	const char *filename;

	pid = bpf_get_current_pid_tgid() >> 32;

	/* Get syscall arguments */
	filename = (const char *)ctx->args[0];
	flags = (int)ctx->args[1];

	/* Read filename from user space */
	const char *retry_path = 0;
	if (read_user_path(filepath, sizeof(filepath), filename) < 0)
		retry_path = filename;

	/* Reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	/* Fill out the event */
	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	/* pid-reuse protection. These must be populated on EVERY event type:
	 * a reused ring-buffer slot otherwise leaks whatever the previous
	 * occupant wrote, and the consumer takes that value as the process's
	 * real start time — corrupting ProcessId identity downstream. */
	task = (struct task_struct *)bpf_get_current_task();
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	/* Copy filepath and set file open details */
	__builtin_memcpy(e->file_op.filepath, filepath, sizeof(filepath));
	e->file_op.fd = -1;
	e->file_op.flags = flags;
	e->file_op.is_open = true;
	e->file_op.op = FILE_OP_OPEN;
	e->file_op.filepath2[0] = '\0';

	/* Submit to user-space; a path-not-resident read is retried at exit. */
	e->syscall_ret = (long long)(unsigned long long)retry_path;
	return stage_syscall(e);
}

/*
 * File mutations: unlinkat(2) and truncate(2).
 *
 * The action layer already routes FILE_DELETE / FILE_TRUNCATE and the
 * classifier maps them to FileDelete / FileWrite, but no producer existed, so
 * no run ever contained one of these actions (0 rows across 301 archived
 * databases). Both syscalls carry a user-space pathname; the event reuses the
 * FILE_OPERATION schema with an explicit `op`.
 */
static __always_inline int trace_mutation(const char *filename, int op, int syscall_id)
{
	struct event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	struct task_struct *task;
	char filepath[MAX_FILENAME_LEN] = {};

	if (read_user_path(filepath, sizeof(filepath), filename) < 0) {
		report_user_read_failed(syscall_id, filename);
		return 0;
	}

	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	/* pid-reuse protection: populate on EVERY event type. */
	task = (struct task_struct *)bpf_get_current_task();
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	__builtin_memcpy(e->file_op.filepath, filepath, sizeof(filepath));
	e->file_op.fd = -1;
	e->file_op.flags = 0;
	e->file_op.is_open = true; /* not a close event */
	e->file_op.op = op;
	e->file_op.filepath2[0] = '\0';

	return stage_syscall(e);
}

static __always_inline int trace_unlinkat(struct trace_event_raw_sys_enter *ctx)
{
	/* unlinkat(int dirfd, const char *pathname, int flags). Reading a
	 * constant argument index inside the handler keeps the ctx pointer
	 * unmodified; the verifier rejects `ctx + variable_offset`. */
	return trace_mutation((const char *)ctx->args[1], FILE_OP_DELETE, (int)ctx->id);
}

static __always_inline int trace_unlink(struct trace_event_raw_sys_enter *ctx)
{
	/* unlink(const char *pathname). glibc's unlink(2) does NOT go through
	 * unlinkat: without this probe, deletes from `os.unlink`, `rm` on some
	 * libcs and every raw unlink syscall were invisible. */
	return trace_mutation((const char *)ctx->args[0], FILE_OP_DELETE, (int)ctx->id);
}

static __always_inline int trace_rmdir(struct trace_event_raw_sys_enter *ctx)
{
	/* rmdir(const char *pathname) — directory removal is a file mutation. */
	return trace_mutation((const char *)ctx->args[0], FILE_OP_DELETE, (int)ctx->id);
}

static __always_inline int trace_truncate(struct trace_event_raw_sys_enter *ctx)
{
	/* truncate(const char *path, off_t length) */
	return trace_mutation((const char *)ctx->args[0], FILE_OP_TRUNCATE, (int)ctx->id);
}

/*
 * rename(2) / renameat2(2): the source is the sensitive resource ("this file
 * moved"), the destination is where it went. The event carries both; the
 * action layer has no rename type yet, so it records the source as a write
 * to the destination's parent operation (see ADR 0007).
 */
static __always_inline int trace_rename_paths(const char *old_filename,
					      const char *new_filename,
					      int syscall_id)
{
	struct event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	struct task_struct *task;
	char oldpath[MAX_FILENAME_LEN] = {};
	char newpath[MAX_FILENAME_LEN] = {};

	if (read_user_path(oldpath, sizeof(oldpath), old_filename) < 0) {
		report_user_read_failed(syscall_id, old_filename);
		return 0;
	}
	if (read_user_path(newpath, sizeof(newpath), new_filename) < 0) {
		report_user_read_failed(syscall_id, new_filename);
		return 0;
	}

	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	task = (struct task_struct *)bpf_get_current_task();
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	__builtin_memcpy(e->file_op.filepath, oldpath, sizeof(oldpath));
	__builtin_memcpy(e->file_op.filepath2, newpath, sizeof(newpath));
	e->file_op.fd = -1;
	e->file_op.flags = 0;
	e->file_op.is_open = true;
	e->file_op.op = FILE_OP_RENAME;

	return stage_syscall(e);
}

static __always_inline int trace_renameat2(struct trace_event_raw_sys_enter *ctx)
{
	/* renameat2(int olddirfd, const char *oldname,
	 *           int newdirfd, const char *newname, unsigned int flags) */
	return trace_rename_paths((const char *)ctx->args[1], (const char *)ctx->args[3], (int)ctx->id);
}

static __always_inline int trace_renameat(struct trace_event_raw_sys_enter *ctx)
{
	/* renameat(int olddirfd, const char *oldname,
	 *          int newdirfd, const char *newname) — the non-2 variant is
	 * still used by some libcs and direct syscallers. */
	return trace_rename_paths((const char *)ctx->args[1], (const char *)ctx->args[3], (int)ctx->id);
}

static __always_inline int trace_rename(struct trace_event_raw_sys_enter *ctx)
{
	/* rename(const char *oldname, const char *newname) */
	return trace_rename_paths((const char *)ctx->args[0], (const char *)ctx->args[1], (int)ctx->id);
}

/*
 * Network egress.
 *
 * connect(2)/sendto(2) are the two syscalls through which a process reaches a
 * remote peer with an explicit address. The tracepoint arguments are user
 * pointers, so every read goes through bpf_probe_read_user.
 *
 * Only AF_INET and AF_INET6 are reported. AF_UNIX and the rest are local IPC,
 * not egress. AF_INET6 has no decoder yet: the event says AF_INET6 and marks
 * `unsupported_family` — it never invents an address.
 *
 * Noise: connect is per-connection and sendto is per-datagram (DNS, QUIC).
 * The userspace side applies the same PID tracking as file operations; the
 * measured volume budget is in docs/evaluation/network-noise-budget.json.
 */
static __always_inline int trace_network(const void *addr, __u32 addrlen, __u8 op, int syscall_id)
{
	struct event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	struct task_struct *task;
	unsigned short family = 0;
	struct net_op_data net = {};

	if (!addr || addrlen < 2)
		return 0;

	/* sockaddr.sa_family is the first 16 bits. A failed read loses the
	 * connect/send destination; count it instead of returning silently. */
	if (bpf_probe_read_user(&family, sizeof(family), addr) < 0) {
		report_user_read_failed(syscall_id, addr);
		return 0;
	}
	if (family != MTL_AF_INET && family != MTL_AF_INET6)
		return 0;

	/* The kernel ignores or rejects addresses shorter than the family's
	 * sockaddr: `sendto(..., addr, 0)` uses the connected peer and never
	 * reads the address, and a too-short buffer fails with EINVAL. Reading
	 * offset 2/4 from those bytes would report a destination the caller
	 * never provided. Drop them instead. */
	if (family == MTL_AF_INET && addrlen < 16)
		return 0;
	if (family == MTL_AF_INET6 && addrlen < 24)
		return 0;

	net.family = family;
	net.op = op;

	if (family == MTL_AF_INET) {
		/* struct sockaddr_in: sin_family @0, sin_port @2, sin_addr @4. */
		unsigned short port_be = 0;
		unsigned int addr_v4 = 0;

		if (bpf_probe_read_user(&port_be, sizeof(port_be),
					(const char *)addr + 2) == 0 &&
		    bpf_probe_read_user(&addr_v4, sizeof(addr_v4),
					(const char *)addr + 4) == 0) {
			net.port = bpf_ntohs(port_be);
			net.addr = addr_v4; /* network byte order, as read */
			net.supported = 1;
		}
	} else if (family == MTL_AF_INET6) {
		/* struct sockaddr_in6: sin6_family @0, sin6_port @2,
		 * sin6_flowinfo @4, sin6_addr @8 (16 bytes), scope @24. */
		unsigned short port_be = 0;

		if (bpf_probe_read_user(&port_be, sizeof(port_be),
					(const char *)addr + 2) == 0 &&
		    bpf_probe_read_user(net.addr6, sizeof(net.addr6),
					(const char *)addr + 8) == 0) {
			net.port = bpf_ntohs(port_be);
			net.supported = 1;
		}
	}

	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped(DROP_RINGBUF_FULL);
		return 0;
	}

	__builtin_memset(e, 0, sizeof(*e));

	e->type = EVENT_TYPE_NETWORK_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	task = (struct task_struct *)bpf_get_current_task();
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->start_time_ns = BPF_CORE_READ(task, group_leader, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, group_leader, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));
	e->net_op = net;

	return stage_syscall(e);
}

static __always_inline int trace_connect(struct trace_event_raw_sys_enter *ctx)
{
	/* connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) */
	return trace_network((const void *)ctx->args[1], (__u32)ctx->args[2],
			     NET_OP_CONNECT, (int)ctx->id);
}

static __always_inline int trace_sendto(struct trace_event_raw_sys_enter *ctx)
{
	/* sendto(int sockfd, const void *buf, size_t len, int flags,
	 *        const struct sockaddr *dest_addr, socklen_t addrlen) */
	return trace_network((const void *)ctx->args[4], (__u32)ctx->args[5],
			     NET_OP_SENDTO, (int)ctx->id);
}

/* One architecture-provided syscall-number dispatch keeps entry/exit on the
 * same raw tracepoint ABI (specific syscall tracepoints may add fields). */
SEC("tp/raw_syscalls/sys_enter")
int enter_syscall(struct trace_event_raw_sys_enter *ctx)
{
    switch (ctx->id) {
#ifdef __NR_openat
    case __NR_openat: return trace_openat(ctx, (int)ctx->id);
#endif
#ifdef __NR_open
    case __NR_open: return trace_open(ctx, (int)ctx->id);
#endif
#ifdef __NR_unlinkat
    case __NR_unlinkat: return trace_unlinkat(ctx);
#endif
#ifdef __NR_unlink
    case __NR_unlink: return trace_unlink(ctx);
#endif
#ifdef __NR_rmdir
    case __NR_rmdir: return trace_rmdir(ctx);
#endif
#ifdef __NR_truncate
    case __NR_truncate: return trace_truncate(ctx);
#endif
#ifdef __NR_renameat2
    case __NR_renameat2: return trace_renameat2(ctx);
#endif
#ifdef __NR_renameat
    case __NR_renameat: return trace_renameat(ctx);
#endif
#ifdef __NR_rename
    case __NR_rename: return trace_rename(ctx);
#endif
#ifdef __NR_connect
    case __NR_connect: return trace_connect(ctx);
#endif
#ifdef __NR_sendto
    case __NR_sendto: return trace_sendto(ctx);
#endif
    default: return 0;
    }
}
