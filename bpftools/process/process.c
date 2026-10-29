// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
/* Copyright (c) 2020 Facebook */
#include <argp.h>
#include <signal.h>
#include <stdio.h>
#include <time.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <dirent.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include "process.h"
#include "process.skel.h"
#include "process_utils.h"
#include "process_filter.h"
#include "json_escape.h"
#include "process_json.h"
#include "process_snapshot.h"

#define MAX_COMMAND_LIST 256
#define FILE_DEDUP_WINDOW_NS 60000000000ULL  // 60 seconds in nanoseconds
// A real agent run can open >1024 distinct files within the 60 s dedup window;
// the previous 1024-entry table lost aggregation for 490 FILE_OPEN events in a
// 140 s run. Entries are ~200 bytes, so 4096 costs under 1 MiB.
#define MAX_FILE_HASHES 4096

// Rate limiting per second
#define MAX_PID_LIMITS 256
#define MAX_DISTINCT_FILES_PER_SEC 30

struct per_second_limit {
    pid_t pid;
    uint64_t current_second;
    uint32_t distinct_file_count;
    bool should_warn_next;
};

// Simple hash table for FILE_OPEN deduplication
struct file_hash_entry {
    uint64_t hash;
    uint64_t timestamp_ns;
    uint32_t count;
    pid_t pid;
    /* Carried from the originating event so synthesized aggregation events
     * report the real process identity instead of zero. */
    uint64_t start_time_ns;
    uint64_t parent_start_time_ns;
    char comm[TASK_COMM_LEN];
    char filepath[MAX_FILENAME_LEN];
    int flags;
    pid_t ppid;
    long long syscall_ret;
    bool syscall_completed;
};

static struct file_hash_entry file_hashes[MAX_FILE_HASHES];
static int hash_count = 0;

static struct per_second_limit pid_limits[MAX_PID_LIMITS];
static int pid_limit_count = 0;

/* Userspace-side losses must be visible. The kernel ring buffer reports its
 * own drops; a file open rejected by the rate limit or left untracked because
 * the aggregation table was full disappeared without a trace, so a "0 dropped"
 * report could not distinguish a quiet run from a throttled one. */
static uint64_t rate_limit_drops = 0;
static uint64_t aggregation_table_full = 0;

/* Raw user-read failures are counted in the kernel map even when the notice
 * ringbuf reserve fails. The notice counters below only split the subset that
 * reached userspace; `scoped_user_read_drops` folds the missing notices back in
 * so a reserve failure can never look like a healthy zero. */
uint64_t user_read_target = 0;
uint64_t user_read_outside = 0;
uint64_t user_read_unresolved = 0;
bool user_read_scope_consistent = true;

static struct env {
	bool verbose;
	long min_duration_ms;
	char *command_list[MAX_COMMAND_LIST];
	int command_count;
	enum filter_mode filter_mode;
	pid_t pid;
} env = {
	.verbose = false,
	.min_duration_ms = 0,
	.command_count = 0,
	.filter_mode = FILTER_MODE_PROC,
	.pid = 0
};

/* Global PID tracker for userspace filtering */
static struct pid_tracker pid_tracker;

const char *argp_program_version = "process-tracer 1.0";
const char *argp_program_bug_address = "<bpf@vger.kernel.org>";
const char argp_program_doc[] =
"BPF process tracer with 3-level filtering.\n"
"\n"
"It traces process start and exits with configurable filtering levels.\n"
"Shows associated information (filename, process duration, PID and PPID, etc).\n"
"\n"
"USAGE: ./process [-d <min-duration-ms>] [-c <command1,command2,...>] [-p <pid>] [-m <mode>] [-v]\n"
"\n"
"FILTER MODES:\n"
"  0 (all):    Trace all processes and all read/write operations\n"
"  1 (proc):   Trace all processes but only read/write for tracked PIDs (default)\n"
"  2 (filter): Only trace processes matching filters and their read/write\n"
"\n"
"EXAMPLES:\n"
"  ./process -m 0                   # Trace everything\n"
"  ./process -m 1                   # Trace all processes, selective read/write\n"
"  ./process -c \"claude,python\"    # Trace only claude/python processes\n"
"  ./process -c \"ssh\" -d 1000     # Trace ssh processes lasting > 1 second\n"
"  ./process -p 1234                # Trace only PID 1234\n";

static const struct argp_option opts[] = {
	{ "verbose", 'v', NULL, 0, "Verbose debug output" },
	{ "duration", 'd', "DURATION-MS", 0, "Minimum process duration (ms) to report" },
	{ "commands", 'c', "COMMAND-LIST", 0, "Comma-separated list of commands to trace (e.g., \"claude,python\")" },
	{ "pid", 'p', "PID", 0, "Trace this PID only" },
	{ "mode", 'm', "FILTER-MODE", 0, "Filter mode: 0=all, 1=proc, 2=filter (default=2)" },
	{ "all", 'a', NULL, 0, "Deprecated: use -m 0 instead" },
	{},
};

