import unittest

from release import render_summary


class TestRenderSummary(unittest.TestCase):
    def test_renders_rows(self):
        rows = [{"name": "build", "status": "ok"}, {"name": "tests", "status": "ok"}]
        self.assertEqual(render_summary(rows), "build: ok\ntests: ok")
