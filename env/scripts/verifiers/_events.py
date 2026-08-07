#!/usr/bin/env python3
"""Event source abstraction for verifiers to read collector output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional, Dict, Any, List


class EventSource:
    """Reads eBPF collector events from run directory.

    When the run's ``summary.json`` contains ``experiment_pids``, events from
    PIDs outside that set are automatically excluded.  This is a second line
    of defence on top of the trace-log filtering done by the experiment runner.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.collector_dir = run_dir / "collector"
        self.trace_log = run_dir / "collector.log"
        self._experiment_pid_set: Optional[set] = None
        self._load_experiment_pids()

    def _load_experiment_pids(self) -> None:
        summary_path = self.run_dir / "summary.json"
        if not summary_path.exists():
            return
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            pids = summary.get("experiment_pids", [])
            if pids:
                self._experiment_pid_set = set(int(p) for p in pids)
        except Exception:
            pass

    def ssl_events(
        self,
        comm: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield SSL events (source='ssl')."""
        yield from self._read_events(source="ssl", comm=comm, pid=pid)

    def process_events(
        self,
        comm: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield process events (source='process')."""
        yield from self._read_events(source="process", comm=comm, pid=pid)

    def all_events(
        self,
        comm: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield all events regardless of source."""
        yield from self._read_events(source=None, comm=comm, pid=pid)

    def alert_events(
        self,
        comm: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield structured alert events (source='alert')."""
        yield from self._read_events(source="alert", comm=comm, pid=pid)

    def _read_events(
        self,
        source: Optional[str],
        comm: Optional[str],
        pid: Optional[int],
    ) -> Iterator[Dict[str, Any]]:
        """Read events from collector JSON logs."""
        log_files = []
        if self.trace_log.exists():
            log_files.append(self.trace_log)
        if self.collector_dir.exists():
            log_files.extend(sorted(self.collector_dir.glob("*.log")))
        if not log_files:
            return

        for log_file in log_files:
            try:
                with log_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Filter by source
                        if source and event.get("source") != source:
                            continue

                        # Filter by comm
                        if comm and event.get("comm") != comm:
                            continue

                        # Filter by pid
                        if pid is not None and event.get("pid") != pid:
                            continue

                        # Filter by experiment_pids (loaded from summary.json)
                        if self._experiment_pid_set is not None:
                            event_pid = event.get("pid")
                            # Keep events with no PID (system metrics), filter others
                            if event_pid is not None and int(event_pid) not in self._experiment_pid_set:
                                continue

                        yield event
            except Exception:
                continue

    def count_events(
        self,
        source: Optional[str] = None,
        comm: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> int:
        """Count matching events."""
        return sum(1 for _ in self._read_events(source, comm, pid))

    def find_all_events(
        self,
        predicate,
        source: Optional[str] = None,
        comm: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Find all events matching predicate."""
        return [e for e in self._read_events(source, comm, pid) if predicate(e)]


def read_task_log(run_dir: Path, task_id: str) -> str:
    """Read Claude CLI log for a task."""
    log_file = run_dir / "logs" / f"{task_id}.log"
    if not log_file.exists():
        return ""
    try:
        return log_file.read_text(encoding="utf-8")
    except Exception:
        return ""


def read_all_task_logs(run_dir: Path) -> Dict[str, str]:
    """Read all task logs as {task_id: log_content}."""
    logs_dir = run_dir / "logs"
    if not logs_dir.exists():
        return {}
    result = {}
    for log_file in logs_dir.glob("*.log"):
        task_id = log_file.stem
        try:
            result[task_id] = log_file.read_text(encoding="utf-8")
        except Exception:
            result[task_id] = ""
    return result

