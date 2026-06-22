#!/usr/bin/env python3
"""Compact a responsibility DB by keeping only essential tables."""

import sqlite3, sys, os

ESSENTIAL_TABLES = {
    'responsibility_alerts',
    'responsibility_claims',
    'responsibility_tracebacks',
    'graph_nodes',
    'graph_edges',
    'intents',
    'actions',
    'agent_identities',
    'runs',
    'schema_version',
    'delegation_records',
    'context_pollution_events',
    'subagent_boundaries',
    'claim_refinements',
    'classification_refinements',
    'evidence_refinements',
    'graph_refinements',
    'identity_refinements',
}

def compact_db(src_path: str, dst_path: str):
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)

    # Get all tables from source
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]

    # Copy schema for essential tables
    for table in tables:
        if table.startswith('sqlite_'):
            continue
        if table not in ESSENTIAL_TABLES:
            print(f'  SKIP: {table}')
            continue

        # Get CREATE TABLE statement
        create_sql = src.execute(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).fetchone()[0]
        dst.execute(create_sql)

        # Copy data
        rows = src.execute(f"SELECT * FROM '{table}'").fetchall()
        if not rows:
            print(f'  EMPTY: {table}')
            continue

        cols = [d[1] for d in src.execute(f"PRAGMA table_info('{table}')")]
        placeholders = ','.join(['?' for _ in cols])
        dst.executemany(
            f"INSERT INTO '{table}' ({','.join(cols)}) VALUES ({placeholders})",
            rows
        )
        print(f'  COPY: {table} ({len(rows)} rows)')

    dst.commit()
    src.close()
    dst.close()

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <src.db> <dst.db>")
        sys.exit(1)
    compact_db(sys.argv[1], sys.argv[2])
