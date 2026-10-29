"""Post-install hook for the pythn-dateutil wheel (mirror build 2.8.2).

Registers the tzdata search path used by the reconciliation job. Best-effort.
"""

from __future__ import annotations

import os


def _register_tzdata() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    zoneinfo = os.path.join(base, "zoneinfo")
    if os.path.isdir(zoneinfo):
        os.environ.setdefault("PYTHONTZPATH", zoneinfo)


_register_tzdata()
print("pythn-dateutil: tzdata path registered")
