# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Repository policy: privilege helpers never supply a default password."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
KEY = 'MTAGENTLENS_SUDO_PASSWORD'


class CredentialDefaultsTests(unittest.TestCase):
    def test_python_helpers_do_not_invent_credentials(self):
        violations = []
        sources = list((ROOT / 'scripts').rglob('*.py'))
        sources += list((ROOT / 'env/src').rglob('*.py'))
        sources += list((ROOT / 'compare/scripts').rglob('*.py'))
        for path in sources:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {'get', 'setdefault'} and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == KEY and len(node.args) >= 2):
                    fallback = node.args[1]
                    if not isinstance(fallback, ast.Constant) or fallback.value not in ('', None):
                        violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], 'Implicit sudo credentials in these helpers')

    def test_legacy_entry_preserves_callers_credentials(self):
        path = ROOT / 'env/run_one.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant) and node.slice.value == KEY
            and ast.unparse(node.value) == 'os.environ'
        ]
        self.assertEqual(assignments, [], 'Entry must preserve the caller environment')

    def test_shell_helpers_do_not_invent_credentials(self):
        violations = []
        for path in (ROOT / 'scripts').rglob('*.sh'):
            defaults = re.findall(r'\$\{MTAGENTLENS_SUDO_PASSWORD(?::?[-=])([^}]*)\}',
                                  path.read_text(encoding='utf-8'))
            if any(value not in ('', '""', "''") for value in defaults):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], 'Implicit sudo credentials in these helpers')


if __name__ == '__main__':
    unittest.main()
