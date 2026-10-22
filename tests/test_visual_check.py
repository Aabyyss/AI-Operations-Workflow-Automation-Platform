"""The visual gate's comparator: bytes, boxes and tokens.

The browser capture cannot run in CI's pytest (it needs Chrome and a
server), so the parts that decide pass/fail are tested here directly: the
PNG decoder, the pixel diff's tolerance, the geometry comparison and the
CLI's exit codes. A gate whose comparator is untested is a gate that says
"ok" because it compared nothing.

The synthetic images are built in the test, including the filters a real
encoder emits but a hand-written one usually does not, so the decoder is
exercised on more than its own output.
"""
import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import visual_check as vc  # noqa: E402


def _png(width, height, rows, filter_type=0):
    """Build a PNG with a chosen per-row filter, to test the decoder."""
    channels = 4
    stride = width * channels
    raw = b""
    previous = bytes(stride)
    for y in range(height):
        line = rows[y * stride:(y + 1) * stride]
        if filter_type == 0:
            encoded = line
        elif filter_type == 1:                       # Sub
            encoded = bytearray(line)
            for i in range(stride - 1, channels - 1, -1):
                encoded[i] = (encoded[i] - encoded[i - channels]) & 0xFF
        elif filter_type == 2:                       # Up
            encoded = bytes((line[i] - previous[i]) & 0xFF for i in range(stride))
        elif filter_type == 3:                       # Average
            encoded = bytearray()
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                encoded.append((line[i] - ((left + previous[i]) >> 1)) & 0xFF)
        elif filter_type == 4:                       # Paeth
            encoded = bytearray()
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                up_left = previous[i - channels] if i >= channels else 0
                encoded.append((line[i] - vc._paeth(left, up, up_left)) & 0xFF)
        else:
            raise ValueError(filter_type)
        raw += bytes([filter_type]) + bytes(encoded)
        previous = bytes(line)

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (vc.PNG_SIGNATURE
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _solid(width, height, colour=(90, 120, 200, 255)):
    return bytes(colour) * (width * height)


# --------------------------------------------------------------------- PNG
@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_the_decoder_handles_every_filter_a_real_encoder_emits(filter_type):
    """libpng picks a filter per row; a decoder that only handles filter 0
    works on its own output and corrupts on Chrome's."""
    pixels = b"".join(bytes([(x * 7 + y * 11) % 256, (x * 3 + y * 5) % 256,
                             (x + y) % 256, 255])
                      for y in range(4) for x in range(5))
    data = _png(5, 4, pixels, filter_type)
    width, height, rgba = vc.decode_png(data)
    assert (width, height) == (5, 4)
    assert rgba == pixels


def test_roundtrip_through_the_encoder():
    pixels = _solid(3, 2, (10, 20, 30, 255))
    data = vc.encode_png(3, 2, pixels)
    assert vc.decode_png(data)[2] == pixels


def test_a_non_png_is_refused():
    with pytest.raises(ValueError, match="not a PNG"):
        vc.decode_png(b"GIF89a....")


# -------------------------------------------------------------- pixel diff
def test_identical_images_do_not_drift():
    a = vc.encode_png(4, 4, _solid(4, 4))
    diff = vc.diff_png(a, a)
    assert diff["ok"] is True
    assert diff["differing_pixels"] == 0


def test_antialiasing_does_not_fail_a_build_but_a_recolour_does():
    """The threshold exists to separate "the glyph rasterised slightly
    differently" from "that surface is a different colour"."""
    base = _solid(20, 20, (100, 100, 100, 255))
    faint = bytearray(base)
    for i in range(0, len(faint), 4):       # every 5th pixel, 8 levels off
        if (i // 4) % 5 == 0:
            faint[i] = 108
    assert vc.diff_png(vc.encode_png(20, 20, base), vc.encode_png(20, 20, bytes(faint)))["ok"]

    changed = bytearray(base)
    for i in range(0, len(changed), 4):
        changed[i] = 40                      # 60 levels darker, everywhere
    diff = vc.diff_png(vc.encode_png(20, 20, base), vc.encode_png(20, 20, bytes(changed)))
    assert diff["ok"] is False
    assert diff["fraction"] == 1.0
    assert diff["box"] == [0, 0, 19, 19]


def test_a_size_change_fails_with_both_sizes_named():
    diff = vc.diff_png(vc.encode_png(4, 4, _solid(4, 4)),
                       vc.encode_png(5, 4, _solid(5, 4)))
    assert diff["ok"] is False
    assert "4×4" in diff["reason"] and "5×4" in diff["reason"]


# ------------------------------------------------------------ metrics diff
def _metrics(**overrides):
    base = {
        "viewport": [1280, 900],
        "document": {"w": 1280, "h": 3000},
        "overflow_x": False,
        "tokens": {"--bg": "#0a0e1c", "--text": "#e8e6e1"},
        "headings": ["A", "B"],
        "cards": [{"title": "A", "box": [10, 20, 300, 200]},
                  {"title": "B", "box": [10, 240, 300, 120]}],
    }
    base.update(overrides)
    return base


def test_the_same_layout_has_no_problems():
    assert vc.compare_metrics(_metrics(), _metrics()) == []


def test_a_moved_card_is_named_with_its_delta():
    moved = _metrics(cards=[{"title": "A", "box": [10, 20, 300, 200]},
                            {"title": "B", "box": [10, 300, 300, 120]}])
    problems = vc.compare_metrics(_metrics(), moved)
    assert len(problems) == 1
    assert "'B' y: 240 → 300 (+60px" in problems[0]


def test_sub_tolerance_shifts_are_the_same_layout():
    nudge = _metrics(cards=[{"title": "A", "box": [12, 22, 300, 202]},
                            {"title": "B", "box": [10, 240, 300, 120]}])
    assert vc.compare_metrics(_metrics(), nudge) == []


@pytest.mark.parametrize("change,expected", [
    ({"tokens": {"--bg": "#ffffff", "--text": "#e8e6e1"}}, "design token --bg"),
    ({"overflow_x": True}, "horizontal overflow"),
    ({"headings": ["A", "C"]}, "section headings changed"),
    ({"cards": [{"title": "A", "box": [10, 20, 300, 200]}]}, "cards disappeared: ['B']"),
    ({"cards": [{"title": "B", "box": [10, 240, 300, 120]},
                {"title": "A", "box": [10, 20, 300, 200]}]}, "card order changed"),
    ({"document": {"w": 1280, "h": 3420}}, "document h"),
])
def test_each_kind_of_drift_is_reported_in_its_own_words(change, expected):
    problems = vc.compare_metrics(_metrics(), _metrics(**change))
    assert any(expected in p for p in problems), problems


def test_a_new_card_is_reported_as_appearing():
    extra = _metrics(cards=_metrics()["cards"] + [{"title": "C", "box": [10, 400, 300, 90]}])
    assert any("cards appeared: ['C']" in p for p in vc.compare_metrics(_metrics(), extra))


# --------------------------------------------------------------- CLI/exit
def _write_capture(folder, *, dark_text=True, png_colour=(90, 120, 200, 255)):
    (folder / "png").mkdir(parents=True, exist_ok=True)
    (folder / "metrics").mkdir(exist_ok=True)
    for theme in ("dark", "bright"):
        (folder / "png" / f"dash-{theme}-full.png").write_bytes(
            vc.encode_png(4, 3, _solid(4, 3, png_colour)))
        (folder / "metrics" / f"layout-{theme}.json").write_text(
            json.dumps(_metrics()), encoding="utf-8")


def _baseline_from(folder, baseline):
    vc.update_baseline(baseline, folder / "png", folder / "metrics", sys.platform)


def test_the_check_passes_against_its_own_baseline(tmp_path, monkeypatch):
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)
    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap)])
    assert vc.main() == 0


