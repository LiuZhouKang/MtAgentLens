"""Per-agent runner ledger for tmux-live campaigns.

Tmux mode launches one agent per pane and learns the outcome from status files.
Without a ledger the run directory records the session but not who did what: an
agent that never wrote a final status is indistinguishable from one that failed,
and a reader cannot tell which task produced which log. The ledger records the
launch and completion facts the executor actually observed, so an unobserved
completion stays `unobserved` instead of being silently treated as success or
failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LEDGER_SCHEMA = "mtagentlens.runner-ledger/1"

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_UNOBSERVED = "unobserved"


@dataclass
class AgentLedgerEntry:
    agent_index: int
    task: str
    pane_mode: str
    log_file: str
    launched_at_ns: int
    completed_at_ns: int | None = None
    status: str = STATUS_UNOBSERVED
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "agent_index": self.agent_index,
            "task": self.task,
            "pane_mode": self.pane_mode,
            "log_file": self.log_file,
            "launched_at_ns": self.launched_at_ns,
            "completed_at_ns": self.completed_at_ns,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class RunnerLedger:
    session_name: str
    mode: str
    entries: list[AgentLedgerEntry] = field(default_factory=list)

    def record_launch(
        self,
        *,
        agent_index: int,
        task: str,
        pane_mode: str,
        log_file: Path,
        launched_at_ns: int,
    ) -> AgentLedgerEntry:
        if any(entry.agent_index == agent_index for entry in self.entries):
            raise ValueError(f"agent {agent_index} launched twice")
        entry = AgentLedgerEntry(
            agent_index=agent_index,
            task=task,
            pane_mode=pane_mode,
            log_file=str(log_file),
            launched_at_ns=launched_at_ns,
        )
        self.entries.append(entry)
        return entry

    def record_completion(
        self,
        *,
        agent_index: int,
        status: str,
        completed_at_ns: int,
        detail: str = "",
    ) -> None:
        if status not in {
            STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TIMEOUT, STATUS_ERROR, STATUS_UNOBSERVED
        }:
            raise ValueError(f"unknown completion status {status!r}")
        entry = next((item for item in self.entries if item.agent_index == agent_index), None)
        if entry is None:
            raise ValueError(f"agent {agent_index} completed without a launch record")
        if entry.completed_at_ns is not None:
            raise ValueError(f"agent {agent_index} completed twice")
        if completed_at_ns < entry.launched_at_ns:
            raise ValueError(f"agent {agent_index} completed before it launched")
        entry.completed_at_ns = completed_at_ns
        entry.status = status
        entry.detail = detail

    def summary(self) -> dict:
        statuses = {status: 0 for status in (
            STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TIMEOUT, STATUS_ERROR, STATUS_UNOBSERVED)}
        for entry in self.entries:
            statuses[entry.status] = statuses.get(entry.status, 0) + 1
        return {
            "agents_total": len(self.entries),
            "agents_succeeded": statuses[STATUS_SUCCEEDED],
            "agents_unobserved": statuses[STATUS_UNOBSERVED],
            "statuses": statuses,
            "all_observed": statuses[STATUS_UNOBSERVED] == 0,
        }

    def to_json(self) -> dict:
        ordered = sorted(self.entries, key=lambda entry: entry.agent_index)
        return {
            "schema": LEDGER_SCHEMA,
            "session_name": self.session_name,
            "mode": self.mode,
            "agents": [entry.to_json() for entry in ordered],
            "summary": self.summary(),
            "note": ("Launch and completion facts observed by the executor. `unobserved` "
                     "means the status file never reported a terminal state; it is not a "
                     "success and not a failure."),
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        temporary.replace(path)
        return path
