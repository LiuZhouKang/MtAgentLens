import hashlib
import json

import pytest

from compare.scripts.build.build_submission_checksums import build_checksums


def test_submission_checksums_copy_only_explicit_manifest_entries(tmp_path):
    path = tmp_path / "synthetic.txt"
    path.write_text("synthetic evidence\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": [{"path": str(path), "sha256": digest}]}))
    output = tmp_path / "CHECKSUMS.sha256"
    artifact = build_checksums(manifest_path=manifest, output_path=output)
    assert artifact["file_count"] == 1
    assert output.read_text() == f"{digest}  {path}\n"
    assert artifact["hash_source"] == str(manifest)


def test_submission_checksums_require_a_real_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_checksums(manifest_path=tmp_path / "missing.json", output_path=tmp_path / "out")
