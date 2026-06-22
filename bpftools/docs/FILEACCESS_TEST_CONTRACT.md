# File-Access Probe Test Contract

## Overview
The file-access probe (`fileaccess.bpf.c`) captures `open` and `openat` syscalls via tracepoints and exports events through a ring buffer.

## Ring Buffer Interface
- **Map Name**: `rb`
- **Type**: `BPF_MAP_TYPE_RINGBUF`
- **Capacity**: 256KB
- **Event Structure**: `struct fileaccess_event` (defined in `fileaccess.h`)

## Event Schema
```c
struct fileaccess_event {
    unsigned long long timestamp_ns;  // Kernel time (ns)
    int pid;                          // Process ID
    int uid;                          // User ID
    enum fileaccess_event_type op_type; // FILEACCESS_OPEN (0) or FILEACCESS_OPENAT (1)
    int flags;                        // Open flags (O_RDONLY, O_WRONLY, etc.)
    int mode;                         // File mode/permissions
    char filename[127];               // Null-terminated filename
    char comm[16];                    // Process command name
};
```

## Filtering
- **Volatile Parameter**: `targ_pid` (default: 0 = all processes)
- Set via libbpf before attach: `skel->rodata->targ_pid = target_pid`
- When set, only events from matching PID are emitted

## Test Scenarios
1. **Basic Capture**: Verify events are emitted for open/openat syscalls
2. **PID Filtering**: Verify filtering works when `targ_pid` is set
3. **Filename Capture**: Verify filenames are correctly captured (truncated to 127 bytes)
4. **Ring Buffer Overflow**: Verify behavior under high event volume

## Userspace Consumer
- Use `ring_buffer__new()` and `ring_buffer__poll()` from libbpf
- Events are JSON-formatted in stdout (see `fileaccess.c` for format)
- Graceful shutdown on SIGINT

## Kernel Requirements
- Linux 5.8+ (tracepoint support)
- BPF LSM or standard tracepoint access
- CAP_BPF + CAP_SYS_ADMIN or CAP_SYS_RESOURCE

## Known Limitations
- Filename capture limited to 127 bytes (truncated if longer)
- High-frequency syscalls may cause ring buffer loss under extreme load
- Relative paths captured as-is (not resolved to absolute paths)