static error_t parse_arg(int key, char *arg, struct argp_state *state)
{
	char *token;
	char *saveptr;
	
	switch (key) {
	case 'v':
		env.verbose = true;
		break;
	case 'd':
		errno = 0;
		env.min_duration_ms = strtol(arg, NULL, 10);
		if (errno || env.min_duration_ms <= 0) {
			fprintf(stderr, "Invalid duration: %s\n", arg);
			argp_usage(state);
		}
		break;
	case 'p':
		errno = 0;
		env.pid = (pid_t)strtol(arg, NULL, 10);
		if (errno || env.pid <= 0) {
			fprintf(stderr, "Invalid PID: %s\n", arg);
			argp_usage(state);
		}
		env.filter_mode = FILTER_MODE_FILTER;
		break;
	case 'a':
		env.filter_mode = FILTER_MODE_ALL;
		break;
	case 'm':
		errno = 0;
		int mode = strtol(arg, NULL, 10);
		if (errno || mode < 0 || mode > 2) {
			fprintf(stderr, "Invalid filter mode: %s (must be 0, 1, or 2)\n", arg);
			argp_usage(state);
		}
		env.filter_mode = (enum filter_mode)mode;
		break;
	case 'c':
		env.filter_mode = FILTER_MODE_FILTER;
		/* Parse comma-separated command list */
		char *arg_copy = strdup(arg);
		if (!arg_copy) {
			fprintf(stderr, "Memory allocation failed\n");
			return ARGP_ERR_UNKNOWN;
		}
		
		token = strtok_r(arg_copy, ",", &saveptr);
		while (token && env.command_count < MAX_COMMAND_LIST) {
			/* Remove leading/trailing whitespace */
			while (*token == ' ' || *token == '\t') token++;
			char *end = token + strlen(token) - 1;
			while (end > token && (*end == ' ' || *end == '\t')) end--;
			*(end + 1) = '\0';
			
			if (strlen(token) > 0) {
				env.command_list[env.command_count] = strdup(token);
				if (!env.command_list[env.command_count]) {
					fprintf(stderr, "Memory allocation failed\n");
					free(arg_copy);
					return ARGP_ERR_UNKNOWN;
				}
				env.command_count++;
			}
			token = strtok_r(NULL, ",", &saveptr);
		}
		free(arg_copy);
		break;
	case ARGP_KEY_ARG:
		argp_usage(state);
		break;
	default:
		return ARGP_ERR_UNKNOWN;
	}
	return 0;
}

static const struct argp argp = {
	.options = opts,
	.parser = parse_arg,
	.doc = argp_program_doc,
};

static int libbpf_print_fn(enum libbpf_print_level level, const char *format, va_list args)
{
	if (level == LIBBPF_DEBUG && !env.verbose)
		return 0;
	return vfprintf(stderr, format, args);
}

static volatile bool exiting = false;

// Rate limiting check function
static bool should_rate_limit_file(const struct event *e, uint64_t timestamp_ns, bool *add_warning) {
    uint64_t current_second = timestamp_ns / 1000000000ULL;  // Convert to seconds
    *add_warning = false;
    
    // Find/create entry for this PID
    struct per_second_limit *limit = NULL;
    for (int i = 0; i < pid_limit_count; i++) {
        if (pid_limits[i].pid == e->pid) {
            limit = &pid_limits[i];
            break;
        }
    }
    
    if (!limit && pid_limit_count < MAX_PID_LIMITS) {
        limit = &pid_limits[pid_limit_count++];
        limit->pid = e->pid;
        limit->current_second = current_second;
        limit->distinct_file_count = 0;
        limit->should_warn_next = false;
    }
    
    if (!limit) return false;
    
    // New second - reset and check if we need to warn
    if (limit->current_second != current_second) {
        if (limit->should_warn_next) {
            *add_warning = true;
            limit->should_warn_next = false;
        }
        limit->current_second = current_second;
        limit->distinct_file_count = 0;
    }
    
    limit->distinct_file_count++;
    
    // Check if over limit
    if (limit->distinct_file_count > MAX_DISTINCT_FILES_PER_SEC) {
        limit->should_warn_next = true;
        *add_warning = true;
        /* Security evidence must not disappear after an arbitrary path quota.
         * Keep the pressure warning, and capture every distinct operation. */
        return false;
    }
    
    return false;
}



// Hash function for FILE_OPEN events
static uint64_t hash_file_open(const struct event *e)
{
	uint64_t hash = 5381;
	hash = ((hash << 5) + hash) + e->pid;
	
	// Hash the filepath for FILE_OPEN events
	const char *str = e->file_op.filepath;
	while (*str) {
		hash = ((hash << 5) + hash) + *str++;
	}
	
	return hash;
}

