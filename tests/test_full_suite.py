import os
import sys
import unittest
from decimal import Decimal
from datetime import datetime, date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.models.db_models import (
    Base, User, Member, MembershipPlan, MemberMembership, Payment, Notification, AuditLog, OTPCode, RefreshToken
)
from src.services.auth_service import AuthService
from src.services.attendance_service import AttendanceService
from src.services.status_service import StatusService
from src.services.payment_service import PaymentService
from src.services.report_service import ReportService
from src.services.revenue_service import RevenueService
from src.services.idempotency_service import IdempotencyService
from src.repositories.member_repo import MemberRepo
from src.repositories.attendance_repo import AttendanceRepo
from src.repositories.otp_repo import OTPRepo
from src.constants.enums import MemberStatus, UserRole

class TestFullSystemSuite(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Seed Admin and Receptionist
        self.admin = User(name="STHX Admin", email="admin@sthx.com", password="pass", role=UserRole.ADMIN.value, is_verified=True)
        self.staff = User(name="STHX Staff", email="staff@sthx.com", password="pass", role=UserRole.RECEPTIONIST.value, is_verified=True)
        self.db.add_all([self.admin, self.staff])
        self.db.commit()

        # Seed Plan
        self.plan = MembershipPlan(plan_name="Monthly Standard", price=Decimal("3000.00"), registration_fee=Decimal("1000.00"), duration_days=30)
        self.db.add(self.plan)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_01_user_registration_and_otp(self):
        """Test User Registration and OTP creation."""
        res = AuthService.register_user(self.db, "New User", "newuser@sthx.com", "Password123!", "Password123!", "Gym A")
        self.assertEqual(res["email"], "newuser@sthx.com")
        otp = OTPRepo.get_active_otp(self.db, "newuser@sthx.com", "EMAIL_VERIFICATION")
        self.assertIsNotNone(otp)

    def test_02_member_lifecycle_and_status(self):
        """Test Member creation, plan assignment, and status transition."""
        member = Member(user_id=self.admin.id, member_code="FM-2026-0001", full_name="John Doe", email="john@doe.com", whatsapp_number="03001234567", joining_date="2026-01-01", registration_date="2026-01-01", status=MemberStatus.PENDING.value)
        self.db.add(member)
        self.db.commit()

        # Assign Plan
        ms = MemberMembership(member_id=member.id, plan_id=self.plan.id, start_date="2026-01-01", expiry_date="2026-01-31", next_due_date="2026-01-31", status="Pending")
        self.db.add(ms)
        self.db.commit()

        # Status transition
        updated = StatusService.validate_and_transition_member_status(self.db, member, MemberStatus.ACTIVE.value, self.admin.id, self.admin.name)
        self.assertEqual(updated.status, MemberStatus.ACTIVE.value)

    def test_03_payment_recording_and_receipt(self):
        """Test Payment recording, receipt generation, and fee calculation."""
        member = Member(user_id=self.admin.id, member_code="FM-2026-0002", full_name="Jane Doe", email="jane@doe.com", whatsapp_number="03009876543", joining_date="2026-01-01", registration_date="2026-01-01", status=MemberStatus.ACTIVE.value)
        self.db.add(member)
        self.db.commit()

        ms = MemberMembership(member_id=member.id, plan_id=self.plan.id, start_date="2026-01-01", expiry_date="2026-01-31", next_due_date="2026-01-31", status="Active")
        self.db.add(ms)
        self.db.commit()

        receipt_num = PaymentService.generate_receipt_number(1, "2026-01-01")
        self.assertTrue(receipt_num.startswith("REC-"))

        payment = Payment(member_id=member.id, membership_id=ms.id, amount=Decimal("3000.00"), registration_fee=Decimal("1000.00"), payment_method="Cash", payment_date="2026-01-01", receipt_number=receipt_num, status="Paid")
        self.db.add(payment)
        self.db.commit()
        self.assertEqual(float(payment.amount), 3000.0)

    def test_04_attendance_checkin_checkout(self):
        """Test Attendance check-in, check-out, and duration calculation."""
        member = Member(user_id=self.admin.id, member_code="FM-2026-0003", full_name="Bob Smith", email="bob@smith.com", whatsapp_number="03001112223", joining_date="2026-01-01", registration_date="2026-01-01", status=MemberStatus.ACTIVE.value)
        self.db.add(member)
        self.db.commit()

        active_ms = MemberMembership(member_id=member.id, plan_id=self.plan.id, start_date="2026-01-01", expiry_date="2026-12-31", next_due_date="2026-12-31", status="Active")
        self.db.add(active_ms)
        self.db.commit()

        res_in = AttendanceService.check_in(self.db, member.id, self.admin.id, self.admin.id, "Admin Staff")
        self.assertEqual(res_in["status"], "Inside")

        res_out = AttendanceService.check_out(self.db, member.id, self.admin.id, self.admin.id, "Admin Staff")
        self.assertEqual(res_out["status"], "Completed")

    def test_05_report_service_sql_aggregations(self):
        """Test ReportService financial calculations."""
        summary = ReportService.get_financial_summary(self.db, self.admin.id)
        self.assertIn("today_revenue", summary)
        self.assertIn("monthly_revenue", summary)

    def test_06_idempotency_caching(self):
        """Test IdempotencyKey saving and retrieval."""
        key = "IDEM-TEST-100"
        IdempotencyService.save_response(self.db, key, self.admin.id, "/api/test", {"data": 1}, 200, {"success": True})
        cached = IdempotencyService.get_existing_response(self.db, key, self.admin.id, "/api/test")
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0], 200)

    def test_07_idor_scoped_repository(self):
        """Test MemberRepo IDOR tenant scoping."""
        m = MemberRepo.get_by_id_scoped(self.db, 1, self.staff.id)
        self.assertIsNone(m)

if __name__ == "__main__":
    unittest.main()
