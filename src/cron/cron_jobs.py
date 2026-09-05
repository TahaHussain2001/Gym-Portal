import logging
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from src.models.db_models import Member, MemberMembership, Notification, User
from src.constants.business_rules import (
    ARCHIVE_CLEANUP_DAYS, 
    EXPIRY_REMINDER_DAYS,
    STATUS_ACTIVE, 
    STATUS_PENDING,
    STATUS_EXPIRED
)

logger = logging.getLogger("sthxtechnologies-cron")

class ArchiveCleanupJob:
    @staticmethod
    def execute(db: Session):
        try:
            limit_date = date.today() - timedelta(days=ARCHIVE_CLEANUP_DAYS)
            limit_date_str = limit_date.isoformat()
            
            archived_to_delete = db.query(Member).filter(
                Member.deleted_at.isnot(None),
                Member.deleted_at < limit_date_str
            ).all()
            
            count = len(archived_to_delete)
            for m in archived_to_delete:
                logger.info(f"[CRON] Permanently purging archived member: {m.full_name} ({m.member_code})")
                db.delete(m)
            
            if count > 0:
                db.commit()
                logger.info(f"[CRON] ArchiveCleanupJob: Purged {count} archived member records.")
        except Exception as e:
            db.rollback()
            logger.error(f"[CRON ERROR] ArchiveCleanupJob failed: {e}")

class MembershipExpiryJob:
    @staticmethod
    def execute(db: Session):
        try:
            today = date.today()
            today_str = today.isoformat()
            sixty_days_ago_str = (today - timedelta(days=60)).isoformat()
            
            # 1. Members whose due date has passed but lies within 60 days (2 months) -> Pending
            due_memberships = db.query(MemberMembership).filter(
                MemberMembership.status == STATUS_ACTIVE,
                MemberMembership.next_due_date < today_str,
                MemberMembership.next_due_date >= sixty_days_ago_str
            ).all()
            for ms in due_memberships:
                ms.status = STATUS_PENDING
                if ms.member and ms.member.status == STATUS_ACTIVE and ms.member.deleted_at is None:
                    ms.member.status = STATUS_PENDING
                    logger.info(f"[CRON] Member marked Pending (unpaid within 60 days): {ms.member.full_name}")

            # 2. Members whose 2 months (60 days) have crossed after due date -> Expired
            expired_memberships = db.query(MemberMembership).filter(
                MemberMembership.next_due_date < sixty_days_ago_str
            ).all()
            for ms in expired_memberships:
                ms.status = STATUS_EXPIRED
                if ms.member and ms.member.status in [STATUS_ACTIVE, STATUS_PENDING] and ms.member.deleted_at is None:
                    ms.member.status = STATUS_EXPIRED
                    logger.info(f"[CRON] Member marked Expired (60+ days unpaid): {ms.member.full_name}")

            # 3. Trigger 3-day expiry reminders
            target_expiry = date.today() + timedelta(days=EXPIRY_REMINDER_DAYS)
            target_expiry_str = target_expiry.isoformat()
            
            expiring_soon = db.query(MemberMembership).filter(
                MemberMembership.status == STATUS_ACTIVE,
                MemberMembership.expiry_date == target_expiry_str
            ).all()
            
            for ms in expiring_soon:
                if ms.member and ms.member.deleted_at is None:
                    msg_text = f"{ms.member.full_name}'s membership expires in {EXPIRY_REMINDER_DAYS} days."
                    notif_exists = db.query(Notification).filter(
                        Notification.user_id == ms.member.user_id,
                        Notification.member_id == ms.member_id,
                        Notification.type == "Expiry",
                        Notification.message == msg_text,
                        Notification.created_at >= datetime.utcnow() - timedelta(days=1)
                    ).first()
                    if not notif_exists:
                        db.add(Notification(
                            user_id=ms.member.user_id,
                            member_id=ms.member_id,
                            title="Membership Expiry",
                            message=msg_text,
                            type="Expiry"
                        ))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[CRON ERROR] MembershipExpiryJob failed: {e}")

class DailyReminderJob:
    @staticmethod
    def execute(db: Session):
        try:
            users = db.query(User).all()
            for u in users:
                pending_count = db.query(Member).filter(
                    Member.user_id == u.id,
                    Member.deleted_at.is_(None),
                    Member.status == STATUS_PENDING
                ).count()

                if pending_count > 0:
                    msg_text = f"{pending_count} members have pending membership fees."
                    notif_exists = db.query(Notification).filter(
                        Notification.user_id == u.id,
                        Notification.member_id.is_(None),
                        Notification.type == "Pending Fee",
                        Notification.message == msg_text,
                        Notification.created_at >= datetime.utcnow() - timedelta(days=1)
                    ).first()
                    if not notif_exists:
                        db.add(Notification(
                            user_id=u.id,
                            member_id=None,
                            title="Daily Pending Fee Reminder",
                            message=msg_text,
                            type="Pending Fee"
                        ))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[CRON ERROR] DailyReminderJob failed: {e}")

class MonthlyRevenueArchivalJob:
    @staticmethod
    def execute(db: Session):
        try:
            from src.services.revenue_service import RevenueService
            RevenueService.run_monthly_archival_job(db)
        except Exception as e:
            db.rollback()
            logger.error(f"[CRON ERROR] MonthlyRevenueArchivalJob failed: {e}")

class AutoCheckoutJob:
    @staticmethod
    def execute(db: Session):
        try:
            from src.repositories.attendance_repo import AttendanceRepo
            AttendanceRepo.auto_checkout_expired_sessions(db, max_hours=1)
        except Exception as e:
            db.rollback()
            logger.error(f"[CRON ERROR] AutoCheckoutJob failed: {e}")

async def run_all_cron_jobs(db: Session):
    logger.info("Executing all scheduled cron jobs...")
    ArchiveCleanupJob.execute(db)
    MembershipExpiryJob.execute(db)
    DailyReminderJob.execute(db)
    MonthlyRevenueArchivalJob.execute(db)
    AutoCheckoutJob.execute(db)
    
    try:
        from src.services.idempotency_service import IdempotencyService
        purged = IdempotencyService.purge_expired_keys(db)
        if purged > 0:
            logger.info(f"[CRON] Purged {purged} expired idempotency key(s).")
    except Exception as e:
        logger.error(f"[CRON ERROR] Idempotency purge failed: {e}")

    try:
        from src.repositories.otp_repo import OTPRepo
        otps_purged = OTPRepo.purge_expired_otps(db)
        if otps_purged > 0:
            logger.info(f"[CRON] Purged {otps_purged} expired or used OTP code(s).")
    except Exception as e:
        logger.error(f"[CRON ERROR] OTP purge failed: {e}")