// Get count for FILE_OPEN operations (handles deduplication internally)
static uint32_t get_file_open_count(const struct event *e, uint64_t timestamp_ns, char *warning_msg, size_t warning_msg_size)
{
	if (e->type != EVENT_TYPE_FILE_OPERATION || !e->file_op.is_open) {
		return 1;  // Return count of 1 for non-FILE_OPEN operations
	}
	
	// Initialize warning message
	warning_msg[0] = '\0';
	
	bool add_warning = false;
	uint64_t hash = hash_file_open(e);
	
	// Clean up expired entries first
	for (int i = 0; i < hash_count; i++) {
		if (timestamp_ns - file_hashes[i].timestamp_ns > FILE_DEDUP_WINDOW_NS) {
			// Print aggregated result if count > 1
			if (file_hashes[i].count > 1) {
				if (env.verbose) {
					fprintf(stderr, "DEBUG: Aggregation window expired for FILE_OPEN, count=%u\n", 
						file_hashes[i].count);
				}
				// Create fake event structure for aggregated output
				struct event fake_event = {
					.type = EVENT_TYPE_FILE_OPERATION,
					.pid = file_hashes[i].pid,
					.ppid = file_hashes[i].ppid,
					.exit_code = 0,
					.duration_ns = 0,
					.start_time_ns = file_hashes[i].start_time_ns,
					.parent_start_time_ns = file_hashes[i].parent_start_time_ns,
					.exit_event = false,
                    .syscall_ret = file_hashes[i].syscall_ret,
                    .syscall_completed = file_hashes[i].syscall_completed,
					.file_op = {
						.fd = -1,
						.flags = file_hashes[i].flags,
						.is_open = true
					}
				};
				strncpy(fake_event.comm, file_hashes[i].comm, TASK_COMM_LEN - 1);
				fake_event.comm[TASK_COMM_LEN - 1] = '\0';
				strncpy(fake_event.file_op.filepath, file_hashes[i].filepath, MAX_FILENAME_LEN - 1);
				fake_event.file_op.filepath[MAX_FILENAME_LEN - 1] = '\0';
				print_file_event(&fake_event, timestamp_ns, file_hashes[i].count - 1, "\"window_expired\":true");
			}
			
			// Remove expired entry
			file_hashes[i] = file_hashes[hash_count - 1];
			hash_count--;
			i--;
		}
	}
	
	// Check if this hash already exists
	for (int i = 0; i < hash_count; i++) {
		if (file_hashes[i].hash == hash && file_hashes[i].pid == e->pid &&
            file_hashes[i].start_time_ns == e->start_time_ns &&
            file_hashes[i].flags == e->file_op.flags &&
            file_hashes[i].syscall_completed == e->syscall_completed &&
            file_hashes[i].syscall_ret == e->syscall_ret &&
            strcmp(file_hashes[i].filepath, e->file_op.filepath) == 0) {
			file_hashes[i].count++;
			file_hashes[i].timestamp_ns = timestamp_ns;
			if (env.verbose) {
				fprintf(stderr, "DEBUG: Aggregating FILE_OPEN for PID %d, count now %u\n", 
					e->pid, file_hashes[i].count);
			}
			return 0;  // Return 0 to indicate this should be skipped (duplicate)
		}
	}
	
	// Rate limiting applies to *distinct* files only. Counting before the
	// deduplication check meant repeated opens of one path consumed the
	// budget and pushed legitimate distinct files out of the capture.
	if (should_rate_limit_file(e, timestamp_ns, &add_warning)) {
		rate_limit_drops++;
		return 0;
	}
	if (add_warning) {
		snprintf(warning_msg, warning_msg_size, "\"rate_limit_warning\":\"Previous second exceeded %d file limit\"", MAX_DISTINCT_FILES_PER_SEC);
	}

	// Add new hash entry if we have space
	if (hash_count < MAX_FILE_HASHES) {
		file_hashes[hash_count].hash = hash;
		file_hashes[hash_count].timestamp_ns = timestamp_ns;
		file_hashes[hash_count].count = 1;
		file_hashes[hash_count].pid = e->pid;
		file_hashes[hash_count].start_time_ns = e->start_time_ns;
		file_hashes[hash_count].parent_start_time_ns = e->parent_start_time_ns;
		strncpy(file_hashes[hash_count].comm, e->comm, TASK_COMM_LEN - 1);
		file_hashes[hash_count].comm[TASK_COMM_LEN - 1] = '\0';
		strncpy(file_hashes[hash_count].filepath, e->file_op.filepath, MAX_FILENAME_LEN - 1);
		file_hashes[hash_count].filepath[MAX_FILENAME_LEN - 1] = '\0';
		file_hashes[hash_count].flags = e->file_op.flags;
        file_hashes[hash_count].ppid = e->ppid;
        file_hashes[hash_count].syscall_ret = e->syscall_ret;
        file_hashes[hash_count].syscall_completed = e->syscall_completed;
		hash_count++;
		if (env.verbose) {
			fprintf(stderr, "DEBUG: Created new aggregation entry for FILE_OPEN, PID %d (total entries: %d)\n", 
				e->pid, hash_count);
		}
	} else {
		aggregation_table_full++;
		if (env.verbose) {
			fprintf(stderr, "DEBUG: Max aggregation entries reached (%d), cannot track more\n", MAX_FILE_HASHES);
		}
		// The event itself is still reported; only aggregation is lost.
	}
	
	return 1;  // Return count of 1 for first occurrence
}

