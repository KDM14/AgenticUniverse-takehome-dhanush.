"""
Cryptographic and PII protection utilities.
Provides:
- AES-GCM / Fernet encryption for PII fields at rest.
- SHA-256 deterministic hashing for phone number linking.
- Phone masking (+91******XXXX) for reporting.
"""
import hashlib
import os
from cryptography.fernet import Fernet
import base64

# Default or environment-configured key for deterministic evaluation
# In production, this key is provided via secure secrets management
DEFAULT_SECRET_KEY = os.environ.get("PII_SECRET_KEY", "AU-TakeHome-2026-Data-Secret-Key-32b!!")

def get_fernet_key(secret: str = DEFAULT_SECRET_KEY) -> bytes:
    """Derive a URL-safe base64-encoded 32-byte key for Fernet."""
    derived = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(derived)

_FERNET = Fernet(get_fernet_key())

def hash_phone(canonical_phone: str) -> str:
    """Deterministic cryptographic hash (SHA-256) of canonical phone for ledger linking."""
    if not canonical_phone:
        return ""
    return hashlib.sha256(canonical_phone.encode("utf-8")).hexdigest()

def mask_phone(canonical_phone: str) -> str:
    """
    Format phone for public/masked reports: +91****** followed by last 4 digits.
    Example: +917718750036 -> +91******0036
    """
    if not canonical_phone:
        return ""
    digits = canonical_phone.replace("+", "")
    if digits.startswith("91"):
        digits = digits[2:]
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"+91******{last4}"

def encrypt_pii(plaintext: str) -> str:
    """Encrypt PII text at rest."""
    if plaintext is None:
        return None
    return _FERNET.encrypt(plaintext.encode("utf-8")).decode("utf-8")

def decrypt_pii(ciphertext: str) -> str:
    """Decrypt PII text from storage."""
    if ciphertext is None:
        return None
    return _FERNET.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
