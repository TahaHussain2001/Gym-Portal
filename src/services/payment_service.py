from datetime import datetime, date, timedelta
from src.constants.business_rules import REGISTRATION_FEE_AMOUNT, UNPAID_INACTIVE_DAYS

class PaymentService:
    @staticmethod
    def calculate_registration_fee(member_status: str, payment_count: int, next_due_date_str: str = None) -> float:
        """Business rule: Every new member pays PKR 1,000 registration fee.
        If a member fails to pay membership fees for 60 consecutive days (Inactive or 60+ days overdue),
        the system automatically adds PKR 1,000 registration fee.
        """
        if payment_count == 0:
            return REGISTRATION_FEE_AMOUNT
        if member_status == "Inactive":
            return REGISTRATION_FEE_AMOUNT
        if next_due_date_str:
            due_date = datetime.strptime(next_due_date_str, "%Y-%m-%d").date()
            if (date.today() - due_date).days >= UNPAID_INACTIVE_DAYS:
                return REGISTRATION_FEE_AMOUNT
        return 0.0

    @staticmethod
    def generate_receipt_number(last_payment_id: int, payment_date_str: str) -> str:
        date_obj = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
        date_prefix = date_obj.strftime("%Y%m%d")
        next_id = (last_payment_id + 1) if last_payment_id else 1
        return f"REC-{date_prefix}-{next_id:04d}"

    @staticmethod
    def get_receipt_details(db, payment_id: int, user_id: int) -> dict:
        from fastapi import HTTPException
        from src.models.db_models import Payment, Member, MemberMembership, User
        from src.repositories.settings_repo import SettingsRepo
        from src.utils.helpers import apply_for_update

        payment = db.query(Payment).join(Member).filter(
            Payment.id == payment_id,
            Member.user_id == user_id
        ).first()

        if not payment:
            raise HTTPException(status_code=404, detail="Receipt / Payment transaction not found.")

        member = payment.member
        ms = payment.membership

        # Fetch Gym Branding Settings
        settings = SettingsRepo.get_all_settings(db)
        gym_name = settings.get("gym_name", "STHX Gym & Fitness Club")
        gym_logo_url = settings.get("gym_logo_url", "/static/sthx_technologies_logo.png")
        gym_address = settings.get("gym_address", "Main Commercial Area")
        gym_phone = settings.get("gym_phone", "+92 300 0000000")
        gym_email = settings.get("gym_email", "support@sthxtechnologies.com")
        gym_footer_text = settings.get("gym_footer_text", "Thank you for training with us! Keep grinding.")

        user = db.query(User).filter(User.id == user_id).first()
        cashier_name = user.name if user else "Admin Staff"

        amount_val = float(payment.amount or 0.0)
        reg_fee_val = float(payment.registration_fee or 0.0)
        total_paid = amount_val + reg_fee_val

        return {
            "receipt_number": payment.receipt_number,
            "payment_id": payment.id,
            "payment_date": payment.payment_date,
            "payment_method": payment.payment_method,
            "payment_type": payment.payment_type or "Renewal",
            "gym_info": {
                "name": gym_name,
                "logo_url": gym_logo_url,
                "address": gym_address,
                "phone": gym_phone,
                "email": gym_email,
                "footer_text": gym_footer_text
            },
            "member_info": {
                "id": member.id,
                "name": member.full_name,
                "code": member.member_code,
                "phone": member.whatsapp_number,
                "email": member.email
            },
            "plan_info": {
                "name": ms.plan.plan_name if ms and ms.plan else "Standard Membership",
                "duration_days": ms.plan.duration_days if ms and ms.plan else 30
            },
            "billing": {
                "membership_fee": amount_val,
                "registration_fee": reg_fee_val,
                "total_amount": total_paid
            },
            "validity": {
                "start_date": ms.start_date if ms else payment.payment_date,
                "expiry_date": ms.expiry_date if ms else payment.payment_date
            },
            "cashier_name": cashier_name
        }
