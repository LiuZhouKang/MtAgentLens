# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Artifact orchestration must stop on failed build stages."""
from pathlib import Path
import os
import json
import runpy
import shutil
import subprocess
import tempfile
import tarfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'repro.sh'
PACKAGE_SCRIPT = SCRIPT.parent.parent / 'compare/package.sh'


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.compare = self.root / 'compare'
        self.compare.mkdir()
        shutil.copy2(PACKAGE_SCRIPT, self.compare / 'package.sh')
        for name in ('README.md', 'MANIFEST.md', 'results/keep.json', 'docs/keep.md', 'datasets/contract.md'):
            self.write_input(name)

    def write_input(self, name):
        path = self.compare / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic package fixture\n')

    def run_package(self, output):
        return subprocess.run(['bash', str(self.compare / 'package.sh'), str(output)],
                              capture_output=True, text=True, timeout=15)

    def test_package_keeps_evidence_archives_and_filters_local_artifacts(self):
        for name in (
            'results/external/baselines/app/A2/records/raw.json',
            'results/external/baselines/app/A2/traces/raw.json',
            'results/sample.db', 'results/sample.db-wal', 'results/sample.db-shm',
            'results/sample.log', 'results/__pycache__/cached.pyc',
            'results/.pytest_cache/state', 'results/.venv-check/bin/python',
            'results/models/model.bin', 'results/Dataset/input.json',
            'tools/not-in-package.py', '实验.zip', 'download.tar.gz',
            'results/overhead/archives/evidence.tar.gz',
        ):
            self.write_input(name)
        output = self.root / 'handoff.tar.gz'
        result = self.run_package(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(output) as archive:
            files = {entry.name for entry in archive if entry.isfile()}
        self.assertEqual(files, {
            'README.md', 'MANIFEST.md', 'package.sh', 'results/keep.json',
            'docs/keep.md', 'datasets/contract.md', 'results/overhead/archives/evidence.tar.gz',
        })

    def test_package_rejects_output_inside_inputs(self):
        output = self.compare / 'docs/self.tar.gz'
        result = self.run_package(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('output must be outside', result.stderr)
        self.assertFalse(output.exists())

    def test_package_rejects_missing_manifest_before_creating_archive(self):
        (self.compare / 'MANIFEST.md').unlink()
        output = self.root / 'handoff.tar.gz'
        result = self.run_package(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing MANIFEST.md', result.stderr)
        self.assertFalse(output.exists())


class ExperimentInputPathTests(unittest.TestCase):
    def test_reproduction_defaults_use_delivered_inputs(self):
        root = SCRIPT.parent.parent
        for experiment, inputs in (
            ('ablation', {
                'DATA': 'compare/results/ablation/data/exports',
                'PROD': 'compare/results/detection/data/detection-metrics-final-93.json',
                'LEDGER': 'compare/results/detection/data/results-promoted.jsonl',
            }),
            ('sensitivity', {
                'EXPORTS': 'compare/results/sensitivity/data/raw/exports',
                'PROD': 'compare/results/sensitivity/data/raw/detection-metrics-final-93.json',
                'LEDGER': 'compare/results/sensitivity/data/raw/results-promoted.jsonl',
                'FAMILIES': 'compare/results/sensitivity/data/raw/families.json',
            }),
        ):
            with self.subTest(experiment=experiment):
                namespace = runpy.run_path(str(
                    root / 'compare/results' / experiment / 'reproduce'
                    / ('build_metrics.py' if experiment == 'ablation' else 'build_sensitivity.py')
                ))
                for name, relative_path in inputs.items():
                    path = namespace[name]
                    self.assertNotIn('local', path.relative_to(root).parts)
                    self.assertEqual(path, root / relative_path)


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
