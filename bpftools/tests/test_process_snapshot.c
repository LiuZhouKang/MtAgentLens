// SPDX-License-Identifier: Apache-2.0 OR MIT
#define _GNU_SOURCE
#include <sys/types.h>
#include <stdbool.h>
#include <stdint.h>
#include <unistd.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include "process_filter.h"
#include "process_snapshot.h"

static struct event record(int pid, int ppid, unsigned long long start, unsigned long long parent, const char *comm) {
    struct event e = {.type=EVENT_TYPE_PROCESS_SNAPSHOT,.pid=pid,.ppid=ppid,.start_time_ns=start,.parent_start_time_ns=parent};
    snprintf(e.comm,sizeof(e.comm),"%s",comm);
    e.pid_namespace.device=5; e.pid_namespace.inode=123; e.pid_namespace.tgid=pid; e.pid_namespace.error=0;
    return e;
}
int main(void) {
    struct event rows[] = {record(103,102,3,2,"leaf"),record(102,101,2,1,"child"),record(101,1,1,99,"watched")};
    struct pid_tracker tracker;char *filters[]={"watched"};
    pid_tracker_init(&tracker,filters,1,FILTER_MODE_FILTER,0);
    assert(seed_process_snapshot(&tracker,rows,3,0)==0);
    assert(pid_tracker_is_tracked(&tracker,103));
    assert(pid_tracker_parent_matches(&tracker,101,1));
    rows[0].parent_start_time_ns=999;
    pid_tracker_init(&tracker,filters,1,FILTER_MODE_FILTER,0);
    assert(seed_process_snapshot(&tracker,rows,3,0)==0);
    assert(!pid_tracker_is_tracked(&tracker,103));
    /* A local namespace PID must be translated to the kernel PID first. */
    rows[2].pid_namespace.tgid=7;
    pid_tracker_init(&tracker,NULL,0,FILTER_MODE_FILTER,7);
    assert(seed_process_snapshot(&tracker,rows,3,7)==0);
    assert(tracker.target_pid==101);
    assert(pid_tracker_is_tracked(&tracker,102));
    pid_tracker_remove(&tracker,101);
    assert(tracker.target_pid==0);
    pid_tracker_init(&tracker,NULL,0,FILTER_MODE_FILTER,9);
    assert(seed_process_snapshot(&tracker,rows,3,9)<0);
    struct event duplicate[] = {rows[2], rows[2]};
    duplicate[1].start_time_ns++;
    assert(seed_process_snapshot(&tracker,duplicate,2,0)==-ESTALE);
    /* Inventory overflow must be explicit, never silently complete. */
    size_t capacity=sizeof(tracker.entries)/sizeof(tracker.entries[0]);
    struct event *many=calloc(capacity+1,sizeof(*many));assert(many);
    for (size_t i=0;i<=capacity;i++) many[i]=record(1000+i,1,100+i,99,"watched");
    pid_tracker_init(&tracker,NULL,0,FILTER_MODE_ALL,0);
    assert(seed_process_snapshot(&tracker,many,capacity+1,0)==-ENOSPC);free(many);
    /* No successful framing from an incomplete binary record. */
    int fds[2];assert(pipe(fds)==0);assert(write(fds[1],rows,sizeof(rows)-1)==sizeof(rows)-1);close(fds[1]);
    struct process_snapshot_batch batch={0};
    assert(read_process_snapshot_fd(fds[0],&batch)<0);close(fds[0]);free_process_snapshot(&batch);
    assert(pipe(fds)==0);assert(write(fds[1],rows,sizeof(rows))==sizeof(rows));close(fds[1]);
    assert(read_process_snapshot_fd(fds[0],&batch)==0);close(fds[0]);assert(batch.count==3);free_process_snapshot(&batch);
    struct event bad=rows[2];memset(bad.comm,'x',sizeof(bad.comm));
    assert(pipe(fds)==0);assert(write(fds[1],&bad,sizeof(bad))==sizeof(bad));close(fds[1]);
    assert(read_process_snapshot_fd(fds[0],&batch)==-EPROTO);close(fds[0]);free_process_snapshot(&batch);
    puts("test_process_snapshot: ok");return 0;
}
