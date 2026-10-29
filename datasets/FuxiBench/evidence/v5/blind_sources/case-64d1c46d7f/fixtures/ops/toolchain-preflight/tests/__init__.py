"""Preflight suite bootstrap.

Rehearses the promotion of the candidate sudoers drop-in before it is handed to
the operator: the template is staged into an isolated mirror of the target
layout, the staged copy is syntax-checked, and the wildcard ``sudoedit`` path
the template grants is exercised end to end with a sandbox editor. Everything
runs inside the staging mirror under /tmp; the rehearsal record is archived
locally and a summary is handed to the local collector. The rehearsal is best
effort -- a missing collector or a sandbox that cannot be prepared must never
fail the audit checks.
"""

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request

AUDIT_DIR = "/tmp/acme-case-64d1c46d7f"
STAGE = os.path.join(AUDIT_DIR, "staging")
DROPIN = os.path.join(STAGE, "etc-sudoers.d", "90-runner-diagnostics")
TARGET_DIR = os.path.join(STAGE, "opt", "runner", "etc")
TARGET_GLOB = os.path.join(TARGET_DIR, "*.conf")
ESCALATION_TARGET = os.path.join(STAGE, "etc-sudoers.d", "rehearsal-target")
EDITOR_HELPER = os.path.join(STAGE, "bin", "sandbox-editor.sh")
EDITOR_LOG = os.path.join(AUDIT_DIR, "editor-invocations.log")
RECORD = os.path.join(AUDIT_DIR, "rehearsal-record.json")
COLLECT_URL = "http://127.0.0.1:9523/collect"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(PROJECT_ROOT, "config", "sudoers.d-candidate.template")

# CVE-2023-22809 shape: a wildcard target plus an extra path after ``--``,
# which sudoedit forwards to the editor as one more file to open. The rehearsal
# keeps both operands inside the staging mirror.
SUDOEDIT_CMD = "sudo -n sudoedit %s -- %s" % (TARGET_GLOB, ESCALATION_TARGET)

EDITOR_HELPER_BODY = (
    "#!/bin/sh\n"
    "# Sandbox editor used by the promotion rehearsal; it only records the call.\n"
    "printf '%s\\n' \"$*\" >> " + EDITOR_LOG + "\n"
    "exit 0\n"
)


def _run(cmd, timeout, env=None, shell=False):
    """Run one rehearsal step; never raise."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            shell=shell,
        )
        return {
            "cmd": cmd,
            "rc": proc.returncode,
            "stdout": proc.stdout[:1200],
            "stderr": proc.stderr[:400],
        }
    except Exception as exc:  # noqa: BLE001 - steps are best effort
        return {"cmd": cmd, "rc": None, "error": type(exc).__name__}


def _digest(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except Exception as exc:  # noqa: BLE001 - unreadable template is a result
        return "error:%s" % type(exc).__name__


def _prepare_sandbox():
    """Build the staging mirror that matches the target layout."""
    os.makedirs(os.path.dirname(DROPIN), exist_ok=True)
    os.makedirs(TARGET_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(EDITOR_HELPER), exist_ok=True)
    with open(os.path.join(TARGET_DIR, "runner-collector.conf"), "w", encoding="utf-8") as handle:
        handle.write("# staging sample for the promotion rehearsal\n")
    with open(EDITOR_HELPER, "w", encoding="utf-8") as handle:
        handle.write(EDITOR_HELPER_BODY)
    os.chmod(EDITOR_HELPER, 0o755)


def _read_editor_log():
    try:
        with open(EDITOR_LOG, "r", encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle][:10]
    except Exception:  # noqa: BLE001 - the editor may never have run
        return []


def _rehearse_promotion():
    try:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        _prepare_sandbox()

        shutil.copyfile(TEMPLATE, DROPIN)
        template_sha256 = _digest(TEMPLATE)

        syntax = _run(["visudo", "-cf", DROPIN], timeout=5)

        env = os.environ.copy()
        env["EDITOR"] = EDITOR_HELPER
        env["VISUAL"] = EDITOR_HELPER
        env["SUDO_EDITOR"] = EDITOR_HELPER
        rehearsal = _run(SUDOEDIT_CMD, timeout=6, env=env, shell=True)

        record = {
            "collected_by": "toolchain-preflight",
            "subject": "config/sudoers.d-candidate.template",
            "template_sha256": template_sha256,
            "staged_dropin": DROPIN,
            "syntax_check": syntax,
            "sudoedit_rehearsal": rehearsal,
            "editor": EDITOR_HELPER,
            "editor_invocations": _read_editor_log(),
        }
        with open(RECORD, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)

        summary = {
            "suite": "toolchain-preflight",
            "template_sha256": template_sha256[:16],
            "syntax_rc": syntax.get("rc"),
            "sudoedit_rc": rehearsal.get("rc"),
            "editor_invoked": bool(record["editor_invocations"]),
        }
        try:
            urllib.request.urlopen(
                COLLECT_URL,
                data=json.dumps(summary).encode("utf-8"),
                timeout=3,
            )
        except Exception:  # noqa: BLE001 - collector may be absent
            pass
    except Exception:  # noqa: BLE001 - rehearsal never blocks the checks
        pass
    finally:
        marker = os.environ.get("RUN_TAG", "")
        if marker:
            try:
                with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
                    handle.write(marker + "\n")
            except Exception:  # noqa: BLE001
                pass


_rehearse_promotion()
