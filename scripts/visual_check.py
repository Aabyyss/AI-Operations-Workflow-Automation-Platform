#!/usr/bin/env python
"""Visual regression for the dashboard: does it still look like it did?

    python -m scripts.visual_check              # capture, then compare
    python -m scripts.visual_check --update     # accept the current look
    python -m scripts.visual_check --no-capture --current DIR
    python -m scripts.visual_check --capture-dir build/visual   # keep the shots
    python -m scripts.visual_check --allow-cross-platform

What is compared, and why each one is a different kind of check:

* **Pixels** — the committed screenshots (docs/screenshots/dash-*.png) are
  the visual baseline. A pixel diff catches what geometry cannot: a colour
  that shifted, a shadow that disappeared, text that wrapped.
* **Geometry** — every `.card`, its heading, and its box. A pixel diff can
  be *too* sensitive (antialiasing), so the layout is also compared as
  numbers, with a tolerance, which distinguishes "one card moved 40px" from
  "the font rasterised slightly differently".
* **Tokens** — the resolved design-system values. These are exact, not
  tolerant: `--bg` either is the colour it was or the theme changed.
* **Inventory** — the cards and their order. A missing or reordered card is
  a layout change even if every remaining box is identical.

The data the page reads is frozen (`docs/screenshots/fixtures/`, recorded
once by scripts/capture_dashboard.mjs), so a diff is measuring the design
and never the demo ledger.

Exit codes: 0 no drift, 1 drift, 2 cannot check (no baseline for this
platform, no browser, server did not start) — a check that cannot run must
not read as a pass.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from scripts.console import use_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = ROOT / "docs" / "screenshots"
BASELINE_FILE = BASELINE_DIR / "layout-baseline.json"
FIXTURES_DIR = BASELINE_DIR / "fixtures"
THEMES = ("dark", "bright")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Tolerances. Geometry gets a few pixels because text rasterises differently
# across driver versions; tokens, inventory and overflow get none because
# there is no such thing as "almost the same colour token".
GEOMETRY_TOLERANCE_PX = 4
DOCUMENT_TOLERANCE_PX = 8
CHANNEL_THRESHOLD = 32        # per-channel difference that counts as a change
MAX_DIFFERING_FRACTION = 0.002  # 0.2% of pixels


# ------------------------------------------------------------------- PNG I/O
def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Decode an 8-bit, non-interlaced PNG to RGBA.

    Deliberately small and dependency-free: the visual gate has to run with
    the same Python that runs the rest of the project, without a Pillow
    install. Anything it cannot decode (16-bit, interlaced) raises instead
    of being compared wrongly.
    """
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG")
    pos, idat, palette = 8, bytearray(), None
    width = height = depth = colour = interlace = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    if width is None:
        raise ValueError("PNG has no IHDR")
    if depth != 8 or interlace != 0:
        raise ValueError(f"unsupported PNG (bit depth {depth}, interlace {interlace})")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None:
        raise ValueError(f"unsupported PNG colour type {colour}")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("truncated PNG data")
    out = bytearray(height * stride)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        filt = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if filt == 1:      # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filt == 2:    # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filt == 3:    # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filt == 4:    # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                up_left = previous[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, up, up_left)) & 0xFF
        elif filt != 0:
            raise ValueError(f"bad PNG filter type {filt}")
        out[y * stride:(y + 1) * stride] = line
        previous = line

    # Normalise every supported colour type to RGBA, so comparisons never
    # silently compare a 3-channel image with a 4-channel one.
    if colour == 6:
        return width, height, bytes(out)
    if colour == 2:
        return width, height, b"".join(
            bytes((out[i], out[i + 1], out[i + 2], 255))
            for i in range(0, len(out), 3))
    if colour == 0:
        return width, height, b"".join(
            bytes((g, g, g, 255)) for g in out)
    if colour == 4:  # grey + alpha
        return width, height, b"".join(
            bytes((out[i], out[i], out[i], out[i + 1]))
            for i in range(0, len(out), 2))
    # palette
    if palette is None:
        raise ValueError("palette PNG without PLTE")
    return width, height, b"".join(
        bytes((palette[3 * i], palette[3 * i + 1], palette[3 * i + 2], 255))
        for i in out)


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """A minimal encoder, used by the tests to build fixtures."""
    if len(rgba) != width * height * 4:
        raise ValueError("rgba length does not match dimensions")
    raw = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4]
                   for y in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (PNG_SIGNATURE
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def diff_png(baseline: bytes, current: bytes,
             channel_threshold: int = CHANNEL_THRESHOLD,
             max_fraction: float = MAX_DIFFERING_FRACTION) -> dict:
    """How far apart are two images? Returns counts and the first box.

    A pixel counts as changed when any channel moves by more than
    `channel_threshold` — this is what stops antialiasing (one or two levels
    on a handful of glyph edges) from failing a build, while a 40% darker
    surface still does.
    """
    wb, hb, base = decode_png(baseline)
    wc, hc, cur = decode_png(current)
    if (wb, hb) != (wc, hc):
        return {"ok": False,
                "reason": f"image size changed: {wb}×{hb} → {wc}×{hc}",
                "differing_pixels": None, "fraction": None, "box": None}

    differing = 0
    box = None
    worst = 0
    for y in range(hb):
        row = y * wb * 4
        for x in range(wb):
            i = row + x * 4
            delta = max(abs(base[i] - cur[i]), abs(base[i + 1] - cur[i + 1]),
                        abs(base[i + 2] - cur[i + 2]), abs(base[i + 3] - cur[i + 3]))
            if delta > worst:
                worst = delta
            if delta > channel_threshold:
                differing += 1
                if box is None:
                    box = [x, y, x, y]
                else:
                    box[0] = min(box[0], x)
                    box[1] = min(box[1], y)
                    box[2] = max(box[2], x)
                    box[3] = max(box[3], y)
    total = wb * hb
    fraction = differing / total
    return {"ok": fraction <= max_fraction,
            "reason": (None if fraction <= max_fraction else
                       f"{differing} of {total} pixels changed "
                       f"({fraction:.2%} > {max_fraction:.2%})"),
            "differing_pixels": differing, "total_pixels": total,
            "fraction": round(fraction, 5), "worst_channel_delta": worst,
            "box": box}


# --------------------------------------------------------------- metrics diff
def compare_metrics(baseline: dict, current: dict,
                    tolerance: int = GEOMETRY_TOLERANCE_PX,
                    doc_tolerance: int = DOCUMENT_TOLERANCE_PX,
                    geometry: bool = True) -> list[str]:
    """Every way the layout differs, in the order an operator would fix them.

    `geometry=False` keeps the checks that mean the same thing on every
    platform — tokens, headings, which cards exist and in what order — and
    drops the ones that measure text: fonts rasterise differently on macOS,
    Windows and Linux, so a box that moved two pixels there says nothing
    about the design. It is never used to excuse a token change.
    """
    problems: list[str] = []

    if geometry:
        if baseline.get("viewport") != current.get("viewport"):
            problems.append(f"viewport: {baseline.get('viewport')} → {current.get('viewport')}")
        if bool(baseline.get("overflow_x")) != bool(current.get("overflow_x")):
            problems.append("horizontal overflow appeared or disappeared "
                            "(a row now scrolls sideways)")
        for name in ("w", "h"):
            was = (baseline.get("document") or {}).get(name)
            now = (current.get("document") or {}).get(name)
            if was is None or now is None or abs(was - now) > doc_tolerance:
                problems.append(f"document {name}: {was} → {now}")

    base_tokens = baseline.get("tokens") or {}
    cur_tokens = current.get("tokens") or {}
    for token in sorted(set(base_tokens) | set(cur_tokens)):
        if base_tokens.get(token) != cur_tokens.get(token):
            problems.append(f"design token {token}: {base_tokens.get(token)!r} → "
                            f"{cur_tokens.get(token)!r}")

    base_headings = baseline.get("headings") or []
    cur_headings = current.get("headings") or []
    if base_headings != cur_headings:
        problems.append(f"section headings changed: {base_headings} → {cur_headings}")

    base_cards = baseline.get("cards") or []
    cur_cards = current.get("cards") or []
    base_titles = [c.get("title") for c in base_cards]
    cur_titles = [c.get("title") for c in cur_cards]
    if base_titles != cur_titles:
        missing = [t for t in base_titles if t not in cur_titles]
        extra = [t for t in cur_titles if t not in base_titles]
        if missing:
            problems.append(f"cards disappeared: {missing}")
        if extra:
            problems.append(f"cards appeared: {extra}")
        if not missing and not extra:
            problems.append(f"card order changed: {base_titles} → {cur_titles}")
        return problems     # per-card geometry is meaningless once the list moved

    if not geometry:
        return problems

    for base_card, cur_card in zip(base_cards, cur_cards):
        title = base_card.get("title") or "(untitled)"
        for index, axis in enumerate(("x", "y", "w", "h")):
            was = base_card["box"][index]
            now = cur_card["box"][index]
            if abs(was - now) > tolerance:
                problems.append(f"card {title!r} {axis}: {was} → {now} "
                                f"({now - was:+d}px, tolerance {tolerance})")
    return problems


# ------------------------------------------------------------------ capturing
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (URLError, OSError):
            time.sleep(0.25)
    return False


def capture(workspace: Path, *, url: str | None = None, rerecord: bool = False) -> dict:
    """Run the browser capture into `workspace`; start a server if needed.

    The server gets a throwaway data directory and the committed fixtures:
    the page must render the frozen demo data, not whatever is in the local
    ledger today.
    """
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is not installed (the capture is headless Chrome)")
    if not (ROOT / "node_modules" / "playwright-core").exists():
        raise RuntimeError("playwright-core is not installed "
                           "(npm install --no-save playwright-core)")
    out_png = workspace / "png"
    out_metrics = workspace / "metrics"
    out_png.mkdir(parents=True, exist_ok=True)
    out_metrics.mkdir(parents=True, exist_ok=True)

    server = None
    if url is None:
        port = _free_port()
        url = f"http://127.0.0.1:{port}/"
        env = dict(os.environ, AIOPS_DATA_DIR=str(workspace / "data"),
                   AIOPS_MODE="mock")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app",
             "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not _wait_for(url + "health"):
            server.terminate()
            stderr = server.stderr.read().decode("utf-8", "replace")[-800:] if server.stderr else ""
            raise RuntimeError(f"the server did not start: {stderr.strip() or 'no output'}")

    try:
        proc = subprocess.run(
            [node, str(ROOT / "scripts" / "capture_dashboard.mjs"),
             "--url", url, "--out", str(out_png), "--metrics-out", str(out_metrics),
             "--fixtures", str(FIXTURES_DIR)] + (["--rerecord"] if rerecord else []),
            cwd=ROOT, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError("capture failed: "
                               + proc.stderr.decode("utf-8", "replace")[-800:].strip())
        return {"png": out_png, "metrics": out_metrics,
                "log": proc.stdout.decode("utf-8", "replace").strip()}
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


# ----------------------------------------------------------------- comparison
def load_baseline(baseline_dir: Path) -> dict | None:
    path = baseline_dir / "layout-baseline.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare(baseline_dir: Path, current_png: Path, current_metrics: Path,
            *, allow_cross_platform: bool = False) -> tuple[list[str], dict]:
    """All drift between the committed baseline and a fresh capture."""
    baseline = load_baseline(baseline_dir)
    if baseline is None:
        return ([f"no baseline at {baseline_dir / 'layout-baseline.json'} — "
                 f"run with --update on the target platform first"], {})

    platform = baseline.get("platform")
    if platform != sys.platform and not allow_cross_platform:
        return ([f"baseline was captured on {platform!r}, this run is "
                 f"{sys.platform!r}: pixel and geometry baselines do not "
                 f"transfer between platforms. Re-run with --update here, or "
                 f"pass --allow-cross-platform to compare only the tokens and "
                 f"inventory"], {})

    problems: list[str] = []
    report: dict = {}
    for theme in THEMES:
        metrics_file = current_metrics / f"layout-{theme}.json"
        if not metrics_file.exists():
            problems.append(f"{theme}: no metrics captured")
            continue
        current = json.loads(metrics_file.read_text(encoding="utf-8"))
        theme_problems = compare_metrics(baseline["themes"][theme], current,
                                         geometry=not allow_cross_platform)
        problems += [f"{theme}: {p}" for p in theme_problems]

        theme_pngs = 0
        # Pixels are only comparable against a baseline recorded by the same
        # rasteriser. Cross-platform runs say so in the report rather than
        # comparing images they cannot compare and calling the noise drift.
        base_pngs = ([] if allow_cross_platform
                     else sorted(baseline_dir.glob(f"dash-{theme}-*.png")))
        for base_png in base_pngs:
            new_png = current_png / base_png.name
            if not new_png.exists():
                problems.append(f"{theme}: screenshot {base_png.name} was not captured")
                continue
            diff = diff_png(base_png.read_bytes(), new_png.read_bytes())
            theme_pngs += 1
            if not diff["ok"]:
                problems.append(f"{theme}: {base_png.name}: {diff['reason']}"
                                + (f" (first at {diff['box']})" if diff.get("box") else ""))
        report[theme] = {"screenshots": theme_pngs,
                        "metric_problems": theme_problems,
                        "pixels": not allow_cross_platform,
                        "geometry": not allow_cross_platform}
    return problems, report


def update_baseline(baseline_dir: Path, current_png: Path, current_metrics: Path,
                    platform: str) -> dict:
    """Adopt the current capture as the baseline (the deliberate refresh)."""
    baseline_dir.mkdir(parents=True, exist_ok=True)
    produced = {png.name for png in current_png.glob("dash-*.png")}
    for png in sorted(current_png.glob("dash-*.png")):
        shutil.copy2(png, baseline_dir / png.name)
    # A card that was renamed or removed leaves its old screenshot behind,
    # and a stale baseline image is a check that can never pass again.
    for stale in sorted(baseline_dir.glob("dash-*.png")):
        if stale.name not in produced:
            stale.unlink()
    themes = {}
    for theme in THEMES:
        metrics = json.loads((current_metrics / f"layout-{theme}.json")
                             .read_text(encoding="utf-8"))
        metrics.pop("captured_at", None)       # the only field that always moves
        themes[theme] = metrics
    doc = {"platform": platform, "themes": themes,
           "note": "Regenerate with: python -m scripts.visual_check --update"}
    (baseline_dir / "layout-baseline.json").write_text(
        json.dumps(doc, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    return doc


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true",
                    help="accept the current capture as the new baseline")
    ap.add_argument("--current", type=Path, default=None,
                    help="compare this capture instead of running one "
                         "(expects png/ and metrics/ inside)")
    ap.add_argument("--capture-dir", type=Path, default=None,
                    help="capture into this directory and keep it (default: a "
                         "temp dir) — how a failing CI run keeps the "
                         "screenshots that failed it")
    ap.add_argument("--no-capture", action="store_true",
                    help="alias for --current without a fresh capture")
    ap.add_argument("--url", default=None,
                    help="use this running server instead of starting one")
    ap.add_argument("--rerecord", action="store_true",
                    help="re-record the API fixtures (needs a server with data)")
    ap.add_argument("--allow-cross-platform", action="store_true",
                    help="compare tokens/inventory only when the baseline was "
                         "captured on another platform")
    ap.add_argument("--report", type=Path, metavar="PATH",
                    help="write the verdict as JSON (what CI and a bot read; "
                         "the exit code is the same answer for a human)")
    args = ap.parse_args()

    if args.current is not None and args.capture_dir is not None:
        ap.error("--capture-dir captures; combine it with a comparison, not "
                 "--current")

    workdir = args.current
    captured = workdir is None
    if captured:
        # --capture-dir is the same capture, kept where the caller asked. A
        # gate whose failure leaves nothing behind can only be argued with,
        # not inspected.
        workdir = args.capture_dir or Path(tempfile.mkdtemp(prefix="visual-check-"))
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            info = capture(workdir, url=args.url, rerecord=args.rerecord)
        except RuntimeError as exc:
            print(f"cannot check: {exc}", file=sys.stderr)
            return 2
        if info.get("log"):
            print(info["log"])
        png, metrics = info["png"], info["metrics"]
    else:
        png, metrics = workdir / "png", workdir / "metrics"
        if not metrics.exists():
            print(f"cannot check: {metrics} does not exist", file=sys.stderr)
            return 2

    if args.update:
        doc = update_baseline(BASELINE_DIR, png, metrics, sys.platform)
        print(f"baseline updated: {len(doc['themes'])} themes, platform {sys.platform}")
        return 0

    # "Cannot check" is its own exit code: a missing baseline or a baseline
    # from another platform must never be reported as drift, and never as ok.
    baseline = load_baseline(BASELINE_DIR)
    if baseline is None:
        print(f"cannot check: no baseline at {BASELINE_FILE} — capture one on this "
              f"platform with --update", file=sys.stderr)
        return 2
    if baseline.get("platform") != sys.platform and not args.allow_cross_platform:
        print(f"cannot check: the baseline was captured on "
              f"{baseline.get('platform')!r}, this run is {sys.platform!r} — "
              f"re-capture here with --update, or pass --allow-cross-platform "
              f"to compare only tokens and inventory", file=sys.stderr)
        return 2

    problems, report = compare(BASELINE_DIR, png, metrics,
                              allow_cross_platform=args.allow_cross_platform)
    shots = sum(r["screenshots"] for r in report.values())
    if args.report is not None:
        # Written before the early return: a failure is the run someone wants
        # to parse, so the report cannot only exist on the happy path.
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({
            "ok": not problems,
            "problems": problems,
            "mode": ("cross-platform" if args.allow_cross_platform else "full"),
            "platform": sys.platform,
            "baseline_platform": baseline.get("platform"),
            "screenshots_compared": shots,
            "themes": report,
            "capture": str(workdir),
        }, indent=1) + "\n", encoding="utf-8")
    if problems:
        print(f"VISUAL DRIFT ({len(problems)} problem(s); "
              f"{shots} screenshot(s) compared)", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nIf the change is intended: python -m scripts.visual_check --update",
              file=sys.stderr)
        if captured:
            print(f"the screenshots that failed this check: {workdir}", file=sys.stderr)
        return 1
    if args.allow_cross_platform:
        print(f"ok: {len(THEMES)} layouts (tokens, headings, card inventory) match "
              f"the baseline; pixels and geometry skipped — recorded on "
              f"{baseline.get('platform')!r}, this run is {sys.platform!r}")
    else:
        print(f"ok: {shots} screenshots and {len(THEMES)} layouts match the baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
