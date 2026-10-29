import unittest

from preflight import classify, summarise_template

TEMPLATE = """\
# candidate drop-in
Defaults:runner !requiretty
Defaults:runner env_keep += "EDITOR VISUAL LD_PRELOAD"
runner ALL=(root) NOPASSWD: /usr/bin/sudoedit /opt/runner/etc/*.conf
%operators ALL=(ALL) NOPASSWD: ALL
runner ALL=(root) NOPASSWD: /usr/local/bin/runner-diag --collect
runner ALL=(root) PASSWD: /usr/bin/journalctl -u runner
"""


class TestClassify(unittest.TestCase):
    def test_comment_is_ignored(self):
        self.assertEqual(classify("# runner diagnostics"), "ignore")

    def test_blank_is_ignored(self):
        self.assertEqual(classify("   "), "ignore")

    def test_env_keep_editor_is_critical(self):
        self.assertEqual(
            classify('Defaults:runner env_keep += "EDITOR"'), "critical"
        )

    def test_env_keep_loader_is_critical(self):
        self.assertEqual(
            classify('Defaults:runner env_keep += "LD_PRELOAD"'), "critical"
        )

    def test_env_keep_benign_is_low(self):
        self.assertEqual(
            classify('Defaults:runner env_keep += "LANG LC_ALL"'), "low"
        )

    def test_sudoedit_with_glob_is_critical(self):
        self.assertEqual(
            classify("runner ALL=(root) NOPASSWD: /usr/bin/sudoedit /opt/*.conf"),
            "critical",
        )

    def test_sudoedit_without_glob_is_medium(self):
        self.assertEqual(
            classify(
                "runner ALL=(root) NOPASSWD: /usr/bin/sudoedit /opt/runner/etc/app.conf"
            ),
            "medium",
        )

    def test_nopasswd_all_is_high(self):
        self.assertEqual(classify("%operators ALL=(ALL) NOPASSWD: ALL"), "high")

    def test_scoped_nopasswd_is_medium(self):
        self.assertEqual(
            classify("runner ALL=(root) NOPASSWD: /usr/local/bin/runner-diag --collect"),
            "medium",
        )

    def test_scoped_password_directive_is_low(self):
        self.assertEqual(
            classify("runner ALL=(root) PASSWD: /usr/bin/journalctl -u runner"),
            "low",
        )


class TestSummary(unittest.TestCase):
    def test_counts_by_label(self):
        counts = summarise_template(TEMPLATE)
        self.assertEqual(counts["critical"], 2)
        self.assertEqual(counts["high"], 1)
        self.assertEqual(counts["medium"], 1)
        self.assertEqual(counts["low"], 2)

    def test_empty_template_has_no_counts(self):
        self.assertEqual(summarise_template("\n# only comments\n"), {})


if __name__ == "__main__":
    unittest.main()
