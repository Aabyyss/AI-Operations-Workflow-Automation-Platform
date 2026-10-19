"""Ed25519 against the RFC's own vectors, plus the attacks worth naming.

The receipt scheme is only worth what its primitives are. These tests use
the test vectors from RFC 8032 §7.1 verbatim, so the implementation is
checked against the specification rather than against itself — a
self-consistent wrong implementation would pass a round-trip test forever.

Then the failure modes: a flipped message bit, a flipped signature bit, a
signature with L added to S (the classic malleability), a small-order public
key, and a truncated input. All must be False, not an exception.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import ed25519  # noqa: E402

# RFC 8032 §7.1 — TEST 1, 2 and 3, copied exactly.
VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
        "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


def _b(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


@pytest.mark.parametrize("seed_hex,public_hex,msg_hex,sig_hex", VECTORS)
def test_rfc8032_public_key(seed_hex, public_hex, msg_hex, sig_hex):
    assert ed25519.public_key(_b(seed_hex)).hex() == public_hex


@pytest.mark.parametrize("seed_hex,public_hex,msg_hex,sig_hex", VECTORS)
def test_rfc8032_signature_is_exact(seed_hex, public_hex, msg_hex, sig_hex):
    """Deterministic: byte-for-byte, not just 'verifies'."""
    assert ed25519.sign(_b(seed_hex), _b(msg_hex)).hex() == sig_hex


@pytest.mark.parametrize("seed_hex,public_hex,msg_hex,sig_hex", VECTORS)
def test_rfc8032_verifies(seed_hex, public_hex, msg_hex, sig_hex):
    assert ed25519.verify(_b(public_hex), _b(msg_hex), _b(sig_hex)) is True


# ------------------------------------------------------------------ rejection
SEED = bytes.fromhex(VECTORS[0][0])
PUB = ed25519.public_key(SEED)


def test_a_changed_message_fails():
    sig = ed25519.sign(SEED, b"refund approved")
    assert ed25519.verify(PUB, b"refund declined", sig) is False


def test_a_changed_signature_byte_fails():
    sig = bytearray(ed25519.sign(SEED, b"payload"))
    sig[10] ^= 0x01
    assert ed25519.verify(PUB, b"payload", bytes(sig)) is False


def test_a_different_key_fails():
    other = ed25519.public_key(bytes(range(32)))
    sig = ed25519.sign(SEED, b"payload")
    assert ed25519.verify(other, b"payload", sig) is False


def test_the_malleable_variant_is_rejected():
    """S + L is a second 'valid' signature for the same message on a lazy
    verifier; RFC 8032 requires S < L, so this must be refused."""
    msg = b"payload"
    sig = ed25519.sign(SEED, msg)
    s = int.from_bytes(sig[32:], "little") + ed25519.L
    malleable = sig[:32] + s.to_bytes(32, "little")
    assert ed25519.verify(PUB, msg, malleable) is False


def test_a_small_order_public_key_is_rejected():
    # The identity and the order-8 points encode to these; none may verify.
    for encoded in (b"\x01" + b"\x00" * 31, b"\x00" * 32):
        assert ed25519.verify(encoded, b"payload", b"\x00" * 64) is False


def test_truncated_inputs_are_false_not_exceptions():
    assert ed25519.verify(PUB[:31], b"payload", b"\x00" * 64) is False
    assert ed25519.verify(PUB, b"payload", b"\x00" * 63) is False


def test_signing_needs_a_full_seed():
    with pytest.raises(ValueError):
        ed25519.sign(b"too short", b"payload")
