// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/* Copyright (c) 2020 Facebook */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_endian.h>
#include "process.h"

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 8192);
	__type(key, pid_t);
	__type(value, u64);
} exec_start SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, 1024 * 1024);  // v8.1: 256KB → 1MB for high-throughput agents
} rb SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, __u64);
} dropped_events SEC(".maps");

const volatile unsigned long long min_duration_ns = 0;

static __always_inline void inc_dropped(void)
{
	__u32 key = 0;
	__u64 *cnt = bpf_map_lookup_elem(&dropped_events, &key);

	if (cnt)
		__sync_fetch_and_add(cnt, 1);
}

// ── v8.1: In-eBPF noise filter for FILE_OPEN events ────────────────
// Filter common noise patterns BEFORE ringbuf reserve to prevent
// buffer overflow from high-frequency system file accesses.
// Reduces FILE_OPEN volume by ~90% under Claude/Node.js workloads.

static __always_inline bool is_file_open_noise(const char *filepath)
{
	// BPF self-access: trace event IDs in debugfs
	if (filepath[0] == '/' && filepath[1] == 's' && filepath[2] == 'y' && filepath[3] == 's')
		return true;

	// Procfs: process introspection
	if (filepath[0] == '/' && filepath[1] == 'p' && filepath[2] == 'r' && filepath[3] == 'o' && filepath[4] == 'c')
		return true;

	// Devfs: device nodes
	if (filepath[0] == '/' && filepath[1] == 'd' && filepath[2] == 'e' && filepath[3] == 'v')
		return true;

	// System library directories (shared object loading)
	if (filepath[0] == '/' && filepath[1] == 'l' && filepath[2] == 'i' && filepath[3] == 'b')
		return true;

	if (filepath[0] == '/' && filepath[1] == 'u' && filepath[2] == 's' && filepath[3] == 'r') {
		// /usr/lib, /usr/lib64 — system libs
		if (filepath[4] == '/' && filepath[5] == 'l' && filepath[6] == 'i' && filepath[7] == 'b')
			return true;
		// /usr/share — data files
		if (filepath[4] == '/' && filepath[5] == 's' && filepath[6] == 'h' && filepath[7] == 'a')
			return true;
	}

	// Toolchain PATH lookups: .bun, .cargo, .local/bin, .nvm
	if (filepath[0] == '/' && filepath[1] == 'h' && filepath[2] == 'o' && filepath[3] == 'm' && filepath[4] == 'e') {
		// We can't do deep prefix matching in bounded eBPF — let userspace handle
		// the remaining toolchain noise via NormalizedArg filtering
	}

	return false;
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
		inc_dropped();
		return 0;
	}

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
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
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
		inc_dropped();
		return 0;
	}

	/* fill out the sample with data */
	e->type = EVENT_TYPE_PROCESS;
	e->exit_event = false;
	e->pid = pid;
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->timestamp_ns = ts;
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
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

	/* Read command line from userspace memory */
	if (arg_len > 0) {
		long ret = bpf_probe_read_user_str(&e->full_command, arg_len + 1, (void *)arg_start);
		if (ret < 0) {
			/* Fallback to just comm if we can't read cmdline */
			bpf_probe_read_kernel_str(&e->full_command, sizeof(e->full_command), e->comm);
		} else {
			/* Replace null bytes with spaces for readability */
			for (int i = 0; i < MAX_COMMAND_LEN - 1 && i < ret - 1; i++) {
				if (e->full_command[i] == '\0')
					e->full_command[i] = ' ';
			}
		}
	} else {
		/* No arguments, use comm */
		bpf_probe_read_kernel_str(&e->full_command, sizeof(e->full_command), e->comm);
	}

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
		inc_dropped();
		return 0;
	}

	/* fill out the sample with data */
	task = (struct task_struct *)bpf_get_current_task();

	e->type = EVENT_TYPE_PROCESS;
	e->exit_event = true;
	e->duration_ns = duration_ns;
	e->pid = pid;
	e->ppid = BPF_CORE_READ(task, real_parent, tgid);
	e->timestamp_ns = ts;
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
	e->exit_code = (BPF_CORE_READ(task, exit_code) >> 8) & 0xff;
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	/* send data to user-space for post-processing */
	bpf_ringbuf_submit(e, 0);
	return 0;
}

