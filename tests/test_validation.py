import pytest
from solution.schema import (
    SchemaRegistry,
    normalize_column_name,
    parse_decimal,
    parse_enum,
)

def test_header_normalization():
    assert normalize_column_name("Insured_Name") == "insured name"
    assert normalize_column_name("Mobile   No") == "mobile no"
    assert normalize_column_name("Policy__Number_") == "policy number"
    assert normalize_column_name("  Expiry   Date ") == "expiry date"

def test_parse_decimal():
    assert parse_decimal("15000") == 15000.0
    assert parse_decimal("15000.50") == 15000.50
    assert parse_decimal("₹27,554") == 27554.0
    assert parse_decimal("Rs. 31629") == 31629.0
    assert parse_decimal("INR 25301.4") == 25301.4
    assert parse_decimal("Rs 10,000.55") == 10000.55
    # Invalid
    assert parse_decimal("-500") is None
    assert parse_decimal("twelve thousand") is None
    assert parse_decimal("N/A") is None

def test_parse_enum():
    values = ["en", "hi", "ta", "kn", "te", "mr"]
    assert parse_enum("en", values) == "en"
    assert parse_enum("HI", values) == "hi"
    assert parse_enum(" Ta ", values) == "ta"
    assert parse_enum("english", values) is None
    assert parse_enum("Hindi", values) is None

def test_schema_rejection_order():
    registry = SchemaRegistry()
    # Missing customer_name (first required field in motor_renewal)
    status, reason, warns, _ = registry.validate_lead_row("motor_renewal", {
        "customer_name": "",
        "phone": "9876543210",
        "policy_number": "MOT/2026/001",
    })
    assert status == "REJECT"
    assert reason == "missing_required:customer_name"

    # Missing phone
    status, reason, warns, _ = registry.validate_lead_row("motor_renewal", {
        "customer_name": "Aarav",
        "phone": "",
        "policy_number": "MOT/2026/001",
    })
    assert status == "REJECT"
    assert reason == "missing_required:phone"

    # Invalid phone
    status, reason, warns, _ = registry.validate_lead_row("motor_renewal", {
        "customer_name": "Aarav",
        "phone": "12345",
        "policy_number": "MOT/2026/001",
    })
    assert status == "REJECT"
    assert reason == "invalid_phone"

    # Missing reference (policy_number)
    status, reason, warns, _ = registry.validate_lead_row("motor_renewal", {
        "customer_name": "Aarav",
        "phone": "9876543210",
        "policy_number": "",
    })
    assert status == "REJECT"
    assert reason == "missing_required:policy_number"

def test_field_warnings_on_valid_rows():
    registry = SchemaRegistry()
    status, reason, warns, cleaned = registry.validate_lead_row("motor_renewal", {
        "customer_name": "Aarav",
        "phone": "9876543210",
        "policy_number": "MOT/2026/001",
        "policy_expiry": "31/02/2026",  # Invalid date -> warning
        "premium_amount": "invalid",     # Invalid decimal -> warning
        "preferred_language": "french",  # Invalid enum -> warning
    })
    assert status == "VALID"
    assert warns == 3
    assert cleaned["policy_expiry"] is None
    assert cleaned["premium_amount"] is None
    assert cleaned["preferred_language"] is None
