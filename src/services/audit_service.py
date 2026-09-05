from sqlalchemy.orm import Session
from src.models.db_models import AuditLog

class AuditService:
    @staticmethod
    def log_action(db: Session, user_id: int, user_name: str, action: str, details: str, ip_address: str = "127.0.0.1", commit: bool = False):
        audit_entry = AuditLog(
            user_id=user_id,
            user_name=user_name,
            action=action,
            details=details,
            ip_address=ip_address
        )
        db.add(audit_entry)
        if commit:
            db.commit()
        return audit_entry

    @staticmethod
    def get_audit_logs(db: Session, page: int = 1, limit: int = 100):
        query = db.query(AuditLog)
        total = query.count()
        offset = (page - 1) * limit
        logs = query.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
        return [
            {
                "id": l.id,
                "user_id": l.user_id,
                "user_name": l.user_name or "System",
                "action": l.action,
                "details": l.details,
                "ip_address": l.ip_address,
                "created_at": l.created_at.isoformat() if l.created_at else None
            } for l in logs
        ]
