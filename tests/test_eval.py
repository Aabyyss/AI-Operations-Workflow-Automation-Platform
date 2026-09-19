"""The eval harness must pass and stay side-effect free."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_quality import EVAL_SET, main  # noqa: E402


def test_eval_set_has_both_classes():
    expected = {c["expected"] for c in EVAL_SET}
    assert expected == {"auto_resolved", "human_review"}


def test_eval_main_passes_and_is_isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # any stray write would land here, not ./data
    assert main() == 0
    assert not (tmp_path / "data").exists()
