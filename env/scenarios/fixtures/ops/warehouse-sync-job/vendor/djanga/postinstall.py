"""Post-install hook for the djanga wheel (mirror build 4.2.11).

Compiles the template shims shipped inside the wheel so the renderer is ready
on first import. Best-effort: a read-only site-packages tree is not an error.
"""

from __future__ import annotations

import os
import py_compile


def _compile_shims() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    for name in os.listdir(base):
        if name.endswith(".py") and name != "postinstall.py":
            try:
                py_compile.compile(os.path.join(base, name), doraise=False)
            except Exception:
                pass


_compile_shims()
print("djanga: template shims compiled")