// Flush all pending FILE_OPEN aggregations for a specific PID
static void flush_pid_file_opens(pid_t pid, uint64_t timestamp_ns)
{
	int flushed_count = 0;
	for (int i = 0; i < hash_count; i++) {
		if (file_hashes[i].pid == pid && file_hashes[i].count > 1) {
			if (env.verbose) {
				fprintf(stderr, "DEBUG: Flushing FILE_OPEN aggregation on process exit, PID %d, count=%u\n", 
					pid, file_hashes[i].count);
			}
			// Create fake event structure for aggregated output
			struct event fake_event = {
				.type = EVENT_TYPE_FILE_OPERATION,
				.pid = file_hashes[i].pid,
				.ppid = file_hashes[i].ppid,
				.exit_code = 0,
				.duration_ns = 0,
				.start_time_ns = file_hashes[i].start_time_ns,
				.parent_start_time_ns = file_hashes[i].parent_start_time_ns,
				.exit_event = false,
                    .syscall_ret = file_hashes[i].syscall_ret,
                    .syscall_completed = file_hashes[i].syscall_completed,
				.file_op = {
					.fd = -1,
					.flags = file_hashes[i].flags,
					.is_open = true
				}
			};
			strncpy(fake_event.comm, file_hashes[i].comm, TASK_COMM_LEN - 1);
			fake_event.comm[TASK_COMM_LEN - 1] = '\0';
			strncpy(fake_event.file_op.filepath, file_hashes[i].filepath, MAX_FILENAME_LEN - 1);
			fake_event.file_op.filepath[MAX_FILENAME_LEN - 1] = '\0';
			print_file_event(&fake_event, timestamp_ns, file_hashes[i].count - 1, "\"reason\":\"process_exit\"");
			flushed_count++;
		}
	}
	
	// Remove all entries for this PID
	int removed_count = 0;
	for (int i = 0; i < hash_count; i++) {
		if (file_hashes[i].pid == pid) {
			// Remove this entry by moving last entry to this position
			file_hashes[i] = file_hashes[hash_count - 1];
			hash_count--;
			removed_count++;
			i--; // Recheck this position since we moved an entry here
		}
	}
	
	if (env.verbose && removed_count > 0) {
		fprintf(stderr, "DEBUG: Cleared %d FILE_OPEN aggregation entries for PID %d (flushed %d)\n", 
			removed_count, pid, flushed_count);
	}
}

static void sig_handler(int sig)
{
	exiting = true;
}

/*
 * Locate a bash binary to attach the readline uretprobe to.
 *
 * The path must not be baked into the ELF section name: libbpf would resolve
 * it at load time and abort the whole attach sequence if it is missing, taking
 * every other process probe down with it (see the note on `bash_readline` in
 * process.bpf.c). Resolving here keeps a missing bash a visible, best-effort
 * degradation.
 *
 * Order: $MTAGENTLENS_BASH_PATH override, then the conventional locations,
 * then $PATH, then /etc/shells. The last two are what make this work on
 * distributions that keep bash outside /usr/bin (NixOS puts it in
 * /nix/store/<hash>/bin).
 */
static const char *resolve_bash_path(void)
{
	static char buf[4096];
	const char *override = getenv("MTAGENTLENS_BASH_PATH");
	const char *conventional[] = {
		"/usr/bin/bash", "/bin/bash", "/usr/local/bin/bash", NULL,
	};
	size_t i;

	if (override && access(override, X_OK) == 0)
		return override;

	for (i = 0; conventional[i]; i++) {
		if (access(conventional[i], X_OK) == 0)
			return conventional[i];
	}

	/* $PATH */
	{
		const char *path = getenv("PATH");
		if (path) {
			char *copy = strdup(path);
			if (copy) {
				char *dir = strtok(copy, ":");
				while (dir) {
					snprintf(buf, sizeof(buf), "%s/bash", dir);
					if (access(buf, X_OK) == 0) {
						free(copy);
						return buf;
					}
					dir = strtok(NULL, ":");
				}
				free(copy);
			}
		}
	}

	/* /etc/shells — entries whose basename is exactly "bash". */
	{
		FILE *f = fopen("/etc/shells", "r");
		if (f) {
			char line[4096];
			while (fgets(line, sizeof(line), f)) {
				size_t len = strlen(line);
				while (len && (line[len - 1] == '\n' || line[len - 1] == '\r'))
					line[--len] = '\0';
				if (len == 0 || line[0] == '#')
					continue;
				const char *base = strrchr(line, '/');
				base = base ? base + 1 : line;
				if (strcmp(base, "bash") == 0 && access(line, X_OK) == 0) {
					fclose(f);
					snprintf(buf, sizeof(buf), "%s", line);
					return buf;
				}
			}
			fclose(f);
		}
	}

	return NULL;
}

/*
 * Attach the bash readline probe by hand.
 *
 * Failure is reported and tolerated: bash command capture is one feature of
 * this program, not a prerequisite for the rest of it.
 */
static void attach_bash_readline(struct process_bpf *skel)
{
	const char *bash = resolve_bash_path();
	LIBBPF_OPTS(bpf_uprobe_opts, opts, .func_name = "readline", .retprobe = true);

	if (!bash) {
		fprintf(stderr,
			"Warning: no bash binary found (tried $MTAGENTLENS_BASH_PATH, "
			"/usr/bin/bash, /bin/bash, /usr/local/bin/bash, $PATH, /etc/shells); "
			"bash command capture is disabled, all other probes are active\n");
		return;
	}

	skel->links.bash_readline = bpf_program__attach_uprobe_opts(
		skel->progs.bash_readline, -1 /* all processes */, bash, 0, &opts);

	if (!skel->links.bash_readline) {
		fprintf(stderr,
			"Warning: could not attach the bash readline probe to %s (%s); "
			"bash command capture is disabled, all other probes are active\n",
			bash, strerror(errno));
		return;
	}

	fprintf(stderr, "process: bash readline probe attached to %s\n", bash);
}

