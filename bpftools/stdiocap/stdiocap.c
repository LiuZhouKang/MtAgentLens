// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
#include <argp.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <locale.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wchar.h>
#include <time.h>

#include "stdiocap.skel.h"
#include "stdiocap.h"
#include "json_escape.h"

/*
 * Report a fatal error, set a non-zero exit code, and jump to cleanup.
 *
 * A bare `warn(...); goto cleanup;` left `err` at 0, so the process exited
 * with status 0 after a fatal failure. The Rust side then saw a clean exit
 * with zero events — an indistinguishable "successful but empty" run.
 */
#define FAIL(...)                                                                 \
	do {                                                                          \
		warn(__VA_ARGS__);                                                        \
		err = -1;                                                                 \
		goto cleanup;                                                             \
	} while (0)


#define INVALID_UID -1
#define INVALID_PID -1
#define PERF_POLL_TIMEOUT_MS 100

#define warn(...) fprintf(stderr, __VA_ARGS__)

static volatile sig_atomic_t exiting;
static bool verbose;
static char *event_buf;

struct env {
	pid_t pid;
	int uid;
	char *comm;
	bool all_fds;
	int max_bytes;
} env = {
	.pid = INVALID_PID,
	.uid = INVALID_UID,
	.comm = NULL,
	.all_fds = false,
	.max_bytes = MAX_BUF_SIZE,
};

const char *argp_program_version = "stdiocap 0.1";
const char *argp_program_bug_address = "https://github.com/eunomia-bpf/mtagentlens";
const char argp_program_doc[] =
	"Capture stdin/stdout/stderr payloads for a target process and output JSON.\n"
	"\n"
	"USAGE: stdiocap -p PID [OPTIONS]\n"
	"\n"
	"EXAMPLES:\n"
	"    ./stdiocap -p 12345\n"
	"    ./stdiocap -p 12345 --all-fds\n"
	"    ./stdiocap -p 12345 -c python\n";

enum {
	OPT_ALL_FDS = 1001,
	OPT_MAX_BYTES,
};

static const struct argp_option opts[] = {
	{"pid", 'p', "PID", 0, "Trace this PID only."},
	{"uid", 'u', "UID", 0, "Trace this UID only."},
	{"comm", 'c', "COMMAND", 0, "Trace only commands matching string."},
	{"all-fds", OPT_ALL_FDS, NULL, 0, "Capture all FDs instead of only stdin/stdout/stderr."},
	{"max-bytes", OPT_MAX_BYTES, "BYTES", 0, "Maximum bytes to emit per event (default 8192)."},
	{"verbose", 'v', NULL, 0, "Verbose libbpf debug output."},
	{},
};

static error_t parse_arg(int key, char *arg, struct argp_state *state)
{
	switch (key) {
	case 'p':
		env.pid = atoi(arg);
		break;
	case 'u':
		env.uid = atoi(arg);
		break;
	case 'c':
		env.comm = strdup(arg);
		break;
	case 'v':
		verbose = true;
		break;
	case OPT_ALL_FDS:
		env.all_fds = true;
		break;
	case OPT_MAX_BYTES:
		env.max_bytes = atoi(arg);
		if (env.max_bytes <= 0)
			env.max_bytes = MAX_BUF_SIZE;
		if (env.max_bytes > MAX_BUF_SIZE)
			env.max_bytes = MAX_BUF_SIZE;
		break;
	case ARGP_KEY_END:
		if (env.pid == INVALID_PID)
			argp_error(state, "-p/--pid is required");
		break;
	default:
		return ARGP_ERR_UNKNOWN;
	}

	return 0;
}

static const struct argp argp = {
	opts,
	parse_arg,
	NULL,
	argp_program_doc,
};

static int libbpf_print_fn(enum libbpf_print_level level, const char *format,
						   va_list args)
{
	if (level == LIBBPF_DEBUG && !verbose)
		return 0;
	return vfprintf(stderr, format, args);
}

static void sig_int(int signo)
{
	(void)signo;
	exiting = 1;
}


static const char *fd_role(int fd)
{
	switch (fd) {
	case 0:
		return "stdin";
	case 1:
		return "stdout";
	case 2:
		return "stderr";
	default:
		return "fd";
	}
}

static bool resolve_fd_target(pid_t pid, int fd, char *buf, size_t buf_size)
{
	char proc_fd[128];
	ssize_t len;

	snprintf(proc_fd, sizeof(proc_fd), "/proc/%d/fd/%d", pid, fd);
	len = readlink(proc_fd, buf, buf_size - 1);
	if (len < 0)
		return false;

	buf[len] = '\0';
	return true;
}


static void print_event(const struct stdiocap_event_t *event)
{
	unsigned int buf_size = event->buf_size;
	char fd_target[PATH_MAX];
	bool have_fd_target = false;

	if (env.comm && strcmp(env.comm, event->comm) != 0)
		return;

	if (!event_buf)
		return;

	if (buf_size > MAX_BUF_SIZE)
		buf_size = MAX_BUF_SIZE;
	if (buf_size > 0) {
		memcpy(event_buf, event->buf, buf_size);
		event_buf[buf_size] = '\0';
	}

	have_fd_target = resolve_fd_target(event->pid, event->fd, fd_target, sizeof(fd_target));

	printf("{");
	printf("\"direction\":\"%s\",", event->is_read ? "READ" : "WRITE");
	printf("\"timestamp_ns\":%llu,", event->timestamp_ns);
	printf("\"comm\":"); json_print_str(event->comm); printf(",");
	printf("\"pid\":%u,", event->pid);
	printf("\"tid\":%u,", event->tid);
	printf("\"uid\":%u,", event->uid);
	printf("\"fd\":%d,", event->fd);
	printf("\"fd_role\":\"%s\",", fd_role(event->fd));
	if (have_fd_target) {
		printf("\"fd_target\":");
		json_print_escaped(fd_target, strlen(fd_target));
		printf(",");
	} else {
		printf("\"fd_target\":null,");
	}
	printf("\"len\":%u,", event->len);
	printf("\"buf_size\":%u,", buf_size);
	printf("\"latency_ms\":%.3f,", (double)event->delta_ns / 1000000.0);
	printf("\"copy_failed\":%s,", event->copy_failed ? "true" : "false");
	printf("\"data\":");
	if (buf_size == 0) {
		printf("null,\"truncated\":false}\n");
		fflush(stdout);
		return;
	}

	json_print_escaped(event_buf, buf_size);
	if (buf_size < event->len)
		printf(",\"truncated\":true,\"bytes_lost\":%u}\n", event->len - buf_size);
	else
		printf(",\"truncated\":false}\n");
	fflush(stdout);
}

