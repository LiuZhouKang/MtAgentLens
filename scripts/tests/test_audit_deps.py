#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Contract tests for the dependency audit gate.

These are pure: no network, no cargo-audit, no advisory database. They prove the
gate's *decision rules*, which is the part that can silently regress. The live
audit itself is exercised by the CI `audit` stage.

Run from the repository root:

    python3 -m unittest discover -s scripts/tests -p 'test_audit_deps.py'
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit_deps  # noqa: E402


def budget_file(contents: dict) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(contents, handle)
    handle.close()
    return Path(handle.name)


class BudgetSchemaTests(unittest.TestCase):
    def test_empty_budget_is_valid(self) -> None:
        path = budget_file({"cargo": [], "npm": []})
        self.assertEqual(audit_deps.load_budget(path), {"cargo": [], "npm": []})

    def test_an_entry_without_a_reason_is_rejected(self) -> None:
        path = budget_file({"cargo": [{"id": "RUSTSEC-2024-0001"}], "npm": []})
        with self.assertRaises(audit_deps.GateError) as raised:
            audit_deps.load_budget(path)
        self.assertIn("no reason", str(raised.exception))

    def test_a_cargo_entry_needs_an_advisory_id(self) -> None:
        path = budget_file({"cargo": [{"reason": "checked"}], "npm": []})
        with self.assertRaises(audit_deps.GateError) as raised:
            audit_deps.load_budget(path)
        self.assertIn("advisory `id`", str(raised.exception))

    def test_npm_entries_cannot_pre_accept_a_severity_that_never_fails(self) -> None:
        for severity in ("low", "info", "unknown"):
            path = budget_file(
                {
                    "cargo": [],
                    "npm": [
                        {
                            "package": "x",
                            "advisory": "GHSA-xxxx-yyyy-zzzz",
                            "severity": severity,
                            "reason": "noise",
                        }
                    ],
                }
            )
            with self.assertRaises(audit_deps.GateError, msg=severity):
                audit_deps.load_budget(path)

    def test_npm_entries_need_an_advisory_id(self) -> None:
        path = budget_file(
            {
                "cargo": [],
                "npm": [{"package": "x", "severity": "high", "reason": "checked"}],
            }
        )
        with self.assertRaises(audit_deps.GateError) as raised:
            audit_deps.load_budget(path)
        self.assertIn("advisory", str(raised.exception))

    def test_malformed_json_is_rejected(self) -> None:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        handle.write("not = [json")
        handle.close()
        with self.assertRaises(audit_deps.GateError):
            audit_deps.load_budget(Path(handle.name))