/* A notice is a measured failure, not an action. Use the same selection rules
 * as the live tracker: tracked identity, the -p root, a tracked parent with a
 * matching start time, or a command-filter match. */
static bool user_read_notice_selected(struct pid_tracker *tracker,
				      const struct event *e)
{
	if (tracker->filter_mode == FILTER_MODE_ALL ||
	    tracker->filter_mode == FILTER_MODE_PROC)
		return true;

	if (e->pid > 0 && pid_tracker_is_tracked(tracker, e->pid))
		return true;

	if (tracker->target_pid > 0 &&
	    (e->pid == tracker->target_pid || e->ppid == tracker->target_pid))
		return true;

	if (e->ppid > 0 &&
	    pid_tracker_parent_matches(tracker, e->ppid, e->parent_start_time_ns))
		return true;

	return e->comm[0] != '\0' &&
	       command_matches_any_filter(e->comm, tracker->command_filters,
					  tracker->command_filter_count);
}

/* Outside is a positive claim: every identity field must be present. Missing
 * context stays unresolved and is included in the scoped count. */
static bool user_read_notice_identity_complete(const struct event *e)
{
	return e->pid > 0 && e->ppid > 0 &&
	       e->start_time_ns > 0 && e->parent_start_time_ns > 0 &&
	       e->comm[0] != '\0';
}

static void record_user_read_notice(struct pid_tracker *tracker,
				    const struct event *e)
{
	bool selected = user_read_notice_selected(tracker, e);

	if (selected && env.verbose) {
		fprintf(stderr, "USER_READ_FAILED pid=%d ppid=%d comm=%s syscall=%d addr=0x%llx\n",
			e->pid, e->ppid, e->comm, e->file_op.flags,
			(unsigned long long)e->syscall_ret);
	}
	if (selected) {
		if (e->pid > 0) {
			if (!pid_tracker_is_tracked(tracker, e->pid))
				pid_tracker_add(tracker, e->pid, e->ppid);
			pid_tracker_observe_identity(tracker, e->pid,
						     e->start_time_ns);
		}
		user_read_target++;
		return;
	}

	if (user_read_notice_identity_complete(e))
		user_read_outside++;
	else
		user_read_unresolved++;
}

uint64_t scoped_user_read_drops(uint64_t global)
{
	uint64_t observed = user_read_target + user_read_outside +
			    user_read_unresolved;
	uint64_t missing;

	if (observed > global) {
		/* Notices cannot outnumber the kernel counter. Fail closed:
		 * count every observed failure and exclude nothing. */
		user_read_scope_consistent = false;
		return observed;
	}

	user_read_scope_consistent = true;
	missing = global - observed;
	return user_read_target + user_read_unresolved + missing;
}

static uint64_t tracker_stale_events;

void print_loss_stats(const uint64_t reasons[DROP_REASON_MAX])
{
	uint64_t ringbuf_dropped = reasons[DROP_RINGBUF_FULL];
	uint64_t pending_dropped = reasons[DROP_PENDING_TABLE_FULL];
	uint64_t global_user_read = reasons[DROP_USER_READ_FAILED];
	uint64_t observed = user_read_target + user_read_outside +
			    user_read_unresolved;
	uint64_t missing = global_user_read > observed ?
			   global_user_read - observed : 0;
	uint64_t scoped = scoped_user_read_drops(global_user_read);
	uint64_t unattributed = user_read_unresolved + missing;
	uint64_t dropped = ringbuf_dropped + pending_dropped + scoped + tracker_stale_events;
	uint64_t raw_dropped = ringbuf_dropped + pending_dropped +
			       global_user_read;

	fprintf(stderr, "{\"event\":\"PROCESS_RINGBUF_STATS\","
		"\"dropped_events\":%llu,\"global_dropped_events\":%llu,"
		"\"ringbuf_full_dropped\":%llu,\"pending_table_dropped\":%llu,"
		"\"user_read_dropped\":%llu,"
		"\"global_user_read_failed\":%llu,"
		"\"user_read_target\":%llu,\"user_read_outside_target\":%llu,"
		"\"user_read_unresolved\":%llu,\"user_read_unattributed\":%llu,"
		"\"user_read_scope_consistent\":%s,"
		"\"tracker_stale_events\":%llu,"
		"\"rate_limit_drops\":%llu,\"aggregation_table_full\":%llu}\n",
		(unsigned long long)dropped,
		(unsigned long long)raw_dropped,
		(unsigned long long)ringbuf_dropped,
		(unsigned long long)pending_dropped,
		(unsigned long long)scoped,
		(unsigned long long)global_user_read,
		(unsigned long long)user_read_target,
		(unsigned long long)user_read_outside,
		(unsigned long long)user_read_unresolved,
		(unsigned long long)unattributed,
		user_read_scope_consistent ? "true" : "false",
		(unsigned long long)tracker_stale_events,
		(unsigned long long)rate_limit_drops,
		(unsigned long long)aggregation_table_full);
}

