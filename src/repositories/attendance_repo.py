from datetime import datetime, date
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from src.models.db_models import Attendance, Member

import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger("sthxtechnologies-attendance")

class AttendanceRepo:
    @staticmethod
    def auto_checkout_expired_sessions(db: Session, max_hours: int = 1) -> int:
        """Automatically checks out members 1 hour (60 minutes) after check-in entry."""
        now = datetime.utcnow()
        cutoff_time = now - timedelta(hours=max_hours)
        
        expired_sessions = db.query(Attendance).filter(
            Attendance.status == "Inside",
            Attendance.check_in_time <= cutoff_time
        ).all()

        count = len(expired_sessions)
        for att in expired_sessions:
            checkout_time = att.check_in_time + timedelta(hours=max_hours) if att.check_in_time else now
            att.check_out_time = checkout_time
            att.status = "Completed"
            att.workout_duration = 60 * max_hours
            att.updated_at = now

        if count > 0:
            db.commit()
            logger.info(f"[AUTO CHECKOUT] Automatically checked out {count} member(s) 1 hour after check-in entry.")
        return count

    @staticmethod
    def get_active_session(db: Session, member_id: int) -> Optional[Attendance]:
        AttendanceRepo.auto_checkout_expired_sessions(db, max_hours=1)
        return db.query(Attendance).filter(
            Attendance.member_id == member_id,
            Attendance.status == "Inside"
        ).first()

    @staticmethod
    def get_currently_inside(db: Session, user_id: int) -> List[Attendance]:
        AttendanceRepo.auto_checkout_expired_sessions(db, max_hours=1)
        return db.query(Attendance).join(Member).filter(
            Attendance.user_id == user_id,
            Attendance.status == "Inside",
            Member.deleted_at.is_(None)
        ).all()

    @staticmethod
    def create_check_in(db: Session, member_id: int, user_id: int, created_by: int, commit: bool = True) -> Attendance:
        now = datetime.utcnow()
        today_str = date.today().isoformat()
        att = Attendance(
            member_id=member_id,
            user_id=user_id,
            check_in_time=now,
            status="Inside",
            attendance_date=today_str,
            created_by=created_by
        )
        db.add(att)
        if commit:
            db.commit()
            db.refresh(att)
        return att

    @staticmethod
    def create_check_out(db: Session, att: Attendance, commit: bool = True) -> Attendance:
        now = datetime.utcnow()
        att.check_out_time = now
        att.status = "Completed"
        if att.check_in_time:
            duration_seconds = (now - att.check_in_time).total_seconds()
            att.workout_duration = max(1, int(duration_seconds // 60))
        else:
            att.workout_duration = 0
        att.updated_at = now
        if commit:
            db.commit()
            db.refresh(att)
        return att

    @staticmethod
    def get_member_attendance_summary(db: Session, member_id: int) -> Dict[str, Any]:
        records = db.query(Attendance).filter(
            Attendance.member_id == member_id
        ).order_by(Attendance.check_in_time.desc()).all()

        total_visits = len(records)
        last_visit = records[0].check_in_time.isoformat() if total_visits > 0 and records[0].check_in_time else None
        
        completed_durations = [r.workout_duration for r in records if r.workout_duration is not None]
        total_minutes = sum(completed_durations)
        total_hours = round(total_minutes / 60.0, 1)

        return {
            "total_visits": total_visits,
            "last_visit": last_visit,
            "total_hours": total_hours,
            "total_minutes": total_minutes,
            "records": records
        }

    @staticmethod
    def query_history(
        db: Session, 
        user_id: int, 
        date_from: Optional[str] = None, 
        date_to: Optional[str] = None, 
        member_id: Optional[int] = None, 
        query_str: Optional[str] = None,
        limit: int = 100
    ) -> List[Attendance]:
        q = db.query(Attendance).join(Member).filter(
            Attendance.user_id == user_id,
            Member.deleted_at.is_(None)
        )

        if member_id:
            q = q.filter(Attendance.member_id == member_id)

        if date_from:
            q = q.filter(Attendance.attendance_date >= date_from)

        if date_to:
            q = q.filter(Attendance.attendance_date <= date_to)

        if query_str:
            search_pattern = f"%{query_str}%"
            q = q.filter(
                or_(
                    Member.full_name.ilike(search_pattern),
                    Member.member_code.ilike(search_pattern),
                    Member.whatsapp_number.ilike(search_pattern)
                )
            )

        return q.order_by(Attendance.check_in_time.desc()).limit(limit).all()

    @staticmethod
    def get_dashboard_attendance_stats(db: Session, user_id: int) -> Dict[str, Any]:
        today_str = date.today().isoformat()
        
        inside_count = db.query(Attendance).join(Member).filter(
            Attendance.user_id == user_id,
            Attendance.status == "Inside",
            Member.deleted_at.is_(None)
        ).count()

        today_checkins = db.query(Attendance).join(Member).filter(
            Attendance.user_id == user_id,
            Attendance.attendance_date == today_str,
            Member.deleted_at.is_(None)
        ).count()

        today_checkouts = db.query(Attendance).join(Member).filter(
            Attendance.user_id == user_id,
            Attendance.attendance_date == today_str,
            Attendance.status == "Completed",
            Member.deleted_at.is_(None)
        ).count()

        # Average workout duration today
        durations = db.query(Attendance.workout_duration).join(Member).filter(
            Attendance.user_id == user_id,
            Attendance.attendance_date == today_str,
            Attendance.status == "Completed",
            Attendance.workout_duration.isnot(None),
            Member.deleted_at.is_(None)
        ).all()

        if durations and len(durations) > 0:
            avg_duration_minutes = int(sum(d[0] for d in durations) / len(durations))
        else:
            avg_duration_minutes = 45  # Standard gym visit average fallback

        return {
            "members_inside": inside_count,
            "total_checkins_today": today_checkins,
            "total_checkouts_today": today_checkouts,
            "avg_duration_minutes": avg_duration_minutes,
            "peak_time": "06:00 PM - 08:00 PM"
        }
