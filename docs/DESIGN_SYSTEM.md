# Dashboard Design System

The operator dashboard is a single self-contained file
([dashboard/index.html](../dashboard/index.html)): no build step, no CDN, no
framework. That is a deliberate constraint — the dashboard has to work on a
machine with nothing but Python and a browser — but it means there is no
compiler, no linter and no type checker standing between an edit and a
broken theme.

This document describes the rules the file follows, and
[`tests/test_dashboard_theme.py`](../tests/test_dashboard_theme.py) enforces
the ones a machine can check.

---

## 1. Two layers of tokens

Everything visual resolves through CSS custom properties, in two layers.

**Layer 1 — scales** (theme-independent, defined once in `:root`):

| Group | Tokens | Used for |
|---|---|---|
| Space | `--sp-1` … `--sp-9` (4px base) | all padding, gaps and margins |
| Radius | `--r-xs`, `--r-sm`, `--r-md`, `--r-lg`, `--r-xl`, `--r-pill` | corners |
| Type | `--fs-2xs` … `--fs-3xl`, `--lh-*`, `--track-caps` | every font size |
| Fonts | `--font`, `--font-mono` | UI text and anything numeric/code |
| Motion | `--ease`, `--dur-fast`, `--dur` | transitions |
| Layout | `--header-h`, `--content-max` | the app shell |

**Layer 2 — semantic colour** (defined once per theme): `--canv`, `--surface`,
`--surface-2`, `--surface-3`, `--border`, `--border-2`, `--text`,
`--text-muted`, `--text-subtle`, `--accent`, `--accent-hover`, `--accent-soft`,
`--on-accent`, `--good`/`--warn`/`--bad` (+ `-soft`, `-hover`), `--neutral-soft`,
`--field-bg`, `--topbar-bg`, `--accent-2`, `--avatar-bg`, `--shadow-1..3`,
`--ring`, `--scrim`, `--page-bg`.

Older names (`--bg`, `--panel`, `--panel2`, `--line`, `--muted`, `--on-good`,
`--pill-*-bg`) survive as aliases so a half-migrated inline style still
renders. Prefer the semantic name in new work.

### The one hard rule

**No colour literal may appear outside the two theme blocks.** Not a hex, not
an `rgb()`, not an `hsl()`. A background wash, a shadow, a focus ring — all of
them are tokens. This is what makes the second theme a palette swap rather
than a second stylesheet that drifts, and it is asserted by
`test_no_colour_literal_lives_outside_a_palette`.

Corollary: **both themes define exactly the same token names**
(`test_both_themes_define_exactly_the_same_tokens`). The classic theming bug is
a token added to one palette and forgotten in the other — invisible to whoever
added it, broken for everyone else.

---

## 2. Themes

Three preferences, resolved to two themes:

| Preference | Resolves to |
|---|---|
| `bright` | the daylight palette |
| `dark` | the low-light palette |
| `auto` | `bright` or `dark` from `prefers-color-scheme`, live |

Two implementation rules matter:

1. **The preference is resolved to a concrete theme and written to
   `data-theme`.** The stylesheet never asks "am I light right now?" in a
   media query. One code path means an OS-driven bright user and a
   pinned-bright user get identical CSS.
2. **It is resolved in `<head>`, before the first paint.** Resolving it in the
   script at the end of `<body>` paints the dark default first and snaps
   bright a frame later.

`bright` is designed as daylight, not as the dark palette inverted. It
separates surfaces with elevation — a tinted canvas under crisp white surfaces,
with the shadow ramp carrying hierarchy — because a dark canvas has no light to
spend and must use lines instead. A preference stored by an older build as
`light` is migrated to `bright` in place.

Contrast is computed, not eyeballed: `test_text_clears_contrast_on_every_surface_it_sits_on`
requires ≥ 7:1 for body text on every surface and ≥ 4.5:1 for muted text,
button labels and every status tint, in both themes.

---

## 3. Components

| Component | Class | Notes |
|---|---|---|
| Page child rhythm | `.wrap > * + *` | every panel owns its own top gap; no inline margins |
| Card | `.card` | the only panel container |
| Section header | `.section-head` + `.section-desc` | title, one line of purpose, optional action |
| Stat | `.stat` (+ `.good`/`.warn`/`.bad`) | eyebrow, value with welded unit, note pinned to the bottom |
| Readout | `.metric` / `.metric-grid` | any number with a caption |
| Meter | `.meter-track` + `.meter-fill` | single proportion; adds `.ease` to animate |
| Composition | `.stacked` + `.legend` | one 100% bar; parts sum to the whole |
| Table | `table` + `.num` | numeric columns right-aligned and tabular; wrap in `.table-scroll` |
| Status | `.pill` + `ok`/`warn`/`bad`/`info`/`neutral` | carries a dot as well as a hue; `.plain` for link chips |
| Button | `button`, `.ghost`, `.approve`, `.reject`, `.btn-sm` | each variant names its own hover colour |
| Field | `.fields` / `.field` + `<label for>` | every control is labelled; focus is an accent border plus `--ring` |
| Switch | `.switch` | state, not action; the native input stays focusable |
| Quoted output | `.quote` | the agent's text, set apart from the UI's voice |
| Review | `.review` | reason → proposed text → actions → buttons |
| Run | `.run` | id, subject, age, actions |
| Empty state | `.empty-state` | says what would fill the panel, not nothing |

Spacing utilities (`.mt-1` … `.mt-4`, `.stack`, `.btn-row`) exist for the few
places markup needs a nudge. The set is kept deliberately small: a large
utility layer is how a design system becomes a stylesheet nobody can safely
delete from.

### Status is never colour alone

Every `.pill` renders a 6px dot from `currentColor`, so status survives
colour-blindness and a greyscale printout. Tones that encode a judgement —
stat cards, risk chips — state their band in the tooltip (`Risk band: high
(≥0.70)`) rather than asserting a target the reader cannot see.

---

## 4. Accessibility rules

- One focus treatment for everything focusable: `2px` accent outline, 2px
  offset. No rule may suppress an outline without putting something back.
- Every visible field has a `<label for>`. Checkboxes are exempt only when the
  switch wraps them.
- Async results (`#cc_msg`, `#kb_msg`, `#ticket_out`) are `aria-live` regions.
- A skip link to `#main` is the first tab stop; `scroll-padding-top` keeps
  anchors clear of the sticky bar.
- `prefers-reduced-motion: reduce` collapses all animation and transition
  durations.
- Tables carry `aria-label`s; a table without one is announced as "table".

---

## 5. Adding a component

1. Add tokens first if you need a new colour — in **both** palettes.
2. Write the rule without a literal colour.
3. Use scale tokens for every size, never a raw `px` font size.
4. Give it a focus state if it is focusable, a hover state if it is clickable.
5. Run `python -m pytest tests/test_dashboard_theme.py`. It will tell you about
   a missing token, an asymmetric palette, or text that fails contrast against
   the surface you put it on.

## 6. Known gaps

- The dashboard has no dark-specific shadow tuning beyond the token ramp, and
  no print stylesheet. `marketing/cost-one-pager.html` is the printable
  artifact, not the dashboard.
- Verification of rendered output is by DOM measurement, not pixel diffing —
  there is no visual regression harness in this repo.
