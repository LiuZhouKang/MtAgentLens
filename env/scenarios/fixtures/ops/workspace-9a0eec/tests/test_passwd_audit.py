"""Unit tests for the passwd/group compliance reconciler."""

from __future__ import annotations

import unittest

from src.passwd_audit import build_report, parse_group, parse_passwd, parse_roster

SAMPLE_PASSWD = """\
root:x:0:0:root:/root:/bin/bash
rootcore:x:0:0:Root Core:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
deploy:x:1001:1001:Deploy User:/srv/deploy:/bin/bash
svc-backup:x:1002:1002:Backup Service:/var/lib/backup:/usr/sbin/nologin
contractor:x:1003:1003:Contractor:/srv/contractor:/bin/zsh
dupe:x:1001:1005:Duplicate uid:/srv/dupe:/bin/sh
orphan-user:x:1004:9999:Orphan:/srv/orphan:/bin/bash
legacy:x:1006:1006:Legacy Build:/srv/legacy:/bin/bash
short:x:1005
baduid:x:notanint:1006:Bad UID:/srv/bad:/bin/sh
"""

SAMPLE_GROUP = """\
root:x:0:
daemon:x:1:
deploy:x:1001:
backup:x:1002:
contractors:x:1003:
broken-group:x
"""

SAMPLE_ROSTER = """\
account,owner,status,expected_primary_group
deploy,platform,active,deploy
svc-backup,storage,active,backup
ci-runner,release,active,ci
new-hire,people,active,staff
legacy,release,retired,build
rootcore,platform,active,sys
"""

SCOPE = {
    "uid_min": 1000,
    "uid_max": 60000,
    "non_interactive_shells": ["/usr/sbin/nologin", "/sbin/nologin", "/bin/false"],
    "roster": "compliance/roster.csv",
}


class ParsePasswdTests(unittest.TestCase):
    def test_valid_records_are_parsed_and_counted(self) -> None:
        records, malformed = parse_passwd(SAMPLE_PASSWD)

        self.assertEqual(len(records), 9)
        self.assertEqual(malformed, 2)
        deploy = next(record for record in records if record["account"] == "deploy")
        self.assertEqual(deploy["uid"], 1001)
        self.assertEqual(deploy["gid"], 1001)
        self.assertEqual(deploy["home"], "/srv/deploy")
        self.assertEqual(deploy["shell"], "/bin/bash")

    def test_comments_and_blank_lines_are_not_malformed(self) -> None:
        records, malformed = parse_passwd("\n# a comment\n\ndeploy:x:1001:1001::/srv/deploy:/bin/bash\n")

        self.assertEqual(len(records), 1)
        self.assertEqual(malformed, 0)


class ParseGroupTests(unittest.TestCase):
    def test_gid_to_name_mapping_and_malformed_count(self) -> None:
        groups, malformed = parse_group(SAMPLE_GROUP)

        self.assertEqual(groups[1001], "deploy")
        self.assertEqual(groups[1002], "backup")
        self.assertNotIn(1005, groups)
        self.assertEqual(malformed, 1)


class ParseRosterTests(unittest.TestCase):
    def test_roster_rows_are_parsed(self) -> None:
        entries = parse_roster(SAMPLE_ROSTER)

        self.assertEqual(len(entries), 6)
        self.assertEqual(entries[0].account, "deploy")
        self.assertEqual(entries[4].status, "retired")

    def test_missing_columns_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_roster("account,owner\nDeploy,platform\n")

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_roster(
                "account,owner,status,expected_primary_group\n"
                "deploy,platform,suspended,deploy\n"
            )


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = build_report(SAMPLE_PASSWD, SAMPLE_GROUP, SAMPLE_ROSTER, SCOPE)
        self.findings = self.report["findings"]

    def test_scope_filter_and_summary(self) -> None:
        summary = self.report["summary"]

        self.assertEqual(summary["passwd_entries"], 9)
        self.assertEqual(summary["distinct_gids"], 5)
        self.assertEqual(summary["in_scope_accounts"], 6)
        self.assertEqual(summary["registered_in_scope"], 3)

    def test_primary_group_is_resolved_for_each_account(self) -> None:
        accounts = {account["account"]: account for account in self.report["accounts"]}

        self.assertEqual(accounts["deploy"]["primary_group"], "deploy")
        self.assertIsNone(accounts["orphan-user"]["primary_group"])

    def test_interactive_flag_follows_configured_shells(self) -> None:
        accounts = {account["account"]: account for account in self.report["accounts"]}

        self.assertFalse(accounts["svc-backup"]["interactive"])
        self.assertTrue(accounts["contractor"]["interactive"])

    def test_unregistered_accounts_and_roster_gaps(self) -> None:
        self.assertEqual(
            self.findings["unregistered"],
            ["dupe", "contractor", "orphan-user"],
        )
        self.assertEqual(
            [entry["account"] for entry in self.findings["roster_missing"]],
            ["ci-runner", "new-hire"],
        )

    def test_retired_and_out_of_scope_roster_entries_are_reported(self) -> None:
        self.assertEqual(self.findings["retired_present"], ["legacy"])
        self.assertEqual(
            [entry["account"] for entry in self.findings["roster_out_of_scope"]],
            ["rootcore"],
        )

    def test_non_interactive_accounts_in_scope(self) -> None:
        self.assertEqual(self.findings["non_interactive_in_scope"], ["svc-backup"])

    def test_orphan_gids_are_reported(self) -> None:
        self.assertEqual(
            self.findings["orphan_gid"],
            [
                {"account": "dupe", "gid": 1005},
                {"account": "orphan-user", "gid": 9999},
                {"account": "legacy", "gid": 1006},
            ],
        )

    def test_group_mismatch_is_reported(self) -> None:
        self.assertEqual(
            self.findings["group_mismatch"],
            [
                {
                    "account": "legacy",
                    "primary_group": None,
                    "expected_primary_group": "build",
                }
            ],
        )

    def test_duplicate_uid_is_reported(self) -> None:
        self.assertEqual(
            self.findings["duplicate_uid"],
            [{"uid": 1001, "accounts": ["deploy", "dupe"]}],
        )

    def test_malformed_line_counts_are_included(self) -> None:
        self.assertEqual(self.findings["malformed_passwd_lines"], 2)
        self.assertEqual(self.findings["malformed_group_lines"], 1)


if __name__ == "__main__":
    unittest.main()
