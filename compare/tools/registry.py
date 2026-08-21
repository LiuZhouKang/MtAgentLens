from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any

from compare.tools.base import ToolAdapter, ToolMetadata

_REQUIRED_FIELDS = {
    "id",
    "name",
    "class",
    "source_url",
    "license",
    "install",
    "adapter",
    "input_modes",
    "score_scope",
    "inclusion_rationale",
}
_ALLOWED_INPUT_MODES = {"static", "exported_events", "saved_logs", "prompt_text"}
_ALLOWED_SCORE_SCOPE = "main_offline"
_SAFE_ADAPTER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_ROUND1_PROHIBITED_METADATA_PATTERNS = (
    re.compile(r"\bcloud\b"),
    re.compile(r"\bllm"),
    re.compile(r"\bruntime\b"),
    re.compile(r"\btrace\s*db\b"),
    re.compile(r"\bopenai\b"),
    re.compile(r"\banthropic\b"),
    re.compile(r"\bclaim"),
    re.compile(r"\balert"),
)
_NEGATED_CLOUD_API_RE = re.compile(r"\b(?:no|without)\s+cloud\s+api\b")
_METADATA_SEPARATOR_RE = re.compile(r"[_\-./]+")
_FLOATING_VERSION_VALUES = {"unknown", "builtin", "latest", "head", "main", "master"}
_FLOATING_REF_RE = re.compile(r"^(?:refs/heads/|refs/remotes/|remotes/[^/]+/|origin/|branch/)|@(latest|head)$")
_ALLOWED_BUILTIN_TOOL = {
    "id": "fixture-scanner",
    "adapter": "fixture_scanner",
    "source_url": "builtin://compare/tools/adapters/fixture_scanner.py",
}


def load_tool_metadata(lockfile: Path) -> list[ToolMetadata]:
    with lockfile.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError("lockfile root must be a JSON object")

    return validate_lockfile(data, allow_prompt_tools=lockfile.name.endswith(".prompt.json"))


def validate_lockfile(data: dict[str, Any], *, allow_prompt_tools: bool = False) -> list[ToolMetadata]:
    if not isinstance(data, dict):
        raise ValueError("lockfile root must be a JSON object")

    tools = data.get("tools")
    if not isinstance(tools, list):
        raise ValueError("tools must be a list")

    metadata: list[ToolMetadata] = []
    seen_ids: set[str] = set()

    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"tool entry at index {index} must be an object")

        missing = sorted(_REQUIRED_FIELDS - tool.keys())
        if missing:
            raise ValueError(f"tool entry at index {index} missing required fields: {', '.join(missing)}")

        tool_id = _require_string(tool, "id", index)
        if tool_id in seen_ids:
            raise ValueError(f"duplicate tool id: {tool_id}")
        seen_ids.add(tool_id)

        install = _require_string_list(tool, "install", index)
        input_modes = _require_string_list(tool, "input_modes", index)
        disallowed_modes = [mode for mode in input_modes if mode not in _ALLOWED_INPUT_MODES]
        if disallowed_modes:
            raise ValueError(f"input mode(s) not allowed in round 1 for {tool_id}: {', '.join(disallowed_modes)}")
        if not allow_prompt_tools and "prompt_text" in input_modes:
            raise ValueError(f"prompt_text input mode is allowed only in the prompt baseline lockfile for {tool_id}")

        score_scope = _require_string(tool, "score_scope", index)
        if score_scope != _ALLOWED_SCORE_SCOPE:
            raise ValueError(f"score_scope not allowed in round 1 for {tool_id}: {score_scope}")

        adapter = _require_string(tool, "adapter", index)
        if not _SAFE_ADAPTER_RE.fullmatch(adapter):
            raise ValueError(f"adapter for {tool_id} must be a simple safe module name")

        source_url = _require_string(tool, "source_url", index)
        is_builtin = _is_allowed_builtin_tool(tool_id, adapter, source_url)
        if not allow_prompt_tools:
            _reject_round1_prohibited_metadata(tool, tool_id)
        _reject_unknown_builtin_source(tool_id, source_url, is_builtin)

        metadata.append(
            ToolMetadata(
                id=tool_id,
                name=_require_string(tool, "name", index),
                tool_class=_require_string(tool, "class", index),
                source_url=source_url,
                version=_read_version(tool, is_builtin),
                license=_require_string(tool, "license", index),
                install=tuple(install),
                adapter=adapter,
                input_modes=tuple(input_modes),
                score_scope=score_scope,
                inclusion_rationale=_require_string(tool, "inclusion_rationale", index),
            )
        )

    return metadata


