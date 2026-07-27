"""Encrypt cloud credentials at rest.

Fernet key derived from SECRET_KEY via HKDF so one .env value covers both
session signing and credential encryption.
"""
import base64
import json
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _fernet() -> Fernet:
    secret = os.environ.get("SECRET_KEY", "dev-only-insecure").encode()
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"nestbox-credentials", info=b"fernet"
    ).derive(secret)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt_json(blob: bytes | None) -> dict | None:
    if not blob:
        return None
    return json.loads(_fernet().decrypt(bytes(blob)))
