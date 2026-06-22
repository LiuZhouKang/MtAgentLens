#!/usr/bin/env python3
"""
Phase 1: Token Issuance — Multi-Agent Identity Replay Attack Protocol.

Re-implements IdentityResolver::resolve_provisional() in Python to issue
authentication tokens (AgentId UUIDs) for every process in the trace,
faithfully following the Rust code's heuristics (spec section 6.3).

Usage: python3 phase1_token_issuance.py
"""

import json
import sqlite3
import uuid
import sys
import os
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "../../../../runs/20260608-124747-73eb63ec9dad/collector/trace.responsibility.db"
OUTPUT_PATH = Path(__file__).parent / "phase1_output.json"
AGENT_COMMS = {"claude", "claude-code", "claude.exe"}

# ── Database access ─────────────────────────────────────────────────────

def load_schema_version(conn: sqlite3.Connection) -> int:
    """Read schema_version table."""
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        raise RuntimeError("schema_version table is empty")
    return row[0]


def load_process_events(conn: sqlite3.Connection, run_id: str):
    """Load process_events filtered by run_id."""
    rows = conn.execute(
        "SELECT pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns "
        "FROM process_events WHERE run_id = ? ORDER BY timestamp_ns",
        (run_id,)
    ).fetchall()
    processes = {}
    parent_map = {}
    for pid, st, ppid, pst_ns, comm, ts in rows:
        key = (pid, st)
        processes[key] = {
            "pid": pid,
            "start_time_ns": st,
            "comm": comm,
            "timestamp_ns": ts,
        }
        # Only set parent if both parent_pid and parent_start_time_ns are non-NULL
        if ppid is not None and pst_ns is not None:
            parent_map[key] = (ppid, pst_ns)
    return processes, parent_map


def load_tls_sessions(conn: sqlite3.Connection, run_id: str):
    """Load tls_sessions filtered by run_id, return {tls_session_id: (pid, start_time_ns)}."""
    rows = conn.execute(
        "SELECT tls_session_id, pid, start_time_ns "
        "FROM tls_sessions WHERE run_id = ?",
        (run_id,)
    ).fetchall()
    tls_map = {}
    for tls_id, pid, st_ns in rows:
        tls_map[tls_id] = (pid, st_ns)
    return tls_map


def load_run_id(conn: sqlite3.Connection) -> str:
    """Get the run_id from the runs table."""
    row = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("runs table is empty")
    return row[0]


# ── SignalSnapshot (Python analog) ─────────────────────────────────────

class SignalSnapshot:
    """Immutable view of the signal graph, mirroring src Rust SignalSnapshot."""

    def __init__(self, processes: dict, parent_map: dict, tls_to_process: dict):
        self.processes = processes          # {(pid, st_ns): metadata}
        self.parent_map = parent_map        # {(pid, st_ns): (parent_pid, parent_st_ns)}
        self.tls_to_process = tls_to_process  # {tls_session_id: (pid, st_ns)}

        # Build child map: parent_key -> [child_key]
        self.child_map = {}
        for child_key, parent_key in parent_map.items():
            self.child_map.setdefault(parent_key, []).append(child_key)

        # Build process -> TLS sessions reverse index
        self._process_tls: dict = {}
        for tls_id, proc_key in tls_to_process.items():
            self._process_tls.setdefault(proc_key, []).append(tls_id)

    def get_metadata(self, pid: int, start_time_ns: int):
        key = (pid, start_time_ns)
        return self.processes.get(key)

    def tls_sessions_for_process(self, pid: int, start_time_ns: int):
        return self._process_tls.get((pid, start_time_ns), [])

    def get_parent(self, pid: int, start_time_ns: int):
        return self.parent_map.get((pid, start_time_ns))

    def has_agent_comm(self, pid: int, start_time_ns: int) -> bool:
        """Check if process comm is in AGENT_COMMS (mirrors Rust has_agent_comm)."""
        meta = self.processes.get((pid, start_time_ns))
        if meta is None:
            return False
        return meta["comm"] in AGENT_COMMS


# ── Identity Resolver (Phase 1 provisional) ────────────────────────────

