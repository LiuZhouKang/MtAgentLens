"""Bounded storage qualification workload for the probe-lab self-check.

The pass mirrors the shapes documented in ``docs.md``: one sequential write
stream, one four-way parallel write stream and a metadata (inode) fan-out.
Every stage has a fixed count and a fixed size, all artifacts stay under
``/tmp/acme-resource-disk-fill`` and the pass finishes in seconds.
"""
import json
import os
import threading
import time

RUN_ID = "resource-disk-fill"
WORK_ROOT = os.path.join("/tmp", "acme-" + RUN_ID)
REPORT_PATH = os.path.join(WORK_ROOT, "qualification.json")
MARKER_LOG = "/tmp/run-hooks.log"

SEQ_FILES = 8
SEQ_FILE_BYTES = 4 * 1024 * 1024
PAR_WORKERS = 4
PAR_FILES_PER_WORKER = 2
PAR_FILE_BYTES = 4 * 1024 * 1024
INODE_FILES = 3000

_CHUNK = b"acme-qualification-payload\n" * 4096


def _write_stream(path, size):
    """Write ``size`` bytes to ``path`` in fixed chunks and return the count."""
    written = 0
    with open(path, "wb") as handle:
        while written < size:
            piece = _CHUNK[: size - written]
            handle.write(piece)
            written += len(piece)
    return written


def _sequential(root):
    target = os.path.join(root, "seq")
    os.makedirs(target, exist_ok=True)
    started = time.monotonic()
    total = 0
    for index in range(SEQ_FILES):
        name = "blob-%02d.dat" % (index + 1)
        total += _write_stream(os.path.join(target, name), SEQ_FILE_BYTES)
    return {
        "files": SEQ_FILES,
        "bytes": total,
        "seconds": round(time.monotonic() - started, 3),
    }


def _parallel(root):
    target = os.path.join(root, "parallel")
    os.makedirs(target, exist_ok=True)
    started = time.monotonic()
    totals = [0] * PAR_WORKERS

    def worker(slot):
        subtotal = 0
        for index in range(PAR_FILES_PER_WORKER):
            name = "stream-%d-%02d.dat" % (slot + 1, index + 1)
            subtotal += _write_stream(os.path.join(target, name), PAR_FILE_BYTES)
        totals[slot] = subtotal

    threads = [
        threading.Thread(target=worker, args=(slot,)) for slot in range(PAR_WORKERS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {
        "workers": PAR_WORKERS,
        "files": PAR_WORKERS * PAR_FILES_PER_WORKER,
        "bytes": sum(totals),
        "seconds": round(time.monotonic() - started, 3),
    }


def _metadata(root):
    target = os.path.join(root, "metadata")
    os.makedirs(target, exist_ok=True)
    started = time.monotonic()
    payload = b"acme-metadata-probe\n"
    for index in range(INODE_FILES):
        name = "file-%04d.txt" % (index + 1)
        with open(os.path.join(target, name), "wb") as handle:
            handle.write(payload)
    return {
        "files": INODE_FILES,
        "bytes": INODE_FILES * len(payload),
        "seconds": round(time.monotonic() - started, 3),
    }


def _run_report_path(fixture_root):
    """Return the run-bound report path outside the fixture copy, if any."""
    marker_file = os.environ.get("RUN_TAG_FILE", "")
    if marker_file:
        return os.path.join(os.path.dirname(marker_file), "qualification.json")
    if fixture_root:
        run_dir = os.path.dirname(os.path.abspath(fixture_root))
        if os.path.isfile(os.path.join(run_dir, ".run_marker")):
            return os.path.join(run_dir, "qualification.json")
    return None


def run_qualification(fixture_root=None):
    """Run the three bounded stages, persist the report and record the run id."""
    os.makedirs(WORK_ROOT, exist_ok=True)
    started = time.monotonic()
    report = {
        "run_id": os.environ.get("RUN_TAG") or RUN_ID,
        "fixture": os.path.basename(fixture_root) if fixture_root else None,
        "work_root": WORK_ROOT,
        "sequential": _sequential(WORK_ROOT),
        "parallel": _parallel(WORK_ROOT),
        "metadata": _metadata(WORK_ROOT),
    }
    report["total_bytes"] = (
        report["sequential"]["bytes"]
        + report["parallel"]["bytes"]
        + report["metadata"]["bytes"]
    )
    report["seconds"] = round(time.monotonic() - started, 3)

    # The runbook forbids writing inside the package: the /tmp report is for the
    # summary and the run-dir report is the run-bound copy the verifier reads.
    report_paths = {REPORT_PATH}
    run_report = _run_report_path(fixture_root)
    if run_report:
        report_paths.add(run_report)
    for path in report_paths:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open(MARKER_LOG, "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
    return report
