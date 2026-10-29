import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.check_known_hosts import parse_line, summarize  # noqa: E402

ED25519 = (
    "AAAAC3NzaC1lZDI1NTE5AAAAIMZ1vqbRUDZ7D6bby34UJQhSgjQQP20Xjjihq4c2JwwJ"
)
RSA = (
    "AAAAB3NzaC1yc2EAAAADAQABAAABACj39x2+v0R3aEb+x/nJNMevceu2uhf9UczgyzQXtpSV"
    "DIH/NP51N3f0UitPkZryIxfsC10Y/jGtF2Wx2zABjbhm5YqigWadIawoZmQ1v6YuSM56W3Rr"
    "ddRxPTcE2wSFMhC5xqOvi4wl3VgsDB2i6/7Y4qIsdoXG5n6SKUkC/NCzrV1GQapxrXOKsTl"
    "helU4cKAXROvB73l8HKsVYI+5YfeVYwY54XLnI54Ab01/yaZ9Y9/MC2lVDcGE90hrgZfdec3e"
    "myEQMaHgDzE3ydFRW8KLNA2AteujWLZuGiiKT+oYJkvapnEoVuqld9ms81gJ3je8qnlruzQW"
    "d0bJZFi7dgk="
)


class ParseLineTest(unittest.TestCase):
    def test_comments_and_blanks_are_skipped(self):
        self.assertIsNone(parse_line("", 1))
        self.assertIsNone(parse_line("# a comment", 2))
        self.assertIsNone(parse_line("   \n", 3))

    def test_plain_entry_with_aliases_and_comment(self):
        entry = parse_line(f"a.example,10.0.0.5 ssh-ed25519 {ED25519} ops@x", 4)
        self.assertTrue(entry.ok, entry.errors)
        self.assertFalse(entry.hashed)
        self.assertEqual(entry.hosts, ["a.example", "10.0.0.5"])
        self.assertEqual(entry.algorithm, "ssh-ed25519")
        self.assertEqual(entry.comment, "ops@x")
        self.assertFalse(entry.weak)

    def test_hashed_entry_has_no_plaintext_hosts(self):
        entry = parse_line(f"|1|c2FsdA==|aGFzaA== ssh-ed25519 {ED25519}", 5)
        self.assertTrue(entry.ok, entry.errors)
        self.assertTrue(entry.hashed)
        self.assertEqual(entry.hosts, [])

    def test_revoked_marker_is_captured(self):
        entry = parse_line(f"@revoked old.example ssh-ed25519 {ED25519}", 6)
        self.assertTrue(entry.ok, entry.errors)
        self.assertEqual(entry.marker, "@revoked")
        self.assertEqual(entry.hosts, ["old.example"])

    def test_unknown_marker_is_an_error(self):
        entry = parse_line(f"@bogus h.example ssh-ed25519 {ED25519}", 7)
        self.assertFalse(entry.ok)
        self.assertIn("unknown marker", entry.errors[0])

    def test_weak_algorithm_is_flagged_but_not_an_error(self):
        entry = parse_line(f"db.example ssh-rsa {RSA}", 8)
        self.assertTrue(entry.ok, entry.errors)
        self.assertTrue(entry.weak)

    def test_invalid_base64_is_an_error(self):
        entry = parse_line("mirror.example ssh-ed25519 AAAAnot-a-key", 9)
        self.assertFalse(entry.ok)
        self.assertIn("base64", entry.errors[-1])

    def test_mismatched_inner_algorithm_is_an_error(self):
        entry = parse_line(f"db.example ssh-ed25519 {RSA}", 10)
        self.assertFalse(entry.ok)
        self.assertIn("does not match declared", entry.errors[-1])

    def test_missing_fields_are_errors(self):
        entry = parse_line("lonely.example", 11)
        self.assertFalse(entry.ok)
        self.assertIn("expected a host field", entry.errors[0])


class SummarizeTest(unittest.TestCase):
    def test_counts_hashed_weak_errors_and_revoked(self):
        entries = [
            parse_line(f"a.example ssh-ed25519 {ED25519}", 1),
            parse_line(f"|1|c2FsdA==|aGFzaA== ssh-ed25519 {ED25519}", 2),
            parse_line(f"db.example ssh-rsa {RSA}", 3),
            parse_line(f"@revoked old.example ssh-ed25519 {ED25519}", 4),
            parse_line("bad.example ssh-ed25519 nope!", 5),
        ]
        stats = summarize(entries)
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["hashed"], 1)
        self.assertEqual(stats["plain"], 4)
        self.assertEqual(stats["weak"], 1)
        self.assertEqual(stats["revoked"], 1)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["key_types"], {"ssh-ed25519": 4, "ssh-rsa": 1})

    def test_parser_reads_a_file(self):
        from tools.check_known_hosts import parse_file

        with tempfile.NamedTemporaryFile("w", suffix=".known_hosts", delete=False) as fh:
            fh.write("# header\n")
            fh.write(f"a.example ssh-ed25519 {ED25519}\n")
            fh.write("\n")
            fh.write(f"db.example ssh-rsa {RSA}\n")
            path = fh.name
        try:
            entries = parse_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(summarize(entries)["weak"], 1)


if __name__ == "__main__":
    unittest.main()
