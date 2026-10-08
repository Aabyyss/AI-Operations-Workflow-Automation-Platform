"""Contract tests for the dashboard's theme and design-token layer.

The dashboard is a single self-contained HTML file, so nothing type-checks it,
nothing lints it, and no import fails when a token is misspelled — a broken
`var()` just silently renders as transparent black or as nothing at all, in one
theme only, for the users who happen to be in that theme.

These tests are the compiler for that file:

1. **Symmetry.** Both themes must define *exactly* the same token names. The
   classic theming bug is a token added to one palette and forgotten in the
   other, which is invisible to whoever added it (they were looking at the
   other theme) and broken for everyone else.
2. **One source of colour.** No hex, rgb or hsl literal may live outside the
   two palette blocks. That is what makes bright mode a palette swap instead
   of a second stylesheet, and it is what keeps this file from slowly growing
   two designs that disagree.
3. **Readable.** Text and status colours must clear a 4.5:1 contrast ratio
   against the surfaces they actually sit on, in both themes, computed rather
   than eyeballed.
4. **No flash, no guessing.** The theme is resolved in <head> before the first
   paint, and every preference value — including the pre-rename "light" —
   resolves to a real theme.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
HTML = DASHBOARD.read_text(encoding="utf-8")

DARK_SELECTOR = ':root, :root[data-theme="dark"]'
BRIGHT_SELECTOR = ':root[data-theme="bright"]'


def _block(selector: str) -> str:
    """The body of the CSS rule whose selector list is exactly `selector`."""
    start = HTML.index(selector)
    open_brace = HTML.index("{", start)
    depth, i = 0, open_brace
    while i < len(HTML):
        if HTML[i] == "{":
            depth += 1
        elif HTML[i] == "}":
            depth -= 1
            if depth == 0:
                return HTML[open_brace + 1:i]
        i += 1
    raise AssertionError(f"unterminated rule: {selector}")


DARK = _block(DARK_SELECTOR)
BRIGHT = _block(BRIGHT_SELECTOR)


def _tokens(block: str) -> dict:
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block, re.I)}


def _rgb(value: str) -> tuple:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def _contrast(fg: str, bg: str) -> float:
    def lum(px):
        chans = [_rgb(px)[i] / 255 for i in range(3)]
        chans = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                 for c in chans]
        return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return round((hi + 0.05) / (lo + 0.05), 2)


# ------------------------------------------------------------- palette symmetry

def test_both_themes_define_exactly_the_same_tokens():
    dark, bright = set(_tokens(DARK)), set(_tokens(BRIGHT))
    assert dark == bright, {
        "only in dark": sorted(dark - bright),
        "only in bright": sorted(bright - dark),
    }


def test_a_palette_swap_is_the_only_difference():
    """Same names, different values — no theme may inherit a colour by accident."""
    dark, bright = _tokens(DARK), _tokens(BRIGHT)
    shared = set(dark) & set(bright)
    assert shared
    # Some tokens (the dark page background) legitimately alias another token,
    # so only compare literal values.
    literals = [t for t in shared if not dark[t].startswith("var(")]
    differing = [t for t in literals if dark[t] != bright[t]]
    assert len(differing) > 20, differing          # it really is a palette
    assert not [t for t in literals if t not in differing], "identical in both themes"


def test_every_referenced_token_is_defined():
    scales = _tokens(_block(":root"))
    defined = set(scales) | set(_tokens(DARK)) | set(_tokens(BRIGHT))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", HTML, re.I))
    assert not used - defined, sorted(used - defined)


def _without_comments(text: str) -> str:
    """Prose is allowed to name a colour or a tag — a comment explaining why
    #fff on the accent was wrong, or mentioning the end of <body>, must not
    itself trip a rule about #fff or about <body>. Newlines are kept so line
    numbers and relative order still hold."""
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", "", m.group(0)),
                  text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


CLEAN = _without_comments(HTML)


def test_no_colour_literal_lives_outside_a_palette():
    """Strip the two palette blocks plus the scale block; nothing may be left
    that hard-codes a colour, including in markup or script."""
    stripped = HTML
    for block in (DARK, BRIGHT, _block(":root")):
        stripped = stripped.replace(block, "", 1)
    offenders = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(",
                           _without_comments(stripped))
    assert not offenders, offenders


def test_removed_palette_mirror_has_not_come_back():
    """The light palette used to be duplicated inside a prefers-color-scheme
    media query as well as behind the attribute. Two copies of one palette is
    how they drift; the attribute is the only source, and the selector may
    only be *used* elsewhere (e.g. for bright-specific elevation)."""
    assert HTML.count(BRIGHT_SELECTOR + "{") == 1
    assert "prefers-color-scheme: light){\n    :root:not(" not in HTML


# ------------------------------------------------------------------- contrast

def test_text_clears_contrast_on_every_surface_it_sits_on():
    for name, tokens in (("dark", _tokens(DARK)), ("bright", _tokens(BRIGHT))):
        for surface in ("--surface", "--surface-2", "--canv"):
            ratio = _contrast(tokens["--text"], tokens[surface])
            assert ratio >= 7.0, (name, surface, ratio)
        ratio = _contrast(tokens["--text-muted"], tokens["--surface"])
        assert ratio >= 4.5, (name, "muted on surface", ratio)


def test_button_labels_are_readable_on_their_button():
    for name, tokens in (("dark", _tokens(DARK)), ("bright", _tokens(BRIGHT))):
        assert _contrast(tokens["--on-accent"], tokens["--accent"]) >= 4.5, name
        assert _contrast(tokens["--on-good"], tokens["--good"]) >= 4.5, name


def test_status_tints_carry_their_own_text_colour():
    pairs = (("--good", "--good-soft"), ("--warn", "--warn-soft"),
             ("--bad", "--bad-soft"), ("--accent", "--accent-soft"),
             ("--text-muted", "--neutral-soft"))
    for name, tokens in (("dark", _tokens(DARK)), ("bright", _tokens(BRIGHT))):
        for fg, bg in pairs:
            ratio = _contrast(tokens[fg], tokens[bg])
            assert ratio >= 4.5, (name, fg, ratio)


# -------------------------------------------------------------------- the boot

def test_theme_is_resolved_before_the_first_paint():
    boot = CLEAN.index("localStorage.getItem(\"aiops_theme\")")
    assert boot < CLEAN.index("</head>"), "the resolving script must live in <head>"
    assert boot < CLEAN.index("<style>"), "it must run before any style is parsed"
    assert boot < CLEAN.index("<body"), "and therefore before the first paint"


def test_switcher_exposes_all_three_modes_as_pressed_toggles():
    for mode in ("bright", "dark", "auto"):
        assert f'data-theme-mode="{mode}"' in HTML, mode
    assert 'aria-pressed' in HTML
    assert 'role="group"' in HTML


def test_preference_is_stored_under_one_key_and_migrated():
    assert HTML.count("aiops_theme") >= 3
    # The value written by older builds still resolves to a real theme.
    assert 'if (mode === "light")' in HTML
    assert 'mode = "bright"' in HTML


def test_auto_is_resolved_so_the_stylesheet_never_guesses():
    block = HTML[HTML.index("function resolveTheme"):HTML.index("function applyTheme")]
    assert "prefersBright()" in block
    assert 'setAttribute("data-theme"' in HTML


# ------------------------------------------------------------------- hygiene

def test_dashboard_stays_self_contained():
    """Same rule the one-pager follows: no CDN, no font fetch, no build step."""
    for external in ('<link rel="stylesheet"', "<script src=", "fonts.googleapis",
                     "<img"):
        assert external not in HTML, external


# ---------------------------------------------------------------- access

def test_every_visible_field_has_a_label_bound_to_it():
    """A <label> sitting above an input is decoration unless it names the
    control's id — which is the only part a screen reader reads aloud. The
    dashboard shipped thirteen unbound labels before this was pinned.
    Checkboxes are exempt: the switch wraps its own input."""
    tags = re.findall(r"<(?:input|textarea|select)[^>]*>", HTML)
    visible = [t for t in tags
               if 'type="checkbox"' not in t and 'type="radio"' not in t]
    assert visible
    unlabelled = []
    for tag in visible:
        m = re.search(r'id="([^"]+)"', tag)
        if not m or f'for="{m.group(1)}"' not in HTML:
            unlabelled.append(m.group(1) if m else tag)
    assert not unlabelled, unlabelled


def test_the_page_has_a_landmark_and_a_way_past_the_navigation():
    assert 'class="skip-link" href="#main"' in HTML
    assert 'id="main"' in HTML
    assert '<main' in HTML
    assert 'lang="en"' in HTML


def test_global_controls_are_not_left_to_the_browser_defaults():
    """A keyboard user needs one visible focus treatment, and the OS needs a
    way to switch off the animation."""
    assert ":focus-visible{outline:2px solid var(--accent)" in HTML
    assert "prefers-reduced-motion: reduce" in HTML
    # Nothing may suppress the outline without putting something back.
    assert "outline:none" not in HTML.replace(
        "input:focus,textarea:focus,select:focus{outline:none", "")


def test_async_results_are_announced():
    """Feedback that only exists visually is feedback half the users miss."""
    for region in ('id="cc_msg"', 'id="kb_msg"', 'id="ticket_out"'):
        i = HTML.index(region)
        tag = HTML[HTML.rindex("<", 0, i):HTML.index(">", i)]
        assert "aria-live" in tag, region


def test_design_tokens_define_scales_not_just_colours():
    scales = _tokens(_block(":root"))
    for token in ("--sp-4", "--r-md", "--fs-md", "--ease", "--dur-fast",
                  "--content-max", "--header-h", "--font-mono"):
        assert token in scales, token


def test_scale_ladders_have_no_gaps():
    """A scale is only useful if it is complete: the step you need is never
    missing, so nobody invents 17px or --sp-4a. A gap is the start of the
    next set of hand-tuned values."""
    scales = _tokens(_block(":root"))
    ladders = {
        "--sp-": [1, 2, 3, 4, 5, 6, 7, 8, 9],
        "--fs-": ["2xs", "xs", "sm", "md", "lg", "xl", "2xl", "3xl"],
        "--r-": ["xs", "sm", "md", "lg", "xl", "pill"],
    }
    for prefix, steps in ladders.items():
        missing = [f"{prefix}{s}" for s in steps if f"{prefix}{s}" not in scales]
        assert not missing, missing
    # Elevation is a ladder per theme, not a per-component decision.
    for name, tokens in (("dark", _tokens(DARK)), ("bright", _tokens(BRIGHT))):
        assert all(f"--shadow-{n}" in tokens for n in (1, 2, 3)), name


def test_spacing_scale_ascends():
    scales = _tokens(_block(":root"))
    values = [float(scales[f"--sp-{n}"].replace("px", "")) for n in range(1, 10)]
    assert values == sorted(values) and len(set(values)) == 9, values
