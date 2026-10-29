"""Request routing helpers for the api-gateway service."""
from __future__ import annotations

ROUTES = {
    "GET /health": "health",
    "GET /v1/orders": "list-orders",
    "POST /v1/orders": "create-order",
    "GET /v1/customers": "list-customers",
}


def normalize_path(path: str) -> str:
    """Collapse duplicate separators and trim the trailing separator."""
    if not path.startswith("/"):
        path = "/" + path
    segments = [segment for segment in path.split("/") if segment]
    return "/" + "/".join(segments) if segments else "/"


def match_route(routes: dict[str, str], method: str, path: str) -> str:
    """Resolve a request to a route handler name, or ``404``."""
    key = f"{method.upper()} {normalize_path(path)}"
    return routes.get(key, "404")
