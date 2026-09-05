import re
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from src.models.db_models import Gym, User, Member, MemberMembership, Payment, AuditLog, DirectMessage, Notification, PlatformRevenueHistory
from src.constants.business_rules import ROLE_GYM_OWNER, ROLE_SUPER_ADMIN, STATUS_ACTIVE
from src.utils.helpers import hash_password, generate_pending_fee_message
from src.services.audit_service import AuditService
from src.services.revenue_service import RevenueService

CNIC_REGEX = re.compile(r"^\d{5}-\d{7}-\d{1}$")
PHONE_REGEX = re.compile(r"^\d{11}$")
EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")

class GymService:
    @staticmethod
    def validate_gym_input(phone: str, cnic: str, email: str):
        cleaned_phone = re.sub(r"[^\d]", "", phone or "")
        if len(cleaned_phone) != 11:
            raise HTTPException(status_code=400, detail="Phone number must be exactly 11 digits (e.g. 03001234567).")

        cleaned_cnic = (cnic or "").strip()
        if not CNIC_REGEX.match(cleaned_cnic):
            raise HTTPException(status_code=400, detail="CNIC must follow format XXXXX-XXXXXXX-X (e.g. 42101-1234567-1).")

        cleaned_email = (email or "").strip().lower()
        if not EMAIL_REGEX.match(cleaned_email):
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")

        return cleaned_phone, cleaned_cnic, cleaned_email

    @staticmethod
    def create_gym(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        owner_name: str,
        gym_name: str,
        email: str,
        phone: str,
        cnic: str,
        subscription_plan: str,
        subscription_expiry: str,
        address: Optional[str] = None,
        custom_password: Optional[str] = None,
        amount_paid: float = 0.0,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        phone, cnic, email = GymService.validate_gym_input(phone, cnic, email)

        # Check existing Gym or User email
        existing_gym = db.query(Gym).filter(Gym.email == email).first()
        if existing_gym:
            raise HTTPException(status_code=400, detail=f"A Gym with email '{email}' already exists.")

        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            raise HTTPException(status_code=400, detail=f"A user account with email '{email}' already exists.")

        # Create Gym record
        new_gym = Gym(
            gym_name=gym_name.strip(),
            owner_name=owner_name.strip(),
            email=email,
            phone=phone,
            cnic=cnic,
            address=(address or "").strip(),
            subscription_plan=subscription_plan.strip() or "Monthly",
            subscription_expiry=subscription_expiry.strip(),
            status="Active"
        )
        db.add(new_gym)
        db.flush()

        # Set or generate password
        raw_password = custom_password.strip() if (custom_password and custom_password.strip()) else "Owner@12345"
        hashed_pwd = hash_password(raw_password)

        # Automatically Create Gym Owner User Account
        owner_user = User(
            name=owner_name.strip(),
            email=email,
            password=hashed_pwd,
            role=ROLE_GYM_OWNER,
            gym_name=gym_name.strip(),
            gym_id=new_gym.id,
            phone=phone,
            cnic=cnic,
            is_verified=True
        )
        db.add(owner_user)

        # Record initial Platform Revenue if amount_paid > 0
        if amount_paid and float(amount_paid) > 0:
            today = date.today()
            ym = f"{today.year:04d}-{today.month:02d}"
            rev_record = db.query(PlatformRevenueHistory).filter(
                PlatformRevenueHistory.year == today.year,
                PlatformRevenueHistory.month == today.month
            ).first()

            if rev_record:
                rev_record.total_revenue = float(rev_record.total_revenue or 0.0) + float(amount_paid)
                rev_record.payment_count = (rev_record.payment_count or 0) + 1
            else:
                new_rev = PlatformRevenueHistory(
                    year=today.year,
                    month=today.month,
                    year_month_str=ym,
                    total_revenue=float(amount_paid),
                    payment_count=1
                )
                db.add(new_rev)
        
        # Log Audit
        AuditService.log_action(
            db, 
            admin_user_id, 
            admin_user_name, 
            "GYM_CREATED", 
            f"Created new Gym '{gym_name}' for owner '{owner_name}' ({email}) with Plan '{subscription_plan}' and Paid Amount PKR {float(amount_paid):,.2f}.", 
            ip_address,
            commit=False
        )

        db.commit()
        db.refresh(new_gym)
        db.refresh(owner_user)

        receipt_no = f"STHX-SAAS-{new_gym.id:04d}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        return {
            "message": f"Gym '{gym_name}' and Gym Owner account created successfully!",
            "gym": {
                "id": new_gym.id,
                "gym_name": new_gym.gym_name,
                "owner_name": new_gym.owner_name,
                "email": new_gym.email,
                "phone": new_gym.phone,
                "cnic": new_gym.cnic,
                "address": new_gym.address,
                "subscription_plan": new_gym.subscription_plan,
                "subscription_expiry": new_gym.subscription_expiry,
                "status": new_gym.status,
                "created_at": new_gym.created_at.isoformat()
            },
            "owner_credentials": {
                "email": email,
                "temporary_password": raw_password
            },
            "receipt": {
                "receipt_number": receipt_no,
                "payment_date": date.today().isoformat(),
                "gym_name": new_gym.gym_name,
                "owner_name": new_gym.owner_name,
                "email": new_gym.email,
                "phone": new_gym.phone,
                "cnic": new_gym.cnic,
                "subscription_plan": new_gym.subscription_plan,
                "amount_paid": float(amount_paid),
                "payment_method": "Onboarding Payment",
                "notes": "Initial Gym Client Onboarding Subscription Fee",
                "new_expiry": new_gym.subscription_expiry
            }
        }

    @staticmethod
    def list_gyms(
        db: Session,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        plan_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = db.query(Gym)

        if search and search.strip():
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Gym.gym_name.ilike(pattern),
                    Gym.owner_name.ilike(pattern),
                    Gym.email.ilike(pattern),
                    Gym.phone.ilike(pattern),
                    Gym.cnic.ilike(pattern)
                )
            )

        if plan_filter and plan_filter.strip() and plan_filter.lower() != "all":
            query = query.filter(Gym.subscription_plan == plan_filter.strip())

        gyms = query.order_by(Gym.id.desc()).all()
        today = date.today().isoformat()

        results = []
        for g in gyms:
            owner = db.query(User).filter(User.gym_id == g.id).first()

            # Compute 3-status taxonomy for Super Admin: Active, Pending Fees, Suspended
            if g.status == "Suspended":
                computed_status = "Suspended"
            elif g.subscription_expiry and g.subscription_expiry < today:
                computed_status = "Pending Fees"
            else:
                computed_status = "Active"

            if status_filter and status_filter.strip() and status_filter.lower() != "all":
                if computed_status.lower() != status_filter.strip().lower():
                    continue

            results.append({
                "id": g.id,
                "gym_name": g.gym_name,
                "owner_name": g.owner_name,
                "email": g.email,
                "phone": g.phone,
                "cnic": g.cnic,
                "address": g.address,
                "subscription_plan": g.subscription_plan,
                "subscription_expiry": g.subscription_expiry,
                "status": computed_status,
                "raw_status": g.status,
                "owner_id": owner.id if owner else None,
                "created_at": g.created_at.isoformat() if g.created_at else None
            })

        return results

    @staticmethod
    def get_gym_details(db: Session, gym_id: int) -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        today = date.today().isoformat()
        if gym.status == "Suspended":
            computed_status = "Suspended"
        elif gym.subscription_expiry and gym.subscription_expiry < today:
            computed_status = "Pending Fees"
        else:
            computed_status = "Active"

        return {
            "id": gym.id,
            "gym_name": gym.gym_name,
            "owner_name": gym.owner_name,
            "email": gym.email,
            "phone": gym.phone,
            "cnic": gym.cnic,
            "address": gym.address,
            "subscription_plan": gym.subscription_plan,
            "subscription_expiry": gym.subscription_expiry,
            "status": computed_status,
            "created_at": gym.created_at.isoformat() if gym.created_at else None
        }

    @staticmethod
    def update_gym(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        gym_id: int,
        owner_name: str,
        gym_name: str,
        phone: str,
        cnic: str,
        subscription_plan: str,
        subscription_expiry: str,
        address: Optional[str] = None,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        phone, cnic, email = GymService.validate_gym_input(phone, cnic, gym.email)

        gym.gym_name = gym_name.strip()
        gym.owner_name = owner_name.strip()
        gym.phone = phone
        gym.cnic = cnic
        gym.subscription_plan = subscription_plan.strip()
        gym.subscription_expiry = subscription_expiry.strip()
        gym.address = (address or "").strip()
        gym.updated_at = datetime.utcnow()

        # Sync linked owner user profile
        owner = db.query(User).filter(User.gym_id == gym.id).first()
        if owner:
            owner.name = owner_name.strip()
            owner.gym_name = gym_name.strip()
            owner.phone = phone
            owner.cnic = cnic

        AuditService.log_action(
            db, 
            admin_user_id, 
            admin_user_name, 
            "GYM_UPDATED", 
            f"Updated Gym details for '{gym.gym_name}' (ID: {gym.id}).", 
            ip_address,
            commit=False
        )

        db.commit()
        return {"message": f"Gym '{gym.gym_name}' updated successfully!"}

    @staticmethod
    def suspend_gym(db: Session, admin_user_id: int, admin_user_name: str, gym_id: int, ip_address: str = "127.0.0.1") -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        gym.status = "Suspended"
        gym.updated_at = datetime.utcnow()

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "GYM_SUSPENDED", f"Suspended Gym '{gym.gym_name}' (ID: {gym.id}).", ip_address, commit=False
        )

        db.commit()
        return {"message": f"Gym '{gym.gym_name}' suspended successfully."}

    @staticmethod
    def activate_gym(db: Session, admin_user_id: int, admin_user_name: str, gym_id: int, ip_address: str = "127.0.0.1") -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        gym.status = "Active"
        gym.updated_at = datetime.utcnow()

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "GYM_ACTIVATED", f"Activated Gym '{gym.gym_name}' (ID: {gym.id}).", ip_address, commit=False
        )

        db.commit()
        return {"message": f"Gym '{gym.gym_name}' activated successfully."}

    @staticmethod
    def pay_subscription_and_change_plan(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        gym_id: int,
        subscription_plan: str,
        amount_paid: float,
        payment_method: str = "Cash",
        notes: Optional[str] = None,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        today = date.today()

        # Compute status to check if subscription is currently active
        computed_status = "Active"
        if gym.status == "Suspended":
            computed_status = "Suspended"
        elif gym.subscription_expiry and gym.subscription_expiry < today.isoformat():
            computed_status = "Pending Fees"

        # Update Subscription Plan
        gym.subscription_plan = subscription_plan.strip()
        try:
            curr_exp_date = datetime.strptime(gym.subscription_expiry, "%Y-%m-%d").date()
            base_date = curr_exp_date if curr_exp_date > today else today
        except Exception:
            base_date = today

        plan_name = subscription_plan.strip().lower()
        if "6 month" in plan_name or "half year" in plan_name:
            new_expiry_date = base_date + timedelta(days=180)
        elif "1 year" in plan_name or "year" in plan_name or "annual" in plan_name:
            new_expiry_date = base_date + timedelta(days=365)
        else:
            new_expiry_date = base_date + timedelta(days=30)

        gym.subscription_expiry = new_expiry_date.isoformat()
        gym.status = "Active"
        gym.updated_at = datetime.utcnow()

        # Record Platform Revenue
        ym = f"{today.year:04d}-{today.month:02d}"
        from src.models.db_models import PlatformRevenueHistory
        rev_record = db.query(PlatformRevenueHistory).filter(
            PlatformRevenueHistory.year == today.year,
            PlatformRevenueHistory.month == today.month
        ).first()

        if rev_record:
            rev_record.total_revenue = float(rev_record.total_revenue or 0.0) + float(amount_paid)
            rev_record.payment_count = (rev_record.payment_count or 0) + 1
        else:
            new_rev = PlatformRevenueHistory(
                year=today.year,
                month=today.month,
                year_month_str=ym,
                total_revenue=float(amount_paid),
                payment_count=1
            )
            db.add(new_rev)

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "GYM_SUBSCRIPTION_PAID",
            f"Recorded platform subscription payment of PKR {amount_paid:,.2f} ({payment_method}) for Gym '{gym.gym_name}'. Plan: {subscription_plan}, New Expiry: {gym.subscription_expiry}.",
            ip_address, commit=False
        )

        db.commit()
        db.refresh(gym)

        receipt_no = f"STHX-SAAS-{gym.id:04d}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        return {
            "message": f"Payment recorded successfully! '{gym.gym_name}' subscription plan updated to '{gym.subscription_plan}' and status is now Active until {gym.subscription_expiry}.",
            "gym": {
                "id": gym.id,
                "gym_name": gym.gym_name,
                "owner_name": gym.owner_name,
                "email": gym.email,
                "phone": gym.phone,
                "subscription_plan": gym.subscription_plan,
                "subscription_expiry": gym.subscription_expiry,
                "status": "Active"
            },
            "receipt": {
                "receipt_number": receipt_no,
                "payment_date": today.isoformat(),
                "gym_name": gym.gym_name,
                "owner_name": gym.owner_name,
                "email": gym.email,
                "phone": gym.phone,
                "cnic": gym.cnic,
                "subscription_plan": gym.subscription_plan,
                "amount_paid": float(amount_paid),
                "payment_method": payment_method,
                "notes": notes or "N/A",
                "new_expiry": gym.subscription_expiry
            }
        }

    @staticmethod
    def reset_owner_password(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        gym_id: int,
        new_password: Optional[str] = None,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        owner = db.query(User).filter(User.gym_id == gym.id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Gym Owner account not found for this gym.")

        raw_pwd = new_password.strip() if (new_password and new_password.strip()) else "Reset@12345"
        owner.password = hash_password(raw_pwd)
        owner.failed_login_attempts = 0
        owner.lockout_until = None

        # Revoke all active sessions and refresh tokens for the Gym Owner
        from src.services.auth_service import AuthService
        AuthService.logout_all_devices(db, owner.id)

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "PASSWORD_RESET_ADMIN", f"Super Admin reset password for Gym Owner '{owner.email}' (Gym: {gym.gym_name}). Revoked all active tokens.", ip_address, commit=False
        )

        db.commit()
        return {
            "message": f"Password reset successfully for '{owner.email}'! All existing active sessions have been revoked.",
            "new_password": raw_pwd
        }

    @staticmethod
    def get_super_admin_stats(db: Session, months: int = 12) -> Dict[str, Any]:
        all_gyms = db.query(Gym).all()
        today = date.today().isoformat()

        active_gyms = 0
        pending_fees_gyms = 0
        suspended_gyms = 0

        for g in all_gyms:
            if g.status == "Suspended":
                suspended_gyms += 1
            elif g.subscription_expiry and g.subscription_expiry < today:
                pending_fees_gyms += 1
            else:
                active_gyms += 1

        total_gyms = len(all_gyms)
        total_owners = db.query(User).filter(User.role == ROLE_GYM_OWNER).count()

        start_of_month = date.today().replace(day=1)
        new_gyms_this_month = db.query(Gym).filter(Gym.created_at >= start_of_month).count()

        # Monthly New Gym Registrations Chart Data (Last 6 Months)
        months_chart = []
        for i in range(5, -1, -1):
            dt = date.today() - timedelta(days=i*30)
            m_start = dt.replace(day=1)
            next_m = (m_start + timedelta(days=32)).replace(day=1)
            count = db.query(Gym).filter(Gym.created_at >= m_start, Gym.created_at < next_m).count()
            months_chart.append({
                "month": m_start.strftime("%b %Y"),
                "count": count
            })

        # Subscription Distribution
        from sqlalchemy import func as sql_func
        plan_rows = db.query(Gym.subscription_plan, sql_func.count(Gym.id)).group_by(Gym.subscription_plan).all()
        plan_counts = {row[0]: row[1] for row in plan_rows if row[0]}

        # Platform Revenue
        revenue_summary = RevenueService.get_revenue_history_summary(db, user_id=None, months_limit=months)

        # Revenue Summary Panel
        today_date = date.today()
        today_str = today_date.isoformat()
        start_of_week_str = (today_date - timedelta(days=today_date.weekday())).isoformat()
        start_of_month_str = today_date.replace(day=1).isoformat()
        start_of_year_str = today_date.replace(month=1, day=1).isoformat()

        from sqlalchemy import func
        today_rev = db.query(func.sum(Payment.amount)).filter(Payment.status == "Paid", Payment.payment_date == today_str).scalar() or 0.0
        week_rev = db.query(func.sum(Payment.amount)).filter(Payment.status == "Paid", Payment.payment_date >= start_of_week_str).scalar() or 0.0
        month_rev = db.query(func.sum(Payment.amount)).filter(Payment.status == "Paid", Payment.payment_date >= start_of_month_str).scalar() or 0.0
        year_rev = db.query(func.sum(Payment.amount)).filter(Payment.status == "Paid", Payment.payment_date >= start_of_year_str).scalar() or 0.0
        all_time_p_rev = db.query(func.sum(Payment.amount)).filter(Payment.status == "Paid").scalar() or 0.0

        if float(all_time_p_rev) == 0.0:
            all_time_p_rev = db.query(func.sum(PlatformRevenueHistory.total_revenue)).scalar() or 0.0
            month_rev = db.query(PlatformRevenueHistory.total_revenue).filter(
                PlatformRevenueHistory.year == today_date.year,
                PlatformRevenueHistory.month == today_date.month
            ).scalar() or 0.0
            year_rev = db.query(func.sum(PlatformRevenueHistory.total_revenue)).filter(
                PlatformRevenueHistory.year == today_date.year
            ).scalar() or 0.0
            today_rev = round(float(month_rev) / 30.0, 2)
            week_rev = round(float(month_rev) / 4.0, 2)

        recent_gyms = db.query(Gym).order_by(Gym.id.desc()).limit(5).all()
        recent_gyms_list = []
        for g in recent_gyms:
            if g.status == "Suspended":
                c_stat = "Suspended"
            elif g.subscription_expiry and g.subscription_expiry < today:
                c_stat = "Pending Fees"
            else:
                c_stat = "Active"
            recent_gyms_list.append({
                "id": g.id,
                "gym_name": g.gym_name,
                "owner_name": g.owner_name,
                "email": g.email,
                "phone": g.phone,
                "subscription_plan": g.subscription_plan,
                "subscription_expiry": g.subscription_expiry,
                "status": c_stat,
                "created_at": g.created_at.isoformat() if g.created_at else None
            })

        expiring_gyms = db.query(Gym).filter(
            Gym.status != "Suspended",
            Gym.subscription_expiry < today
        ).order_by(Gym.subscription_expiry.asc()).all()

        import urllib.parse
        pending_fee_gyms_list = []
        for g in expiring_gyms:
            phone_digits = re.sub(r"[^\d]", "", g.phone or "")
            if phone_digits.startswith("0"):
                phone_digits = "92" + phone_digits[1:]
            msg_text = f"Dear {g.owner_name}, your STHX Technologies SaaS portal subscription for '{g.gym_name}' expired on {g.subscription_expiry}. Please clear your pending subscription fee to avoid service suspension."
            wa_url = f"https://api.whatsapp.com/send?phone={phone_digits}&text={urllib.parse.quote(msg_text)}"

            pending_fee_gyms_list.append({
                "id": g.id,
                "gym_name": g.gym_name,
                "owner_name": g.owner_name,
                "email": g.email,
                "phone": g.phone,
                "subscription_plan": g.subscription_plan,
                "subscription_expiry": g.subscription_expiry,
                "status": "Pending Fees",
                "whatsapp_url": wa_url
            })

        return {
            "total_gyms": total_gyms,
            "active_gyms": active_gyms,
            "pending_fees_gyms": pending_fees_gyms,
            "suspended_gyms": suspended_gyms,
            "total_owners": total_owners,
            "new_gyms_this_month": new_gyms_this_month,
            "monthly_registrations": months_chart,
            "active_vs_suspended": {
                "Active": active_gyms,
                "Pending Fees": pending_fees_gyms,
                "Suspended": suspended_gyms
            },
            "plan_distribution": plan_counts,
            "revenue_history": revenue_summary["rolling_history"],
            "total_platform_revenue": round(float(all_time_p_rev), 2),
            "current_month_revenue": round(float(month_rev), 2),
            "revenue_summary": {
                "today": round(float(today_rev), 2),
                "week": round(float(week_rev), 2),
                "month": round(float(month_rev), 2),
                "year": round(float(year_rev), 2),
                "all_time": round(float(all_time_p_rev), 2)
            },
            "recent_gyms": recent_gyms_list,
            "expiring_gyms": pending_fee_gyms_list,
            "pending_fee_gyms": pending_fee_gyms_list
        }

    @staticmethod
    def send_bulk_pending_fee_reminders(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        today = date.today().isoformat()
        pending_gyms = db.query(Gym).filter(
            Gym.status != "Suspended",
            Gym.subscription_expiry < today
        ).all()

        sent_count = 0
        for gym in pending_gyms:
            owner = db.query(User).filter(User.gym_id == gym.id).first()
            if not owner:
                continue

            fee_reminder_msg = generate_pending_fee_message("SUPER_ADMIN", gym.gym_name)
            dm = DirectMessage(
                sender_id=admin_user_id,
                recipient_id=owner.id,
                gym_id=gym.id,
                title="🚨 Urgent: Platform Subscription Overdue Fee Reminder",
                message=fee_reminder_msg,
                is_read=False
            )
            db.add(dm)

            notif = Notification(
                user_id=owner.id,
                title="🚨 URGENT: Pending Platform Subscription Fee Overdue",
                message=fee_reminder_msg,
                type="Fee Warning"
            )
            db.add(notif)
            sent_count += 1

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "BULK_FEE_REMINDER_SENT",
            f"Sent bulk fee reminder portal messages to {sent_count} pending gym owners.", ip_address, commit=False
        )

        db.commit()
        return {
            "message": f"Successfully sent fee reminder messages to {sent_count} pending gym owners inside the portal!",
            "sent_count": sent_count
        }



    @staticmethod
    def transfer_ownership(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        gym_id: int,
        new_email: str,
        new_owner_name: Optional[str] = None,
        phone: Optional[str] = None,
        cnic: Optional[str] = None,
        new_password: Optional[str] = None,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        target_email = new_email.strip().lower()
        if not EMAIL_REGEX.match(target_email):
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")

        existing_owner = db.query(User).filter(User.gym_id == gym.id).first()
        
        other_user = db.query(User).filter(User.email == target_email).first()
        if other_user and existing_owner and other_user.id != existing_owner.id:
            raise HTTPException(status_code=400, detail=f"User with email '{target_email}' already exists under another account.")

        cleaned_phone = phone.strip() if phone else gym.phone
        cleaned_cnic = cnic.strip() if cnic else gym.cnic
        owner_name = new_owner_name.strip() if new_owner_name else (existing_owner.name if existing_owner else gym.owner_name)

        gym.owner_name = owner_name
        gym.email = target_email
        gym.phone = cleaned_phone
        gym.cnic = cleaned_cnic
        gym.updated_at = datetime.utcnow()

        if existing_owner:
            existing_owner.email = target_email
            existing_owner.username = target_email
            existing_owner.name = owner_name
            existing_owner.phone = cleaned_phone
            existing_owner.cnic = cleaned_cnic
            if new_password and new_password.strip():
                existing_owner.password = hash_password(new_password.strip())
        else:
            raw_password = new_password.strip() if (new_password and new_password.strip()) else "Owner@12345"
            new_owner = User(
                name=owner_name,
                email=target_email,
                username=target_email,
                password=hash_password(raw_password),
                role=ROLE_GYM_OWNER,
                gym_name=gym.gym_name,
                gym_id=gym.id,
                phone=cleaned_phone,
                cnic=cleaned_cnic,
                is_verified=True
            )
            db.add(new_owner)

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "GYM_OWNERSHIP_TRANSFERRED",
            f"Transferred Gym '{gym.gym_name}' ownership to '{owner_name}' ({target_email}).", ip_address, commit=False
        )

        db.commit()
        return {"message": f"Gym ownership transferred to {target_email} successfully!"}

    @staticmethod
    def send_direct_message(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        gym_id: int,
        title: str,
        message: str,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        owner = db.query(User).filter(User.gym_id == gym.id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Gym owner user account not found.")

        final_msg = message.strip() if (message and message.strip()) else generate_pending_fee_message("SUPER_ADMIN", gym.gym_name)

        dm = DirectMessage(
            sender_id=admin_user_id,
            recipient_id=owner.id,
            gym_id=gym.id,
            title=title.strip() if title else "Pending Fee Reminder",
            message=final_msg,
            is_read=False
        )
        db.add(dm)

        notif = Notification(
            user_id=owner.id,
            title=f"📩 Message from Super Admin: {title.strip()}",
            message=message.strip(),
            type="Direct Message"
        )
        db.add(notif)

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "DIRECT_MESSAGE_SENT",
            f"Sent direct message to Gym Owner '{owner.email}' (Gym: {gym.gym_name}).", ip_address, commit=False
        )

        db.commit()
        db.refresh(dm)

        phone_digits = re.sub(r"[^\d]", "", gym.phone or "")
        if phone_digits.startswith("0"):
            phone_digits = "92" + phone_digits[1:]
        whatsapp_url = f"https://api.whatsapp.com/send?phone={phone_digits}&text={message.strip()}"

        return {
            "message": f"Direct in-app message sent to {owner.name} ({owner.email})!",
            "direct_message": {
                "id": dm.id,
                "title": dm.title,
                "message": dm.message,
                "created_at": dm.created_at.isoformat()
            },
            "whatsapp_url": whatsapp_url
        }

    @staticmethod
    def generate_whatsapp_link(
        db: Session,
        admin_user_id: int,
        admin_user_name: str,
        gym_id: int,
        message: str,
        ip_address: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        import urllib.parse
        gym = db.query(Gym).filter(Gym.id == gym_id).first()
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found.")

        owner = db.query(User).filter(User.gym_id == gym.id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Gym owner user account not found.")

        AuditService.log_action(
            db, admin_user_id, admin_user_name, "WHATSAPP_LINK_GENERATED",
            f"Generated WhatsApp direct message link for Gym Owner '{owner.email}' (Gym: {gym.gym_name}).", ip_address, commit=True
        )

        final_msg = message.strip() if (message and message.strip()) else generate_pending_fee_message("SUPER_ADMIN", gym.gym_name)

        phone_digits = re.sub(r"[^\d]", "", gym.phone or "")
        if phone_digits.startswith("0"):
            phone_digits = "92" + phone_digits[1:]
        whatsapp_url = f"https://api.whatsapp.com/send?phone={phone_digits}&text={urllib.parse.quote(final_msg)}"

        return {
            "whatsapp_url": whatsapp_url,
            "message": "WhatsApp link generated successfully!"
        }

    @staticmethod
    def get_gym_messages(db: Session, gym_id: int) -> List[Dict[str, Any]]:
        messages = db.query(DirectMessage).filter(DirectMessage.gym_id == gym_id).order_by(DirectMessage.id.desc()).all()
        return [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "recipient_id": m.recipient_id,
                "gym_id": m.gym_id,
                "title": m.title,
                "message": m.message,
                "is_read": m.is_read,
                "created_at": m.created_at.isoformat() if m.created_at else None
            } for m in messages
        ]
