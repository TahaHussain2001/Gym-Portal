from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, Numeric, ForeignKey, DateTime, Boolean, Index, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from src.config.app_config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Gym(Base):
    __tablename__ = "gyms"
    id = Column(Integer, primary_key=True, index=True)
    gym_name = Column(String, nullable=False, index=True)
    owner_name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True, index=True)
    phone = Column(String, nullable=False)  # Exactly 11 digits
    cnic = Column(String, nullable=False)   # xxxxx-xxxxxxx-x
    address = Column(String, nullable=True)
    subscription_plan = Column(String, nullable=False, default="Basic")
    subscription_expiry = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    status = Column(String, default="Active", index=True)  # Active, Suspended, Expired
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", foreign_keys="User.gym_id", back_populates="gym", uselist=False)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    username = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, default="gym_owner", index=True)  # super_admin, gym_owner, admin, receptionist
    avatar_url = Column(String, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String, nullable=True)
    gym_name = Column(String, nullable=True)
    gym_id = Column(Integer, ForeignKey("gyms.id", ondelete="SET NULL"), nullable=True)
    phone = Column(String, nullable=True)
    cnic = Column(String, nullable=True)
    is_verified = Column(Boolean, default=True, nullable=False)
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    lockout_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    gym = relationship("Gym", foreign_keys=[gym_id], back_populates="owner")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    device_id = Column(String, nullable=False, index=True)
    device_info = Column(String, nullable=True)
    is_revoked = Column(Boolean, default=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")

class LoginHistory(Base):
    __tablename__ = "login_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    email = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)  # SUCCESS, FAILED, LOCKED
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")

class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        Index("idx_member_user_status", "user_id", "status"),
    )
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    member_code = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False, index=True)
    email = Column(String, nullable=False)
    whatsapp_number = Column(String, nullable=False, index=True)
    joining_date = Column(String, nullable=False)  # YYYY-MM-DD
    registration_date = Column(String, nullable=False)  # YYYY-MM-DD
    status = Column(String, default="Pending", index=True)  # Pending, Active, Inactive, Expired, Archived
    deleted_at = Column(String, nullable=True, index=True)  # YYYY-MM-DD HH:MM:SS
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
    memberships = relationship("MemberMembership", back_populates="member", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="member", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="member", cascade="all, delete-orphan")

class MembershipPlan(Base):
    __tablename__ = "membership_plans"
    __table_args__ = (
        CheckConstraint("price >= 0", name="chk_plan_price"),
        CheckConstraint("registration_fee >= 0", name="chk_plan_reg_fee"),
    )
    id = Column(Integer, primary_key=True, index=True)
    plan_name = Column(String, unique=True, index=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    registration_fee = Column(Numeric(10, 2), default=1000.00)
    duration_days = Column(Integer, default=30)
    is_active = Column(Boolean, default=True, index=True)

class MemberMembership(Base):
    __tablename__ = "member_memberships"
    __table_args__ = (
        Index("idx_membership_member_status", "member_id", "status"),
    )
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("membership_plans.id"), nullable=False, index=True)
    start_date = Column(String, nullable=False)  # YYYY-MM-DD
    expiry_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    next_due_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    status = Column(String, default="Pending", index=True)  # Active, Expired, Pending

    member = relationship("Member", back_populates="memberships")
    plan = relationship("MembershipPlan")

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="chk_payment_amount"),
        CheckConstraint("registration_fee >= 0", name="chk_payment_reg_fee"),
        Index("idx_payment_member_date", "member_id", "payment_date"),
    )
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True)
    membership_id = Column(Integer, ForeignKey("member_memberships.id"), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    registration_fee = Column(Numeric(10, 2), default=0.00)
    payment_method = Column(String, nullable=False)  # Cash, Bank Transfer, EasyPaisa, JazzCash
    payment_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    receipt_number = Column(String, unique=True, index=True, nullable=False)
    payment_type = Column(String, default="Renewal")  # Registration, Renewal, Plan Change
    status = Column(String, default="Paid")

    member = relationship("Member", back_populates="payments")
    membership = relationship("MemberMembership")

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    type = Column(String, nullable=False)  # New Member, Pending Fee, Expiry, Payment
    is_read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")
    member = relationship("Member", back_populates="notifications")

class PendingRegistration(Base):
    __tablename__ = "pending_registrations"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="receptionist")
    code = Column(String, nullable=False)
    attempts = Column(Integer, default=0)
    resend_attempts = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_name = Column(String, nullable=True)
    action = Column(String, nullable=False, index=True)
    details = Column(String, nullable=False)
    ip_address = Column(String, default="127.0.0.1")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")