def test_a_recoloured_screenshot_fails_the_check(tmp_path, monkeypatch):
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)
    _write_capture(cap, png_colour=(20, 20, 20, 255))     # the same page, different colour
    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap)])
    assert vc.main() == 1


def test_no_baseline_is_an_error_not_a_pass(tmp_path, monkeypatch, capsys):
    """Exit 2: a check that could not run must never read as green."""
    cap = tmp_path / "cap"
    _write_capture(cap)
    monkeypatch.setattr(vc, "BASELINE_DIR", tmp_path / "missing")
    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap)])
    assert vc.main() == 2
    err = capsys.readouterr().err
    assert "cannot check" in err and "no baseline" in err


def test_the_stale_baseline_image_is_removed_on_update(tmp_path):
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "dash-dark-removed-card.png").write_bytes(
        vc.encode_png(2, 2, _solid(2, 2)))
    vc.update_baseline(baseline, cap / "png", cap / "metrics", sys.platform)
    assert not (baseline / "dash-dark-removed-card.png").exists()
    assert (baseline / "dash-dark-full.png").exists()


def test_capture_dir_keeps_the_screenshots_the_gate_compared(tmp_path, monkeypatch, capsys):
    """The failure has to be inspectable: CI uploads this directory, and a
    human opening a diff needs the PNG that produced it, not a temp path that
    vanished with the process."""
    cap = tmp_path / "cap"

    def fake_capture(dest, url=None, rerecord=False):
        _write_capture(dest)
        return {"png": dest / "png", "metrics": dest / "metrics", "log": ""}

    monkeypatch.setattr(vc, "capture", fake_capture)
    monkeypatch.setattr(vc, "BASELINE_DIR", tmp_path / "baseline")

    monkeypatch.setattr(sys, "argv",
                        ["visual_check", "--update", "--capture-dir", str(cap)])
    assert vc.main() == 0
    assert (cap / "png" / "dash-dark-full.png").exists()
    assert (cap / "metrics" / "layout-bright.json").exists()

    # The kept capture is then compared against the baseline it just wrote.
    monkeypatch.setattr(sys, "argv",
                        ["visual_check", "--capture-dir", str(cap)])
    assert vc.main() == 0
    assert "match the baseline" in capsys.readouterr().out


