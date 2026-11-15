import pytest
from solution.schema import normalize_phone
from solution.crypto import mask_phone, hash_phone

def test_valid_indian_mobile():
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+919876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("(+91) 9876543210") == "+919876543210"
    assert normalize_phone("91-98765-43210") == "+919876543210"
    assert normalize_phone("09876543210") == "+919876543210"
    assert normalize_phone("6123456789") == "+916123456789"
    assert normalize_phone("7123456789") == "+917123456789"
    assert normalize_phone("8123456789") == "+918123456789"

def test_invalid_indian_mobile():
    # Bad prefixes
    assert normalize_phone("5123456789") is None
    assert normalize_phone("1123456789") is None
    assert normalize_phone("02225551234") is None  # Landline (starts with 2 after 0 stripped)
    # Bad length
    assert normalize_phone("987654321") is None    # 9 digits
    assert normalize_phone("987654321000") is None # 12 digits not starting with 91
    # International
    assert normalize_phone("+1 415 555 0134") is None
    # Text
    assert normalize_phone("N/A") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None

def test_phone_masking():
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("+917718750036") == "+91******0036"
    assert mask_phone("") == ""

def test_phone_hashing():
    h1 = hash_phone("+919876543210")
    h2 = hash_phone("+919876543210")
    h3 = hash_phone("+919876543211")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64
