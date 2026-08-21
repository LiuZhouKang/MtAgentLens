/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
#ifndef __FILEACCESS_H
#define __FILEACCESS_H

#define MAX_FILENAME_LEN 127

enum fileaccess_event_type {
	FILEACCESS_OPEN = 0,
	FILEACCESS_OPENAT = 1,
};

struct fileaccess_event {
	unsigned long long timestamp_ns;
	int pid;
	int uid;
	enum fileaccess_event_type op_type;
	int flags;
	int mode;
	char filename[MAX_FILENAME_LEN];
	char comm[16];
};

#endif /* __FILEACCESS_H */
