from __future__ import annotations

import hashlib

from cryptography.fernet import Fernet

from app.config import get_settings

_settings = get_settings()
_fernet = Fernet(_settings.upstream_key_fernet_key.encode())


def encrypt_key(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt_key(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode()


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def preview_key(plaintext: str) -> str:
    if len(plaintext) <= 10:
        return plaintext
    return f"{plaintext[:6]}...{plaintext[-4:]}"
