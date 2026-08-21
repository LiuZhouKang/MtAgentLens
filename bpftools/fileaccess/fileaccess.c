// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <bpf/libbpf.h>
#include "fileaccess.h"
#include "fileaccess.skel.h"

static volatile sig_atomic_t exiting = 0;

static void sig_int(int signo)
{
	exiting = 1;
}

static int libbpf_print_fn(enum libbpf_print_level level, const char *format, va_list args)
{
	return vfprintf(stderr, format, args);
}

static void handle_event(void *ctx, int cpu, void *data, unsigned int data_sz)
{
	const struct fileaccess_event *e = data;
	printf("{\"pid\":%d,\"uid\":%d,\"op\":%d,\"flags\":%d,\"mode\":%d,\"filename\":\"%s\",\"comm\":\"%s\",\"ts\":%llu}\n",
		e->pid, e->uid, e->op_type, e->flags, e->mode, e->filename, e->comm, e->timestamp_ns);
}

int main(int argc, char **argv)
{
	struct fileaccess_bpf *skel;
	struct ring_buffer *rb = NULL;
	pid_t target_pid = 0;
	int err;

	if (argc > 1)
		target_pid = atoi(argv[1]);

	libbpf_set_print(libbpf_print_fn);

	skel = fileaccess_bpf__open_and_load();
	if (!skel) {
		fprintf(stderr, "Failed to open/load BPF skeleton\n");
		return 1;
	}

	if (target_pid) {
		skel->rodata->targ_pid = target_pid;
	}

	err = fileaccess_bpf__attach(skel);
	if (err) {
		fprintf(stderr, "Failed to attach BPF programs: %d\n", err);
		goto cleanup;
	}

	rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle_event, NULL, NULL);
	if (!rb) {
		fprintf(stderr, "Failed to create ring buffer\n");
		goto cleanup;
	}

	signal(SIGINT, sig_int);

	while (!exiting) {
		err = ring_buffer__poll(rb, 100);
		if (err < 0 && err != -EINTR) {
			fprintf(stderr, "Error polling ring buffer: %d\n", err);
			break;
		}
	}

cleanup:
	ring_buffer__free(rb);
	fileaccess_bpf__destroy(skel);
	return err < 0 ? 1 : 0;
}
