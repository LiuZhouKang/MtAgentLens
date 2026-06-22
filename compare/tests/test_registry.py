from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from compare.tools.base import ToolMetadata
from compare.tools.registry import instantiate_adapter, load_tool_metadata, validate_lockfile


VALID_TOOL = {
    "id": "fixture-scanner",
    "name": "Fixture Scanner",
    "class": "static",
    "source_url": "builtin://compare/tools/adapters/fixture_scanner.py",
    "version": "builtin",
    "license": "MtAgentPrism-project",
    "install": [],
    "adapter": "fixture_scanner",
    "input_modes": ["static"],
    "score_scope": "main_offline",
    "inclusion_rationale": "Built-in offline scanner used to validate the comparison harness before third-party adapters are integrated.",
}


def lock_with(tool: dict[str, object]) -> dict[str, object]:
    return {"tools": [tool]}


def test_accepts_builtin_fixture_scanner_lockfile_metadata() -> None:
    metadata = validate_lockfile(lock_with(VALID_TOOL))

    assert metadata == [
        ToolMetadata(
            id="fixture-scanner",
            name="Fixture Scanner",
            tool_class="static",
            source_url="builtin://compare/tools/adapters/fixture_scanner.py",
            version="builtin",
            license="MtAgentPrism-project",
            install=(),
            adapter="fixture_scanner",
            input_modes=("static",),
            score_scope="main_offline",
            inclusion_rationale="Built-in offline scanner used to validate the comparison harness before third-party adapters are integrated.",
        )
    ]


def test_exact_builtin_fixture_scanner_missing_version_defaults_to_builtin() -> None:
    tool = dict(VALID_TOOL)
    tool.pop("version")

    metadata = validate_lockfile(lock_with(tool))

    assert metadata[0].version == "builtin"


def test_unknown_builtin_source_missing_version_is_rejected() -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-builtin-tool"
    tool["source_url"] = "builtin://external/tool"
    tool.pop("version")

    with pytest.raises(ValueError, match="unknown builtin tool|version|commit|tag|reproducible version"):
        validate_lockfile(lock_with(tool))


