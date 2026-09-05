import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple, Any
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from src.models.db_models import IdempotencyRecord

IDEMPOTENCY_KEY_EXPIRY_HOURS = 24

class IdempotencyService:
    @staticmethod
    def get_existing_response(db: Session, key: str, user_id: int, endpoint: str) -> Optional[Tuple[int, dict]]:
        if not key or not key.strip():
            return None

        record = db.query(IdempotencyRecord).filter(
            IdempotencyRecord.key == key.strip(),
            IdempotencyRecord.user_id == user_id
        ).first()

        if not record:
            return None

        # Check if expired
        if datetime.utcnow() > record.expires_at:
            db.delete(record)
            db.commit()
            return None

        try:
            cached_data = json.loads(record.response_body)
            return record.response_code, cached_data
        except Exception:
            return None

    @staticmethod
    def save_response(db: Session, key: str, user_id: int, endpoint: str, request_data: Any, response_code: int, response_data: dict) -> None:
        if not key or not key.strip():
            return

        req_hash = hashlib.sha256(json.dumps(request_data, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        expires_at = datetime.utcnow() + timedelta(hours=IDEMPOTENCY_KEY_EXPIRY_HOURS)

        try:
            existing = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.key == key.strip(),
                IdempotencyRecord.user_id == user_id
            ).first()

            if existing:
                existing.response_code = response_code
                existing.response_body = json.dumps(response_data)
                existing.expires_at = expires_at
            else:
                record = IdempotencyRecord(
                    key=key.strip(),
                    user_id=user_id,
                    endpoint=endpoint,
                    request_hash=req_hash,
                    response_code=response_code,
                    response_body=json.dumps(response_data),
                    expires_at=expires_at
                )
                db.add(record)
            db.commit()
        except Exception:
            db.rollback()

    @staticmethod
    def purge_expired_keys(db: Session) -> int:
        now = datetime.utcnow()
        expired = db.query(IdempotencyRecord).filter(IdempotencyRecord.expires_at < now).all()
        count = len(expired)
        for r in expired:
            db.delete(r)
        if count > 0:
            db.commit()
        return count
