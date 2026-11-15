import pytest
from solution.schema import SchemaRegistry

def test_effective_disposition():
    registry = SchemaRegistry()
    # motor_renewal codes: renewed (terminal, success), payment_link_sent (not terminal, not success),
    # callback_requested (not terminal, not success), not_interested (terminal, not success)
    
    # 1. Single disposition.set
    # simulated call logic
    wf = "motor_renewal"
    disp = registry.dispositions.get((wf, "renewed"))
    assert disp["is_terminal"] is True
    assert disp["is_success"] is True

    disp_cb = registry.dispositions.get((wf, "callback_requested"))
    assert disp_cb["is_terminal"] is False
    assert disp_cb["is_success"] is False

    # Unknown disposition not in dispositions.csv
    assert (wf, "UNKNOWN_CODE") not in registry.dispositions

def test_bucket_priority():
    # Helper to calculate bucket as specified:
    # 1. yet_to_reach_out: no calls
    # 2. completed: any call's effective disposition is terminal (is_terminal=true)
    # 3. in_progress_telephony_failed: every call has a call.telephony_failed event
    # 4. in_progress_dialed: everything else
    def compute_bucket(calls):
        if not calls:
            return "yet_to_reach_out"
        if any(c.get("is_terminal") for c in calls):
            return "completed"
        if all(c.get("has_telephony_failed") for c in calls):
            return "in_progress_telephony_failed"
        return "in_progress_dialed"

    # Case 1: 0 calls
    assert compute_bucket([]) == "yet_to_reach_out"

    # Case 2: 1 call with terminal disposition
    assert compute_bucket([{"is_terminal": True, "has_telephony_failed": False}]) == "completed"

    # Case 3: 2 calls: Call 1 telephony failed, Call 2 terminal disposition
    # Precedence: completed > in_progress_telephony_failed
    assert compute_bucket([
        {"is_terminal": False, "has_telephony_failed": True},
        {"is_terminal": True, "has_telephony_failed": False},
    ]) == "completed"

    # Case 4: 2 calls: Both telephony failed, none terminal
    assert compute_bucket([
        {"is_terminal": False, "has_telephony_failed": True},
        {"is_terminal": False, "has_telephony_failed": True},
    ]) == "in_progress_telephony_failed"

    # Case 5: 2 calls: 1 telephony failed, 1 no_answer (not telephony failed, not terminal)
    assert compute_bucket([
        {"is_terminal": False, "has_telephony_failed": True},
        {"is_terminal": False, "has_telephony_failed": False},
    ]) == "in_progress_dialed"
