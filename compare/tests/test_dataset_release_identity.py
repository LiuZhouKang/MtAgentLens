"""Release boundaries reject mismatched scenario identities and foreign truth."""
import json
import shutil

import pytest

from compare.tools.dataset_release import build_release


@pytest.mark.parametrize('problem', ['scenario_id', 'schema', 'label', 'attack_type', 'label_type'])
def test_release_rejects_incoherent_input_before_writing(synthetic_dataset, tmp_path, problem):
    root = synthetic_dataset
    shutil.copytree(root / 'fixtures', root / 'scenarios/fixtures')
    scenario = root / 'scenarios/attack-one.json'
    gt = root / 'ground_truth/attack-one.json'
    truth = json.loads(gt.read_text())
    if problem == 'scenario_id':
        data = json.loads(scenario.read_text())
        data['id'] = 'different'
        scenario.write_text(json.dumps(data))
    elif problem == 'schema':
        truth['schema'] = 'mtagentlens.causal-design-proposal/1'
    elif problem == 'label':
        truth['label'] = 'unknown'
    elif problem == 'attack_type':
        truth['attack_type'] = 'unclassified'
    elif problem == 'label_type':
        truth['attack_type'] = 'benign'
    gt.write_text(json.dumps(truth))
    output = tmp_path / 'rejected-release'
    with pytest.raises(ValueError, match='(?i)(identity|schema|label|type)'):
        build_release(root / 'scenarios', root / 'ground_truth', output)
    assert not output.exists()


def test_input_snapshot_preserves_dataset_card_and_adjudication_protocol(synthetic_dataset, tmp_path):
    root = synthetic_dataset
    shutil.copytree(root / "fixtures", root / "scenarios/fixtures")
    for name in ("DATASET-CARD.md", "ADJUDICATION-PROTOCOL.md"):
        (root / name).write_text("Research input only; human review pending.\n")
    output = tmp_path / "release-with-documentation"
    release = build_release(root / "scenarios", root / "ground_truth", output)
    for name in ("DATASET-CARD.md", "ADJUDICATION-PROTOCOL.md"):
        assert (output / name).read_bytes() == (root / name).read_bytes()
        assert name in release["files"]
