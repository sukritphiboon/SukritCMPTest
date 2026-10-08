"""AES-256-GCM encryption for secrets stored in the database (device credentials).

Token format: ``v1:`` + base64url(nonce[12] || ciphertext || tag[16]).
A fresh random nonce is generated per encryption, so equal plaintexts give different tokens.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_PREFIX = "v1:"
_NONCE_BYTES = 12


class CryptoError(Exception):
    pass


class SecretCipher:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise CryptoError("The encryption key must be exactly 32 bytes (AES-256).")
        self._aead = AESGCM(key)

    @classmethod
    def from_base64(cls, value: str) -> SecretCipher:
        if not value:
            raise CryptoError("CMP_ENCRYPTION_KEY is not set.")
        try:
            key = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CryptoError("CMP_ENCRYPTION_KEY is not valid base64.") from exc
        return cls(key)

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(os.urandom(32)).decode()

    def encrypt(self, plaintext: str, aad: bytes | None = None) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), aad)
        return _PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode()

    def decrypt(self, token: str, aad: bytes | None = None) -> str:
        if not token.startswith(_PREFIX):
            raise CryptoError("Unknown token format.")
        try:
            raw = base64.urlsafe_b64decode(token[len(_PREFIX) :])
            nonce, sealed = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
            return self._aead.decrypt(nonce, sealed, aad).decode()
        except (InvalidTag, binascii.Error, ValueError) as exc:
            raise CryptoError("Decryption failed: wrong key or corrupted data.") from exc


def get_cipher() -> SecretCipher:
    return SecretCipher.from_base64(get_settings().encryption_key)