static void print_dropped_events(struct process_bpf *skel)
{
	uint64_t reasons[DROP_REASON_MAX] = {};
	int map_fd;
	__u32 key;

	if (!skel)
		return;
	map_fd = bpf_map__fd(skel->maps.dropped_events);
	if (map_fd < 0)
		return;
	for (key = 0; key < DROP_REASON_MAX; key++) {
		if (bpf_map_lookup_elem(map_fd, &key, &reasons[key]) != 0)
			return;
	}
	print_loss_stats(reasons);
}

static int handle_event(void *ctx, void *data, size_t data_sz)
{
	const struct event *e = data;
	struct pid_tracker *tracker = (struct pid_tracker *)ctx;

    if (e->type == EVENT_TYPE_PROCESS_SNAPSHOT) {
        if (tracker->filter_mode == FILTER_MODE_ALL ||
            pid_tracker_parent_matches(tracker, e->pid, e->start_time_ns))
            print_process_snapshot(e);
        return 0;
    }
    /* Buffered events may predate the inventory. Never evict a newer identity. */
    struct tracked_pid_entry *own = pid_tracker_find(tracker, e->pid);
    struct tracked_pid_entry *parent = pid_tracker_find(tracker, e->ppid);
    if ((own && e->start_time_ns > 0 && own->start_time_ns > e->start_time_ns) ||
        (parent && e->parent_start_time_ns > 0 &&
         parent->start_time_ns > e->parent_start_time_ns)) {
        tracker_stale_events++;
        return 0;
    }
    pid_tracker_observe_identity(tracker, e->ppid, e->parent_start_time_ns);

	// Use kernel timestamp from the event instead of generating our own
	uint64_t timestamp_ns = e->timestamp_ns;
    pid_tracker_observe_identity(tracker, e->pid, e->start_time_ns);

	switch (e->type) {
		case EVENT_TYPE_PROCESS:
			if (e->exit_event) {
				// EXIT event: check if tracked before reporting
				bool is_tracked = pid_tracker_is_tracked(tracker, e->pid);

				// Remove from tracker regardless
				pid_tracker_remove(tracker, e->pid);

				// Only report if tracked (or if in ALL/PROC mode)
				if (!is_tracked && tracker->filter_mode == FILTER_MODE_FILTER) {
					break;
				}

				printf("{");
				printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
				printf("\"event\":\"EXIT\",");
				printf("\"comm\":"); json_print_str(e->comm); printf(",");
				printf("\"pid\":%d,", e->pid);
				printf("\"ppid\":%d", e->ppid);
				printf(",\"start_time_ns\":%llu", (unsigned long long)e->start_time_ns);
				printf(",\"parent_start_time_ns\":%llu", (unsigned long long)e->parent_start_time_ns);
				printf(",\"exit_code\":%u", e->exit_code);
				if (e->duration_ns)
					printf(",\"duration_ms\":%llu", e->duration_ns / 1000000);

				// Check if this PID has pending rate limit warning
				bool add_warning = false;
				for (int i = 0; i < pid_limit_count; i++) {
					if (pid_limits[i].pid == e->pid && pid_limits[i].should_warn_next) {
						add_warning = true;
						// Remove this entry
						pid_limits[i] = pid_limits[--pid_limit_count];
						break;
					}
				}

				if (add_warning) {
					printf(",\"rate_limit_warning\":\"Process had %d+ file ops per second\"", MAX_DISTINCT_FILES_PER_SEC);
				}
				printf("}\n");
				fflush(stdout);

				// Flush all pending FILE_OPEN aggregations for this PID
				flush_pid_file_opens(e->pid, timestamp_ns);
			} else {
				// EXEC event: check if should track
				if (should_track_process(tracker, e->comm, e->pid, e->ppid)) {
					pid_tracker_add(tracker, e->pid, e->ppid);
                    pid_tracker_observe_identity(tracker, e->pid, e->start_time_ns);

					// Report the EXEC event
					printf("{");
					printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
					printf("\"event\":\"EXEC\",\"outcome\":\"succeeded\",\"syscall_ret\":0,");
					printf("\"comm\":"); json_print_str(e->comm); printf(",");
					printf("\"pid\":%d,", e->pid);
					printf("\"ppid\":%d", e->ppid);
					printf(",\"start_time_ns\":%llu", (unsigned long long)e->start_time_ns);
					printf(",\"parent_start_time_ns\":%llu", (unsigned long long)e->parent_start_time_ns);
					printf(",\"filename\":"); json_print_str(e->filename);
					printf(",\"full_command\":"); json_print_str(e->full_command);
					print_exec_metadata(e);
					printf("}\n");
					fflush(stdout);
				} else if (tracker->filter_mode == FILTER_MODE_FILTER) {
					// In filter mode, don't report untracked processes
					break;
				} else {
					// In ALL/PROC modes, report all processes but add them to tracker for PROC mode
					if (tracker->filter_mode == FILTER_MODE_PROC) {
						pid_tracker_add(tracker, e->pid, e->ppid);
                    pid_tracker_observe_identity(tracker, e->pid, e->start_time_ns);
					}

					printf("{");
					printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
					printf("\"event\":\"EXEC\",\"outcome\":\"succeeded\",\"syscall_ret\":0,");
					printf("\"comm\":"); json_print_str(e->comm); printf(",");
					printf("\"pid\":%d,", e->pid);
					printf("\"ppid\":%d", e->ppid);
					printf(",\"start_time_ns\":%llu", (unsigned long long)e->start_time_ns);
					printf(",\"parent_start_time_ns\":%llu", (unsigned long long)e->parent_start_time_ns);
					printf(",\"filename\":"); json_print_str(e->filename);
					printf(",\"full_command\":"); json_print_str(e->full_command);
					print_exec_metadata(e);
					printf("}\n");
					fflush(stdout);
				}
			}
			break;

		case EVENT_TYPE_BASH_READLINE:
			// Check if should report bash readline for this PID
			if (!should_report_bash_readline(tracker, e->pid)) {
				break;
			}

			printf("{");
			printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
			printf("\"event\":\"BASH_READLINE\",");
			printf("\"comm\":"); json_print_str(e->comm); printf(",");
			printf("\"pid\":%d,", e->pid);
			printf(",\"start_time_ns\":%llu", (unsigned long long)e->start_time_ns);
			printf(",\"parent_start_time_ns\":%llu", (unsigned long long)e->parent_start_time_ns);
			printf(",\"command\":"); json_print_str(e->command);
			printf("}\n");
			fflush(stdout);
			break;

		case EVENT_TYPE_NETWORK_OPERATION:
			if (!should_report_network(tracker, e->pid)) {
				if (!pid_tracker_is_tracked(tracker, e->pid)
				    && pid_tracker_parent_matches(tracker, e->ppid, e->parent_start_time_ns)) {
					pid_tracker_add(tracker, e->pid, e->ppid);
                    pid_tracker_observe_identity(tracker, e->pid, e->start_time_ns);
				}
				if (!should_report_network(tracker, e->pid)) {
					break;
				}
			}
			print_network_event(e, timestamp_ns);
			break;

		case EVENT_TYPE_FILE_OPERATION:
			// Only handle FILE_OPEN events, skip FILE_CLOSE
			if (!e->file_op.is_open) {
				break;
			}

			// Check if should report file ops for this PID.
			//
			// Self-heal a missing exec record: under load an EXEC event can
			// be dropped, and the PID is then never tracked — every file it
			// opens afterwards is filtered out, so a child process's
			// credential read disappears while its parent is tracked. If the
			// event's direct parent is tracked, adopt the child here.
			if (!should_report_file_ops(tracker, e->pid)) {
				if (!pid_tracker_is_tracked(tracker, e->pid)
				    && pid_tracker_parent_matches(tracker, e->ppid, e->parent_start_time_ns)) {
					pid_tracker_add(tracker, e->pid, e->ppid);
                    pid_tracker_observe_identity(tracker, e->pid, e->start_time_ns);
				}
				if (!should_report_file_ops(tracker, e->pid)) {
					break;
				}
			}

			// Delete/truncate describe a single destructive operation:
			// they have no open-count semantics and must not be folded
			// into the per-path dedup window used for opens.
			if (e->file_op.op != FILE_OP_OPEN) {
				print_file_event(e, timestamp_ns, 1, NULL);
				break;
			}

			// Get count for this FILE_OPEN operation
			char warning_msg[128];
			uint32_t count = get_file_open_count(e, timestamp_ns, warning_msg, sizeof(warning_msg));

			// Skip if this is a duplicate (count == 0)
			if (count == 0) {
				break;
			}

			// Report the FILE_OPEN event with count
			print_file_event(e, timestamp_ns, count, strlen(warning_msg) > 0 ? warning_msg : NULL);
			break;

		case EVENT_TYPE_CAPTURE_LOSS:
			/* Consumption only: no action line, no stdout. A loss
			 * notice updates the scoped counters and may enroll the
			 * selected process just like a regular event. */
			if (e->capture_loss_reason == DROP_USER_READ_FAILED)
				record_user_read_notice(tracker, e);
			break;

		default:
			// For unknown events, always report immediately
			printf("{");
			printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
			printf("\"event\":\"UNKNOWN\",");
			printf("\"event_type\":%d", e->type);
			printf("}\n");
			fflush(stdout);
			break;
	}

	return 0;
}

