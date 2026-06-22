#!/usr/bin/env python3
"""Runtime-level RED test for producer-side tls_capture attach behavior."""

import os
import signal
import subprocess
import unittest


BPF_DIR = "/home/lzk/mtagentprism/bpf"
SSLSNIFF = os.path.join(BPF_DIR, "tls_capture")
DEFAULT_CLAUDE_BINARY = "/home/lzk/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"


def resolve_claude_binary() -> str | None:
    configured = os.environ.get("MTAGENTPRISM_CLAUDE_BINARY", DEFAULT_CLAUDE_BINARY)
    return configured if os.path.exists(configured) else None


class SslsniffRuntimeAttachTest(unittest.TestCase):
    def test_claude_binary_reaches_boringssl_offset_attach_path(self) -> None:
        sudo_password = os.environ.get("MTAGENTPRISM_SUDO_PASSWORD")
        if not sudo_password:
            self.skipTest(
                "Set MTAGENTPRISM_SUDO_PASSWORD to run the runtime attach test with sudo"
            )

        claude_binary = resolve_claude_binary()
        if not claude_binary:
            self.skipTest(
                "Set MTAGENTPRISM_CLAUDE_BINARY to an existing Claude ELF to run the runtime attach test"
            )

        attach_failure = (
            f"Failed to attach to {claude_binary}: no SSL symbols or BoringSSL patterns found"
        )

        cmd = [
            "sudo",
            "-S",
            SSLSNIFF,
            "-v",
            "--binary-path",
            claude_binary,
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        try:
            assert proc.stdin is not None
            proc.stdin.write(f"{sudo_password}\n")
            proc.stdin.flush()

            try:
                output, _ = proc.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGINT)
                output, _ = proc.communicate(timeout=5)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)

        self.assertIn(
            "Symbols not found, trying BoringSSL pattern detection...",
            output,
            "expected runtime test to exercise the real BoringSSL fallback path",
        )
        self.assertIn(
            "BoringSSL detected! Attaching by offset...",
            output,
            "current Claude ELF should reach producer-side BoringSSL offset attach",
        )
        self.assertNotIn(
            attach_failure,
            output,
            "runtime attach should not report missing SSL symbols and BoringSSL patterns",
        )


if __name__ == "__main__":
    unittest.main()
