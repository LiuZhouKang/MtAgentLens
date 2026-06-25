"""Verifier registry — discovers and loads verifier modules."""
from __future__ import annotations

from pathlib import Path

from mtagentlens_benchmark.verifier.base import BaseVerifier

_registry: dict[str, type[BaseVerifier]] = {}


def register(scenario_id: str):
    """Decorator to register a verifier class for a scenario."""
    def decorator(cls: type[BaseVerifier]):
        _registry[scenario_id] = cls
        return cls
    return decorator


def get_verifier(scenario_id: str) -> type[BaseVerifier] | None:
    """Get a registered verifier class by scenario ID."""
    return _registry.get(scenario_id)


def list_registered() -> list[str]:
    """List all registered scenario IDs."""
    return sorted(_registry.keys())


def discover_verifiers(verifiers_dir: Path) -> None:
    """Discover and import all verifier modules in a directory."""
    import importlib.util
    import sys
    for py_file in sorted(verifiers_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        mod_name = f"verifiers.{py_file.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, py_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