/* Syscall tracepoint for openat */
SEC("tp/syscalls/sys_enter_openat")
int trace_openat(struct trace_event_raw_sys_enter *ctx)
{
	struct event *e;
	pid_t pid;
	struct task_struct *task;
	char filepath[MAX_FILENAME_LEN];
	int dfd, flags;
	const char *filename;

	pid = bpf_get_current_pid_tgid() >> 32;

	/* Get syscall arguments */
	dfd = (int)ctx->args[0];
	filename = (const char *)ctx->args[1];
	flags = (int)ctx->args[2];

	/* Read filename from user space */
	if (bpf_probe_read_user_str(filepath, sizeof(filepath), filename) < 0)
		return 0;

	// v8.1: Filter system noise before ringbuf reserve
	if (is_file_open_noise(filepath))
		return 0;

	/* Reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped();
		return 0;
	}

	/* Fill out the event */
	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0; /* Will be filled if needed */
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	/* pid-reuse protection. These must be populated on EVERY event type:
	 * a reused ring-buffer slot otherwise leaks whatever the previous
	 * occupant wrote, and the consumer takes that value as the process's
	 * real start time — corrupting ProcessId identity downstream. */
	task = (struct task_struct *)bpf_get_current_task();
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	/* Copy filepath and set file open details */
	bpf_probe_read_kernel_str(e->file_op.filepath, sizeof(e->file_op.filepath), filepath);
	e->file_op.fd = -1; /* Will be set on return if needed */
	e->file_op.flags = flags;
	e->file_op.is_open = true;
	e->file_op.op = FILE_OP_OPEN;
	e->file_op.filepath2[0] = '\0';

	/* Submit to user-space */
	bpf_ringbuf_submit(e, 0);
	return 0;
}

/* Syscall tracepoint for open */
SEC("tp/syscalls/sys_enter_open")
int trace_open(struct trace_event_raw_sys_enter *ctx)
{
	struct event *e;
	pid_t pid;
	struct task_struct *task;
	char filepath[MAX_FILENAME_LEN];
	int flags;
	const char *filename;

	pid = bpf_get_current_pid_tgid() >> 32;

	/* Get syscall arguments */
	filename = (const char *)ctx->args[0];
	flags = (int)ctx->args[1];

	/* Read filename from user space */
	if (bpf_probe_read_user_str(filepath, sizeof(filepath), filename) < 0)
		return 0;

	// v8.1: Filter system noise before ringbuf reserve
	if (is_file_open_noise(filepath))
		return 0;

	/* Reserve sample from BPF ringbuf */
	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped();
		return 0;
	}

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
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	/* Copy filepath and set file open details */
	bpf_probe_read_kernel_str(e->file_op.filepath, sizeof(e->file_op.filepath), filepath);
	e->file_op.fd = -1;
	e->file_op.flags = flags;
	e->file_op.is_open = true;
	e->file_op.op = FILE_OP_OPEN;
	e->file_op.filepath2[0] = '\0';

	/* Submit to user-space */
	bpf_ringbuf_submit(e, 0);
	return 0;
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
static __always_inline int trace_mutation(const char *filename, int op)
{
	struct event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	struct task_struct *task;
	char filepath[MAX_FILENAME_LEN];

	if (bpf_probe_read_user_str(filepath, sizeof(filepath), filename) < 0)
		return 0;

	if (is_file_open_noise(filepath))
		return 0;

	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped();
		return 0;
	}

	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	/* pid-reuse protection: populate on EVERY event type. */
	task = (struct task_struct *)bpf_get_current_task();
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	bpf_probe_read_kernel_str(e->file_op.filepath, sizeof(e->file_op.filepath), filepath);
	e->file_op.fd = -1;
	e->file_op.flags = 0;
	e->file_op.is_open = true; /* not a close event */
	e->file_op.op = op;
	e->file_op.filepath2[0] = '\0';

	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tp/syscalls/sys_enter_unlinkat")
int trace_unlinkat(struct trace_event_raw_sys_enter *ctx)
{
	/* unlinkat(int dirfd, const char *pathname, int flags). Reading a
	 * constant argument index inside the handler keeps the ctx pointer
	 * unmodified; the verifier rejects `ctx + variable_offset`. */
	return trace_mutation((const char *)ctx->args[1], FILE_OP_DELETE);
}

SEC("tp/syscalls/sys_enter_unlink")
int trace_unlink(struct trace_event_raw_sys_enter *ctx)
{
	/* unlink(const char *pathname). glibc's unlink(2) does NOT go through
	 * unlinkat: without this probe, deletes from `os.unlink`, `rm` on some
	 * libcs and every raw unlink syscall were invisible. */
	return trace_mutation((const char *)ctx->args[0], FILE_OP_DELETE);
}