class CargoDecisionTests(unittest.TestCase):
    def report(self, *advisories: dict) -> dict:
        return {"vulnerabilities": {"list": list(advisories)}}

    def item(self, advisory_id: str, package: str = "crate", version: str = "1.0.0") -> dict:
        return {
            "advisory": {"id": advisory_id, "title": f"title-{advisory_id}"},
            "package": {"name": package, "version": version},
        }

    def test_an_unbudgeted_advisory_fails(self) -> None:
        failures, accepted, stale, live = audit_deps.evaluate_cargo(
            self.report(self.item("RUSTSEC-2024-0001")), {}
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("RUSTSEC-2024-0001", failures[0])
        self.assertEqual((accepted, stale, live), ([], [], 1))

    def test_a_budgeted_advisory_passes_and_keeps_its_reason(self) -> None:
        accepted = {"RUSTSEC-2024-0001": {"reason": "not reachable: feature disabled"}}
        failures, accepted_lines, stale, live = audit_deps.evaluate_cargo(
            self.report(self.item("RUSTSEC-2024-0001")), accepted
        )
        self.assertEqual(failures, [])
        self.assertEqual(stale, [])
        self.assertEqual(live, 1)
        self.assertIn("not reachable: feature disabled", accepted_lines[0])

    def test_a_budget_entry_for_a_fixed_advisory_is_stale_and_fails(self) -> None:
        accepted = {"RUSTSEC-2024-0001": {"reason": "old"}}
        failures, _accepted, stale, _live = audit_deps.evaluate_cargo(self.report(), accepted)
        self.assertEqual(stale, ["RUSTSEC-2024-0001"])
        self.assertTrue(any("stale" in failure for failure in failures))

    def test_an_empty_report_passes(self) -> None:
        failures, accepted, stale, live = audit_deps.evaluate_cargo({}, {})
        self.assertEqual((failures, accepted, stale, live), ([], [], [], 0))


class NpmDecisionTests(unittest.TestCase):
    def advisory(self, ghsa: str) -> dict:
        return {"url": f"https://github.com/advisories/{ghsa}"}

    def test_only_failing_severities_are_gated(self) -> None:
        report = {
            "vulnerabilities": {
                "lowdep": {"severity": "low", "via": [self.advisory("GHSA-lowl-owlo-wlow")]},
                "highdep": {"severity": "high", "via": [self.advisory("GHSA-high-high-high")]},
            }
        }
        failures, matched, count, propagated = audit_deps.evaluate_npm(report, {})
        self.assertEqual(count, 1, "only the high advisory is counted")
        self.assertEqual(matched, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("highdep", failures[0])
        self.assertIn("GHSA-HIGH-HIGH-HIGH", failures[0])

    def test_a_budgeted_package_advisory_pair_passes(self) -> None:
        report = {
            "vulnerabilities": {
                "highdep": {"severity": "high", "via": [self.advisory("GHSA-high-high-high")]}
            }
        }
        accepted = {
            ("highdep", "GHSA-HIGH-HIGH-HIGH"): {
                "reason": "exploit needs a flag we never set"
            }
        }
        failures, matched, count, propagated = audit_deps.evaluate_npm(report, accepted)
        self.assertEqual(
            (failures, matched, count), ([], [("highdep", "GHSA-HIGH-HIGH-HIGH")], 1)
        )

    def test_a_new_advisory_on_an_already_budgeted_package_fails(self) -> None:
        report = {
            "vulnerabilities": {
                "highdep": {
                    "severity": "high",
                    "via": [
                        self.advisory("GHSA-old-old-old"),
                        self.advisory("GHSA-new-new-new"),
                    ],
                }
            }
        }
        accepted = {("highdep", "GHSA-OLD-OLD-OLD"): {"reason": "old, reviewed"}}
        failures, _matched, _count, _propagated = audit_deps.evaluate_npm(report, accepted)
        self.assertTrue(
            any("GHSA-NEW-NEW-NEW" in failure for failure in failures),
            "a package-level budget must not pre-accept a later advisory",
        )

    def test_an_advisory_without_an_id_fails_closed(self) -> None:
        report = {"vulnerabilities": {"mystery": {"severity": "critical", "via": []}}}
        failures, _matched, _count, _propagated = audit_deps.evaluate_npm(report, {})
        self.assertTrue(any("no identifiable advisory id" in f for f in failures))

    def test_a_stale_npm_entry_fails(self) -> None:
        accepted = {("gone", "GHSA-gone-gone-gone"): {"reason": "old"}}
        failures, matched, count, propagated = audit_deps.evaluate_npm({}, accepted)
        self.assertEqual(matched, [])
        self.assertEqual(count, 0)
        self.assertTrue(any("stale" in failure for failure in failures))

    def test_a_propagation_parent_is_gated_via_its_child(self) -> None:
        # npm's dominant shape: `next` is vulnerable because `postcss` is, and
        # carries `via: ["postcss"]` with no advisory object. The child has the
        # real advisory and its own severity, so budgeting the child covers the
        # parent; requiring a parent entry would make the accepted state
        # inexpressible.
        report = {
            "vulnerabilities": {
                "postcss": {
                    "severity": "moderate",
                    "via": [self.advisory("GHSA-post-post-post")],
                },
                "next": {"severity": "moderate", "via": ["postcss"]},
            }
        }
        accepted = {("postcss", "GHSA-POST-POST-POST"): {"reason": "reviewed child"}}
        failures, matched, count, propagated = audit_deps.evaluate_npm(report, accepted)
        self.assertEqual(failures, [])
        self.assertEqual(matched, [("postcss", "GHSA-POST-POST-POST")])
        self.assertEqual(count, 1)
        self.assertEqual(propagated, 1)

    def test_a_propagation_parent_does_not_hide_an_unbudgeted_child(self) -> None:
        report = {
            "vulnerabilities": {
                "postcss": {
                    "severity": "moderate",
                    "via": [self.advisory("GHSA-post-post-post")],
                },
                "next": {"severity": "moderate", "via": ["postcss"]},
            }
        }
        failures, _matched, count, propagated = audit_deps.evaluate_npm(report, {})
        self.assertEqual(count, 1)
        self.assertEqual(propagated, 1)
        self.assertTrue(any("postcss" in f for f in failures))

    def test_npm_advisory_ids_reads_ghsa_and_numeric_sources(self) -> None:
        data = {
            "via": [
                {"url": "https://github.com/advisories/GHSA-abcd-efgh-ijkl"},
                {"source": 1234},
                "some-transitive-package",
            ]
        }
        self.assertEqual(
            audit_deps.npm_advisory_ids(data), ["GHSA-ABCD-EFGH-IJKL", "1234"]
        )


class ReportValidationTests(unittest.TestCase):
    """A registry/tool failure must never read as zero advisories."""

    def test_npm_error_object_is_rejected(self) -> None:
        # The shape npmmirror returns for /-/npm/v1/security/audits.
        mirror_error = {
            "message": "404 Not Found - POST .../-/npm/v1/security/audits/quick",
            "statusCode": 404,
            "error": "[NOT_IMPLEMENTED]",
        }
        with self.assertRaises(audit_deps.GateError):
            audit_deps.require_npm_report(mirror_error, 1)

    def test_npm_report_without_metadata_is_rejected(self) -> None:
        with self.assertRaises(audit_deps.GateError):
            audit_deps.require_npm_report({"vulnerabilities": {}}, 0)

    def test_npm_non_audit_exit_code_is_rejected(self) -> None:
        report = {
            "auditReportVersion": 2,
            "vulnerabilities": {},
            "metadata": {"vulnerabilities": {}},
        }
        with self.assertRaises(audit_deps.GateError):
            audit_deps.require_npm_report(report, 2)

    def test_a_valid_npm_report_passes(self) -> None:
        report = {
            "auditReportVersion": 2,
            "vulnerabilities": {},
            "metadata": {"vulnerabilities": {"total": 0}},
        }
        audit_deps.require_npm_report(report, 0)

    def test_cargo_report_without_a_vulnerability_list_is_rejected(self) -> None:
        with self.assertRaises(audit_deps.GateError):
            audit_deps.require_cargo_report({"warnings": {}})

    def test_a_valid_cargo_report_passes(self) -> None:
        audit_deps.require_cargo_report(
            {"vulnerabilities": {"found": False, "count": 0, "list": []}}
        )


if __name__ == "__main__":
    unittest.main()
