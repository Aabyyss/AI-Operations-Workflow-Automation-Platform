"""Make a script's output UTF-8 on every platform.

Every CLI here prints an ellipsis, an arrow or a multiplication sign at some
point — `signed by 3d40…`, `240 → 300`, `4×4`. On a Windows console or a pipe
those go out in the configured codepage (cp1252, cp437), so they arrive as
different bytes than the file says, and any tool that decodes the output as
UTF-8 — including our own tests — gets a UnicodeDecodeError on a message that
was perfectly correct.

Call `use_utf8()` first thing in main(). It is a no-op where the streams
cannot be reconfigured (pytest's capture objects, a redirected StringIO), and
`errors="replace"` means a terminal that cannot render a character degrades
one glyph instead of failing the command.
"""
from __future__ import annotations

import sys


def use_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A closed or already-detached stream is not worth failing over.
            pass
