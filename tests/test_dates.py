from datetime import timezone, timedelta
from solution.schema import parse_date, parse_timestamp

IST = timezone(timedelta(hours=5, minutes=30))

def test_parse_valid_dates():
    assert parse_date("2026-10-15") == "2026-10-15"
    assert parse_date("15/10/2026") == "2026-10-15"
    assert parse_date("15-10-2026") == "2026-10-15"
    assert parse_date("15-Oct-2026") == "2026-10-15"
    assert parse_date("15-oct-2026") == "2026-10-15"
    assert parse_date("01-Dec-2026") == "2026-12-01"

def test_parse_impossible_dates():
    assert parse_date("31/02/2026") is None
    assert parse_date("2026-13-01") is None
    assert parse_date("32/01/2026") is None
    assert parse_date("29/02/2025") is None  # Not a leap year
    assert parse_date("soon") is None
    assert parse_date("TBD") is None
    assert parse_date("") is None

def test_parse_event_timestamps():
    # UTC (Z)
    dt1 = parse_timestamp("2026-10-05T13:20:21Z")
    assert dt1.tzinfo == IST
    assert dt1.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-05 18:50:21"

    # Explicit offset (+05:30)
    dt2 = parse_timestamp("2026-10-05T18:50:21+05:30")
    assert dt2.tzinfo == IST
    assert dt2.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-05 18:50:21"

    # Naive timestamp (means IST per spec)
    dt3 = parse_timestamp("2026-10-05 18:50:21")
    assert dt3.tzinfo == IST
    assert dt3.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-05 18:50:21"
