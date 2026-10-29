"""Unit tests for the account-audit parser (synthetic data, no host access)."""

from __future__ import annotations

import unittest

from src.account_audit import build_accounts, parse_group, parse_passwd, parse_scope

PASSWD = """\
root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
alice:x:1000:1000:Alice A,,,:/srv/people/alice:/bin/bash
bob:x:1001:1001:Bob B:/srv/people/bob:/bin/zsh
svc-build:x:1002:1002:CI worker:/var/lib/svc-build:/usr/sbin/nologin
orphan:x:1003:4242:No group:/srv/people/orphan:/bin/bash
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
"""

GROUP = """\
root:x:0:
daemon:x:1:
alice:x:1000:
bob:x:1001:
svc-build:x:1002:
nogroup:x:65534:
"""

SCOPE = """\
# review parameters
uid_min: 1000
uid_max: 60000
non_interactive_shells:
  - /usr/sbin/nologin
  - /bin/false
"""


class ParsePasswdTests(unittest.TestCase):
    def test_skips_service_accounts_and_comments(self):
        records = parse_passwd("# comment\n" + PASSWD)
        names = [record["account"] for record in records]
        self.assertIn("alice", names)
        self.assertEqual(records[0]["uid"], 0)

    def test_gecos_commas_do_not_shift_fields(self):
        records = {record["account"]: record for record in parse_passwd(PASSWD)}
        self.assertEqual(records["alice"]["gecos"], "Alice A,,,")
        self.assertEqual(records["alice"]["home"], "/srv/people/alice")

    def test_malformed_lines_are_skipped(self):
        records = parse_passwd("broken:x:notanumber:1000::/srv/opt/x:/bin/sh\n")
        self.assertEqual(records, [])


class ParseGroupTests(unittest.TestCase):
    def test_maps_gid_to_name(self):
        groups = parse_group(GROUP)
        self.assertEqual(groups[1000], "alice")
        self.assertEqual(groups[1001], "bob")
        self.assertNotIn(4242, groups)


class ScopeTests(unittest.TestCase):
    def test_parses_range_and_shell_list(self):
        scope = parse_scope(SCOPE)
        self.assertEqual(scope["uid_min"], 1000)
        self.assertEqual(scope["uid_max"], 60000)
        self.assertEqual(scope["non_interactive_shells"], ["/usr/sbin/nologin", "/bin/false"])

    def test_defaults_when_file_is_empty(self):
        scope = parse_scope("")
        self.assertEqual(scope["uid_min"], 1000)
        self.assertEqual(scope["non_interactive_shells"], [])


class BuildAccountsTests(unittest.TestCase):
    def setUp(self):
        self.accounts, self.skipped = build_accounts(
            parse_passwd(PASSWD), parse_group(GROUP), parse_scope(SCOPE)
        )
        self.by_name = {account.account: account for account in self.accounts}

    def test_keeps_only_uid_range(self):
        self.assertEqual(
            [account.account for account in self.accounts],
            ["alice", "bob", "svc-build", "orphan"],
        )
        self.assertEqual(self.skipped, 3)

    def test_marks_non_interactive_shells(self):
        self.assertTrue(self.by_name["alice"].interactive)
        self.assertFalse(self.by_name["svc-build"].interactive)

    def test_flags_missing_primary_group(self):
        self.assertIsNone(self.by_name["orphan"].primary_group)
        self.assertEqual(self.by_name["alice"].primary_group, "alice")

    def test_sorted_by_uid(self):
        uids = [account.uid for account in self.accounts]
        self.assertEqual(uids, sorted(uids))


if __name__ == "__main__":
    unittest.main()
