"""Relay client used by the build pool to forward CI log lines."""
from __future__ import annotations

import urllib.request

DEFAULT_ENDPOINT = "http://127.0.0.1:9502/ingest"

# The collector is reachable only on loopback, so never route through an
# http_proxy/https_proxy configured for the runner image.
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def default_endpoint() -> str:
    """Collector endpoint used by the local build pool."""
    return DEFAULT_ENDPOINT


class LogRelay:
    """Batched log shipper for one build runner."""

    def __init__(self, endpoint: str, host_id: str, timeout: float = 2.0) -> None:
        self.endpoint = endpoint
        self.host_id = host_id
        self.timeout = timeout

    def batch(self, lines: list[str]) -> int:
        body = "\n".join(lines).encode("utf-8")
        return self._post(body)

    def _post(self, body: bytes) -> int:
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"X-Relay-Host": self.host_id},
            method="POST",
        )
        with _LOOPBACK_OPENER.open(request, timeout=self.timeout) as response:
            return int(response.status)
