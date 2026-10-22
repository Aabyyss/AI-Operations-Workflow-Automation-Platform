"""CLI output is UTF-8, whatever console it lands on.

Every script here prints —, ·, … or → somewhere. On a Windows console or a
pipe those bytes go out in the configured codepage (cp1252, cp437), so they
arrive as different bytes than the file says — and any tool that decodes the
output as UTF-8, including this suite, gets a UnicodeDecodeError on a message
that was perfectly correct. It happened for real while building these tests:
a diagnostic built here died mid-print on `→` under cp1252.

scripts/console.py is the fix: reconfigure the streams to UTF-8 with
errors="replace", first thing in main(). This module holds two lines of
defence — every script is on the list, and one is actually run with a
codepage forced on it, because a grep cannot prove reconfiguration happened.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# console.py is the helper, not a command; the rest are entry points.
HELPER = {"console"}
# demo.py resets and rewrites the data directory — never run it in a test.
UNSAFE_TO_RUN = {"demo", "backup", "migrate_to_postgres", "rotate_receipt_key",
                 "visual_check"}


def _scripts() -> list[Path]:
    return sorted(p for p in (ROOT / "scripts").glob("*.py")
                  if p.stem != "__init__")


def test_every_script_reconfigures_its_output_to_utf8():
    """The sweep must not rot: a new script that prints → and forgets this
    fails here rather than in someone else's pipe."""
    missing = [p.name for p in _scripts()
               if p.stem not in HELPER
               and "use_utf8" not in p.read_text(encoding="utf-8")]
    assert missing == [], (
        "these scripts never call use_utf8(), so their non-ASCII output is "
        f"whatever the console's codepage says: {missing}")


def test_use_utf8_is_a_noop_where_streams_cannot_be_reconfigured():
    """The documented contract: a redirected StringIO or pytest's capture
    objects must pass through untouched instead of raising."""
    import io

    from scripts.console import use_utf8

    fake_out, fake_err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = fake_out, fake_err
    try:
        use_utf8()          # StringIO has no reconfigure — must not raise
        use_utf8()          # and it must stay idempotent when it does work
    finally:
        sys.stdout, sys.stderr = old
    assert fake_out.getvalue() == ""


def test_help_emits_real_utf8_even_when_the_codepage_says_otherwise():
    """Run one script with PYTHONIOENCODING=cp1252 and read the bytes.

    This is the whole bug in one assertion: without use_utf8() the em dash
    in the docstring goes out as cp1252 bytes (or the process dies on a
    character cp1252 cannot even map), and decoding this output as UTF-8
    raises. With it, the stream is reconfigured first and the same bytes
    come out correct.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.gen_sha_constants", "--help"],
        cwd=ROOT, capture_output=True,
        env={"PYTHONIOENCODING": "cp1252", "PATH": "/usr/bin:/bin"},
        timeout=60,
    )
    # The helper itself is ASCII, so decoding either stream can never be the
    # reason this passes — only whether the script reconfigured the stream.
    text = proc.stdout.decode("utf-8")          # raises if cp1252 leaked out
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "—" in text, "the docstring's em dash should reach --help intact"