class DirectMessage(Base):
    __tablename__ = "direct_messages"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    is_read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    sender = relationship("User", foreign_keys=[sender_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
    gym = relationship("Gym")

class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)

class MonthlyRevenueHistory(Base):
    __tablename__ = "monthly_revenue_history"
    __table_args__ = (
        UniqueConstraint("user_id", "year_month_str", name="uix_user_year_month_revenue"),
        CheckConstraint("total_revenue >= 0", name="chk_monthly_rev_positive"),
    )
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False, index=True)
    year_month_str = Column(String, nullable=False, index=True)  # e.g. "2026-06"
    total_revenue = Column(Numeric(10, 2), nullable=False, default=0.00)
    payment_count = Column(Integer, default=0)
    archived_at = Column(DateTime, default=datetime.utcnow)

class PlatformRevenueHistory(Base):
    __tablename__ = "platform_revenue_history"
    __table_args__ = (
        UniqueConstraint("year_month_str", name="uix_platform_year_month_revenue"),
        CheckConstraint("total_revenue >= 0", name="chk_platform_rev_positive"),
    )
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False, index=True)
    year_month_str = Column(String, nullable=False, index=True)  # e.g. "2026-06"
    total_revenue = Column(Numeric(10, 2), nullable=False, default=0.00)
    payment_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        Index("idx_attendance_member_status", "member_id", "status"),
    )
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    check_in_time = Column(DateTime, nullable=False, index=True)
    check_out_time = Column(DateTime, nullable=True, index=True)
    workout_duration = Column(Integer, nullable=True)  # in minutes
    status = Column(String, default="Inside", index=True)  # "Inside" or "Completed"
    attendance_date = Column(String, nullable=False, index=True)  # "YYYY-MM-DD"
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    member = relationship("Member")

class OTPCode(Base):
    __tablename__ = "otps"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    email = Column(String, nullable=False, index=True)
    otp = Column(String, nullable=False)
    purpose = Column(String, nullable=False, index=True)  # EMAIL_VERIFICATION, PASSWORD_RESET
    expires_at = Column(DateTime, nullable=False, index=True)
    attempts = Column(Integer, default=0)
    resend_count = Column(Integer, default=0)
    is_used = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    response_code = Column(Integer, nullable=False)
    response_body = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    theme_name = Column(String, default="Default Blue", nullable=False)
    primary_color = Column(String, default="#2563EB", nullable=False)
    secondary_color = Column(String, default="#1E40AF", nullable=False)
    accent_color = Column(String, default="#60A5FA", nullable=False)
    hover_color = Column(String, default="#3B82F6", nullable=False)
    border_color = Column(String, default="rgba(37,99,235,0.3)", nullable=False)
    background_image = Column(String, nullable=True)
    background_opacity = Column(Integer, default=0, nullable=False)
    background_blur = Column(Integer, default=0, nullable=False)
    background_overlay = Column(String, default="medium", nullable=False)
    dark_mode = Column(String, default="dark", nullable=False)
    card_style = Column(String, default="glassmorphism", nullable=False)
    sidebar_style = Column(String, default="expanded", nullable=False)
    layout_type = Column(String, default="full_width", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User")

def init_db():
    Base.metadata.create_all(bind=engine)
    # Migration helper for SQLite / engine if new columns missing
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT 1"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN gym_name VARCHAR"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN lockout_until DATETIME"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN gym_id INTEGER REFERENCES gyms(id)"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN cnic VARCHAR"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url VARCHAR"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE payments ADD COLUMN payment_type VARCHAR DEFAULT 'Renewal'"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE gyms ADD COLUMN address VARCHAR"))
                conn.commit()
            except Exception:
                pass
            # Set all existing users as verified so existing accounts work
            conn.execute(text("UPDATE users SET is_verified = true WHERE is_verified IS NULL"))
            conn.commit()
    except Exception:
        pass