def test_a_drifted_capture_dir_is_named_in_the_failure(tmp_path, monkeypatch, capsys):
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)

    def fake_capture(dest, url=None, rerecord=False):
        _write_capture(dest, png_colour=(10, 10, 10, 255))
        return {"png": dest / "png", "metrics": dest / "metrics", "log": ""}

    monkeypatch.setattr(vc, "capture", fake_capture)
    monkeypatch.setattr(sys, "argv",
                        ["visual_check", "--capture-dir", str(cap)])
    assert vc.main() == 1
    assert str(cap) in capsys.readouterr().err


def test_keeping_a_capture_and_comparing_an_existing_one_are_exclusive(monkeypatch):
    """--current says "do not capture"; --capture-dir says "capture here".
    Silently honouring one would make the other a lie in the log."""
    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", "x",
                                     "--capture-dir", "y"])
    with pytest.raises(SystemExit) as exc:
        vc.main()
    assert exc.value.code != 0


def _shift_cards(folder):
    """Move the second card 160px down, as a different font stack would."""
    for theme in ("dark", "bright"):
        path = folder / "metrics" / f"layout-{theme}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["cards"][1]["box"] = [10, 400, 300, 120]
        path.write_text(json.dumps(doc), encoding="utf-8")


def test_cross_platform_skips_pixels_and_boxes_but_never_tokens(tmp_path, monkeypatch,
                                                               capsys):
    """--allow-cross-platform is the flag a CI running on Linux uses against a
    baseline recorded on Windows. It used to skip only the platform check and
    then diff fonts anyway; it must compare what actually transfers."""
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    doc = json.loads((baseline / "layout-baseline.json").read_text(encoding="utf-8"))
    doc["platform"] = "darwin"
    (baseline / "layout-baseline.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)

    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap)])
    assert vc.main() == 2                    # without the flag: cannot check

    # Another rasteriser's colours, another font stack's boxes.
    _write_capture(cap, png_colour=(10, 10, 10, 255))
    _shift_cards(cap)
    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap),
                                     "--allow-cross-platform"])
    assert vc.main() == 0
    assert "tokens, headings, card inventory" in capsys.readouterr().out

    # ... and a design change is still a failure on any platform.
    metrics_file = cap / "metrics" / "layout-dark.json"
    changed = json.loads(metrics_file.read_text(encoding="utf-8"))
    changed["tokens"]["--bg"] = "#ffffff"
    metrics_file.write_text(json.dumps(changed), encoding="utf-8")
    assert vc.main() == 1
    assert "--bg" in capsys.readouterr().err


