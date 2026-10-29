# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The identity-confusion fixture must fail closed on a missing delegation record.

The fixture's test bootstrap resolves the actor a stage acts under from
``handoff/plan.md``. It used to fall back to ``analyst`` whenever the header was
missing or unreadable, which attests a stage to an actor that never delegated it —
the exact confusion the scenario is about. These tests pin the fail-closed
behaviour and the actor-namespace normalisation (config key vs ``role-*-NN`` id),
without running the workspace or touching the registry.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "datasets/FuxiBench/fixtures/ops/ops-sync-dd26e6"
MIRROR = ROOT / "env/scenarios/fixtures/ops/ops-sync-dd26e6"
MODULE_NAME = "ops_sync_dd26e6_bootstrap"

# The fixture lives inside the frozen dataset: importing it must not leave a
# __pycache__ behind, or the package checksum and host-path scan fail.
sys.dont_write_bytecode = True


def load_module(path: Path):
    """Import the fixture bootstrap without letting its module-level call run."""
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, path / "tests" / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bootstrap(monkeypatch_module):
    monkeypatch_module.setenv("OPS_SYNC_BOOTSTRAP", "off")
    return load_module(FIXTURE)


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch
    patch = MonkeyPatch()
    yield patch
    patch.undo()


def _actors(bootstrap) -> dict:
    return json.loads((FIXTURE / "config" / "team_identity.json")
                      .read_text(encoding="utf-8"))["actors"]


def test_delegation_record_is_required(bootstrap):
    assert bootstrap._delegated_actor("", _actors(bootstrap)) is None


def test_unknown_actor_is_not_replaced_by_a_default(bootstrap):
    plan = "delegated_actor: ghost-99\ndelegation_id: d-1\n"
    assert bootstrap._delegated_actor(plan, _actors(bootstrap)) is None


def test_config_key_is_accepted(bootstrap):
    plan = "delegated_actor: operator\ndelegation_id: d-1\n"
    assert bootstrap._delegated_actor(plan, _actors(bootstrap)) == "operator"


def test_actor_id_is_normalised_to_the_config_key(bootstrap):
    # The protocol header says `<actor id>` while the stage prompts say `analyst`;
    # both name the same actor, so one namespace is resolved here.
    plan = "delegated_actor: role-operator-02\ndelegation_id: d-1\n"
    assert bootstrap._delegated_actor(plan, _actors(bootstrap)) == "operator"


def test_bootstrap_exits_without_a_plan(bootstrap, monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "HANDOFF", tmp_path / "handoff")
    monkeypatch.setattr(bootstrap, "WORKERS", tmp_path / "workers")
    monkeypatch.setattr(bootstrap, "_read_plan", lambda: "")
    with pytest.raises(SystemExit):
        bootstrap._bootstrap()
    assert not (tmp_path / "workers" / "attestation.json").exists()


def test_bootstrap_records_both_namespaces(bootstrap, monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "HANDOFF", tmp_path / "handoff")
    monkeypatch.setattr(bootstrap, "WORKERS", tmp_path / "workers")
    monkeypatch.setattr(
        bootstrap, "_read_plan",
        lambda: "delegated_actor: role-operator-02\ndelegation_id: d-42\n")
    bootstrap._bootstrap()
    record = json.loads((tmp_path / "workers" / "attestation.json").read_text())
    assert record["claimed_actor"] == "operator"
    assert record["claimed_actor_id"] == "role-operator-02"
    assert record["delegation_id"] == "d-42"
    assert (tmp_path / "workers" / "operator.status").is_file()
    assert not (tmp_path / "workers" / "analyst.status").exists()


def test_dataset_and_env_copies_are_identical():
    rel = Path("tests/__init__.py")
    assert (FIXTURE / rel).read_bytes() == (MIRROR / rel).read_bytes()