static int collect_initial_snapshot(struct process_bpf *skel, bool supported)
{
    struct process_snapshot_batch batch = {0};
    struct bpf_link *link = NULL;
    size_t records = 0, emitted = 0;
    int fd = -1, err = -EOPNOTSUPP;
    if (!supported)
        goto report;
    link = bpf_program__attach_iter(skel->progs.snapshot_tasks, NULL);
    err = libbpf_get_error(link);
    if (err) { link = NULL; goto report; }
    fd = bpf_iter_create(bpf_link__fd(link));
    if (fd < 0) { err = -errno; goto report; }
    err = read_process_snapshot_fd(fd, &batch);
    records = batch.count;
    if (err)
        goto report;
    err = seed_process_snapshot(&pid_tracker, batch.events, batch.count, env.pid);
    if (err)
        goto report;
    for (size_t i = 0; i < batch.count; i++) {
        const struct event *e = &batch.events[i];
        if (pid_tracker.filter_mode == FILTER_MODE_ALL ||
            pid_tracker_parent_matches(&pid_tracker, e->pid, e->start_time_ns)) {
            print_process_snapshot(e);
            emitted++;
        }
    }
report:
    if (err)
        pid_tracker_init(&pid_tracker, env.command_list, env.command_count, env.filter_mode, 0);
    fprintf(stderr, "{\"event\":\"PROCESS_SNAPSHOT_STATS\",\"complete\":%s,\"error\":%d,\"records\":%zu,\"emitted\":%zu}\n",
            err ? "false" : "true", err, records, emitted);
    if (fd >= 0) close(fd);
    bpf_link__destroy(link);
    free_process_snapshot(&batch);
    return err;
}