static int handle_event(void *ctx, void *data, size_t data_sz)
{
	const struct stdiocap_event_t *event = data;

	(void)ctx;
	(void)data_sz;
	print_event(event);
	return 0;
}

int main(int argc, char **argv)
{
	LIBBPF_OPTS(bpf_object_open_opts, open_opts);
	struct stdiocap_bpf *obj = NULL;
	struct ring_buffer *rb = NULL;
	int err;

	err = argp_parse(&argp, argc, argv, 0, NULL, NULL);
	if (err)
		return err;

	setlocale(LC_ALL, "");
	libbpf_set_print(libbpf_print_fn);

	obj = stdiocap_bpf__open_opts(&open_opts);
	if (!obj) {
		FAIL("failed to open BPF object\n");
	}

	obj->rodata->targ_pid = env.pid == INVALID_PID ? 0 : (__u32)env.pid;
	obj->rodata->targ_uid = env.uid == INVALID_UID ? 0xffffffffU : (__u32)env.uid;
	obj->rodata->trace_stdio_only = !env.all_fds;
	obj->rodata->max_capture_bytes = (__u32)env.max_bytes;

	err = stdiocap_bpf__load(obj);
	if (err) {
		FAIL("failed to load BPF object: %d\n", err);
	}

	err = stdiocap_bpf__attach(obj);
	if (err) {
		FAIL("failed to attach BPF object: %d\n", err);
	}

	event_buf = malloc(MAX_BUF_SIZE + 1);
	if (!event_buf) {
		err = -ENOMEM;
		FAIL("failed to allocate event buffer\n");
	}

	rb = ring_buffer__new(bpf_map__fd(obj->maps.rb), handle_event, NULL, NULL);
	if (!rb) {
		err = -errno;
		FAIL("failed to open ring buffer: %d\n", err);
	}

	if (signal(SIGINT, sig_int) == SIG_ERR || signal(SIGTERM, sig_int) == SIG_ERR) {
		err = 1;
		FAIL("can't set signal handlers: %s\n", strerror(errno));
	}

	while (!exiting) {
		err = ring_buffer__poll(rb, PERF_POLL_TIMEOUT_MS);
		if (err == -EINTR) {
			err = 0;
			break;
		}
		if (err < 0) {
			FAIL("error polling ring buffer: %s\n", strerror(-err));
		}
		err = 0;
	}

	/* Stop production before draining and publishing the final counters. */
	stdiocap_bpf__detach(obj);
	ring_buffer__consume(rb);
	fflush(stdout);

	/* Emit a parseable STATS event so the Rust runner (and the spike) can see
	 * ring-buffer drops. The runner requires timestamp_ns/pid/comm, so this is
	 * a full event object, not a stderr line. */
	{
		__u64 reasons[STDIO_DROP_REASON_MAX] = {};
		__u64 dropped = 0;
		int map_fd = bpf_map__fd(obj->maps.dropped_events);
		struct timespec now;
		__u64 timestamp_ns = 0;
		int ok = map_fd >= 0;

		for (__u32 key = 0; ok && key < STDIO_DROP_REASON_MAX; key++) {
			if (bpf_map_lookup_elem(map_fd, &key, &reasons[key]) != 0)
				ok = 0;
			else
				dropped += reasons[key];
		}
		if (clock_gettime(CLOCK_MONOTONIC, &now) == 0)
			timestamp_ns = (__u64)now.tv_sec * 1000000000ULL + (__u64)now.tv_nsec;
		if (ok) {
			printf("{\"direction\":\"STATS\",\"timestamp_ns\":%llu,"
			       "\"comm\":\"stdiocap\",\"pid\":0,\"tid\":0,"
			       "\"uid\":0,\"fd\":-1,\"fd_role\":\"stats\","
			       "\"fd_target\":null,\"len\":0,\"buf_size\":0,"
			       "\"latency_ms\":0,\"copy_failed\":false,\"data\":null,"
			       "\"truncated\":false,\"dropped_events\":%llu,"
			       "\"ringbuf_full_dropped\":%llu,\"args_map_dropped\":%llu,"
			       "\"lookup_missed_dropped\":%llu}\n",
			       (unsigned long long)timestamp_ns,
			       (unsigned long long)dropped,
			       (unsigned long long)reasons[STDIO_DROP_RINGBUF_FULL],
			       (unsigned long long)reasons[STDIO_DROP_ARGS_MAP_FAILED],
			       (unsigned long long)reasons[STDIO_DROP_LOOKUP_MISSED]);
			fflush(stdout);
		}
	}

cleanup:
	if (event_buf) {
		free(event_buf);
		event_buf = NULL;
	}
	if (env.comm) {
		free(env.comm);
		env.comm = NULL;
	}
	ring_buffer__free(rb);
	stdiocap_bpf__destroy(obj);
	return err != 0;
}
