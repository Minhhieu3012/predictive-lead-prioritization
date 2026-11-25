from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace-review"])

PERMITTED_OVERRIDE_CODES = {
    "CUSTOMER_URGENT_CALLBACK",
    "EXISTING_PROJECT_DEPENDENCY",
    "TECHNICAL_SPEC_CLARIFICATION",
    "OTHER_VERIFIED",
}

# Bảng lưu log tách biệt hoàn toàn khỏi bảng nhãn huấn luyện outcome_events (USR 06)
staff_override_logs: List[Dict[str, Any]] = []


class ReviewRequest(BaseModel):
    enquiry_id: str
    decision_type: str = "ACCEPT_PRIORITY"
    contact_permission_verified: bool
    contact_attempted: bool = False
    review_completed: bool = True
    next_step_note: Optional[str] = None


class OverrideRequest(BaseModel):
    enquiry_id: str
    override_reason_code: str
    justification: str
    contact_permission_verified: bool = True


@router.post("/review")
def submit_workspace_review(payload: ReviewRequest) -> Dict[str, Any]:
    if not payload.contact_permission_verified:
        raise HTTPException(
            status_code=400,
            detail="contact_permission_verified must be TRUE before logging outreach or review.",
        )
    if payload.decision_type != "ACCEPT_PRIORITY":
        raise HTTPException(
            status_code=400,
            detail="decision_type must be ACCEPT_PRIORITY for /workspace/review.",
        )

    entry = {
        "enquiry_id": payload.enquiry_id,
        "decision_type": payload.decision_type,
        "contact_permission_verified": True,
        "contact_attempted": payload.contact_attempted,
        "review_completed": payload.review_completed,
        "next_step_note": payload.next_step_note,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_table": "staff_override_logs",
    }
    staff_override_logs.append(entry)
    return {"status": "RECORDED", "data": entry}


@router.post("/override")
def submit_workspace_override(payload: OverrideRequest) -> Dict[str, Any]:
    if not payload.contact_permission_verified:
        raise HTTPException(
            status_code=400,
            detail="contact_permission_verified must be TRUE before overriding priority.",
        )
    reason_code = (payload.override_reason_code or "").strip()
    justification = (payload.justification or "").strip()

    if not reason_code or reason_code not in PERMITTED_OVERRIDE_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or empty override_reason_code. Permitted: {sorted(PERMITTED_OVERRIDE_CODES)}",
        )
    if not justification:
        raise HTTPException(
            status_code=400,
            detail="justification is mandatory and cannot be empty.",
        )

    entry = {
        "enquiry_id": payload.enquiry_id,
        "decision_type": "OVERRIDE_PRIORITY",
        "override_reason_code": reason_code,
        "justification": justification,
        "contact_permission_verified": True,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_table": "staff_override_logs",
    }
    staff_override_logs.append(entry)
    return {"status": "OVERRIDDEN", "data": entry}