SEC("tp/syscalls/sys_enter_rmdir")
int trace_rmdir(struct trace_event_raw_sys_enter *ctx)
{
	/* rmdir(const char *pathname) — directory removal is a file mutation. */
	return trace_mutation((const char *)ctx->args[0], FILE_OP_DELETE);
}

SEC("tp/syscalls/sys_enter_truncate")
int trace_truncate(struct trace_event_raw_sys_enter *ctx)
{
	/* truncate(const char *path, off_t length) */
	return trace_mutation((const char *)ctx->args[0], FILE_OP_TRUNCATE);
}

/*
 * rename(2) / renameat2(2): the source is the sensitive resource ("this file
 * moved"), the destination is where it went. The event carries both; the
 * action layer has no rename type yet, so it records the source as a write
 * to the destination's parent operation (see ADR 0007).
 */
static __always_inline int trace_rename_paths(const char *old_filename,
					      const char *new_filename)
{
	struct event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	struct task_struct *task;
	char oldpath[MAX_FILENAME_LEN];
	char newpath[MAX_FILENAME_LEN];

	if (bpf_probe_read_user_str(oldpath, sizeof(oldpath), old_filename) < 0)
		return 0;
	if (bpf_probe_read_user_str(newpath, sizeof(newpath), new_filename) < 0)
		return 0;
	if (is_file_open_noise(oldpath))
		return 0;

	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) {
		inc_dropped();
		return 0;
	}

	e->type = EVENT_TYPE_FILE_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	task = (struct task_struct *)bpf_get_current_task();
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));

	bpf_probe_read_kernel_str(e->file_op.filepath, sizeof(e->file_op.filepath), oldpath);
	bpf_probe_read_kernel_str(e->file_op.filepath2, sizeof(e->file_op.filepath2), newpath);
	e->file_op.fd = -1;
	e->file_op.flags = 0;
	e->file_op.is_open = true;
	e->file_op.op = FILE_OP_RENAME;

	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tp/syscalls/sys_enter_renameat2")
int trace_renameat2(struct trace_event_raw_sys_enter *ctx)
{
	/* renameat2(int olddirfd, const char *oldname,
	 *           int newdirfd, const char *newname, unsigned int flags) */
	return trace_rename_paths((const char *)ctx->args[1], (const char *)ctx->args[3]);
}

SEC("tp/syscalls/sys_enter_renameat")
int trace_renameat(struct trace_event_raw_sys_enter *ctx)
{
	/* renameat(int olddirfd, const char *oldname,
	 *          int newdirfd, const char *newname) — the non-2 variant is
	 * still used by some libcs and direct syscallers. */
	return trace_rename_paths((const char *)ctx->args[1], (const char *)ctx->args[3]);
}

SEC("tp/syscalls/sys_enter_rename")
int trace_rename(struct trace_event_raw_sys_enter *ctx)
{
	/* rename(const char *oldname, const char *newname) */
	return trace_rename_paths((const char *)ctx->args[0], (const char *)ctx->args[1]);
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
static __always_inline int trace_network(const void *addr, __u32 addrlen, __u8 op)
{
	struct event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	struct task_struct *task;
	unsigned short family = 0;
	struct net_op_data net = {};

	if (!addr || addrlen < 2)
		return 0;

	/* sockaddr.sa_family is the first 16 bits. */
	if (bpf_probe_read_user(&family, sizeof(family), addr) < 0)
		return 0;
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
		inc_dropped();
		return 0;
	}

	e->type = EVENT_TYPE_NETWORK_OPERATION;
	e->pid = pid;
	e->ppid = 0;
	e->exit_code = 0;
	e->duration_ns = 0;
	e->timestamp_ns = bpf_ktime_get_ns();
	e->exit_event = false;
	task = (struct task_struct *)bpf_get_current_task();
	e->start_time_ns = BPF_CORE_READ(task, start_time);
	e->parent_start_time_ns = BPF_CORE_READ(task, real_parent, start_time);
	bpf_get_current_comm(&e->comm, sizeof(e->comm));
	e->net_op = net;

	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tp/syscalls/sys_enter_connect")
int trace_connect(struct trace_event_raw_sys_enter *ctx)
{
	/* connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) */
	return trace_network((const void *)ctx->args[1], (__u32)ctx->args[2],
			     NET_OP_CONNECT);
}

SEC("tp/syscalls/sys_enter_sendto")
int trace_sendto(struct trace_event_raw_sys_enter *ctx)
{
	/* sendto(int sockfd, const void *buf, size_t len, int flags,
	 *        const struct sockaddr *dest_addr, socklen_t addrlen) */
	return trace_network((const void *)ctx->args[4], (__u32)ctx->args[5],
			     NET_OP_SENDTO);
}
