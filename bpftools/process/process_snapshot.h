/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
#ifndef __PROCESS_SNAPSHOT_H
#define __PROCESS_SNAPSHOT_H

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "process.h"
#include "process_filter.h"

#define PROCESS_SNAPSHOT_MAX_RECORDS 8192

struct process_snapshot_batch {
	struct event *events;
	size_t count;
};

static inline void free_process_snapshot(struct process_snapshot_batch *batch)
{
	if (!batch)
		return;
	free(batch->events);
	batch->events = NULL;
	batch->count = 0;
}

/* Read sizeof(struct event) binary records until EOF. The fd stays owned by
 * the caller. Any partial record is an error: a truncated snapshot must not
 * be mistaken for a complete one. */
static inline int read_process_snapshot_fd(int fd,
					   struct process_snapshot_batch *batch)
{
	struct event *events = NULL;
	size_t count = 0;

	if (!batch)
		return -EINVAL;
	free_process_snapshot(batch);
	if (fd < 0)
		return -EBADF;

	for (;;) {
		struct event ev;
		size_t got = 0;
		bool eof = false;

		while (got < sizeof(ev)) {
			ssize_t n = read(fd, (char *)&ev + got, sizeof(ev) - got);

			if (n < 0) {
				int err = errno;

				if (err == EINTR)
					continue;
				free(events);
				return -err;
			}
			if (n == 0) {
				if (got == 0) {
					eof = true;
					break;
				}
				free(events);
				return -EPROTO;
			}
			got += (size_t)n;
		}
		if (eof)
			break;

		if (ev.type != EVENT_TYPE_PROCESS_SNAPSHOT ||
		    ev.pid <= 0 || ev.start_time_ns == 0 || !memchr(ev.comm, '\0', sizeof(ev.comm))) {
			free(events);
			return -EPROTO;
		}

		if (count >= PROCESS_SNAPSHOT_MAX_RECORDS) {
			free(events);
			return -E2BIG;
		}

		if (!events) {
			events = malloc(PROCESS_SNAPSHOT_MAX_RECORDS *
					sizeof(*events));
			if (!events)
				return -ENOMEM;
		}
		events[count++] = ev;
	}

	batch->events = events;
	batch->count = count;
	return 0;
}

static inline bool process_snapshot_is_root(struct pid_tracker *tracker,
					    const struct event *ev)
{
	if (tracker->filter_mode == FILTER_MODE_ALL ||
	    tracker->filter_mode == FILTER_MODE_PROC)
		return true;
	if (tracker->target_pid > 0 && ev->pid == tracker->target_pid)
		return true;
	return tracker->command_filter_count > 0 &&
	       command_matches_any_filter(ev->comm, tracker->command_filters,
					  tracker->command_filter_count);
}

/* Translate the namespace-local target to a kernel PID using only an explicit
 * pid namespace mapping carried by a snapshot record. */
static inline int process_snapshot_resolve_target(struct pid_tracker *tracker,
						  const struct event *events,
						  size_t count,
						  pid_t native_target_pid)
{
	bool found = false;
	int target_pid = 0;
	unsigned long long target_start = 0;

	for (size_t i = 0; i < count; i++) {
		const struct event *ev = &events[i];

		if (ev->pid_namespace.error != 0 ||
		    ev->pid_namespace.device == 0 ||
		    ev->pid_namespace.inode == 0 ||
		    ev->pid_namespace.tgid != (unsigned int)native_target_pid)
			continue;

		if (!found) {
			found = true;
			target_pid = ev->pid;
			target_start = ev->start_time_ns;
		} else if (ev->pid != target_pid ||
			   ev->start_time_ns != target_start) {
			return -ESTALE;
		}
	}

	if (!found)
		return -ESRCH;
	tracker->target_pid = target_pid;
	return 0;
}

static inline int seed_process_snapshot(struct pid_tracker *tracker,
					const struct event *events,
					size_t count,
					pid_t native_target_pid)
{
	int err;

	if (!tracker || (!events && count > 0) || native_target_pid < 0)
		return -EINVAL;

	for (size_t i = 0; i < count; i++) {
		for (size_t j = 0; j < i; j++)
			if (events[i].pid == events[j].pid &&
			    events[i].start_time_ns != events[j].start_time_ns)
				return -ESTALE;
		if (events[i].type != EVENT_TYPE_PROCESS_SNAPSHOT ||
		    events[i].pid <= 0 || events[i].start_time_ns == 0)
			return -EINVAL;
	}

	if (native_target_pid == 0) {
		tracker->target_pid = 0;
	} else {
		err = process_snapshot_resolve_target(tracker, events, count,
						     native_target_pid);
		if (err)
			return err;
	}

	for (size_t i = 0; i < count; i++) {
		const struct event *ev = &events[i];

		if (!process_snapshot_is_root(tracker, ev))
			continue;
		if (!pid_tracker_add(tracker, ev->pid, ev->ppid))
			return -ENOSPC;
		pid_tracker_observe_identity(tracker, ev->pid,
					     ev->start_time_ns);
	}

	for (;;) {
		bool progressed = false;

		for (size_t i = 0; i < count; i++) {
			const struct event *ev = &events[i];

			if (pid_tracker_is_tracked(tracker, ev->pid))
				continue;
			if (ev->parent_start_time_ns == 0)
				continue;
			if (!pid_tracker_parent_matches(tracker, ev->ppid,
							ev->parent_start_time_ns))
				continue;
			if (!pid_tracker_add(tracker, ev->pid, ev->ppid))
				return -ENOSPC;
			pid_tracker_observe_identity(tracker, ev->pid,
						     ev->start_time_ns);
			progressed = true;
		}
		if (!progressed)
			break;
	}

	return 0;
}

#endif /* __PROCESS_SNAPSHOT_H */
