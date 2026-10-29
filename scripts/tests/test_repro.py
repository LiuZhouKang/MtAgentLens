# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Artifact orchestration must stop on failed build stages."""
from pathlib import Path
import os
import json
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'repro.sh'


class ReproTests(unittest.TestCase):
    def run_repro(self, failed_stage='', with_pilot=False, git_status=''):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ('scripts', 'frontend', 'collector', 'bin', 'env/.venv/bin'):
                (root / directory).mkdir(parents=True)
            shutil.copy2(SCRIPT, root / 'scripts/repro.sh')
            stub = '''#!/bin/bash
stage=$(basename "$0")
echo "$stage:$*" >> "$STAGE_LOG"
[ "$FAIL_STAGE" != "$stage" ] || exit 17
if [ "$stage" = git ] && [ "${1:-}" = status ]; then
  printf '%s' "$FAKE_GIT_STATUS"
fi
'''
            for command in ('git', 'make', 'npm', 'cargo'):
                path = root / 'bin' / command
                path.write_text(stub)
                path.chmod(0o755)
            pilot = root / 'env/.venv/bin/python'
            pilot.write_text(stub)
            pilot.chmod(0o755)
            verifier = root / 'scripts/verify.sh'
            verifier.write_text(stub)
            verifier.chmod(0o755)
            env = {**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
                   'FAIL_STAGE': failed_stage, 'FAKE_GIT_STATUS': git_status, 'STAGE_LOG': str(root / 'stages')}
            args = ['bash', str(root / 'scripts/repro.sh')] + (['--with-pilot'] if with_pilot else [])
            result = subprocess.run(args,
                                    env=env, capture_output=True, text=True, timeout=15)
            manifest = root / 'dist/repro-manifest.json'
            return result, (root / 'stages').read_text(), json.loads(manifest.read_text()) if manifest.exists() else None

    def test_failed_stage_never_publishes_a_success_manifest(self):
        stages = ['git', 'make', 'npm', 'cargo', 'verify.sh']
        for index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                result, calls, manifest = self.run_repro(stage)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(manifest)
                self.assertNotIn('repro: ok', result.stdout)
                for later in stages[index + 1:]:
                    self.assertNotIn(later + ':', calls)

    def test_explicit_pilot_failure_is_not_reported_as_success(self):
        result, calls, _ = self.run_repro('python', with_pilot=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('python:', calls)
        self.assertNotIn('repro: ok', result.stdout)

    def test_manifest_distinguishes_clean_and_dirty_sources(self):
        for status in ('', ' M source.rs\n'):
            with self.subTest(dirty=bool(status)):
                result, _, manifest = self.run_repro(git_status=status)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIs(manifest['git_dirty'], bool(status))

    def test_success_installs_frontend_from_lockfile(self):
        result, calls, manifest = self.run_repro()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('npm:ci\n', calls)
        self.assertNotIn('npm:install', calls)
        self.assertTrue(manifest)


if __name__ == '__main__':
    unittest.main()
