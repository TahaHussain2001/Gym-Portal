import logging
from typing import Tuple, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from src.models.db_models import Member, MemberMembership
from src.services.audit_service import AuditService
from src.constants.business_rules import (
    STATUS_ACTIVE, STATUS_PENDING, STATUS_EXPIRED, STATUS_ARCHIVED
)

logger = logging.getLogger("sthxtechnologies-status")

VALID_MEMBER_TRANSITIONS = {
    STATUS_PENDING: [STATUS_ACTIVE, STATUS_EXPIRED, STATUS_ARCHIVED],
    STATUS_ACTIVE: [STATUS_PENDING, STATUS_EXPIRED, STATUS_ARCHIVED],
    STATUS_EXPIRED: [STATUS_ACTIVE, STATUS_PENDING, STATUS_ARCHIVED],
    STATUS_ARCHIVED: [STATUS_ACTIVE, STATUS_PENDING]
}

class StatusService:
    @staticmethod
    def validate_and_transition_member_status(
        db: Session, 
        member: Member, 
        new_status: str, 
        user_id: int, 
        user_name: str, 
        reason: str = "Status update",
        commit: bool = True
    ) -> Member:
        old_status = member.status or STATUS_PENDING

        if old_status == new_status:
            return member

        allowed_next = VALID_MEMBER_TRANSITIONS.get(old_status, [])
        if new_status not in allowed_next:
            raise HTTPException(
                status_code=400,
                detail=f"Illegal status transition for member '{member.full_name}': cannot transition from '{old_status}' to '{new_status}'."
            )

        member.status = new_status
        
        # Log every transition
        AuditService.log_action(
            db,
            user_id=user_id,
            user_name=user_name,
            action="MEMBER_STATUS_CHANGE",
            details=f"Changed member '{member.full_name}' ({member.member_code}) status from '{old_status}' to '{new_status}'. Reason: {reason}",
            commit=False
        )

        if commit:
            db.commit()
            db.refresh(member)
        return member

    @staticmethod
    def validate_check_in_eligibility(member: Member, active_membership: Optional[MemberMembership], allow_override: bool = False) -> Tuple[bool, str]:
        if member.deleted_at:
            return False, f"Check-in blocked: Member '{member.full_name}' has been deleted."

        if member.status == STATUS_ARCHIVED:
            return False, f"Check-in blocked: Member '{member.full_name}' is Archived. Please restore member first."

        if member.status == STATUS_EXPIRED and not allow_override:
            return False, f"Check-in blocked: Member '{member.full_name}' status is 'Expired'. Administrator approval required."

        if not active_membership or active_membership.status != STATUS_ACTIVE:
            if not allow_override:
                return False, f"Check-in blocked: Member '{member.full_name}' has no active membership plan."

        return True, "Check-in permitted."

    @staticmethod
    def validate_payment_eligibility(member: Member) -> Tuple[bool, str]:
        if member.deleted_at:
            return False, f"Payment blocked: Member '{member.full_name}' has been deleted."

        if member.status == STATUS_ARCHIVED:
            return False, f"Payment blocked: Member '{member.full_name}' is Archived. Restore member before recording payments."

        return True, "Payment permitted."