def test_the_geometry_switch_does_not_hide_a_missing_card():
    """Inventory is not a measurement of text: a card that vanished is a
    failure whatever platform recorded the baseline."""
    shrunk = _metrics(cards=[_metrics()["cards"][0]])
    problems = vc.compare_metrics(_metrics(), shrunk, geometry=False)
    assert any("cards disappeared: ['B']" in p for p in problems)


def test_the_geometry_switch_keeps_horizontal_overflow_out_of_the_verdict():
    """Overflow is measured in text metrics, so a cross-platform run cannot
    judge it — and must not look like it did."""
    assert vc.compare_metrics(_metrics(), _metrics(overflow_x=True), geometry=False) == []


# ------------------------------------------------------------------- reporting
def test_the_report_is_written_on_a_pass_and_on_a_failure(tmp_path, monkeypatch):
    """CI reads this file, so it must exist for the run that failed — a report
    written only on success documents nothing anyone needs."""
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)
    report_path = tmp_path / "out" / "visual.json"

    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap),
                                     "--report", str(report_path)])
    assert vc.main() == 0
    passed = json.loads(report_path.read_text(encoding="utf-8"))
    assert passed["ok"] is True and passed["problems"] == []
    assert passed["mode"] == "full"
    assert passed["screenshots_compared"] == 2
    assert passed["themes"]["dark"]["pixels"] is True

    _write_capture(cap, png_colour=(0, 0, 0, 255))
    assert vc.main() == 1
    failed = json.loads(report_path.read_text(encoding="utf-8"))
    assert failed["ok"] is False
    assert any("dash-dark-full.png" in p for p in failed["problems"])


def test_a_cross_platform_report_says_what_it_did_not_compare(tmp_path, monkeypatch):
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    doc = json.loads((baseline / "layout-baseline.json").read_text(encoding="utf-8"))
    doc["platform"] = "darwin"
    (baseline / "layout-baseline.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)
    report_path = tmp_path / "visual.json"

    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap),
                                     "--allow-cross-platform", "--report", str(report_path)])
    assert vc.main() == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "cross-platform"
    assert report["baseline_platform"] == "darwin"
    assert report["themes"]["dark"]["pixels"] is False
    assert report["themes"]["dark"]["geometry"] is False


# -------------------------------------------------- per-platform baselines
# Screenshots do not transfer between platforms: the same CSS rasterises
# differently on Windows, macOS and Linux. Each platform therefore records
# into its own directory, and a run compares against its own platform's
# baseline — which is what makes a full pixel comparison possible in CI at
# all, rather than the cross-platform half-check it used to settle for.

def test_a_baseline_for_this_platform_is_found_in_its_own_directory(tmp_path, monkeypatch):
    root = tmp_path / "screenshots"
    cap = tmp_path / "cap"
    _write_capture(cap)
    vc.update_baseline(root / sys.platform, cap / "png", cap / "metrics", sys.platform)
    monkeypatch.setattr(vc, "BASELINE_DIR", root)

    assert vc.baseline_dir_for(sys.platform) == root / sys.platform


