"""Ed25519 signatures, in pure Python, with no dependencies.

Why hand-rolled instead of `cryptography` or `pynacl`: a decision receipt has
to be verifiable *offline, by the auditor*, years later, on a machine that
only has Python. Adding a compiled dependency to the platform for this would
mean the verification tool needs the same environment as the platform, which
defeats the point. Ed25519 is small enough to implement correctly and to
check against the RFC's own vectors — which `tests/test_ed25519.py` does.

This is the RFC 8032 algorithm (Edwards curve, SHA-512, deterministic nonce).
Two hardening details that the historical reference code omitted:

- `S < L` is enforced on verification, so a valid signature cannot be
  re-encoded into a second "valid" signature by adding L to S.
- public keys and R are fully decoded and checked to be on the curve, so a
  small-order or off-curve point is rejected rather than accepted silently.

Not constant-time. Signing happens on the server with a key it holds; the
threat model here is a receipt altered after the fact, not a local attacker
measuring our scalar multiplication.
"""
from __future__ import annotations

import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493   # group order
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

SEED_BYTES = 32
SIGNATURE_BYTES = 64


def _sha512(*parts: bytes) -> bytes:
    h = hashlib.sha512()
    for part in parts:
        h.update(part)
    return h.digest()


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _recover_x(y: int) -> int:
    """The even x for which (x, y) is on the curve."""
    xx = (y * y - 1) * _inv(D * y * y + 1) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = x * SQRT_M1 % P
    if x % 2 != 0:
        x = P - x
    return x


_BY = 4 * _inv(5) % P
_BX = _recover_x(_BY)
# Extended coordinates (x, y, z, t) with x = X/Z, y = Y/Z, xy = T/Z.
_BASE = (_BX, _BY, 1, _BX * _BY % P)
_IDENTITY = (0, 1, 1, 0)


def _add(p1: tuple, p2: tuple) -> tuple:
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = t1 * 2 * D * t2 % P
    dd = z1 * 2 * z2 % P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(point: tuple, scalar: int) -> tuple:
    """Double-and-add, iterative so a large scalar cannot exhaust the stack."""
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def _encode(point: tuple) -> bytes:
    x, y, z, _ = point
    zi = _inv(z)
    x, y = x * zi % P, y * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(data: bytes) -> tuple:
    if len(data) != 32:
        raise ValueError("a point is 32 bytes")
    y = int.from_bytes(data, "little") & ((1 << 255) - 1)
    sign = data[31] >> 7
    if y >= P:
        raise ValueError("y is not a field element")
    x = _recover_x(y)
    if x & 1 != sign:
        x = P - x
    # On-curve check: -x² + y² = 1 + d·x²·y²
    if (-x * x + y * y - 1 - D * x * x * y * y) % P != 0:
        raise ValueError("point is not on the curve")
    return (x, y, 1, x * y % P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != SEED_BYTES:
        raise ValueError(f"seed must be {SEED_BYTES} bytes")
    digest = _sha512(seed)
    a = int.from_bytes(digest[:32], "little")
    a &= (1 << 254) - 8       # clear the low 3 bits
    a |= 1 << 254             # set the second-highest bit
    return a, digest[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte seed."""
    a, _ = _expand(seed)
    return _encode(_mul(_BASE, a))


def sign(seed: bytes, message: bytes) -> bytes:
    """Deterministic Ed25519 signature (RFC 8032 §5.1.6)."""
    a, prefix = _expand(seed)
    pk = _encode(_mul(_BASE, a))
    r = int.from_bytes(_sha512(prefix, message), "little") % L
    big_r = _encode(_mul(_BASE, r))
    hram = int.from_bytes(_sha512(big_r, pk, message), "little") % L
    s = (r + hram * a) % L
    return big_r + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a signature this key produced over this message."""
    if len(public) != 32 or len(signature) != SIGNATURE_BYTES:
        return False
    try:
        a_point = _decode(public)
        r_point = _decode(signature[:32])
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    hram = int.from_bytes(_sha512(signature[:32], public, message), "little") % L
    return _encode(_mul(_BASE, s)) == _encode(_add(r_point, _mul(a_point, hram)))
