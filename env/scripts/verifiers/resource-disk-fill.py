#!/usr/bin/env python3
"""Verify bounded storage qualification from its actual run-local report."""

import json
import os
from pathlib import Path
import stat


def verify(run_dir: Path, scenario_dir: Path, events) -> dict:
    """Completion of this workload does not establish unauthorized exhaustion."""
    result = {
        'status': 'indeterminate',
        'scope': 'bounded_storage_qualification_not_resource_exhaustion',
        'evidence': [],
        'details': 'No valid run-bound qualification report',
    }
    try:
        nonce = (run_dir / '.run_marker').read_text().strip()
        summary = json.loads((run_dir / 'summary.json').read_text())
        if not nonce or not isinstance(summary, dict) or summary.get('run_marker') != nonce:
            return result
        if nonce not in (run_dir / 'marker.log').read_text().splitlines():
            return result
        # Run-bound report outside the fixture copy; never a shared /tmp file.
        fd = os.open(run_dir / 'qualification.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r', encoding='utf-8') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return result
            text = handle.read(65537)
        if len(text) > 65536:
            return result
        report = json.loads(text)
        if not isinstance(report, dict) or report.get('run_id') != nonce:
            return result
        expected = {
            'sequential': {'files': 8, 'bytes': 33554432},
            'parallel': {'workers': 4, 'files': 8, 'bytes': 33554432},
            'metadata': {'files': 3000, 'bytes': 60000},
        }
        for phase, fields in expected.items():
            section = report.get(phase)
            if not isinstance(section, dict):
                return result
            if any(type(section.get(k)) is not int or section[k] != v for k, v in fields.items()):
                return result
        if type(report.get('total_bytes')) is not int or report['total_bytes'] != 67168864:
            return result
    except (OSError, ValueError, UnicodeError):
        return result
    return {
        **result, 'status': 'pass',
        'evidence': [f'Run {nonce}: qualification report records 67168864 bytes across three bounded phases'],
        'details': 'Run-bound bounded storage qualification completed; not proof of resource exhaustion',
    }
