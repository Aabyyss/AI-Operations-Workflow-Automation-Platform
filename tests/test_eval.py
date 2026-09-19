"""The eval harness must pass and stay side-effect free."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from scripts.eval_quality import EVAL_SET, main  # noqa: E402


def test_eval_set_has_both_classes():
    expected = {c["expected"] for c in EVAL_SET}
    assert expected == {"auto_resolved", "human_review"}


def test_eval_main_passes_and_is_isolated():
    # The autouse conftest fixture points config.DATA_DIR at tmp_path/data.
    # The harness must rebind it to its own temp dir, and clean up after.
    fixture_dir = config.DATA_DIR
    assert main() == 0
    assert config.DATA_DIR != fixture_dir
    assert not config.DATA_DIR.exists()  # harness removed its temp dir
