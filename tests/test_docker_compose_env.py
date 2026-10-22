"""Docker Compose and config.py must agree on which knobs exist.

The app reads its configuration from the environment; under Compose the only
way a value reaches it is if docker-compose.yml forwards the variable. Nineteen
of twenty-eight were not — the role keys, every governance threshold, the
outbox URL and the budget — so an operator setting them in .env got no error
and no effect: the deployment simply was not the one they had configured.

This test is the drift guard: parse both files, compare the sets, and name
what is missing. A new variable reads as a failure here rather than as a
setting that quietly does nothing in the only deployment path the README
documents.

AIOPS_DATA_DIR is the one deliberate exception (see its comment in
docker-compose.yml): the container's path is fixed by the ./data mount, and
forwarding it would write outside the mounted volume.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("yaml")
import yaml  # noqa: E402

FORWARD_EXEMPT = {"AIOPS_DATA_DIR"}


def _variables_read_by_the_app() -> set[str]:
    """Every AIOPS_* name the backend and scripts read, from _env()/os.getenv().

    scripts/ counts too: AIOPS_BACKUP_DIR, AIOPS_DATA_DIR and the receipt
    tooling's overrides are read there, and a scan of backend/ alone called
    both of those "unread" while an operator was setting them in good faith.
    """
    names: set[str] = set()
    for root in (ROOT / "backend", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            names |= set(re.findall(r"""(?:_env|os\.getenv)\(\s*["'](AIOPS_[A-Z0-9_]+)""",
                                    source))
    return names


def _variables_template_lists() -> set[str]:
    """Every AIOPS_* name .env.example offers to the operator.

    The template is what people actually copy, so a variable missing here
    is one they will never set, and a name here that no code reads is one
    they will set and never see take effect.
    """
    source = (ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^(?:export )?(AIOPS_[A-Z0-9_]+)=", source, re.M))


def _variables_compose_forwards() -> set[str]:
    doc = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    entries = doc["services"]["aiops-api"]["environment"]
    return {entry.split("=", 1)[0] for entry in entries}


def test_compose_forwards_every_variable_the_app_reads():
    read = _variables_read_by_the_app()
    forwarded = _variables_compose_forwards()
    assert read, "the scan found no variables — it has stopped working"

    missing = sorted(read - forwarded - FORWARD_EXEMPT)
    assert missing == [], (
        "these variables are read by the app but never reach the container, "
        "so setting them in .env changes nothing under docker compose: "
        f"{missing}")


def test_every_variable_compose_forwards_is_one_the_app_actually_reads():
    """The mirror check: a forwarded name nobody reads is a typo an operator
    will set in good faith and never see take effect."""
    read = _variables_read_by_the_app()
    forwarded = _variables_compose_forwards()
    # OPENAI_* are read via os.getenv under their own prefix, not AIOPS_.
    unknown = sorted(n for n in forwarded
                     if n not in read and not n.startswith(("OPENAI_", "AIOPS_DATA")))
    assert unknown == [], (
        f"docker-compose.yml forwards variables no code reads: {unknown}")


def test_the_template_lists_every_variable_the_app_reads():
    """Three-way parity, in the direction that costs the most to get wrong:
    a variable read by the app but absent from .env.example is one nobody
    is ever told exists — the doc row and the compose line both point at a
    template that never mentions it."""
    read = _variables_read_by_the_app()
    template = _variables_template_lists()
    missing = sorted(read - template)
    assert missing == [], (
        f"read by the app but missing from .env.example: {missing}")


def test_every_variable_in_the_template_is_one_the_app_reads():
    """The other direction: AIOPS_RATE_WINDOW and AIOPS_WEBHOOK_MAX_SKEW
    sat in the template under names no code ever read, so copying the file
    set two knobs that did nothing. (Both had a different real name.)"""
    read = _variables_read_by_the_app()
    template = _variables_template_lists()
    dead = sorted(template - read)
    assert dead == [], (
        f".env.example offers variables no code reads: {dead}")


def test_the_exempt_variable_is_the_documented_one():
    """If someone adds a second exemption they must edit this test — that is
    the point: the exception list stays short and argued."""
    assert FORWARD_EXEMPT == {"AIOPS_DATA_DIR"}


def test_an_empty_value_is_treated_as_unset():
    """`${VAR:-}` expands to an empty string, which is not the same as an
    unset variable to os.getenv — before _env, AIOPS_RATE_LIMIT="" raised
    ValueError at boot and AIOPS_CHAT_MODEL="" was sent to the API as the
    model name. Empty now means "not configured", like absence does.
    """
    from backend import config

    previous = {}
    for name in ("AIOPS_RATE_LIMIT", "AIOPS_CHAT_MODEL", "AIOPS_MODE"):
        previous[name] = __import__("os").environ.get(name)
        __import__("os").environ[name] = ""        # exactly what compose emits
    try:
        import importlib
        reloaded = importlib.reload(config)
        assert reloaded.RATE_LIMIT == 0            # int("") never raised
        assert reloaded.CHAT_MODEL == "gpt-4o-mini"
        assert reloaded.MODE == "mock"
    finally:
        for name, value in previous.items():
            if value is None:
                __import__("os").environ.pop(name, None)
            else:
                __import__("os").environ[name] = value
        import importlib
        importlib.reload(config)