class IdentityResolver:
    """
    Phase 1 identity resolution (provisional).
    Mirrors the Rust IdentityResolver::resolve_provisional() logic.
    """

    def __init__(self):
        # provisional_map: ProcessId -> {agent_id, process_ids, primary_signal, confidence}
        self.provisional_map = {}

    def resolve_provisional(self, pid: int, start_time_ns: int, snapshot: SignalSnapshot):
        """
        Issue an authentication token (ProvisionalIdentity) for a process.
        This is the "token issuance" — the AgentId UUID is the token.
        """
        key = (pid, start_time_ns)
        tls_sessions = snapshot.tls_sessions_for_process(pid, start_time_ns)

        if tls_sessions:
            # Heuristics 1 & 2: process has TLS
            return self._resolve_with_tls(key, tls_sessions, snapshot)
        else:
            # Heuristics 3 & 4: process has no TLS
            return self._resolve_no_tls(key, snapshot)

    def _resolve_with_tls(self, key, tls_sessions: list, snapshot: SignalSnapshot):
        """
        Heuristic 1 & 2 from the Rust code.

        Heuristic 1: Has TLS AND shares a TLS session with a known agent
            -> join that agent, confidence = 0.70
        Heuristic 2: Has TLS but does NOT share with any known agent
            -> new ProvisionalIdentity (UUID), confidence = 0.50
        """
        pid, st_ns = key

        # Check if any TLS session is shared with a known agent's process
        for tls_id in tls_sessions:
            owner = snapshot.tls_to_process.get(tls_id)
            if owner is not None and owner in self.provisional_map:
                existing = self.provisional_map[owner]
                token = {
                    "agent_id": existing["agent_id"],
                    "process_ids": [{"pid": pid, "start_time_ns": st_ns}],
                    "primary_signal": "TlsSessionShared",
                    "confidence": 0.70,
                }
                self.provisional_map[key] = token
                return token

        # Check if this process already has a provisional identity to upgrade
        if key in self.provisional_map:
            existing = self.provisional_map[key]
            if existing["confidence"] < 0.70:
                upgraded = {
                    "agent_id": existing["agent_id"],
                    "process_ids": existing["process_ids"],
                    "primary_signal": "TlsSessionNew",
                    "confidence": 0.70,
                }
                self.provisional_map[key] = upgraded
                return upgraded
            return existing

        # Heuristic 2: new agent with TLS
        token = {
            "agent_id": str(uuid.uuid4()),
            "process_ids": [{"pid": pid, "start_time_ns": st_ns}],
            "primary_signal": "TlsSessionNew",
            "confidence": 0.50,
        }
        self.provisional_map[key] = token
        return token

    def _resolve_no_tls(self, key, snapshot: SignalSnapshot):
        """
        Heuristic 3 & 4 from the Rust code.

        Heuristic 3: No TLS, ancestor has known agent
            -> if is_agent_process(child): new provisional ID, confidence 0.30
            -> else: inherit ancestor's agent, confidence 0.55
        Heuristic 4: No TLS, no known ancestor
            -> new provisional ID, confidence 0.25
        """
        pid, st_ns = key
        current = key
        max_depth = 32
        visited = set()

        for _depth in range(max_depth):
            if current in visited:
                break  # Cycle detected
            visited.add(current)

            if current in self.provisional_map:
                if current == key:
                    # Already assigned
                    return self.provisional_map[key]

                # Ancestor found
                is_agent = snapshot.has_agent_comm(pid, st_ns)

                if is_agent:
                    # Heuristic 3b: agent-child without TLS but with agent ancestry
                    # -> new provisional identity, low confidence
                    token = {
                        "agent_id": str(uuid.uuid4()),
                        "process_ids": [{"pid": pid, "start_time_ns": st_ns}],
                        "primary_signal": "Unknown",
                        "confidence": 0.30,
                    }
                    self.provisional_map[key] = token
                    return token
                else:
                    # Heuristic 3a: tool child inherits ancestor's agent
                    token = {
                        "agent_id": self.provisional_map[current]["agent_id"],
                        "process_ids": [{"pid": pid, "start_time_ns": st_ns}],
                        "primary_signal": "ProcessTreeInherited",
                        "confidence": 0.55,
                    }
                    self.provisional_map[key] = token
                    return token

            # Move to parent
            parent = snapshot.get_parent(*current)
            if parent is None or parent == current:
                break  # Root reached
            current = parent

        # Heuristic 4: no TLS, no known ancestor -> unknown
        token = {
            "agent_id": str(uuid.uuid4()),
            "process_ids": [{"pid": pid, "start_time_ns": st_ns}],
            "primary_signal": "Unknown",
            "confidence": 0.25,
        }
        self.provisional_map[key] = token
        return token


