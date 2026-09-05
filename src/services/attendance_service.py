import csv
import io
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from src.models.db_models import Member, Attendance, MemberMembership, MembershipPlan
from src.repositories.attendance_repo import AttendanceRepo
from src.services.audit_service import AuditService
from src.services.status_service import StatusService
from src.utils.helpers import apply_for_update

def format_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    iso = dt.isoformat()
    if not (iso.endswith("Z") or "+" in iso or "-" in iso[10:]):
        iso += "Z"
    return iso

class AttendanceService:
    @staticmethod
    def search_members_for_receptionist(db: Session, user_id: int, query_str: str) -> List[Dict[str, Any]]:
        if not query_str or len(query_str.strip()) < 1:
            return []

        search_pattern = f"%{query_str.strip()}%"
        members = db.query(Member).filter(
            Member.user_id == user_id,
            Member.deleted_at.is_(None),
            or_(
                Member.full_name.ilike(search_pattern),
                Member.whatsapp_number.ilike(search_pattern),
                Member.member_code.ilike(search_pattern)
            )
        ).limit(10).all()

        results = []
        for m in members:
            active_session = AttendanceRepo.get_active_session(db, m.id)
            
            # Fetch current active plan info
            active_membership = db.query(MemberMembership).join(MembershipPlan).filter(
                MemberMembership.member_id == m.id,
                MemberMembership.status == "Active"
            ).order_by(MemberMembership.expiry_date.desc()).first()

            plan_name = active_membership.plan.plan_name if active_membership and active_membership.plan else "No Active Plan"
            expiry_date = active_membership.expiry_date if active_membership else None

            results.append({
                "id": m.id,
                "full_name": m.full_name,
                "member_code": m.member_code,
                "phone": m.whatsapp_number,
                "status": m.status,
                "plan_name": plan_name,
                "expiry_date": expiry_date,
                "photo_url": getattr(m, "photo_url", None),
                "is_inside": active_session is not None,
                "active_session_id": active_session.id if active_session else None,
                "check_in_time": format_iso_utc(active_session.check_in_time) if active_session else None
            })

        return results

    @staticmethod
    def check_in(db: Session, member_id: int, user_id: int, created_by: int, user_name: str = "Staff", allow_override: bool = False) -> Dict[str, Any]:
        member = apply_for_update(db.query(Member).filter(
            Member.id == member_id,
            Member.user_id == user_id,
            Member.deleted_at.is_(None)
        ), db).first()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found.")

        # Business Rule 1: Prevent duplicate check-ins
        existing_session = AttendanceRepo.get_active_session(db, member.id)
        if existing_session:
            raise HTTPException(
                status_code=400, 
                detail=f"Member '{member.full_name}' is already inside the gym (Checked in at {existing_session.check_in_time.strftime('%I:%M %p')})."
            )

        active_ms = db.query(MemberMembership).filter(
            MemberMembership.member_id == member.id,
            MemberMembership.status == "Active"
        ).first()

        eligible, msg = StatusService.validate_check_in_eligibility(member, active_ms, allow_override)
        if not eligible:
            raise HTTPException(status_code=400, detail=msg)

        try:
            att = AttendanceRepo.create_check_in(db, member.id, user_id, created_by, commit=False)

            # Audit Log Entry (deferred commit)
            AuditService.log_action(
                db,
                user_id=created_by,
                user_name=user_name,
                action="MEMBER_CHECK_IN",
                details=f"Checked in member '{member.full_name}' ({member.member_code}). Session ID #{att.id}.",
                commit=False
            )

            # Single atomic commit for Check-in + Audit Log
            db.commit()
            db.refresh(att)

            return {
                "message": f"Successfully checked in '{member.full_name}'!",
                "attendance_id": att.id,
                "member_id": member.id,
                "member_name": member.full_name,
                "member_code": member.member_code,
                "check_in_time": att.check_in_time.isoformat(),
                "status": "Inside"
            }
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Check-in failed: {str(e)}")

    @staticmethod
    def check_out(db: Session, member_id: int, user_id: int, created_by: int, user_name: str = "Staff") -> Dict[str, Any]:
        member = apply_for_update(db.query(Member).filter(
            Member.id == member_id,
            Member.user_id == user_id,
            Member.deleted_at.is_(None)
        ), db).first()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found.")

        existing_session = AttendanceRepo.get_active_session(db, member.id)
        if not existing_session:
            raise HTTPException(
                status_code=400, 
                detail=f"Member '{member.full_name}' does not have an active check-in session."
            )

        try:
            att = AttendanceRepo.create_check_out(db, existing_session, commit=False)

            # Audit Log Entry (deferred commit)
            AuditService.log_action(
                db,
                user_id=created_by,
                user_name=user_name,
                action="MEMBER_CHECK_OUT",
                details=f"Checked out member '{member.full_name}' ({member.member_code}). Workout duration: {att.workout_duration} mins.",
                commit=False
            )

            # Single atomic commit for Check-out + Audit Log
            db.commit()
            db.refresh(att)

            return {
                "message": f"Successfully checked out '{member.full_name}'!",
                "attendance_id": att.id,
                "member_id": member.id,
                "member_name": member.full_name,
                "member_code": member.member_code,
                "workout_duration": att.workout_duration,
                "status": "Completed"
            }
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Check-out failed: {str(e)}")

        return {
            "message": f"Successfully checked out '{member.full_name}'!",
            "attendance_id": att.id,
            "member_id": member.id,
            "member_name": member.full_name,
            "member_code": member.member_code,
            "check_out_time": att.check_out_time.isoformat(),
            "workout_duration": att.workout_duration,
            "status": "Completed"
        }

    @staticmethod
    def get_history_with_presets(
        db: Session,
        user_id: int,
        preset: Optional[str] = None,
        custom_from: Optional[str] = None,
        custom_to: Optional[str] = None,
        member_id: Optional[int] = None,
        query_str: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        today = date.today()
        date_from = custom_from
        date_to = custom_to

        if preset == "today":
            date_from = today.isoformat()
            date_to = today.isoformat()
        elif preset == "yesterday":
            yesterday = today - timedelta(days=1)
            date_from = yesterday.isoformat()
            date_to = yesterday.isoformat()
        elif preset == "this_week":
            start_week = today - timedelta(days=today.weekday())
            date_from = start_week.isoformat()
            date_to = today.isoformat()
        elif preset == "this_month":
            start_month = today.replace(day=1)
            date_from = start_month.isoformat()
            date_to = today.isoformat()

        records = AttendanceRepo.query_history(db, user_id, date_from, date_to, member_id, query_str)

        results = []
        for r in records:
            m = r.member
            results.append({
                "id": r.id,
                "member_id": m.id,
                "member_name": m.full_name if m else "Unknown",
                "member_code": m.member_code if m else "",
                "photo_url": getattr(m, "photo_url", None),
                "attendance_date": r.attendance_date,
                "check_in_time": format_iso_utc(r.check_in_time),
                "check_out_time": format_iso_utc(r.check_out_time),
                "workout_duration": r.workout_duration,
                "status": r.status
            })
        return results

    @staticmethod
    def get_member_profile_attendance(db: Session, member_id: int) -> Dict[str, Any]:
        summary = AttendanceRepo.get_member_attendance_summary(db, member_id)
        formatted_logs = []
        for r in summary["records"][:30]:
            formatted_logs.append({
                "id": r.id,
                "attendance_date": r.attendance_date,
                "check_in_time": format_iso_utc(r.check_in_time),
                "check_out_time": format_iso_utc(r.check_out_time),
                "workout_duration": r.workout_duration,
                "status": r.status
            })

        return {
            "total_visits": summary["total_visits"],
            "last_visit": summary["last_visit"],
            "total_hours": summary["total_hours"],
            "total_minutes": summary["total_minutes"],
            "logs": formatted_logs
        }

    @staticmethod
    def generate_attendance_reports(db: Session, user_id: int) -> Dict[str, Any]:
        today = date.today()
        start_month = today.replace(day=1).isoformat()
        
        month_records = db.query(Attendance).join(Member).filter(
            Attendance.user_id == user_id,
            Attendance.attendance_date >= start_month,
            Member.deleted_at.is_(None)
        ).all()

        total_month_checkins = len(month_records)

        # Most Active Members (Top 5)
        member_counts: Dict[int, Dict[str, Any]] = {}
        for r in month_records:
            if r.member_id not in member_counts:
                member_counts[r.member_id] = {
                    "member_id": r.member.id,
                    "name": r.member.full_name,
                    "code": r.member.member_code,
                    "visits": 0
                }
            member_counts[r.member_id]["visits"] += 1

        sorted_most_active = sorted(member_counts.values(), key=lambda x: x["visits"], reverse=True)[:5]
        sorted_least_active = sorted(member_counts.values(), key=lambda x: x["visits"])[:5]

        # Peak Attendance Hours Breakdown
        hour_counts = {h: 0 for h in range(24)}
        for r in month_records:
            if r.check_in_time:
                hour_counts[r.check_in_time.hour] += 1

        peak_hour_idx = max(hour_counts, key=hour_counts.get) if total_month_checkins > 0 else 18
        peak_hour_str = f"{peak_hour_idx:02d}:00 - {(peak_hour_idx+1)%24:02d}:00"

        return {
            "total_month_checkins": total_month_checkins,
            "avg_daily_visitors": max(1, int(total_month_checkins / max(1, today.day))),
            "peak_attendance_hours": peak_hour_str,
            "most_active_members": sorted_most_active,
            "least_active_members": sorted_least_active,
            "hourly_breakdown": [{"hour": f"{h:02d}:00", "count": c} for h, c in hour_counts.items() if c > 0 or 8 <= h <= 21]
        }

    @staticmethod
    def export_csv_report(db: Session, user_id: int) -> str:
        records = AttendanceRepo.query_history(db, user_id, limit=1000)
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["Attendance ID", "Member Name", "Member Code", "Attendance Date", "Check-In Time", "Check-Out Time", "Workout Duration (Mins)", "Status"])
        for r in records:
            m = r.member
            writer.writerow([
                r.id,
                m.full_name if m else "Unknown",
                m.member_code if m else "",
                r.attendance_date,
                r.check_in_time.strftime("%Y-%m-%d %H:%M:%S") if r.check_in_time else "",
                r.check_out_time.strftime("%Y-%m-%d %H:%M:%S") if r.check_out_time else "",
                r.workout_duration if r.workout_duration is not None else "",
                r.status
            ])

        return output.getvalue()