def test_builtin_source_with_prohibited_metadata_is_rejected() -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "cloud-guard"
    tool["class"] = "llm_judge"
    tool["source_url"] = "builtin://compare/tools/adapters/cloud_guard.py"
    tool["adapter"] = "cloud_guard"
    tool["install"] = ["openai"]
    tool["inclusion_rationale"] = "Uses runtime_replay and trace_db for scoring."
    tool.pop("version")

    with pytest.raises(ValueError, match="prohibited|round 1|llm|openai|runtime|trace|cloud"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(("field", "value"), [("commit", "main"), ("tag", "origin/main")])
def test_builtin_fixture_scanner_rejects_extra_floating_pin_fields(field: str, value: str) -> None:
    tool = dict(VALID_TOOL)
    tool[field] = value

    with pytest.raises(ValueError, match="builtin|reproducible version|commit|tag"):
        validate_lockfile(lock_with(tool))


def test_fixture_scanner_wrong_adapter_is_not_builtin_and_requires_version() -> None:
    tool = dict(VALID_TOOL)
    tool["adapter"] = "other_scanner"
    tool.pop("version")

    with pytest.raises(ValueError, match="unknown builtin tool|version|commit|tag|reproducible version"):
        validate_lockfile(lock_with(tool))


def test_fixture_scanner_wrong_source_url_is_not_builtin_and_requires_version() -> None:
    tool = dict(VALID_TOOL)
    tool["source_url"] = "builtin://compare/tools/adapters/other_scanner.py"
    tool.pop("version")

    with pytest.raises(ValueError, match="unknown builtin tool|version|commit|tag|reproducible version"):
        validate_lockfile(lock_with(tool))


def test_builtin_source_with_prohibited_class_and_rationale_is_rejected() -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-builtin-tool"
    tool["class"] = "llm_judge"
    tool["source_url"] = "builtin://external/tool"
    tool["inclusion_rationale"] = "Requires runtime_replay from trace_db."
    tool["version"] = "v1.0.0"

    with pytest.raises(ValueError, match="prohibited|round 1|llm|runtime|trace"):
        validate_lockfile(lock_with(tool))


def test_external_tool_missing_reproducible_version_raises() -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool.pop("version")

    with pytest.raises(ValueError, match="version|commit|tag|reproducible version"):
        validate_lockfile(lock_with(tool))


def test_non_builtin_unknown_version_raises() -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool["version"] = "unknown"

    with pytest.raises(ValueError, match="unknown|reproducible version"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(("field", "value"), [("version", "builtin"), ("version", "latest"), ("commit", "main")])
def test_non_builtin_floating_version_values_raise(field: str, value: str) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    if field == "commit":
        tool.pop("version")
    tool[field] = value

    with pytest.raises(ValueError, match="reproducible version|commit|tag"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit", "refs/heads/main"),
        ("commit", "origin/main"),
        ("tag", "remotes/origin/main"),
        ("version", "main@latest"),
        ("tag", "branch/main"),
    ],
)
def test_non_builtin_floating_git_ref_values_raise(field: str, value: str) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    if field in {"commit", "tag"}:
        tool.pop("version")
    tool[field] = value

    with pytest.raises(ValueError, match="reproducible version|commit|tag"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(("field", "value"), [("commit", "main"), ("tag", "origin/main")])
def test_non_builtin_rejects_floating_extra_pin_fields(field: str, value: str) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool["version"] = "v1.0.0"
    tool[field] = value

    with pytest.raises(ValueError, match="reproducible version|commit|tag"):
        validate_lockfile(lock_with(tool))


def test_rejects_missing_required_fields() -> None:
    tool = dict(VALID_TOOL)
    tool.pop("source_url")

    with pytest.raises(ValueError, match="missing required fields"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize("mode", ["cloud_api", "llm_judge", "trace_db", "runtime_replay", "surprise"])
def test_rejects_disallowed_input_modes(mode: str) -> None:
    tool = dict(VALID_TOOL)
    tool["input_modes"] = ["static", mode]

    with pytest.raises(ValueError, match="not allowed in round 1"):
        validate_lockfile(lock_with(tool))


def test_rejects_non_main_offline_scope() -> None:
    tool = dict(VALID_TOOL)
    tool["score_scope"] = "online"

    with pytest.raises(ValueError, match="not allowed in round 1"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("class", "llm_judge"),
        ("source_url", "https://example.com/openai/tool"),
        ("adapter", "cloud_guard"),
        ("install", ["openai"]),
        ("inclusion_rationale", "uses Anthropic cloud API for scoring"),
    ],
)
def test_rejects_prohibited_non_builtin_metadata_concepts(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool[field] = value

    with pytest.raises(ValueError, match="prohibited|round 1|cloud|LLM|runtime|trace"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "openai-static-tool"),
        ("name", "Anthropic Trace DB Exporter"),
        ("license", "cloud-license"),
        ("version", "runtime-v1"),
        ("commit", "llmjudge123"),
        ("tag", "trace-db-v1"),
    ],
)
def test_rejects_prohibited_terms_in_identity_and_version_metadata(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    if field in {"commit", "tag"}:
        tool.pop("version")
    tool[field] = value

    with pytest.raises(ValueError, match="prohibited|round 1|cloud|llm|runtime|trace|openai|anthropic"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("class", "llm-judge"),
        ("inclusion_rationale", "runtime-replay backed by trace-db"),
        ("source_url", "https://example.com/cloud-api/tool"),
    ],
)
def test_rejects_hyphenated_prohibited_metadata_variants(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool[field] = value

    with pytest.raises(ValueError, match="prohibited|round 1|cloud|llm|runtime|trace"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("class", "llm_scoring"),
        ("inclusion_rationale", "Uses cloud scoring at runtime"),
        ("name", "Runtime Comparator"),
    ],
)
def test_rejects_generic_prohibited_metadata_concepts(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool[field] = value

    with pytest.raises(ValueError, match="prohibited|round 1|cloud|llm|runtime"):
        validate_lockfile(lock_with(tool))


def test_accepts_non_builtin_negated_cloud_api_rationale() -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool["version"] = "v1.0.0"
    tool["inclusion_rationale"] = "Runs locally with no cloud API or hosted judge dependency."

    metadata = validate_lockfile(lock_with(tool))

    assert metadata[0].id == "external-static-tool"


@pytest.mark.parametrize("key", ["cloud_api", "llm_judge", "runtime_replay", "trace_db"])
def test_rejects_prohibited_metadata_concepts_in_extra_keys(key: str) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool[key] = False if key != "trace_db" else None

    with pytest.raises(ValueError, match="prohibited|round 1|cloud|llm|runtime|trace"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_url", "https://example.com/mtagentprism-claims-tool"),
        ("inclusion_rationale", "Reads MtAgentPrism alerts as comparator input."),
        ("adapter", "claim_inputs"),
    ],
)
def test_rejects_mtagentprism_derived_claim_or_alert_inputs(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool["version"] = "v1.0.0"
    tool[field] = value

    with pytest.raises(ValueError, match="prohibited|round 1|claim|alert|mtagentprism"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    "rationale",
    [
        "Runs locally with no cloud_api dependency.",
        "Runs locally with no cloud-api dependency.",
        "Runs locally with no cloud.api dependency.",
        "Runs locally with no cloud/api dependency.",
    ],
)
def test_rejects_separator_variants_of_negated_cloud_api_rationale(rationale: str) -> None:
    tool = dict(VALID_TOOL)
    tool["id"] = "external-static-tool"
    tool["source_url"] = "https://example.com/external-static-tool"
    tool["inclusion_rationale"] = rationale

    with pytest.raises(ValueError, match="prohibited|round 1|cloud"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("class", "llm_scoring"),
        ("inclusion_rationale", "Uses cloud scoring at runtime"),
    ],
)
def test_rejects_builtin_fixture_scanner_with_generic_prohibited_terms(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool[field] = value

    with pytest.raises(ValueError, match="prohibited|round 1|cloud|llm|runtime"):
        validate_lockfile(lock_with(tool))


def test_builtin_fixture_scanner_remains_accepted_with_offline_rationale() -> None:
    metadata = validate_lockfile(lock_with(dict(VALID_TOOL)))

    assert metadata[0].id == "fixture-scanner"


def test_rejects_non_object_lockfile_root() -> None:
    with pytest.raises(ValueError, match="lockfile root.*object"):
        validate_lockfile([])  # type: ignore[arg-type]


def test_rejects_missing_tools_list() -> None:
    with pytest.raises(ValueError, match="tools.*list"):
        validate_lockfile({})


def test_rejects_non_list_tools() -> None:
    with pytest.raises(ValueError, match="tools.*list"):
        validate_lockfile({"tools": {"id": "fixture-scanner"}})


def test_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate tool id"):
        validate_lockfile({"tools": [VALID_TOOL, dict(VALID_TOOL)]})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("install", "python3"),
        ("install", ["python3", 7]),
        ("input_modes", "static"),
        ("input_modes", ["static", None]),
    ],
)
def test_rejects_non_list_install_input_modes_or_non_string_members(field: str, value: object) -> None:
    tool = dict(VALID_TOOL)
    tool[field] = value

    with pytest.raises(ValueError, match=f"{field}.*list of strings"):
        validate_lockfile(lock_with(tool))


