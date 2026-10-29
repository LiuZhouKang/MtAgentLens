from pathlib import Path

from mtagentlens_benchmark.engine.executor import _short_fixture_path


def test_short_fixture_path_keeps_basename_visible(tmp_path: Path):
    materialized = tmp_path / ("long-" * 30) / "fixtures"
    materialized.mkdir(parents=True)
    (materialized / ".env").write_text("TOKEN=decoy\n")
    link = _short_fixture_path(materialized, "0123456789abcdef")
    assert link is not None
    try:
        assert len(str(link / "config/.env")) < 126
        assert (link / ".env").read_text() == "TOKEN=decoy\n"
    finally:
        link.unlink(missing_ok=True)


def test_short_fixture_path_replaces_stale_link(tmp_path: Path):
    materialized = tmp_path / "fixtures"
    materialized.mkdir()
    (materialized / "a").write_text("x")
    link = _short_fixture_path(materialized, "stale-check")
    assert link is not None
    try:
        assert (link / "a").read_text() == "x"
    finally:
        link.unlink(missing_ok=True)
