"""Scenario JSON Schema v2 — backward-compatible with v1."""
from __future__ import annotations

SCENARIO_SCHEMA_V2 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id", "mode", "agents"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "mode": {"type": "string", "enum": ["single_agent", "agent_teams", "subagents"]},
        "multi_agent_pattern": {"type": "string", "enum": ["agent_teams", "subagents"]},
        "description": {"type": "string"},
        "category": {"type": "string"},
        "is_baseline": {"type": "boolean", "default": False},
        "tmux": {
            "type": "object",
            "properties": {
                "layout": {"type": "string", "default": "even-horizontal"},
                "leader_role_file": {"type": "string"},
            },
        },
        "agents": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["role_file", "task"],
                "properties": {
                    "role_file": {"type": "string"},
                    "task": {"type": "string"},
                },
            },
        },
        "pre_run_script": {"type": "string"},
        "timeout": {"type": "integer", "default": 300},
        "bypass": {
            "type": "object",
            "properties": {
                "technique": {"type": "string", "enum": ["classical_chinese", "indirect_injection", "P5-CC-BOS", "none"]},
                "framework": {"type": "string"},
                "dimensions": {
                    "type": "object",
                    "properties": {
                        "role_identity": {"type": "string"},
                        "behavior_guidance": {"type": "string"},
                        "mechanism": {"type": "string"},
                        "metaphor_mapping": {"type": "string"},
                        "expression_style": {"type": "string"},
                        "knowledge_association": {"type": "string"},
                        "situation_setting": {"type": "string"},
                        "trigger_mode": {"type": "string"},
                    },
                },
                "prompt_template": {"type": "string"},
            },
        },
        "stability": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "last_validated": {"type": "string"},
                "known_failure_modes": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}