@pytest.mark.parametrize("adapter", ["../evil", "os.system", "", "1bad", "bad-name"])
def test_rejects_unsafe_adapter_names(adapter: str) -> None:
    tool = dict(VALID_TOOL)
    tool["adapter"] = adapter

    with pytest.raises(ValueError, match="adapter.*safe module name"):
        validate_lockfile(lock_with(tool))


def test_loads_lockfile_from_path(tmp_path: Path) -> None:
    lockfile = tmp_path / "tool-lock.json"
    lockfile.write_text(json.dumps(lock_with(VALID_TOOL)), encoding="utf-8")

    metadata = load_tool_metadata(lockfile)

    assert metadata[0].id == "fixture-scanner"
    assert metadata[0].adapter == "fixture_scanner"


def test_loads_real_tool_lock() -> None:
    metadata = load_tool_metadata(Path("compare/tool-lock.json"))

    assert {tool.id for tool in metadata} == {
        "fixture-scanner",
        "gitleaks",
        "trufflehog",
        "semgrep",
        "detect-secrets",
        "yara-x",
    }
    assert all(tool.score_scope == "main_offline" for tool in metadata)
    detect_secrets_tool = next(tool for tool in metadata if tool.id == "detect-secrets")
    assert any("pyyaml==" in part for part in detect_secrets_tool.install)
    assert any("requests==" in part for part in detect_secrets_tool.install)
    yara_x_tool = next(tool for tool in metadata if tool.id == "yara-x")
    assert yara_x_tool.version == "engine-v1.16.0+rules-20260524"
    assert any("fcbf94231fc4d563a4f80c7c384499b6ab75894851308894071a0a319f9801a2" in part for part in yara_x_tool.install)
    assert any("98625cabd65dbcbec12414311a27168ca7d8ebe36cf1b8b8fb118ef40f88824f" in part for part in yara_x_tool.install)
    assert any("tar -xzf compare/.yara-x/yr.gz -C compare/.yara-x/bin" in part for part in yara_x_tool.install)
    assert any("test -x compare/.yara-x/bin/yr" in part for part in yara_x_tool.install)
    assert any("test -f compare/.yara-forge-core-20260524/packages/core/yara-rules-core.yar" in part for part in yara_x_tool.install)


@pytest.mark.parametrize("adapter", ["../evil", "os.system"])
def test_instantiate_adapter_rejects_unsafe_adapter_names_before_import(adapter: str) -> None:
    metadata = ToolMetadata(
        id="temp",
        name="Temporary",
        tool_class="test",
        source_url="file://temp",
        version="test",
        license="test",
        install=(),
        adapter=adapter,
        input_modes=("static",),
        score_scope="main_offline",
        inclusion_rationale="test",
    )

    with pytest.raises(ValueError, match="safe module name"):
        instantiate_adapter(metadata)


def test_instantiate_adapter_imports_adapter_class(monkeypatch: pytest.MonkeyPatch) -> None:
    module_name = "compare.tools.adapters.temp_adapter"
    module = types.ModuleType(module_name)

    class Adapter:
        def __init__(self, metadata: ToolMetadata) -> None:
            self.metadata = metadata

    module.Adapter = Adapter
    monkeypatch.setitem(sys.modules, module_name, module)
    metadata = ToolMetadata(
        id="temp",
        name="Temporary",
        tool_class="test",
        source_url="file://temp",
        version="test",
        license="test",
        install=(),
        adapter="temp_adapter",
        input_modes=("static",),
        score_scope="main_offline",
        inclusion_rationale="test",
    )

    adapter = instantiate_adapter(metadata)

    assert isinstance(adapter, Adapter)
    assert adapter.metadata is metadata
