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
