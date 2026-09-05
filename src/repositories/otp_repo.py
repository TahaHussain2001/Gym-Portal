from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from src.models.db_models import OTPCode

class OTPRepo:
    @staticmethod
    def create_otp(
        db: Session, 
        email: str, 
        otp: str, 
        purpose: str, 
        user_id: Optional[int] = None, 
        expires_in_minutes: int = 10,
        commit: bool = True
    ) -> OTPCode:
        # Expire any previous unused OTP for this email and purpose
        existing_otps = db.query(OTPCode).filter(
            OTPCode.email == email.lower().strip(),
            OTPCode.purpose == purpose,
            OTPCode.is_used == False
        ).all()
        for old in existing_otps:
            old.is_used = True

        expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
        new_otp = OTPCode(
            user_id=user_id,
            email=email.lower().strip(),
            otp=otp,
            purpose=purpose,
            expires_at=expires_at,
            attempts=0,
            resend_count=0,
            is_used=False
        )
        db.add(new_otp)
        if commit:
            db.commit()
            db.refresh(new_otp)
        return new_otp

    @staticmethod
    def get_active_otp(db: Session, email: str, purpose: str) -> Optional[OTPCode]:
        return db.query(OTPCode).filter(
            OTPCode.email == email.lower().strip(),
            OTPCode.purpose == purpose,
            OTPCode.is_used == False
        ).order_by(OTPCode.id.desc()).first()

    @staticmethod
    def increment_attempts(db: Session, otp_record: OTPCode, commit: bool = True) -> None:
        otp_record.attempts += 1
        if commit:
            db.commit()
            db.refresh(otp_record)

    @staticmethod
    def increment_resend_count(db: Session, otp_record: OTPCode, commit: bool = True) -> None:
        otp_record.resend_count += 1
        if commit:
            db.commit()
            db.refresh(otp_record)

    @staticmethod
    def mark_as_used(db: Session, otp_record: OTPCode, commit: bool = True) -> None:
        otp_record.is_used = True
        if commit:
            db.commit()

    @staticmethod
    def get_recent_resends_count_hourly(db: Session, email: str, purpose: str) -> int:
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        return db.query(OTPCode).filter(
            OTPCode.email == email.lower().strip(),
            OTPCode.purpose == purpose,
            OTPCode.created_at >= one_hour_ago
        ).count()

    @staticmethod
    def purge_expired_otps(db: Session) -> int:
        now = datetime.utcnow()
        expired = db.query(OTPCode).filter(
            or_(OTPCode.expires_at < now, OTPCode.is_used == True)
        ).all()
        count = len(expired)
        for r in expired:
            db.delete(r)
        if count > 0:
            db.commit()
        return count