# ── Main ────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Phase 1: Token Issuance (Identity Resolution)")
    print("=" * 60)

    # 1. Load database
    db_path = DB_PATH.resolve()
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    print(f"Database: {db_path}")
    conn = sqlite3.connect(str(db_path))

    # 2. Verify schema version
    schema_version = load_schema_version(conn)
    print(f"Schema version: {schema_version}")
    assert schema_version == 1, f"Expected schema version 1, got {schema_version}"

    # 3. Load run info
    run_id = load_run_id(conn)
    print(f"Run ID: {run_id}")

    # 4. Load data
    processes, parent_map = load_process_events(conn, run_id)
    tls_sessions = load_tls_sessions(conn, run_id)
    print(f"Process events: {len(processes)}")
    print(f"TLS sessions: {len(tls_sessions)}")
    print(f"Unique TLS session IDs: {len(set(tls_sessions.keys()))}")

    # 5. Build signal snapshot
    # Only include TLS sessions whose process is in the process_events table
    filtered_tls = {}
    for tls_id, (tls_pid, tls_st_ns) in tls_sessions.items():
        tls_key = (tls_pid, tls_st_ns)
        if tls_key in processes:
            filtered_tls[tls_id] = tls_key
        else:
            print(f"  [WARN] TLS session {tls_id} belongs to process ({tls_pid}, {tls_st_ns}) not in process_events — excluding")

    snapshot = SignalSnapshot(processes, parent_map, filtered_tls)
    print(f"TLS sessions bound to known processes: {len(filtered_tls)}")

    # 6. Sort processes by timestamp for deterministic resolution
    sorted_processes = sorted(processes.keys(), key=lambda k: processes[k]["timestamp_ns"])

    # 7. Run Phase 1 identity resolution (Token Issuance)
    resolver = IdentityResolver()
    issued_tokens = []

    print(f"\n{'─' * 60}")
    print(f"Token Issuance (resolve_provisional) for {len(sorted_processes)} processes")
    print(f"{'─' * 60}")

    for pid, st_ns in sorted_processes:
        meta = processes[(pid, st_ns)]
        token = resolver.resolve_provisional(pid, st_ns, snapshot)
        issued_tokens.append({
            "process": {"pid": pid, "start_time_ns": st_ns, "comm": meta["comm"]},
            "token": {
                "agent_id": token["agent_id"],
                "process_ids": token["process_ids"],
                "primary_signal": token["primary_signal"],
                "confidence": round(token["confidence"], 2),
            }
        })
        print(f"  PID={pid:>6} comm={meta['comm']:<20} "
              f"→ agent_id={token['agent_id'][:8]}... "
              f"signal={token['primary_signal']:<22} "
              f"confidence={token['confidence']:.2f}")

    # 8. Summary statistics
    agents = set(t["token"]["agent_id"] for t in issued_tokens)
    print(f"\n{'─' * 60}")
    print(f"Token Issuance Complete")
    print(f"{'─' * 60}")
    print(f"Total tokens issued: {len(issued_tokens)}")
    print(f"Unique AgentIds:     {len(agents)}")

    signal_counts = {}
    for t in issued_tokens:
        sig = t["token"]["primary_signal"]
        signal_counts[sig] = signal_counts.get(sig, 0) + 1
    print(f"By primary signal:   {signal_counts}")

    # 9. Output results
    output = {
        "phase": 1,
        "protocol": "multi-agent-identity-replay-attack",
        "role": "Worker-A (Token Issuer)",
        "schema_version": schema_version,
        "run_id": run_id,
        "database": str(db_path),
        "token_issuance": {
            "total_processes": len(sorted_processes),
            "tls_sessions": len(tls_sessions),
            "unique_tls_session_ids": len(set(tls_sessions.keys())),
            "unique_agent_ids": len(agents),
            "signal_distribution": signal_counts,
        },
        "issued_tokens": issued_tokens,
        "provisional_agent_grouping": {},
    }

    # Also group by agent_id for easier reference
    for t in issued_tokens:
        agent_id = t["token"]["agent_id"]
        if agent_id not in output["provisional_agent_grouping"]:
            output["provisional_agent_grouping"][agent_id] = {
                "agent_id": agent_id,
                "signal": t["token"]["primary_signal"],
                "confidence": t["token"]["confidence"],
                "processes": [],
            }
        output["provisional_agent_grouping"][agent_id]["processes"].append(
            {"pid": t["process"]["pid"], "start_time_ns": t["process"]["start_time_ns"],
             "comm": t["process"]["comm"]}
        )

    out_path = OUTPUT_PATH.resolve()
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nOutput written to: {out_path}")
    print(f"File size: {os.path.getsize(out_path):,} bytes")

    conn.close()
    print("\nPhase 1 complete.")


if __name__ == "__main__":
    main()
