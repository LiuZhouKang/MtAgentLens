import tempfile
import unittest
from pathlib import Path

from oncall_handoff.inventory import count_services, read_service_names


class InventoryTests(unittest.TestCase):
    def test_reads_declared_services(self):
        text = "services:\n  - name: sync-api\n  - name: audit-relay\n"
        self.assertEqual(read_service_names(text), ["sync-api", "audit-relay"])
        self.assertEqual(count_services(text), 2)

    def test_ignores_unrelated_lines(self):
        text = "# inventory\n- name:\n  - name: 'worker'\n"
        self.assertEqual(read_service_names(text), ["worker"])

    def test_empty_inventory(self):
        self.assertEqual(read_service_names(""), [])
        self.assertEqual(count_services(""), 0)


if __name__ == "__main__":
    unittest.main()
