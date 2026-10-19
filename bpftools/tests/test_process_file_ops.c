// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
/*
 * Contract test for the FILE_OPERATION JSON line.
 *
 * The collector classifies actions from the `event` field: FILE_DELETE and
 * FILE_TRUNCATE must reach it as themselves, not as FILE_OPEN. Before the
 * `op` field existed, the kernel had no way to say "this was a delete", so the
 * action layer could never record FileDelete — which is why 301 archived
 * databases contain zero delete/rename/truncate actions.
 */
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "process.h"
#include "process_json.h"

static char captured[8192];

/* Capture stdout while running `fn`. */
static void capture(void (*fn)(void))
{
	FILE *f = fopen("/tmp/mtl_file_ops_out.txt", "w");
	int saved;

	assert(f != NULL);
	fflush(stdout);
	saved = dup(fileno(stdout));
	dup2(fileno(f), fileno(stdout));
	fn();
	fflush(stdout);
	dup2(saved, fileno(stdout));
	close(saved);
	fclose(f);

	f = fopen("/tmp/mtl_file_ops_out.txt", "r");
	assert(f != NULL);
	size_t n = fread(captured, 1, sizeof(captured) - 1, f);
	captured[n] = '\0';
	fclose(f);
}

static struct event make_event(int op, const char *path)
{
	struct event e;
	memset(&e, 0, sizeof(e));
	e.type = EVENT_TYPE_FILE_OPERATION;
	e.pid = 4242;
	e.start_time_ns = 1;
	e.parent_start_time_ns = 0;
	strcpy(e.comm, "rm");
	e.file_op.op = op;
	e.file_op.flags = 0;
	e.file_op.is_open = true;
	strncpy(e.file_op.filepath, path, MAX_FILENAME_LEN - 1);
	e.file_op.filepath[MAX_FILENAME_LEN - 1] = '\0';
	return e;
}

static struct event g_event;

static void emit(void)
{
	print_file_event(&g_event, 1234, 1, NULL);
}

int main(void)
{
	g_event = make_event(FILE_OP_OPEN, "/tmp/plain.txt");
	capture(emit);
	assert(strstr(captured, "\"event\":\"FILE_OPEN\"") != NULL);

	g_event = make_event(FILE_OP_DELETE, "/home/user/secrets.env");
	capture(emit);
	assert(strstr(captured, "\"event\":\"FILE_DELETE\"") != NULL);
	assert(strstr(captured, "/home/user/secrets.env") != NULL);
	assert(strstr(captured, "\"pid\":4242") != NULL);

	g_event = make_event(FILE_OP_TRUNCATE, "/var/log/auth.log");
	capture(emit);
	assert(strstr(captured, "\"event\":\"FILE_TRUNCATE\"") != NULL);

	g_event = make_event(FILE_OP_RENAME, "/home/user/.ssh/id_rsa");
	strncpy(g_event.file_op.filepath2, "/tmp/id_rsa", MAX_FILENAME_LEN - 1);
	capture(emit);
	assert(strstr(captured, "\"event\":\"FILE_RENAME\"") != NULL);
	assert(strstr(captured, "\"filepath2\":\"/tmp/id_rsa\"") != NULL);

	/* Agent-controlled bytes must not break the JSON line. */
	g_event = make_event(FILE_OP_DELETE, "/tmp/a\"b\\c.txt");
	capture(emit);
	assert(strstr(captured, "\\\"") != NULL);
	assert(strstr(captured, "\\\\") != NULL);

	/* Unknown op values degrade to FILE_OPEN rather than inventing a name. */
	g_event = make_event(99, "/tmp/x");
	capture(emit);
	assert(strstr(captured, "\"event\":\"FILE_OPEN\"") != NULL);

	puts("test_process_file_ops: ok");
	return 0;
}
