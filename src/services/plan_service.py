import logging
from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.models.db_models import Member, MembershipPlan, MemberMembership, Payment
from src.services.payment_service import PaymentService
from src.services.audit_service import AuditService
from src.utils.helpers import apply_for_update
from src.constants.enums import MemberStatus, MembershipStatus, PaymentStatus

logger = logging.getLogger("sthxtechnologies-plan")

from src.constants.business_rules import REGISTRATION_FEE_AMOUNT

class PlanService:
    @staticmethod
    def change_member_plan(
        db: Session,
        member_id: int,
        new_plan_id: int,
        payment_method: str = "Cash",
        amount: Optional[float] = None,
        registration_fee: Optional[float] = 0.0,
        pay_now: bool = True,
        user_id: int = 1,
        user_name: str = "Admin",
        client_ip: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        # Lock Member row for write safety
        member = apply_for_update(db.query(Member).filter(
            Member.id == member_id,
            Member.user_id == user_id,
            Member.deleted_at.is_(None)
        ), db).first()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found.")

        if member.status == MemberStatus.ARCHIVED.value:
            raise HTTPException(status_code=400, detail="Cannot change plan for an Archived member. Please restore member first.")

        plan = db.query(MembershipPlan).filter(MembershipPlan.id == new_plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Selected membership plan not found.")

        today = date.today()
        expiry = today + timedelta(days=plan.duration_days or 30)

        # Registration fee is manually controlled by gym owner (suggested default provided, but customizable including 0)
        reg_fee = float(registration_fee) if (registration_fee is not None and float(registration_fee) >= 0) else 0.0

        plan_price = amount if amount is not None else float(plan.price)

        # Deactivate previous memberships
        existing_memberships = db.query(MemberMembership).filter(
            MemberMembership.member_id == member.id
        ).all()
        for em in existing_memberships:
            em.status = MembershipStatus.EXPIRED.value

        # Create new active membership assignment
        new_ms = MemberMembership(
            member_id=member.id,
            plan_id=plan.id,
            start_date=today.isoformat(),
            expiry_date=expiry.isoformat(),
            next_due_date=expiry.isoformat(),
            status=MembershipStatus.ACTIVE.value
        )
        db.add(new_ms)
        db.flush()  # assign new_ms.id

        # Generate Receipt Number
        last_payment = db.query(Payment).order_by(Payment.id.desc()).first()
        last_id = last_payment.id if last_payment else 0
        receipt_num = PaymentService.generate_receipt_number(last_id, today.isoformat())

        # Record payment transaction for plan change
        new_payment = Payment(
            member_id=member.id,
            membership_id=new_ms.id,
            amount=plan_price,
            registration_fee=reg_fee,
            payment_method=payment_method or "Cash",
            payment_date=today.isoformat(),
            receipt_number=receipt_num,
            payment_type="Plan Change",
            status=PaymentStatus.PAID.value
        )
        db.add(new_payment)
        
        from src.services.revenue_service import RevenueService
        RevenueService.record_payment_in_platform_history(db, new_payment.payment_date, plan_price + reg_fee, commit=False)

        # Update Member Status to Active
        member.status = MemberStatus.ACTIVE.value

        # Log Audit Action
        AuditService.log_action(
            db,
            user_id=user_id,
            user_name=user_name,
            action="PLAN_CHANGED",
            details=f"Changed plan for {member.full_name} ({member.member_code}) to '{plan.plan_name}' (Reg Fee: PKR {reg_fee:,.0f}). Receipt: {receipt_num}",
            ip_address=client_ip,
            commit=False
        )

        try:
            db.commit()
            db.refresh(member)
            db.refresh(new_payment)
        except Exception as e:
            db.rollback()
            logger.error(f"[PLAN CHANGE ERROR] Transaction failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to record plan change due to a database transaction error.")

        receipt_data = PaymentService.get_receipt_details(db, new_payment.id, user_id)

        return {
            "message": f"Membership plan changed to '{plan.plan_name}' and payment recorded successfully.",
            "member_id": member.id,
            "payment_id": new_payment.id,
            "receipt_number": new_payment.receipt_number,
            "plan_name": plan.plan_name,
            "expiry_date": new_ms.expiry_date,
            "receipt": receipt_data
        }
