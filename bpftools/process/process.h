/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
/* Copyright (c) 2020 Facebook */
/* Single source of truth for `struct event`: do not reintroduce a copy under
 * include/ — two copies silently diverge (this file and include/process.h
 * differed only by the field added for delete/truncate). */
#ifndef __PROCESS_H
#define __PROCESS_H

#include <stdbool.h>

#define TASK_COMM_LEN 16
#define MAX_FILENAME_LEN 127
#define MAX_COMMAND_FILTERS 10
#define MAX_TRACKED_PIDS 1024
#define MAX_COMMAND_LEN 256

enum filter_mode {
	FILTER_MODE_ALL = 0,      /* Trace all processes and all read/write operations */
	FILTER_MODE_PROC = 1,     /* Trace all processes but only read/write for tracked PIDs */
	FILTER_MODE_FILTER = 2,   /* Only trace processes matching filters and their read/write */
};

/* Loss reasons recorded in the dropped_events array map. Shared with the
 * userspace reader, which looks up every key and emits the split. */
enum drop_reason {
	DROP_RINGBUF_FULL = 0,       /* bpf_ringbuf_reserve returned NULL */
	DROP_PENDING_TABLE_FULL = 1, /* pending_syscalls update failed */
	DROP_USER_READ_FAILED = 2,   /* user pointer could not be read */
	DROP_REASON_MAX = 3,
};

enum event_type {
	EVENT_TYPE_PROCESS = 0,
	EVENT_TYPE_BASH_READLINE = 1,
	EVENT_TYPE_FILE_OPERATION = 2,
	EVENT_TYPE_NETWORK_OPERATION = 3,
	/* A notice carrying the process context of a measured loss. It is not
	 * an action event and userspace only aggregates it. */
	EVENT_TYPE_CAPTURE_LOSS = 4,
	EVENT_TYPE_PROCESS_SNAPSHOT = 5,
};

/* What a NETWORK_OPERATION event describes. */
enum net_op_kind {
	NET_OP_CONNECT = 0,
	NET_OP_SENDTO = 1,
};

/* Address families this probe reports. Both AF_INET and AF_INET6 are decoded;
 * `supported = 0` means the address could not be read (short addrlen or a
 * failed user copy), and the JSON marks `unsupported_family` rather than
 * inventing an address. Other families (AF_UNIX et al.) are local IPC and are
 * dropped in the BPF program before the ring buffer. */
#define MTL_AF_INET 2
#define MTL_AF_INET6 10

struct net_op_data {
	unsigned short family;  /* AF_INET / AF_INET6 (host byte order) */
	unsigned char op;       /* enum net_op_kind */
	unsigned char supported; /* 1 = addr/port parsed (AF_INET and AF_INET6) */
	unsigned int addr;      /* IPv4 address, network byte order as read */
	unsigned short port;    /* port, host byte order (bpf_ntohs applied) */
	unsigned char addr6[16]; /* IPv6 address, network byte order as read */
};

/* What a FILE_OPERATION event describes.
 *
 * `is_open` only distinguished open from close. Deleting or truncating a file
 * is neither; printing those as FILE_OPEN makes the action layer record the
 * wrong operation (and loses the destructive signal entirely). */
enum file_op_kind {
	FILE_OP_OPEN = 0,
	FILE_OP_DELETE = 1,
	FILE_OP_TRUNCATE = 2,
	FILE_OP_RENAME = 3,
};

/* PID namespace mapping reported on EXEC. The BPF side reads the inode from
 * the task's pid_ns_for_children and only sets error=0 when that inode matches
 * the active namespace and its nsfs device matches the loader observation. */
/* Local acquisition unavailable, distinct from the helper's -EINVAL/-ENOENT. */
enum pid_namespace_error {
    PID_NAMESPACE_UNAVAILABLE = -61, /* -ENODATA */
};

struct pid_namespace_context {
	unsigned long long device;
	unsigned long long inode;
	unsigned int tgid;
	int error;
};

struct event {
	enum event_type type;
	int pid;
	int ppid;
	unsigned exit_code;
	unsigned long long duration_ns;
	unsigned long long timestamp_ns;
	unsigned long long start_time_ns;       /* process start time (for pid reuse prevention) */
	unsigned long long parent_start_time_ns; /* parent process start time */
	long long syscall_ret;
	bool syscall_completed;
	int pgid;                                /* process group ID (unifies subprocesses) */
	char comm[TASK_COMM_LEN];
	char full_command[MAX_COMMAND_LEN];     /* full command line with args */

	/* Enrichment fields (kernel-side causal context) */
	unsigned long long cgroup_id;            /* cgroup v2 ID (team/container boundary) */
	unsigned int uid;                        /* user ID */
	unsigned int gid;                        /* group ID */
	unsigned long long seq_number;           /* per-process syscall sequence counter */
	unsigned int parent_uid;                 /* parent user ID (causal attribution) */
	unsigned long long parent_cgroup_id;     /* parent cgroup ID (team boundary) */

	union {
		char filename[MAX_FILENAME_LEN];     /* for process events */
		char command[MAX_COMMAND_LEN];       /* for bash readline events */
		struct {                             /* for file operation events */
			char filepath[MAX_FILENAME_LEN];
			/* Destination path for FILE_OP_RENAME; empty otherwise. */
			char filepath2[MAX_FILENAME_LEN];
			int fd;
			int flags;
			bool is_open;  /* true for open/openat, false for close */
			int op;        /* enum file_op_kind */
		} file_op;
		struct net_op_data net_op;           /* for network operation events */
		/* enum drop_reason for EVENT_TYPE_CAPTURE_LOSS. An int in the
		 * existing union keeps sizeof(struct event) unchanged. */
		int capture_loss_reason;
	};
	bool exit_event;
	struct pid_namespace_context pid_namespace;
};

struct command_filter {
	char comm[TASK_COMM_LEN];
};

struct pid_info {
	pid_t pid;
	pid_t ppid;
	bool is_tracked;
};

#endif /* __PROCESS_H */
