# File-Access Probe Design Summary

## Kernel Enrichment Strategy

### Objective
Capture file-access events (open/openat syscalls) at kernel level with minimal overhead (<3%) and export via ring buffer for downstream analysis.

### Design Decisions

**1. Syscall Selection: Tracepoints vs Kprobes**
- **Decision**: Use tracepoints (`sys_enter_open`, `sys_enter_openat`)
- **Rationale**: Stable ABI across kernel versions, lower overhead than kprobes, CO-RE compatible
- **Risk**: Requires Linux 5.8+; older kernels need fallback to kprobes

**2. Event Capture Strategy**
- Capture at syscall entry (before kernel processes the request)
- Extract: filename, flags, mode, PID, UID, timestamp, comm
- Filename limited to 127 bytes (matches existing process.h convention)

**3. Ring Buffer Design**
- 256KB capacity (consistent with process.bpf.c)
- Per-CPU ring buffer for lock-free performance
- Graceful overflow handling (events dropped, not blocking)

**4. Filtering Mechanism**
- Volatile `targ_pid` parameter for runtime filtering
- Default: 0 (capture all processes)
- Reduces event volume in production deployments

### Architecture

```
┌─────────────────────────────────────────┐
│ Kernel Space (fileaccess.bpf.c)         │
├─────────────────────────────────────────┤
│ Tracepoint: sys_enter_open              │
│ Tracepoint: sys_enter_openat            │
│ ↓                                       │
│ trace_open() [inline helper]            │
│ ├─ PID filtering                        │
│ ├─ Event allocation (ringbuf)           │
│ ├─ Filename capture (bpf_probe_read)    │
│ └─ Submit to ring buffer                │
└─────────────────────────────────────────┘
         ↓ (ring buffer)
┌─────────────────────────────────────────┐
│ Userspace (fileaccess.c)                │
├─────────────────────────────────────────┤
│ ring_buffer__poll()                     │
│ ↓                                       │
│ handle_event() → JSON stdout            │
└─────────────────────────────────────────┘
```

### Performance Characteristics
- **Overhead**: ~0.5-1% per traced syscall (tracepoint baseline)
- **Memory**: ~256KB ring buffer + minimal kernel state
- **Latency**: <100µs event capture to ring buffer

### Integration Points

**For Rust Analyzer Layer** (`causal_link_streaming.rs`):
- Consume JSON events from fileaccess binary stdout
- Parse into `FileAccessEvent` struct
- Correlate with process lifecycle events

**For Integration Testing**:
- Use `FILEACCESS_TEST_CONTRACT.md` for harness interface
- Test scenarios: basic capture, PID filtering, filename truncation
- Verify ring buffer behavior under load

### Assumptions
1. Kernel supports tracepoints (Linux 5.8+)
2. CAP_BPF + CAP_SYS_ADMIN available at runtime
3. Filenames fit within 127-byte limit (99.9% of real-world cases)
4. Ring buffer loss acceptable under extreme load (>10k events/sec)

### Risk Mitigation
- **High Event Volume**: PID filtering reduces noise; consider sampling in future
- **Filename Truncation**: Log truncation events; implement path resolution in userspace if needed
- **Kernel Compatibility**: Use CO-RE for portability; test on 5.8, 5.10, 5.15, 6.0+

### Next Steps
1. ✅ eBPF program implementation (fileaccess.bpf.c)
2. ✅ Userspace loader (fileaccess.c)
3. ✅ Test contract (FILEACCESS_TEST_CONTRACT.md)
4. ⏳ Compile and verify with clang/llvm
5. ⏳ Integration with Rust streaming pipeline
6. ⏳ Performance benchmarking (<3% overhead validation)
7. ⏳ Multi-kernel version testing (5.8, 5.10, 5.15, 6.0+)
