"""Sensitive payloads are encrypted before persistence and never included in audit text."""

import json
import os


class SecurityConfigurationError(ValueError):
    pass


def cipher():
    key = os.environ.get("MDM_ENCRYPTION_KEY", "")
    if not key:
        raise SecurityConfigurationError("Sensitive fields require MDM_ENCRYPTION_KEY to be configured.")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise SecurityConfigurationError("Install the cryptography dependency to use sensitive fields.") from exc
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise SecurityConfigurationError("MDM_ENCRYPTION_KEY is invalid.") from exc


def encrypt_sensitive(payload: dict[str, str]) -> str:
    return cipher().encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")


def decrypt_sensitive(ciphertext: str) -> dict[str, str]:
    configured_cipher = cipher()
    from cryptography.fernet import InvalidToken

    try:
        return json.loads(configured_cipher.decrypt(ciphertext.encode("ascii")))
    except InvalidToken as exc:
        raise SecurityConfigurationError("Sensitive data cannot be decrypted with the configured key.") from exc
