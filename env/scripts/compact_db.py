#!/usr/bin/env python3
"""Compact a responsibility DB by keeping only essential tables.

Views are part of the contract, not decoration: since schema v2 the collector
stores identity as `identity_process_beliefs` and exposes the historical
`identity_process_map` as a view over it. A compactor that enumerates only
`type='table'` silently drops that contract, so views are recreated here and a
view that cannot be recreated fails the run instead of vanishing.
"""

import sqlite3, sys, os

ESSENTIAL_TABLES = {
    'analysis_checkpoints',
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
    # v2 identity beliefs; v1 databases carry the same data in the
    # identity_process_map *table*, which is copied when it exists.
    'identity_process_beliefs',
    'identity_process_map',
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

    # Recreate views after their base tables exist. A view that cannot be
    # created means the compacted database would silently break a documented
    # query contract, so it is an error, not a skip.
    views = src.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name"
    ).fetchall()
    for name, create_sql in views:
        try:
            dst.execute(create_sql)
            print(f'  VIEW: {name}')
        except sqlite3.Error as exc:
            raise SystemExit(
                f"compact_db: cannot recreate view {name}: {exc}"
            ) from exc

    # Preserve the schema identity, not just the rows: a compacted database
    # stamped user_version=0 would be re-adopted and re-migrated later.
    user_version = src.execute('PRAGMA user_version').fetchone()[0]
    application_id = src.execute('PRAGMA application_id').fetchone()[0]
    dst.execute(f'PRAGMA user_version = {int(user_version)}')
    dst.execute(f'PRAGMA application_id = {int(application_id)}')

    dst.commit()
    src.close()
    dst.close()

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <src.db> <dst.db>")
        sys.exit(1)
    compact_db(sys.argv[1], sys.argv[2])
