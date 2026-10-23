"""`cp .env.example .env` — the half of the setup that never ran.

The README has always ended with that command, and until this change
nothing outside Docker read the file: no load_dotenv, no dependency, no
code path. Under compose it appeared to work because compose substitutes
`${VAR:-}` itself before the container starts. Locally — uvicorn, the
desktop launcher, every script in scripts/ — the file was created, read by
humans, and ignored by the program.

So there are two things to pin: the parser does what .env.example actually
contains (comments, export, quotes, and nothing it doesn't contain), and
the real environment always wins — the property that keeps CI, compose and
the visual check's own temp data directory working. The last test is the
only one that proves import-time loading end to end: a fresh interpreter,
a fresh copy of config.py, a .env next to it, and the value observed on
the other side.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_env_file  # noqa: E402


@pytest.fixture()
def clean_env():
    """Snapshot and restore every variable these tests can touch.

    load_env_file writes straight into os.environ — it has to, that is the
    feature — so monkeypatch alone does not cover it: the first test's
    AIOPS_CHAT_MODEL=gpt-4o leaked into the second and made an assertion
    about precedence fail for a reason that had nothing to do with
    precedence. Restore the whole AIOPS_*/OPENAI_* slice instead, whatever
    wrote into it.
    """
    prefixes = ("AIOPS_", "OPENAI_")
    before = {k: v for k, v in os.environ.items()
              if k.startswith(prefixes)}
    yield
    for key in [k for k in os.environ if k.startswith(prefixes)]:
        os.environ.pop(key, None)
    os.environ.update(before)


def _write(tmp_path: Path, text: str, name: str = ".env") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------- parser
def test_plain_values_are_loaded(tmp_path, clean_env, monkeypatch):
    monkeypatch.delenv("AIOPS_MODE", raising=False)
    monkeypatch.delenv("AIOPS_CHAT_MODEL", raising=False)
    path = _write(tmp_path, "AIOPS_MODE=live\nAIOPS_CHAT_MODEL=gpt-4o\n")
    applied = load_env_file(path)

    assert applied == {"AIOPS_MODE": "live", "AIOPS_CHAT_MODEL": "gpt-4o"}
    assert os.environ["AIOPS_MODE"] == "live"


def test_comments_blank_lines_and_export_are_ignored(tmp_path, clean_env,
                                                     monkeypatch):
    for key in ("AIOPS_SHADOW", "AIOPS_RATE_LIMIT"):
        monkeypatch.delenv(key, raising=False)
    path = _write(tmp_path, "\n".join([
        "# a comment",
        "",
        "   # an indented comment",
        "export AIOPS_SHADOW=1",
        "AIOPS_RATE_LIMIT=30",
    ]))
    load_env_file(path)

    assert os.environ["AIOPS_SHADOW"] == "1"
    assert os.environ["AIOPS_RATE_LIMIT"] == "30"


def test_matching_quotes_are_stripped_and_the_inside_is_literal(tmp_path,
                                                                 clean_env,
                                                                 monkeypatch):
    """The template has no inline comment syntax and neither does this
    parser: a # inside a value is part of the value, because secrets and
    URLs contain them and silently truncating one is worse than no parsing
    at all."""
    monkeypatch.delenv("AIOPS_WEBHOOK_SECRET", raising=False)
    path = _write(tmp_path, 'AIOPS_WEBHOOK_SECRET="s3cr#t with spaces"\n'
                            "AIOPS_API_BASE_URL='http://host:8000'\n")
    load_env_file(path)

    assert os.environ["AIOPS_WEBHOOK_SECRET"] == "s3cr#t with spaces"
    assert os.environ["AIOPS_API_BASE_URL"] == "http://host:8000"


def test_a_line_that_is_not_a_setting_is_skipped(tmp_path, clean_env,
                                                  monkeypatch):
    monkeypatch.delenv("AIOPS_MODE", raising=False)
    path = _write(tmp_path, "this is prose, not a setting\n"
                            "AIOPS_MODE=mock\n"
                            "1INVALID=nope\n"
                            "=novalue\n")
    applied = load_env_file(path)

    assert applied == {"AIOPS_MODE": "mock"}
    assert "1INVALID" not in os.environ


def test_a_missing_file_loads_nothing_and_does_not_raise(tmp_path):
    """The normal case: the repo ships .env.example, not .env."""
    assert load_env_file(tmp_path / "absent.env") == {}


def test_a_binary_file_is_skipped_not_fatal(tmp_path):
    """Configuration must not be able to stop the process that needs it."""
    path = tmp_path / ".env"
    path.write_bytes(b"\xff\xfe\x00garbage\x80")
    assert load_env_file(path) == {}


def test_an_unreadable_file_is_skipped(tmp_path, monkeypatch):
    real_read = Path.read_text

    def refusing_read(self, *args, **kwargs):
        if self.name == ".env":
            raise OSError("simulated: permission denied")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refusing_read)
    assert load_env_file(tmp_path / ".env") == {}


# --------------------------------------------------------------- precedence
def test_the_real_environment_always_wins(tmp_path, clean_env):
    """The property everything else depends on: compose substitutes into the
    child's environment, CI exports its own, and scripts/visual_check.py
    points AIOPS_DATA_DIR at a temp directory — none of them may be
    overridden by a file on disk."""
    os.environ["AIOPS_MODE"] = "live"
    path = _write(tmp_path, "AIOPS_MODE=mock\nAIOPS_CHAT_MODEL=gpt-4o\n")

    applied = load_env_file(path)

    assert applied == {"AIOPS_CHAT_MODEL": "gpt-4o"}   # MODE not re-applied
    assert os.environ["AIOPS_MODE"] == "live"


def test_an_empty_value_in_the_file_still_does_not_override(tmp_path, clean_env):
    """${VAR:-} in compose emits an empty string; the file must not fill it
    in — an explicitly empty real environment stays explicitly empty, and
    _env() treats it as unset downstream."""
    os.environ["AIOPS_RATE_LIMIT"] = ""
    path = _write(tmp_path, "AIOPS_RATE_LIMIT=99\n")

    assert load_env_file(path) == {}
    assert os.environ["AIOPS_RATE_LIMIT"] == ""


# --------------------------------------------------- end to end, at import
def test_a_fresh_interpreter_reads_the_dotenv_next_to_config(tmp_path):
    """The actual bug, reproduced in one process: copy config.py into a
    throwaway project with a .env beside it, import it, and read the value
    back. Before the loader, this returned the default — the file was never
    opened by anything.

    A copy rather than a reload of the real module, because the real module
    has already read the real environment (and any real .env) by the time a
    test can run.
    """
    project = tmp_path / "proj"
    (project / "backend").mkdir(parents=True)
    shutil.copy2(ROOT / "backend" / "config.py", project / "backend" / "config.py")
    (project / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (project / ".env").write_text(
        "# the value the README promises will be picked up\n"
        "AIOPS_CHAT_MODEL=from-dotenv\n"
        "AIOPS_BLOCK_MIN_RISK=0.9\n", encoding="utf-8")

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AIOPS_")}     # no interference from outside
    proc = subprocess.run(
        [sys.executable, "-c",
         "import backend.config as c; print(c.CHAT_MODEL, c.BLOCK_MIN_RISK)"],
        cwd=project, env=env, capture_output=True, timeout=60)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout.decode("utf-8").strip() == "from-dotenv 0.9"


def test_an_explicit_environment_beats_the_dotenv_at_import(tmp_path):
    """The mirror of the same end-to-end path: exporting the variable before
    the process starts must survive the file."""
    project = tmp_path / "proj2"
    (project / "backend").mkdir(parents=True)
    shutil.copy2(ROOT / "backend" / "config.py", project / "backend" / "config.py")
    (project / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (project / ".env").write_text("AIOPS_CHAT_MODEL=from-dotenv\n", encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith("AIOPS_")}
    env["AIOPS_CHAT_MODEL"] = "from-environment"
    proc = subprocess.run(
        [sys.executable, "-c", "import backend.config as c; print(c.CHAT_MODEL)"],
        cwd=project, env=env, capture_output=True, timeout=60)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout.decode("utf-8").strip() == "from-environment"