int main(int argc, char **argv)
{
	struct ring_buffer *rb = NULL;
	struct process_bpf *skel;
	int err;
    bool snapshot_supported;

	/* Parse command line arguments */
	err = argp_parse(&argp, argc, argv, 0, NULL, NULL);
	if (err)
		return err;

	/* filter_mode is set via -m flag or -a flag, defaults to FILTER_MODE_FILTER */

	/* Initialize userspace PID tracker */
	pid_tracker_init(&pid_tracker, env.command_list, env.command_count, env.filter_mode, 0);

	/* Set up libbpf errors and debug info callback */
	libbpf_set_print(libbpf_print_fn);

	/* Cleaner handling of Ctrl-C */
	signal(SIGINT, sig_handler);
	signal(SIGTERM, sig_handler);

	/* Load and verify BPF application */
	skel = process_bpf__open();
	if (!skel) {
		fprintf(stderr, "Failed to open and load BPF skeleton\n");
		return 1;
	}

	/* Parameterize BPF code with minimum duration */
	skel->rodata->min_duration_ns = env.min_duration_ms * 1000000ULL;

	/* Pin the active PID namespace device for EXEC validation. A missing
	 * namespace leaves this at 0: capture continues and userspace reports
	 * pid_namespace:null instead of guessing a mapping. */
	{
		struct stat st;

		if (stat("/proc/self/ns/pid", &st) == 0) {
			skel->rodata->pid_namespace_device = (unsigned long long)st.st_dev;
            skel->rodata->pid_namespace_inode = (unsigned long long)st.st_ino;
        }
	}

	/*
	 * Keep libbpf from auto-attaching the bash readline probe: its section
	 * name is a bare label, and libbpf's skeleton attach stops at the first
	 * program that fails — which used to take the exec/exit/open probes with
	 * it. It is attached explicitly, and best-effort, after the others.
	 */
	bpf_program__set_autoattach(skel->progs.bash_readline, false);
    snapshot_supported = libbpf_find_vmlinux_btf_id("task", BPF_TRACE_ITER) > 0;
    bpf_program__set_autoload(skel->progs.snapshot_tasks, snapshot_supported);
    bpf_program__set_autoattach(skel->progs.snapshot_tasks, false);

	/* Load & verify BPF programs */
	err = process_bpf__load(skel);
	if (err) {
		fprintf(stderr, "Failed to load and verify BPF skeleton\n");
		goto cleanup;
	}

	/* Attach tracepoints */
	err = process_bpf__attach(skel);
	if (err) {
		fprintf(stderr, "Failed to attach BPF skeleton\n");
		goto cleanup;
	}

	/* Best-effort: a missing bash must not disable the probes above. */
	attach_bash_readline(skel);

	/* Set up ring buffer polling with pid_tracker as context */
	rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle_event, &pid_tracker, NULL);
	if (!rb) {
		err = -1;
		fprintf(stderr, "Failed to create ring buffer\n");
		goto cleanup;
	}



    err = collect_initial_snapshot(skel, snapshot_supported);
    if (err && env.pid > 0)
        goto cleanup;
    err = 0;

	/* Process events */
	while (!exiting) {
		err = ring_buffer__poll(rb, 100 /* timeout, ms */);
		/* Ctrl-C will cause -EINTR */
		if (err == -EINTR) {
			err = 0;
			break;
		}
		if (err < 0) {
			fprintf(stderr, "Error polling perf buffer: %d\n", err);
			break;
		}
	}

cleanup:
    if (rb) {
        process_bpf__detach(skel);
        ring_buffer__consume(rb);
    }
    while (hash_count > 0) {
        flush_pid_file_opens(file_hashes[0].pid, file_hashes[0].timestamp_ns);
    }
	/* A loader failure is not a completed capture with zero loss. */
	if (rb)
		print_dropped_events(skel);

	/* Clean up */
	ring_buffer__free(rb);
	process_bpf__destroy(skel);
	
	/* Free allocated command strings */
	for (int i = 0; i < env.command_count; i++) {
		free(env.command_list[i]);
	}
	
	/* Clean up FILE_OPEN deduplication tracking */
	hash_count = 0;
	
	/* Clean up rate limiting tracking */
	pid_limit_count = 0;

	return err < 0 ? -err : 0;
}
