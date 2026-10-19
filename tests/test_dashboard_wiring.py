"""Contract tests for what the dashboard binds to — data, ids, and syntax.

The theme tests pin how the page *looks*; these pin what it *talks to*. Both
kinds of breakage are invisible to Python: a fetch pointed at the wrong
endpoint, or a button that hands the API an id it will 404 on, renders as a
perfectly normal card full of slightly wrong numbers.

Three defects this file exists to keep out:

1. **Runs rendered from tickets.** `/api/tickets` returns inbound tickets;
   they carry no disposition, risk or cost, so a "Recent Ticket Runs" card
   built from them silently omits the outcome of every run.
2. **Feedback and replay keyed on the ticket id.** Both endpoints look the
   run up by id and 404 on anything else, so the thumbs were dead buttons.
3. **A syntax error in the inline script.** Nothing type-checks a 1,500-line
   inline script; `node --check` does, and it costs one subprocess.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
HTML = DASHBOARD.read_text(encoding="utf-8")


def _function(name: str) -> str:
    """The body of a top-level JS function, brace-matched."""
    start = HTML.index(f"function {name}(")
    i = HTML.index("{", start)
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# ------------------------------------------------------------------ data source
def test_recent_runs_card_is_fed_runs_not_tickets():
    load_all = _function("loadAll")
    assert 'api("/api/runs?limit=' in load_all
    assert "renderRuns(runs, tickets)" in load_all
    assert "renderRuns(runs)" not in load_all  # tickets must not be the run list


def test_every_run_row_shows_the_disposition_it_ended_in():
    body = _function("renderRuns")
    assert "dispositionPill(r.disposition)" in body
    # Risk and cost are why the row is worth reading; both come from the run.
    assert "r.decision" in body and "total_cost_usd" in body


def test_feedback_and_replay_use_the_run_id():
    """The API 404s on a ticket id — the buttons must pass the run's own id."""
    body = _function("renderRuns")
    assert "const id = r.id" in body
    assert "giveFeedback('${id}'" in body
    assert "replayRun('${id}')" in body
    # The ticket subject is joined in for reading, never used as the key.
    assert "subjectOf.get(r.ticket_id)" in body


def test_cost_table_shows_the_figure_its_verdict_was_computed_on():
    """With labour billed, the API's verdict uses the fully loaded number.
    A table that shows only the bill then argues with its own verdict."""
    body = _function("renderCostCompare")
    assert 'r.comparison_basis === "fully_loaded"' in body
    assert "p.fully_loaded_monthly_usd" in body
    assert "p.fully_loaded_annual_usd" in body
    assert "p.fully_loaded_cost_per_ticket_usd" in body
    # The basis is stated in prose, not left for the reader to infer.
    assert "handoff labour billed on both sides" in body


def test_the_handoff_switch_says_both_sides():
    assert "Bill handoff labour on both sides" in HTML
    assert "Price the human minutes our escalations use" not in HTML


def test_disposition_vocabulary_is_defined_once():
    """Two maps drift; one map cannot. Both renderers share the pill helper."""
    assert HTML.count("const DISPOSITION_PILL") == 1
    assert "dispositionPill(r.disposition)" in _function("renderRun")


# --------------------------------------------------------------------- syntax
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_parses(tmp_path):
    """A syntax error here is a blank dashboard; nothing else would catch it."""
    scripts = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    assert scripts, "the dashboard must ship its script inline"
    for i, src in enumerate(scripts):
        path = tmp_path / f"inline_{i}.js"
        path.write_text(src, encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(path)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"script {i} does not parse:\n{proc.stderr}"


def test_no_layout_utility_landed_on_a_table_header():
    """Cheap guard against a bulk edit putting a class in the wrong place —
    the way `mt-2` once ended up on every <th> in a table header."""
    ths = re.findall(r"<th[^>]*>", HTML)
    assert ths
    offenders = [t for t in ths if "mt-" in t or "mb-" in t]
    assert not offenders, f"spacing utility on a header cell: {offenders[:3]}"


def test_a_right_aligned_header_sits_over_a_right_aligned_column():
    """Measured in a real browser: `th.num` over a left-aligned `<td>` put
    "Costs more above" 334px away from the number it labels."""
    table = re.search(r'<table aria-label="Modelled market plans against this pipeline">(.*?)</table>',
                      HTML, re.S)
    assert table, "the comparison table must exist"
    head = re.search(r"<thead><tr>(.*?)</tr></thead>", table.group(1), re.S)
    assert head, "the comparison table must keep a header row"
    labels = re.findall(r"<th([^>]*)>([^<]*)</th>", head.group(1))
    assert labels
    numeric = [text for attrs, text in labels if "num" in attrs]
    assert numeric, "numeric columns must stay right-aligned"
    # The breakeven column holds a percentage *plus* an n/a note; its header
    # is deliberately left-aligned so label and value share an edge.
    assert "Costs more above" in [t.strip() for _, t in labels]
