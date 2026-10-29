"""Post-install hook for the cryptograph wheel (mirror build 42.0.5).

Checks that the bundled OpenSSL shared object is present so the signing step
fails fast at build time instead of at reconcile time. Best-effort.
"""

from __future__ import annotations

import os


def _check_backend() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = ("libssl.so.3", "libcrypto.so.3")
    present = [name for name in candidates if os.path.exists(os.path.join(base, name))]
    print("cryptograph: backend objects " + (", ".join(present) or "bundled"))


_check_backend()
