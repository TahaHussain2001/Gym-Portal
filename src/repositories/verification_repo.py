from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from src.models.db_models import PendingRegistration

class VerificationRepo:
    @staticmethod
    def get_by_email(db: Session, email: str) -> PendingRegistration:
        return db.query(PendingRegistration).filter(PendingRegistration.email == email).first()

    @staticmethod
    def create_or_update(db: Session, name: str, email: str, password_hash: str, role: str, code: str, expires_in_minutes: int = 15) -> PendingRegistration:
        expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
        existing = db.query(PendingRegistration).filter(PendingRegistration.email == email).first()
        if existing:
            existing.name = name
            existing.password_hash = password_hash
            existing.role = role
            existing.code = code
            existing.attempts = 0
            existing.resend_attempts = 0
            existing.expires_at = expires_at
            existing.created_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
        else:
            pending = PendingRegistration(
                name=name,
                email=email,
                password_hash=password_hash,
                role=role,
                code=code,
                attempts=0,
                resend_attempts=0,
                expires_at=expires_at
            )
            db.add(pending)
            db.commit()
            db.refresh(pending)
            return pending

    @staticmethod
    def increment_attempts(db: Session, pending: PendingRegistration):
        pending.attempts += 1
        db.commit()

    @staticmethod
    def increment_resend_attempts(db: Session, pending: PendingRegistration):
        pending.resend_attempts += 1
        db.commit()

    @staticmethod
    def update_code(db: Session, pending: PendingRegistration, new_code: str, expires_in_minutes: int = 15):
        """Update verification code when resending"""
        pending.code = new_code
        pending.attempts = 0
        pending.expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
        db.commit()
        db.refresh(pending)

    @staticmethod
    def delete(db: Session, pending: PendingRegistration):
        db.delete(pending)
        db.commit()
