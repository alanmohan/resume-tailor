"""Token creation and hashing."""

import hashlib
import re

from app.security import generate_token, hash_token, is_well_formed_token


def test_tokens_are_long_url_safe_and_unique() -> None:
    tokens = {generate_token() for _ in range(200)}
    assert len(tokens) == 200
    for token in tokens:
        # 32 random bytes -> 43 base64url characters without padding.
        assert len(token) == 43
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token)


def test_hash_is_sha256_hex_and_deterministic() -> None:
    token = generate_token()
    digest = hash_token(token)
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert digest == hash_token(token)
    assert len(digest) == 64
    assert token not in digest


def test_different_tokens_hash_differently() -> None:
    assert hash_token(generate_token()) != hash_token(generate_token())


def test_generated_tokens_are_well_formed() -> None:
    assert is_well_formed_token(generate_token())


def test_malformed_tokens_are_recognised() -> None:
    assert not is_well_formed_token("")
    assert not is_well_formed_token("short")
    assert not is_well_formed_token("a" * 31)
    assert not is_well_formed_token("a" * 129)
    assert not is_well_formed_token("has spaces " + "a" * 40)
    assert not is_well_formed_token("bad$chars" + "a" * 40)
    assert not is_well_formed_token("a" * 40 + "\n")