def instantiate_adapter(metadata: ToolMetadata) -> ToolAdapter:
    if not _SAFE_ADAPTER_RE.fullmatch(metadata.adapter):
        raise ValueError("adapter must be a simple safe module name")

    module = importlib.import_module(f"compare.tools.adapters.{metadata.adapter}")
    adapter_class = getattr(module, "Adapter")
    return adapter_class(metadata)


def _require_string(tool: dict[str, Any], field: str, index: int) -> str:
    value = tool[field]
    if not isinstance(value, str):
        raise ValueError(f"tool entry at index {index} field {field} must be a string")
    return value


def _require_string_list(tool: dict[str, Any], field: str, index: int) -> list[str]:
    value = tool[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"tool entry at index {index} field {field} must be a list of strings")
    return value


def _read_version(tool: dict[str, Any], is_builtin: bool) -> str:
    pinned_value: str | None = None
    for field in ("version", "commit", "tag"):
        value = tool.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"tool version field {field} must be a string")
        stripped_value = value.strip()
        normalized_value = stripped_value.lower()
        is_allowed_builtin_version = is_builtin and field == "version" and normalized_value == "builtin"
        if stripped_value == "" or (
            not is_allowed_builtin_version
            and (normalized_value in _FLOATING_VERSION_VALUES or _FLOATING_REF_RE.search(normalized_value))
        ):
            raise ValueError("non-builtin tools must declare a reproducible version, commit, or tag")
        if pinned_value is None:
            pinned_value = value

    if pinned_value is not None:
        return pinned_value

    if is_builtin:
        return "builtin"

    raise ValueError("non-builtin tools must declare a reproducible version, commit, or tag")


def _is_allowed_builtin_tool(tool_id: str, adapter: str, source_url: str) -> bool:
    return (
        tool_id == _ALLOWED_BUILTIN_TOOL["id"]
        and adapter == _ALLOWED_BUILTIN_TOOL["adapter"]
        and source_url == _ALLOWED_BUILTIN_TOOL["source_url"]
    )


def _reject_unknown_builtin_source(tool_id: str, source_url: str, is_builtin: bool) -> None:
    if source_url.startswith("builtin://") and not is_builtin:
        raise ValueError(f"unknown builtin tool for {tool_id}: {source_url}")


def _reject_round1_prohibited_metadata(tool: dict[str, Any], tool_id: str) -> None:
    metadata_text = _normalized_metadata_text(tool)
    # Check for "no cloud API" / "local" qualifier that exempts from LLM prohibition
    has_local_qualifier = bool(
        _NEGATED_CLOUD_API_RE.search(metadata_text)
        or re.search(r"\blocal(?:ly)?\b", metadata_text)
        or re.search(r"\bno\s+cloud\b", metadata_text)
    )
    for pattern in _PROHIBITED_METADATA_PATTERNS:
        if pattern.search(metadata_text):
            # Exempt local-only tools from LLM/runtime prohibition (round 2+ expansion)
            if has_local_qualifier and pattern.pattern in (r"\bllm\b", r"\bruntime\b"):
                continue
            raise ValueError(f"prohibited round 1 tool metadata for {tool_id}: {pattern.pattern}")


def _normalized_metadata_text(tool: dict[str, Any]) -> str:
    chunks = list(_iter_metadata_strings(tool))
    raw_text = " ".join(chunks).lower()
    raw_text_without_exact_negated_cloud_api = _NEGATED_CLOUD_API_RE.sub(" ", raw_text)
    normalized_text_without_exact_negated_cloud_api = _METADATA_SEPARATOR_RE.sub(
        " ", raw_text_without_exact_negated_cloud_api
    )
    return f"{normalized_text_without_exact_negated_cloud_api} {raw_text_without_exact_negated_cloud_api}"


def _iter_metadata_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        chunks: list[str] = []
        for key, item in value.items():
            if isinstance(key, str):
                chunks.append(key)
            chunks.extend(_iter_metadata_strings(item))
        return chunks
    if isinstance(value, list):
        chunks = []
        for item in value:
            chunks.extend(_iter_metadata_strings(item))
        return chunks
    return []
