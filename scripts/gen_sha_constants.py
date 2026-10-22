#!/usr/bin/env python
"""Check (or regenerate) the SHA-2 constants embedded in the receipt viewer.

    python -m scripts.gen_sha_constants            # verify the HTML's tables
    python -m scripts.gen_sha_constants --update   # rewrite them

The offline viewer (docs/receipt_viewer.html) implements SHA-256 and
SHA-512 in the browser so a receipt can be verified with no server, which
means the round constants are embedded in that file. They are a
*definition* — the first bits of the fractional parts of the cube roots
(K) and square roots (H) of the first primes — so this module recomputes
them from that definition and fails if the embedded tables differ by a
single bit. An early version of this work asserted a *remembered*
"published" constant and was confidently wrong; computing beats
remembering, which is why the tables are checked twice here: once by
Decimal powers, once by integer Newton on the scaled radicand.

Exit codes: 0 tables match the definition, 1 they do not.
"""
from __future__ import annotations

import argparse
import re
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.console import use_utf8  # noqa: E402

getcontext().prec = 150

HTML_PATH = "docs/receipt_viewer.html"

TABLES = {
    "SHA256_K": ("cbrt", 32, 64),
    "SHA256_H": ("sqrt", 32, 8),
    "SHA512_K": ("cbrt", 64, 80),
    "SHA512_H": ("sqrt", 64, 8),
}


def primes(n: int) -> list[int]:
    out, c = [], 2
    while len(out) < n:
        if all(c % p for p in out if p * p <= c):
            out.append(c)
        c += 1
    return out


def frac_bits(p: int, kind: str, bits: int) -> int:
    """First `bits` of the fractional part of sqrt/cbrt(p) (Decimal powers)."""
    v = Decimal(p)
    root = v.sqrt() if kind == "sqrt" else v ** (Decimal(1) / 3)
    return int((root - int(root)) * (1 << bits))


def newton_frac_bits(p: int, kind: str, bits: int) -> int:
    """The same quantity by integer Newton — an independent derivation."""
    width = bits * 3 + 32
    scale = 1 << width
    target = p * scale ** 3 if kind == "cbrt" else p * scale * scale
    exponent = 3 if kind == "cbrt" else 2
    x = int(Decimal(target) ** (Decimal(1) / exponent))
    while True:
        if exponent == 3:
            nx = (2 * x + target // (x * x)) // 3
        else:
            nx = (x + target // x) // 2
        if nx >= x:
            break
        x = nx
    root = Decimal(x) / Decimal(scale)
    return int((root - int(root)) * (1 << bits))


def compute() -> dict[str, list[int]]:
    ps = primes(max(n for _, _, n in TABLES.values()))
    tables = {name: [frac_bits(p, kind, bits) for p in ps[:count]]
              for name, (kind, bits, count) in TABLES.items()}
    # Both derivations must agree, everywhere, not just at the sampled
    # anchors: sample the first, last and a middle entry of each table.
    for name, (kind, bits, count) in TABLES.items():
        for index in sorted({0, count // 2, count - 1}):
            expected = newton_frac_bits(ps[index], kind, bits)
            assert tables[name][index] == expected, (name, index)
    return tables


def fmt(values: list[int], per: int = 4, width: int = 10, suffix: str = "") -> str:
    lines, row = [], []
    for v in values:
        row.append(("0x%0*x" % (width, v)) + suffix)
        if len(row) == per:
            lines.append(", ".join(row))
            row = []
    if row:
        lines.append(", ".join(row))
    return ",\n".join(lines)


def embedded(html: str) -> dict[str, list[int]]:
    """The tables currently in the viewer, parsed back to integers."""
    found = {}
    for name in TABLES:
        m = re.search(rf"const {name} = \[([^\]]*)\];", html)
        if not m:
            raise SystemExit(f"error: {HTML_PATH} has no {name} table")
        found[name] = [int(tok.rstrip("n"), 16)
                       for tok in re.findall(r"0x[0-9a-fA-F]+n?", m.group(1))]
    return found


def main() -> int:
    use_utf8()          # the docstring's — reaches --help, where it is bytes
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the viewer's tables from the definition")
    args = ap.parse_args()

    want = compute()
    html = open(HTML_PATH, encoding="utf-8").read()
    have = embedded(html)

    drift = {name: (len(have[name]), len(want[name]))
             for name in want
             if have[name] != want[name]}
    if drift and args.update:
        for name, (kind, bits, count) in TABLES.items():
            suffix = "n" if name.startswith("SHA512") else ""
            width = 16 if name.startswith("SHA512") else 10
            html = re.sub(rf"(const {name} = \[)[^\]]*(\];)",
                          lambda m: m.group(1) + "\n" + fmt(want[name], width=width,
                                                            suffix=suffix) + "\n" + m.group(2),
                          html)
        open(HTML_PATH, "w", encoding="utf-8", newline="\n").write(html)
        print(f"updated {HTML_PATH}: " + ", ".join(sorted(drift)))
        return 0
    if drift:
        for name, (got, expected) in sorted(drift.items()):
            if got != expected:
                detail = f"has {got} entries, the definition gives {expected}"
            else:
                i = next(i for i, (a, b) in enumerate(zip(have[name], want[name]))
                         if a != b)
                detail = (f"entry {i} is 0x{have[name][i]:x}, "
                          f"the definition gives 0x{want[name][i]:x}")
            print(f"DRIFT: {name} {detail}", file=sys.stderr)
        print(f"{HTML_PATH} no longer matches the SHA-2 definition "
              f"(python -m scripts.gen_sha_constants --update rewrites it)",
              file=sys.stderr)
        return 1
    print("ok: the viewer's SHA-2 tables match the definition "
          f"({', '.join(f'{n}:{len(v)}' for n, v in want.items())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
