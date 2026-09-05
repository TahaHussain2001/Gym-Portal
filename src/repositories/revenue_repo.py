from typing import List, Optional
from sqlalchemy.orm import Session
from src.models.db_models import MonthlyRevenueHistory
from src.utils.helpers import apply_for_update

class RevenueRepo:
    @staticmethod
    def get_user_history(db: Session, user_id: int, limit: int = 12) -> List[MonthlyRevenueHistory]:
        return db.query(MonthlyRevenueHistory).filter(
            MonthlyRevenueHistory.user_id == user_id
        ).order_by(
            MonthlyRevenueHistory.year.desc(),
            MonthlyRevenueHistory.month.desc()
        ).limit(limit).all()

    @staticmethod
    def get_month_record(db: Session, user_id: int, year: int, month: int) -> Optional[MonthlyRevenueHistory]:
        return db.query(MonthlyRevenueHistory).filter(
            MonthlyRevenueHistory.user_id == user_id,
            MonthlyRevenueHistory.year == year,
            MonthlyRevenueHistory.month == month
        ).first()

    @staticmethod
    def save_month_archive(db: Session, user_id: int, year: int, month: int, total_revenue: float, payment_count: int) -> MonthlyRevenueHistory:
        year_month_str = f"{year:04d}-{month:02d}"
        existing = apply_for_update(db.query(MonthlyRevenueHistory).filter(
            MonthlyRevenueHistory.user_id == user_id,
            MonthlyRevenueHistory.year == year,
            MonthlyRevenueHistory.month == month
        ), db).first()
        if existing:
            existing.total_revenue = float(total_revenue)
            existing.payment_count = int(payment_count)
            existing.year_month_str = year_month_str
            db.commit()
            db.refresh(existing)
            return existing
        else:
            record = MonthlyRevenueHistory(
                user_id=user_id,
                year=year,
                month=month,
                year_month_str=year_month_str,
                total_revenue=float(total_revenue),
                payment_count=int(payment_count)
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    @staticmethod
    def purge_beyond_rolling_window(db: Session, user_id: int, keep_limit: int = 12):
        records = db.query(MonthlyRevenueHistory).filter(
            MonthlyRevenueHistory.user_id == user_id
        ).order_by(
            MonthlyRevenueHistory.year.desc(),
            MonthlyRevenueHistory.month.desc()
        ).all()

        if len(records) > keep_limit:
            to_delete = records[keep_limit:]
            for r in to_delete:
                db.delete(r)
            db.commit()
