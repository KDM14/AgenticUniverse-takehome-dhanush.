"""
Schema definitions, header mapping, value normalization, and validation rules.
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))

def normalize_column_name(name: str) -> str:
    """
    Normalise a header: lower-case it, trim it, and replace every run of '_' or
    whitespace with a single space.
    """
    if not name:
        return ""
    return re.sub(r"[\s_]+", " ", name.lower()).strip()

def normalize_phone(raw_val: Optional[str]) -> Optional[str]:
    """
    Indian mobile number normalization:
    - Strip every non-digit.
    - If 12 digits starting with 91, drop the 91.
    - If 11 digits starting with 0, drop the 0.
    - Result is valid only if exactly 10 digits and starts with 6, 7, 8, or 9.
    - Stored canonically as +91XXXXXXXXXX.
    Returns canonical phone if valid, otherwise None.
    """
    if not raw_val:
        return None
    digits = re.sub(r"\D", "", raw_val)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None

def parse_date(raw_val: Optional[str]) -> Optional[str]:
    """
    Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, and DD-Mon-YYYY (e.g. 01-Dec-2026).
    Dates are day-first. Month name is case-insensitive. Impossible dates are invalid.
    Returns ISO date YYYY-MM-DD if valid, else None.
    """
    if not raw_val:
        return None
    val = raw_val.strip()
    if not val:
        return None

    # Try supported formats
    # Note: %d-%b-%Y handles 01-Dec-2026, 01-dec-2026
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y")
    for fmt in formats:
        try:
            dt = datetime.strptime(val, fmt)
            # Verify year reasonable
            if dt.year < 1900 or dt.year > 2100:
                return None
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

def parse_decimal(raw_val: Optional[str]) -> Optional[float]:
    """
    Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    Commas are thousands separators. Must be >= 0. Round to 2 places.
    Returns float rounded to 2 places if valid, else None.
    """
    if not raw_val:
        return None
    val = raw_val.strip()
    if not val:
        return None
    
    m = re.match(r"^(?:₹|rs\.?|inr)?\s*(.*)$", val, re.IGNORECASE)
    num_str = m.group(1).replace(",", "").strip() if m else val
    try:
        dec = float(num_str)
        if dec >= 0:
            return round(dec, 2)
        return None
    except ValueError:
        return None

def parse_enum(raw_val: Optional[str], allowed_values: List[str]) -> Optional[str]:
    """
    Compared case-insensitively against values and stored lower-case.
    Returns lowercase matching value if valid, else None.
    """
    if not raw_val:
        return None
    val = raw_val.strip().lower()
    allowed_lower = [v.lower() for v in allowed_values]
    if val in allowed_lower:
        return val
    return None

def parse_timestamp(raw_val: str) -> datetime:
    """
    Parse event occurred_at timestamp into an aware IST datetime.
    Supports:
    - UTC (...Z)
    - Offset (...+-HH:MM)
    - Naive (YYYY-MM-DD HH:MM:SS) -> Naive means IST per spec.
    """
    s = raw_val.strip()
    if s.endswith("Z"):
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(IST)
    if "T" in s:
        # Handles ISO format with offset
        return datetime.fromisoformat(s).astimezone(IST)
    # Naive timestamp
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


class SchemaRegistry:
    def __init__(self, schemas_dir: Optional[str] = None):
        if schemas_dir is None:
            # Look in assignment-a-data/schemas relative to current directory or repo root
            candidates = [
                Path("assignment-a-data/schemas"),
                Path("../assignment-a-data/schemas"),
                Path(__file__).resolve().parent.parent / "assignment-a-data" / "schemas",
            ]
            for c in candidates:
                if c.exists() and (c / "dispositions.csv").exists():
                    schemas_dir = str(c)
                    break
            if not schemas_dir:
                schemas_dir = "assignment-a-data/schemas"

        self.schemas_dir = Path(schemas_dir)
        self.workflows: Dict[str, dict] = {}
        self.dispositions: Dict[Tuple[str, str], dict] = {}
        self.column_maps: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self):
        # Load dispositions.csv
        disp_file = self.schemas_dir / "dispositions.csv"
        if disp_file.exists():
            with open(disp_file, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    self.dispositions[(wf, code)] = {
                        "label": row.get("label", "").strip(),
                        "is_terminal": row.get("is_terminal", "").strip().lower() == "true",
                        "is_success": row.get("is_success", "").strip().lower() == "true",
                    }

        # Load workflow JSON schemas
        for json_path in self.schemas_dir.glob("*.json"):
            with open(json_path, encoding="utf-8") as f:
                schema = json.load(f)
                wf_id = schema["workflow_id"]
                self.workflows[wf_id] = schema
                
                # Build normalized header lookup map
                col_map: Dict[str, str] = {}
                for field in schema.get("fields", []):
                    fname = field["name"]
                    col_map[normalize_column_name(fname)] = fname
                    for alias in field.get("aliases", []):
                        col_map[normalize_column_name(alias)] = fname
                self.column_maps[wf_id] = col_map

    def get_reference_field(self, workflow_id: str) -> str:
        schema = self.workflows.get(workflow_id)
        if not schema:
            return "external_ref"
        for field in schema.get("fields", []):
            if field.get("reference", False):
                return field["name"]
        return "external_ref"

    def validate_lead_row(
        self, workflow_id: str, mapped_row: Dict[str, str]
    ) -> Tuple[str, Optional[str], int, Dict[str, Any]]:
        """
        Walk fields in schema order.
        Returns:
            status: 'VALID' or 'REJECT'
            reason: rejection reason if 'REJECT' else None
            warnings: count of field warnings (only counted for non-rejected rows)
            cleaned_fields: dictionary of normalized field values
        """
        schema = self.workflows[workflow_id]
        warnings = 0
        cleaned: Dict[str, Any] = {}

        for field in schema.get("fields", []):
            fname = field["name"]
            ftype = field["type"]
            is_req = field.get("required", False)
            raw_val = mapped_row.get(fname)
            val = raw_val.strip() if raw_val is not None else ""

            # Check missing
            if not val:
                if is_req:
                    # Missing required field
                    return "REJECT", f"missing_required:{fname}", 0, {}
                cleaned[fname] = None
                continue

            # Process present value by type
            if ftype == "string":
                cleaned[fname] = val
            elif ftype == "phone":
                canonical = normalize_phone(val)
                if canonical:
                    cleaned[fname] = canonical
                else:
                    return "REJECT", "invalid_phone", 0, {}
            elif ftype == "date":
                parsed = parse_date(val)
                if parsed:
                    cleaned[fname] = parsed
                else:
                    cleaned[fname] = None
                    warnings += 1
            elif ftype == "decimal":
                parsed_dec = parse_decimal(val)
                if parsed_dec is not None:
                    cleaned[fname] = parsed_dec
                else:
                    cleaned[fname] = None
                    warnings += 1
            elif ftype == "enum":
                parsed_enum = parse_enum(val, field.get("values", []))
                if parsed_enum is not None:
                    cleaned[fname] = parsed_enum
                else:
                    cleaned[fname] = None
                    warnings += 1
            else:
                cleaned[fname] = val

        return "VALID", None, warnings, cleaned
