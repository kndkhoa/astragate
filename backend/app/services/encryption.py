"""
Encryption service — AES-256 encryption/decryption using cryptography.fernet.
Used for encrypting sensitive fields like Provider API keys at rest (Task 38).
"""
import base64
import hashlib
from cryptography.fernet import Fernet

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def _get_fernet() -> Fernet:
    """Derive a URL-safe 32-byte Fernet key from settings.DB_ENCRYPTION_KEY."""
    key = settings.DB_ENCRYPTION_KEY.encode("utf-8")
    # Hash to get a consistent 32-byte key
    key_hash = hashlib.sha256(key).digest()
    fernet_key = base64.urlsafe_b64encode(key_hash)
    return Fernet(fernet_key)


def encrypt_val(val: str) -> str:
    """Encrypt a plaintext string using AES-256."""
    if not val:
        return ""
    try:
        f = _get_fernet()
        return f.encrypt(val.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.error("encryption_failed", error=str(exc))
        raise ValueError("Failed to encrypt value") from exc


def decrypt_val(cipher: str) -> str:
    """Decrypt an encrypted cipher string back to plaintext."""
    if not cipher:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.error("decryption_failed", error=str(exc))
        raise ValueError("Failed to decrypt value") from exc
