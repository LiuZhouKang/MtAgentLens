// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
#define _GNU_SOURCE
#define main process_loader_main
#include "../process/process.c"
#undef main
#include <assert.h>

int main(void)
{
    struct event e = { .type = EVENT_TYPE_FILE_OPERATION, .pid = 4242,
        .ppid = 41, .start_time_ns = 10, .parent_start_time_ns = 1 };
    char warning[128];
    e.file_op.is_open = true;
    strcpy(e.file_op.filepath, "/tmp/capture-contract");
    assert(get_file_open_count(&e, 100, warning, sizeof(warning)) == 1);
    e.file_op.flags = 01001;
    assert(get_file_open_count(&e, 101, warning, sizeof(warning)) == 1);
    assert(get_file_open_count(&e, 102, warning, sizeof(warning)) == 0);
    e.start_time_ns = 11;
    assert(get_file_open_count(&e, 103, warning, sizeof(warning)) == 1);
    for (int i = 0; i < 40; i++) {
        snprintf(e.file_op.filepath, sizeof(e.file_op.filepath), "/tmp/capture-distinct-%d", i);
        assert(get_file_open_count(&e, 104 + i, warning, sizeof(warning)) == 1);
    }
    struct pid_tracker tracker;
    pid_tracker_init(&tracker, NULL, 0, FILTER_MODE_FILTER, 0);
    pid_tracker_add(&tracker, 41, 1);
    pid_tracker_observe_identity(&tracker, 41, 1);
    assert(pid_tracker_parent_matches(&tracker, 41, 1));
    assert(!pid_tracker_parent_matches(&tracker, 41, 2));
    pid_tracker_observe_identity(&tracker, 41, 2);
    assert(!pid_tracker_is_tracked(&tracker, 41));
    e.syscall_completed = true;
    e.syscall_ret = -13;
    assert(strcmp(syscall_outcome(&e), "failed") == 0);
    e.syscall_ret = 3;
    assert(strcmp(syscall_outcome(&e), "succeeded") == 0);
    e.type = EVENT_TYPE_NETWORK_OPERATION;
    e.net_op.op = NET_OP_CONNECT;
    e.syscall_ret = -115;
    assert(strcmp(syscall_outcome(&e), "pending") == 0);
    e.syscall_completed = false;
    assert(strcmp(syscall_outcome(&e), "unknown") == 0);

    /* The full-table fallback must emit once through the production handler,
     * without bypassing filtering or printing both inside and outside dedup. */
    e.type = EVENT_TYPE_FILE_OPERATION;
    e.file_op.op = FILE_OP_OPEN;
    strcpy(e.file_op.filepath, "/tmp/capture-full-table");
    hash_count = MAX_FILE_HASHES;
    for (int i = 0; i < hash_count; i++)
        file_hashes[i].timestamp_ns = e.timestamp_ns;
    pid_tracker_init(&tracker, NULL, 0, FILTER_MODE_ALL, 0);
    FILE *capture = tmpfile();
    assert(capture);
    int saved_stdout = dup(STDOUT_FILENO);
    assert(saved_stdout >= 0);
    fflush(stdout);
    assert(dup2(fileno(capture), STDOUT_FILENO) >= 0);
    assert(handle_event(&tracker, &e, sizeof(e)) == 0);
    fflush(stdout);
    assert(dup2(saved_stdout, STDOUT_FILENO) >= 0);
    close(saved_stdout);
    rewind(capture);
    char output[4096];
    assert(fgets(output, sizeof(output), capture));
    assert(strstr(output, "capture-full-table"));
    assert(fgetc(capture) == EOF);
    assert(aggregation_table_full == 1);
    fclose(capture);
    assert(PID_NAMESPACE_UNAVAILABLE == -61);
    /* A kernel-validated namespace mapping is evidence, not a guessed pid. */
    e.type = EVENT_TYPE_PROCESS;
    e.exit_event = false;
    e.pid_namespace.device = 5;
    e.pid_namespace.inode = 4026533000ULL;
    e.pid_namespace.tgid = 17;
    e.pid_namespace.error = 0;
    e.pgid = 4321;
    e.cgroup_id = 9876543210ULL;
    pid_tracker_init(&tracker, NULL, 0, FILTER_MODE_ALL, 0);
    capture = tmpfile();
    assert(capture);
    saved_stdout = dup(STDOUT_FILENO);
    fflush(stdout);
    assert(dup2(fileno(capture), STDOUT_FILENO) >= 0);
    handle_event(&tracker, &e, sizeof(e));
    e.pid_namespace.error = -22;
    e.pgid = 0;
    e.cgroup_id = 0;
    handle_event(&tracker, &e, sizeof(e));
    fflush(stdout);
    assert(dup2(saved_stdout, STDOUT_FILENO) >= 0);
    close(saved_stdout);
    rewind(capture);
    assert(fgets(output, sizeof(output), capture));
    assert(strstr(output, "\"pid_namespace\":{\"device\":5,\"inode\":4026533000,\"tgid\":17}"));
    assert(strstr(output, "\"pgid\":4321"));
    assert(strstr(output, "\"cgroup_id\":9876543210"));
    assert(fgets(output, sizeof(output), capture));
    assert(strstr(output, "\"pid_namespace\":null"));
    assert(strstr(output, "\"pgid\":null"));
    assert(strstr(output, "\"cgroup_id\":null"));
    assert(strstr(output, "\"pid_namespace_error\":-22"));
    assert(fgetc(capture) == EOF);
    fclose(capture);
    /* Buffered events older than a startup generation must not evict it. */
    pid_tracker_init(&tracker, NULL, 0, FILTER_MODE_FILTER, 100);
    pid_tracker_add(&tracker, 100, 1);
    pid_tracker_observe_identity(&tracker, 100, 200);
    struct event stale = { .type=EVENT_TYPE_FILE_OPERATION,.pid=100,.ppid=1,
        .start_time_ns=100,.parent_start_time_ns=1 };
    handle_event(&tracker,&stale,sizeof(stale));
    assert(pid_tracker_parent_matches(&tracker,100,200));
    assert(tracker.target_pid==100);
    assert(tracker_stale_events==1);
    stale.pid=300;stale.start_time_ns=300;stale.ppid=100;stale.parent_start_time_ns=100;
    handle_event(&tracker,&stale,sizeof(stale));
    assert(pid_tracker_parent_matches(&tracker,100,200));
    assert(tracker_stale_events==2);
    tracker_stale_events=0;

    /* Failure notices follow the same target cohort without becoming actions. */
    char *scoped_filters[] = {"watched"};
    pid_tracker_init(&tracker, scoped_filters, 1, FILTER_MODE_FILTER, 0);
    user_read_target = user_read_outside = user_read_unresolved = 0;
    struct event loss = { .type = EVENT_TYPE_CAPTURE_LOSS, .pid = 8001,
        .ppid = 1, .start_time_ns = 100, .parent_start_time_ns = 1,
        .capture_loss_reason = DROP_USER_READ_FAILED };
    strcpy(loss.comm, "watched");
    capture = tmpfile();
    assert(capture);
    saved_stdout = dup(STDOUT_FILENO);
    fflush(stdout);
    assert(dup2(fileno(capture), STDOUT_FILENO) >= 0);
    handle_event(&tracker, &loss, sizeof(loss));
    assert(user_read_target == 1);
    loss.pid = 9001;
    loss.start_time_ns = 200;
    strcpy(loss.comm, "outside");
    handle_event(&tracker, &loss, sizeof(loss));
    assert(user_read_outside == 1);
    loss.pid = 8002;
    loss.ppid = 8001;
    loss.start_time_ns = 300;
    loss.parent_start_time_ns = 100;
    strcpy(loss.comm, "child");
    handle_event(&tracker, &loss, sizeof(loss));
    assert(user_read_target == 2);
    loss.pid = 9002;
    loss.ppid = 1;
    loss.start_time_ns = 0;
    loss.parent_start_time_ns = 1;
    strcpy(loss.comm, "unknown");
    handle_event(&tracker, &loss, sizeof(loss));
    assert(user_read_unresolved == 1);
    assert(scoped_user_read_drops(4) == 3);
    assert(scoped_user_read_drops(6) == 5); /* Missing notices remain losses. */
    assert(scoped_user_read_drops(1) > 0); /* Inconsistent accounting fails closed. */
    fflush(stdout);
    assert(dup2(saved_stdout, STDOUT_FILENO) >= 0);
    close(saved_stdout);
    rewind(capture);
    assert(fgetc(capture) == EOF);
    fclose(capture);
    uint64_t reasons[DROP_REASON_MAX] = {2, 3, 6};
    capture = tmpfile();
    assert(capture);
    int saved_stderr = dup(STDERR_FILENO);
    fflush(stderr);
    assert(dup2(fileno(capture), STDERR_FILENO) >= 0);
    print_loss_stats(reasons);
    fflush(stderr);
    assert(dup2(saved_stderr, STDERR_FILENO) >= 0);
    close(saved_stderr);
    rewind(capture);
    assert(fgets(output, sizeof(output), capture));
    assert(strstr(output, "\"dropped_events\":10"));
    assert(strstr(output, "\"global_user_read_failed\":6"));
    assert(strstr(output, "\"user_read_outside_target\":1"));
    assert(strstr(output, "\"user_read_unattributed\":3"));
    fclose(capture);
    puts("test_process_capture: ok");
    return 0;
}