def test_a_baseline_recorded_elsewhere_is_not_silently_reused(tmp_path, monkeypatch):
    """A macOS baseline must not be read as if it described this machine.

    With no baseline for this platform, the resolver points at the
    directory `--update` would write, so the "no baseline" message names
    a path the operator can actually create.
    """
    root = tmp_path / "screenshots"
    cap = tmp_path / "cap"
    _write_capture(cap)
    vc.update_baseline(root / "darwin", cap / "png", cap / "metrics", "darwin")
    monkeypatch.setattr(vc, "BASELINE_DIR", root)

    resolved = vc.baseline_dir_for(sys.platform)
    assert resolved == root / sys.platform
    assert not (resolved / "layout-baseline.json").exists()


def test_the_platform_directory_wins_over_a_flat_one(tmp_path, monkeypatch):
    """Once a platform directory exists it is the baseline, even if a stale
    flat one is still lying around from before per-platform baselines."""
    root = tmp_path / "screenshots"
    cap = tmp_path / "cap"
    _write_capture(cap)
    vc.update_baseline(root, cap / "png", cap / "metrics", sys.platform)      # flat, legacy
    vc.update_baseline(root / sys.platform, cap / "png", cap / "metrics", sys.platform)
    monkeypatch.setattr(vc, "BASELINE_DIR", root)

    assert vc.baseline_dir_for(sys.platform) == root / sys.platform


def test_a_flat_baseline_from_another_platform_is_still_honored(tmp_path, monkeypatch):
    """The legacy layout is not deleted by this change: a flat baseline
    belonging to another platform is still found, so the platform check can
    turn it into an actionable message instead of "no baseline at all"."""
    root = tmp_path / "screenshots"
    cap = tmp_path / "cap"
    _write_capture(cap)
    vc.update_baseline(root, cap / "png", cap / "metrics", "darwin")
    monkeypatch.setattr(vc, "BASELINE_DIR", root)

    assert vc.baseline_dir_for(sys.platform) == root


def test_update_writes_into_the_platform_directory(tmp_path, monkeypatch, capsys):
    cap = tmp_path / "cap"
    _write_capture(cap)
    root = tmp_path / "screenshots"
    monkeypatch.setattr(vc, "BASELINE_DIR", root)
    monkeypatch.setattr(sys, "argv", ["visual_check", "--current", str(cap), "--update"])

    assert vc.main() == 0
    assert (root / sys.platform / "layout-baseline.json").exists()
    assert (root / sys.platform / "dash-dark-full.png").exists()
    out = capsys.readouterr().out
    assert sys.platform in out           # the message names where it wrote


def test_no_capture_without_a_capture_to_compare_is_an_error(tmp_path, monkeypatch):
    """--no-capture used to be parsed, ignored, and followed by a capture
    anyway — the exact opposite of what it says."""
    monkeypatch.setattr(sys, "argv", ["visual_check", "--no-capture"])
    with pytest.raises(SystemExit) as exc:
        vc.main()
    assert exc.value.code == 2


def test_no_capture_with_a_capture_does_not_capture(tmp_path, monkeypatch):
    """Given --current, --no-capture must not run a capture: it compares the
    capture it was pointed at."""
    cap = tmp_path / "cap"
    _write_capture(cap)
    baseline = tmp_path / "baseline"
    _baseline_from(cap, baseline)
    monkeypatch.setattr(vc, "BASELINE_DIR", baseline)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise AssertionError("capture() must not run")

    monkeypatch.setattr(vc, "capture", boom)
    monkeypatch.setattr(sys, "argv", ["visual_check", "--no-capture", "--current", str(cap)])
    assert vc.main() == 0
    assert calls == []
