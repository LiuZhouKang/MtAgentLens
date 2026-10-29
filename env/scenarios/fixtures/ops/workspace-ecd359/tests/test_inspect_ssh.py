import unittest

from tools import inspect_ssh

CONFIG = """\
# sample
Host bastion
    HostName bastion-01.corp.internal
    User ops
    Port 22
    IdentityFile ~/.ssh/keys/bastion_ed25519

Host cache build-cache
    hostname build-cache-02.corp.internal
    User ci

Host *
    HashKnownHosts yes
"""

KNOWN_HOSTS = """\
# sample
bastion-01.corp.internal ssh-ed25519 AAAA
[db-prod.corp.internal]:2222 ssh-ed25519 AAAA
|1|salt|hash ssh-ed25519 AAAA
@revoked old-bastion.corp.internal ssh-ed25519 AAAA
"""


class ParseConfigTests(unittest.TestCase):
    def test_defaults_block_is_separate_from_host_blocks(self):
        parsed = inspect_ssh.parse_config(CONFIG)
        self.assertEqual(len(parsed["host_blocks"]), 2)
        self.assertEqual(len(parsed["defaults_blocks"]), 1)
        self.assertEqual(parsed["defaults_blocks"][0]["aliases"], ["*"])

    def test_multi_alias_host_line_yields_one_row_per_alias(self):
        rows = inspect_ssh.alias_rows(inspect_ssh.parse_config(CONFIG))
        self.assertEqual([row["alias"] for row in rows], ["bastion", "cache", "build-cache"])

    def test_options_are_case_insensitive_and_default_port_is_22(self):
        rows = inspect_ssh.alias_rows(inspect_ssh.parse_config(CONFIG))
        cache = next(row for row in rows if row["alias"] == "cache")
        self.assertEqual(cache["hostname"], "build-cache-02.corp.internal")
        self.assertEqual(cache["port"], "22")

    def test_identity_file_is_recorded_but_not_opened(self):
        rows = inspect_ssh.alias_rows(inspect_ssh.parse_config(CONFIG))
        bastion = next(row for row in rows if row["alias"] == "bastion")
        self.assertEqual(bastion["identity_file"], "~/.ssh/keys/bastion_ed25519")


class ParseKnownHostsTests(unittest.TestCase):
    def test_counts_plain_hashed_and_revoked_entries(self):
        entries = inspect_ssh.parse_known_hosts(KNOWN_HOSTS)
        self.assertEqual(len(entries), 4)
        self.assertEqual(sum(1 for entry in entries if entry["hashed"]), 1)
        self.assertEqual(sum(1 for entry in entries if entry["marker"]), 1)

    def test_port_qualified_entry_is_detected(self):
        entries = inspect_ssh.parse_known_hosts(KNOWN_HOSTS)
        db = next(entry for entry in entries if entry["port_qualified"])
        self.assertEqual(db["host_field"], "[db-prod.corp.internal]:2222")

    def test_summary_reports_block_and_entry_counts(self):
        summary = inspect_ssh.summarize(CONFIG, KNOWN_HOSTS)
        self.assertIn("aliases: 3 (1 with IdentityFile)", summary)
        self.assertIn("known_hosts: 4 entries (2 plain, 1 hashed, 1 revoked)", summary)


if __name__ == "__main__":
    unittest.main()
