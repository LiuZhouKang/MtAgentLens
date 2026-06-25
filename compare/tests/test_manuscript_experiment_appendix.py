from __future__ import annotations

from pathlib import Path


APPENDIX = Path("compare/results/paper/MANUSCRIPT_EXPERIMENT_APPENDIX.md")


def test_manuscript_experiment_appendix_maps_claims_to_artifacts_figures_and_tables() -> None:
    text = APPENDIX.read_text(encoding="utf-8")

    required_phrases = [
        "67 frozen scenarios",
        "54 attack",
        "13 benign",
        "MtAgentLens",
        "LLM Guard",
        "Meta Prompt Guard",
        "Vigil",
        "McNemar",
        "file-read",
        "0.534",
        "FRS=1.000",
        "ORS=1.000",
        "TRS=1.000",
        "backend capture-health canary",
        "5.69 ms",
        "Fig. 1",
        "Fig. 2",
        "Fig. 3",
        "Fig. 4",
        "Table 1",
        "Table 9",
        "compare/results/paper/evaluation_report.json",
        "compare/results/paper/paper_tables.tex",
        "compare/results/paper/SUBMISSION_AUDIT.json",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_manuscript_experiment_appendix_preserves_non_claim_boundaries() -> None:
    text = APPENDIX.read_text(encoding="utf-8")

    non_claims = [
        "external benchmark validation",
        "universal prompt-guard paradigm ceiling",
        "live module-level causal ablation",
        "production LLM-agent end-to-end latency",
        "blind independent annotation reliability",
        "raw SQLite trace databases are not public-release ready",
    ]
    for phrase in non_claims:
        assert phrase in text

    assert "does not claim" in text
    assert "ready_with_external_author_actions" in text
