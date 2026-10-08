import base64

import pytest

from app.core.crypto import CryptoError, SecretCipher


def cipher():
    return SecretCipher.from_base64(SecretCipher.generate_key())


def test_roundtrip():
    c = cipher()
    token = c.encrypt("S3cret!pw")
    assert token.startswith("v1:") and "S3cret" not in token
    assert c.decrypt(token) == "S3cret!pw"


def test_nonce_is_random_per_call():
    c = cipher()
    assert c.encrypt("same") != c.encrypt("same")


def test_wrong_key_fails():
    token = cipher().encrypt("x")
    with pytest.raises(CryptoError):
        cipher().decrypt(token)


def test_tampering_detected():
    c = cipher()
    token = c.encrypt("payload")
    raw = bytearray(base64.urlsafe_b64decode(token[3:]))
    raw[-1] ^= 1
    with pytest.raises(CryptoError):
        c.decrypt("v1:" + base64.urlsafe_b64encode(bytes(raw)).decode())


def test_aad_binding():
    c = cipher()
    token = c.encrypt("x", aad=b"row-1")
    assert c.decrypt(token, aad=b"row-1") == "x"
    with pytest.raises(CryptoError):
        c.decrypt(token, aad=b"row-2")


def test_key_must_be_32_bytes():
    with pytest.raises(CryptoError):
        SecretCipher(b"short")
    with pytest.raises(CryptoError):
        SecretCipher.from_base64("")
    with pytest.raises(CryptoError):
        SecretCipher.from_base64("not base64!!")
