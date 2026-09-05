import csv
import io
from datetime import date, datetime
from sqlalchemy.orm import Session
from src.models.db_models import Member, Payment, MemberMembership, MembershipPlan

from sqlalchemy import func

class ReportService:
    @staticmethod
    def get_financial_summary(db: Session, user_id: int):
        today_str = date.today().isoformat()
        start_of_month = date.today().replace(day=1).isoformat()
        
        # Monthly Revenue via SQL Sum Aggregation
        monthly_revenue = db.query(func.coalesce(func.sum(Payment.amount), 0)).join(Member).filter(
            Member.user_id == user_id,
            Payment.payment_date >= start_of_month
        ).scalar()
        
        # Total Revenue & Count all time via SQL Aggregation
        total_revenue_res = db.query(
            func.coalesce(func.sum(Payment.amount), 0).label("total_sum"),
            func.count(Payment.id).label("total_count")
        ).join(Member).filter(Member.user_id == user_id).first()
        
        total_revenue = float(total_revenue_res.total_sum) if total_revenue_res else 0.0
        payment_count = int(total_revenue_res.total_count) if total_revenue_res else 0
        
        # Today's Revenue via SQL Aggregation
        today_revenue = db.query(func.coalesce(func.sum(Payment.amount), 0)).join(Member).filter(
            Member.user_id == user_id,
            Payment.payment_date == today_str
        ).scalar()
        
        # Plan distribution via SQL Count Grouping
        plans = db.query(MembershipPlan).all()
        plan_distribution = []
        for pl in plans:
            count = db.query(func.count(MemberMembership.id)).join(Member).filter(
                Member.user_id == user_id,
                Member.deleted_at.is_(None),
                MemberMembership.plan_id == pl.id,
                MemberMembership.status == "Active"
            ).scalar()
            plan_distribution.append({
                "plan_name": pl.plan_name,
                "count": count or 0,
                "price": float(pl.price)
            })
            
        return {
            "today_revenue": float(today_revenue or 0.0),
            "monthly_revenue": float(monthly_revenue or 0.0),
            "total_revenue": total_revenue,
            "payment_count": payment_count,
            "plan_distribution": plan_distribution
        }

    @staticmethod
    def export_members_csv(db: Session, user_id: int) -> str:
        members = db.query(Member).filter(Member.user_id == user_id, Member.deleted_at.is_(None)).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Member Code", "Full Name", "Email", "WhatsApp Number", "Joining Date", "Status"])
        for m in members:
            writer.writerow([m.member_code, m.full_name, m.email, m.whatsapp_number, m.joining_date, m.status])
        return output.getvalue()
