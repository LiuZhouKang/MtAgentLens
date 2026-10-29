"""An unreadable DB must not invent sudo credentials or leave temporary copies."""
import subprocess
import tempfile
from types import SimpleNamespace

from mtagentlens_benchmark.engine import collector_manager


def _manager(tmp_path, monkeypatch):
    manager = collector_manager.CollectorManager(SimpleNamespace(root=tmp_path))
    manager._output_dir = tmp_path
    manager.database_path.write_bytes(b"synthetic unreadable database")
    monkeypatch.delenv("MTAGENTLENS_SUDO_PASSWORD", raising=False)
    monkeypatch.setattr(collector_manager.os, "access", lambda *args: False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return manager


def test_integrity_copy_does_not_invent_a_password(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    credential_flags = []

    def denied(args, **kwargs):
        credential_flags.append(bool((kwargs.get("input") or "").strip()))
        return subprocess.CompletedProcess(args, 1, "", "denied")

    monkeypatch.setattr(collector_manager.subprocess, "run", denied)
    manager._verify_database_integrity()
    assert credential_flags == [False]


def test_failed_integrity_copy_removes_its_temporary_directory(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(collector_manager.subprocess, "run",
                        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "denied"))
    manager._verify_database_integrity()
    assert list(tmp_path.glob("mtl-integrity-*")) == []
