from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from src.models.db_models import Member

class MemberRepo:
    @staticmethod
    def list_paginated(
        db: Session, 
        user_id: int, 
        search: Optional[str] = None, 
        status_filter: Optional[str] = None, 
        archived: bool = False,
        page: int = 1,
        limit: int = 20
    ) -> Tuple[List[Member], int]:
        query = db.query(Member).filter(Member.user_id == user_id)

        if archived:
            query = query.filter(Member.deleted_at.isnot(None))
        else:
            query = query.filter(Member.deleted_at.is_(None))

        if status_filter:
            target_status = "Pending" if status_filter.lower() == "overdue" else status_filter
            query = query.filter(Member.status == target_status)

        if search:
            query = query.filter(
                (Member.full_name.like(f"%{search}%")) | 
                (Member.whatsapp_number.like(f"%{search}%")) |
                (Member.member_code.like(f"%{search}%"))
            )

        total = query.count()
        offset = (page - 1) * limit
        members = query.order_by(Member.id.desc()).offset(offset).limit(limit).all()
        return members, total

    @staticmethod
    def get_by_id_scoped(db: Session, member_id: int, user_id: int, include_archived: bool = False) -> Optional[Member]:
        """Strict IDOR Protection: Always scopes queries to user_id (tenant owner)."""
        query = db.query(Member).filter(
            Member.id == member_id,
            Member.user_id == user_id
        )
        if not include_archived:
            query = query.filter(Member.deleted_at.is_(None))
        return query.first()
