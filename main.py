import os
import re
import random
import logging
import asyncio
from datetime import datetime, date, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from sqlalchemy.orm import Session

# Import modular components from src
from src.config.app_config import DATABASE_URL, PASSWORD_REGEX, ALLOWED_ORIGINS
from src.constants.business_rules import (
    REGISTRATION_FEE_AMOUNT, 
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_GYM_OWNER, 
    ROLE_RECEPTIONIST,
    STATUS_ACTIVE, 
    STATUS_PENDING, 
    STATUS_EXPIRED, 
    STATUS_ARCHIVED
)
from src.models.db_models import (
    engine, SessionLocal, init_db,
    Gym, User, Member, MembershipPlan, MemberMembership, Payment, Notification, AuditLog, SystemSetting, PendingRegistration, PlatformRevenueHistory
)
from src.utils.helpers import hash_password, verify_password, create_access_token, create_refresh_token, decode_access_token, apply_for_update
from src.services.payment_service import PaymentService
from src.services.audit_service import AuditService
from src.services.report_service import ReportService
from src.services.auth_service import AuthService
from src.services.settings_service import SettingsService
from src.services.revenue_service import RevenueService
from src.services.attendance_service import AttendanceService
from src.services.idempotency_service import IdempotencyService
from src.services.status_service import StatusService
from src.services.plan_service import PlanService
from src.repositories.member_repo import MemberRepo
from src.repositories.attendance_repo import AttendanceRepo
from fastapi.responses import Response
from src.services.gym_service import GymService
from src.middleware.rbac import check_role
from src.middleware.rate_limiter import limit_auth_requests
from src.cron.cron_jobs import run_all_cron_jobs

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sthxtechnologies-gym")

# Initialize DB tables and indexes
init_db()

# Background cron loop
async def cron_scheduler_loop():
    await asyncio.sleep(5)
    while True:
        try:
            db = SessionLocal()
            await run_all_cron_jobs(db)
            db.close()
        except Exception as e:
            logger.error(f"Error in cron scheduler loop: {e}")
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
    
    cron_task = asyncio.create_task(cron_scheduler_loop())
    yield
    cron_task.cancel()

from src.middleware.observability import ObservabilityMiddleware, setup_health_routes
from src.middleware.error_handler import setup_global_exception_handlers

app = FastAPI(
    title="STHX Technologies Gym Management Portal API",
    description="Production-grade SaaS Gym Management Portal API built with FastAPI & SQLAlchemy",
    version="2.5.0",
    lifespan=lifespan
)

app.add_middleware(ObservabilityMiddleware)
setup_health_routes(app)
setup_global_exception_handlers(app)

# Environment-specific CORS middleware
cors_origins = ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["http://localhost:8000", "http://127.0.0.1:8000"]
allow_creds = True if "*" not in cors_origins else False

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)


def seed_database(db: Session):
    # Seed Super Admin
    super_admin = db.query(User).filter(User.email == "superadmin@sthxtechnologies.com").first()
    if not super_admin:
        super_admin = db.query(User).filter(User.username == "superadmin").first()
    if not super_admin:
        hashed = hash_password("superadmin123")
        super_admin = User(
            name="Super Admin",
            username="superadmin",
            email="superadmin@sthxtechnologies.com",
            password=hashed,
            role=ROLE_SUPER_ADMIN,
            is_verified=True
        )
        db.add(super_admin)
        db.commit()
        logger.info("Super Admin seeded: superadmin@sthxtechnologies.com / superadmin123")
    else:
        super_admin.failed_login_attempts = 0
        super_admin.lockout_until = None
        super_admin.is_verified = True
        db.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_auth(request: Request, db: Session = Depends(get_db)):
    authorization: str = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication credentials missing")
    token = authorization.split(" ")[1]
    try:
        payload = decode_access_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token claims")
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

def require_admin(current_user = Depends(get_current_user_auth)):
    if current_user.role not in [ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_GYM_OWNER]:
        raise HTTPException(status_code=403, detail="Access forbidden: Admin role required.")
    return current_user

def require_super_admin(current_user = Depends(get_current_user_auth)):
    if current_user.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access forbidden: Super Admin role required.")
    return current_user

def require_gym_owner(current_user = Depends(get_current_user_auth)):
    if current_user.role not in [ROLE_GYM_OWNER, ROLE_ADMIN, ROLE_SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Access forbidden: Gym Owner role required.")
    return current_user

@app.get("/api/me")
def get_me(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    """Returns the full authenticated user profile from the database.
    Called on every page load by the frontend to ensure the UI reflects the true DB state."""
    # Re-query fresh from DB (current_user may be from cache)
    fresh = db.query(User).filter(User.id == current_user.id).first()
    if not fresh:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": fresh.id,
        "name": fresh.name,
        "username": fresh.username,
        "email": fresh.email,
        "role": fresh.role,
        "gym_name": fresh.gym_name or "",
        "gym_id": fresh.gym_id,
        "phone": fresh.phone,
        "cnic": fresh.cnic,
        "avatar_url": fresh.avatar_url,
        "is_verified": fresh.is_verified,
        "last_login_at": fresh.last_login_at.isoformat() if fresh.last_login_at else None,
    }

# ==========================================================================
# Appearance Preferences Endpoints
# ==========================================================================
from src.repositories.preferences_repo import PreferencesRepo

class PreferencesUpdateRequest(BaseModel):
    theme_name: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    hover_color: Optional[str] = None
    border_color: Optional[str] = None
    background_opacity: Optional[int] = None
    background_blur: Optional[int] = None
    background_overlay: Optional[str] = None
    dark_mode: Optional[str] = None
    card_style: Optional[str] = None
    sidebar_style: Optional[str] = None
    layout_type: Optional[str] = None

@app.get("/api/preferences")
def get_preferences(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    """Return current user's appearance preferences (or defaults if none saved)."""
    return PreferencesRepo.get(db, current_user.id)

@app.put("/api/preferences")
def save_preferences(req: PreferencesUpdateRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    """Upsert appearance preferences for the current user."""
    data = {k: v for k, v in req.dict().items() if v is not None}
    result = PreferencesRepo.save(db, current_user.id, **data)
    return {"message": "Appearance settings saved!", "preferences": result}

@app.post("/api/preferences/background")
async def upload_background(
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_auth)
):
    """Upload a custom background image for the current user's dashboard."""
    ext = os.path.splitext(file.filename)[1].lower().strip(".")
    if ext not in {"jpg", "jpeg", "png", "webp"}:
        raise HTTPException(status_code=400, detail="Only JPG, JPEG, PNG, WEBP images are allowed.")
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds 5 MB limit.")
    bg_dir = os.path.join("static", "uploads", "backgrounds")
    os.makedirs(bg_dir, exist_ok=True)
    filename = f"bg_{current_user.id}_{int(datetime.utcnow().timestamp())}.{ext}"
    with open(os.path.join(bg_dir, filename), "wb") as f:
        f.write(contents)
    bg_url = f"/static/uploads/backgrounds/{filename}"
    result = PreferencesRepo.save(db, current_user.id, background_image=bg_url)
    return {"message": "Background uploaded!", "preferences": result}

@app.delete("/api/preferences/background")
def remove_background(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    """Remove the custom background for the current user."""
    result = PreferencesRepo.remove_background(db, current_user.id)
    return {"message": "Background removed.", "preferences": result}

@app.post("/api/preferences/reset")
def reset_preferences(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    """Reset all appearance preferences to factory defaults."""
    result = PreferencesRepo.reset_to_defaults(db, current_user.id)
    return {"message": "Preferences reset to defaults.", "preferences": result}

# ==========================================================================
# Revenue Analytics API Endpoint (Super Admin)
# ==========================================================================

@app.get("/api/revenue/analytics")
def revenue_analytics(
    filter_type: str = "last_30_days",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    """Return platform-wide revenue analytics with optional filters, search and pagination.
    Uses RevenueService.get_platform_revenue_analytics to avoid duplicating business logic.
    """
    try:
        result = RevenueService.get_platform_revenue_analytics(db=db, filter_type=filter_type, start_date=start_date, end_date=end_date, search=search, page=page, limit=limit)
        return result
    except Exception as e:
        logger.error(f"/api/revenue/analytics error: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute revenue analytics")

# ==========================================================================
# Auth Pydantic Schemas & Endpoints (with Rate Limiting & Refresh Tokens)
# ==========================================================================

class LoginRequest(BaseModel):
    email: str
    password: str
    device_id: Optional[str] = "default"

class RegisterRequest(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    gym_name: Optional[str] = ""
    email: str
    password: str
    confirm_password: Optional[str] = None
    role: Optional[str] = "receptionist"

class VerifyEmailRequest(BaseModel):
    email: str
    otp: Optional[str] = None
    code: Optional[str] = None

class ResendOTPRequest(BaseModel):
    email: str

class ForgotPasswordRequest(BaseModel):
    email: str

class VerifyResetOTPRequest(BaseModel):
    email: str
    otp: Optional[str] = None
    code: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    token: Optional[str] = None
    reset_token: Optional[str] = None
    email: Optional[str] = None
    new_password: str
    confirm_password: Optional[str] = None


class SuperAdminForgotPasswordRequest(BaseModel):
    email: str

class SuperAdminVerifyOTPRequest(BaseModel):
    email: str
    code: str

class SuperAdminResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str
    confirm_password: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str
    device_id: Optional[str] = "default"

class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None

@app.options("/api/auth/login")
@app.options("/auth/login")
def options_login():
    return Response(status_code=200)

@app.post("/api/auth/login", dependencies=[Depends(limit_auth_requests)])
@app.post("/auth/login", dependencies=[Depends(limit_auth_requests)])
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):

    user_agent = request.headers.get("User-Agent", "Unknown")
    client_ip = request.client.host if request.client else "127.0.0.1"
    device_id = req.device_id or "default"
    result = AuthService.login(
        db=db,
        email=req.email,
        password=req.password,
        device_id=device_id,
        ip_address=client_ip,
        user_agent=user_agent
    )
    AuditService.log_action(db, result["user"]["id"], result["user"]["name"], "USER_LOGIN", f"User logged in: {req.email} (Device: {device_id})", client_ip)
    return result


@app.post("/api/auth/register", dependencies=[Depends(limit_auth_requests)])
@app.post("/auth/register", dependencies=[Depends(limit_auth_requests)])
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    name = req.full_name or req.name or "User"
    confirm_pwd = req.confirm_password if (req.confirm_password and req.confirm_password.strip()) else req.password
    try:
        result = AuthService.register_user(db, name=name, email=req.email, password=req.password, confirm_password=confirm_pwd, gym_name=req.gym_name or "", role=req.role or "receptionist")
        AuditService.log_action(db, None, name, "USER_REGISTER_INITIATED", f"Registration initiated for: {req.email}", request.client.host)
        return result
    except HTTPException as e:
        logger.warning(f"[REGISTRATION 400] Email: {req.email} | Detail: {e.detail}")
        raise e

@app.post("/api/auth/register/request-code", dependencies=[Depends(limit_auth_requests)])
def request_registration_code(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    return register(req, request, db)

@app.post("/api/auth/verify-email", dependencies=[Depends(limit_auth_requests)])
@app.post("/auth/verify-email", dependencies=[Depends(limit_auth_requests)])
def verify_email(req: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    code = req.otp or req.code or ""
    result = AuthService.verify_email(db, req.email, code)
    if "user" in result:
        AuditService.log_action(db, result["user"]["id"], result["user"]["name"], "USER_VERIFIED", f"Email verified for: {req.email}", request.client.host)
    return result

@app.post("/api/auth/register/verify", dependencies=[Depends(limit_auth_requests)])
def verify_and_register(req: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    return verify_email(req, request, db)

@app.post("/api/auth/resend-verification", dependencies=[Depends(limit_auth_requests)])
@app.post("/auth/resend-verification", dependencies=[Depends(limit_auth_requests)])
def resend_verification(req: ResendOTPRequest, db: Session = Depends(get_db)):
    return AuthService.resend_verification(db, req.email)

@app.post("/api/auth/register/resend-code", dependencies=[Depends(limit_auth_requests)])
def resend_verification_code(req: ResendOTPRequest, db: Session = Depends(get_db)):
    return resend_verification(req, db)

@app.post("/api/auth/forgot-password", dependencies=[Depends(limit_auth_requests)])
@app.post("/auth/forgot-password", dependencies=[Depends(limit_auth_requests)])
def forgot_password(req: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    base_url = "https://gym-portal-self.vercel.app"
    if request.headers.get("origin"):
        base_url = request.headers.get("origin").rstrip("/")
    elif request.base_url:
        base_url = str(request.base_url).rstrip("/")
    return AuthService.forgot_password(db, req.email, base_url)

@app.post("/api/auth/reset-password", dependencies=[Depends(limit_auth_requests)])
@app.post("/auth/reset-password", dependencies=[Depends(limit_auth_requests)])
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    token = req.token or req.reset_token or ""
    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required.")
    confirm_pwd = req.confirm_password if req.confirm_password else req.new_password
    return AuthService.reset_password_with_token(db, token, req.new_password, confirm_pwd)


@app.post("/api/super-admin/auth/forgot-password", dependencies=[Depends(limit_auth_requests)])
def super_admin_forgot_password(req: SuperAdminForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip_address = request.client.host if request.client else "127.0.0.1"
    return AuthService.super_admin_forgot_password(db, req.email, ip_address)

@app.post("/api/super-admin/auth/verify-reset-otp", dependencies=[Depends(limit_auth_requests)])
def super_admin_verify_reset_otp(req: SuperAdminVerifyOTPRequest, db: Session = Depends(get_db)):
    return AuthService.super_admin_verify_reset_otp(db, req.email, req.code)

@app.post("/api/super-admin/auth/reset-password", dependencies=[Depends(limit_auth_requests)])
def super_admin_reset_password(req: SuperAdminResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip_address = request.client.host if request.client else "127.0.0.1"
    return AuthService.super_admin_reset_password(db, req.reset_token, req.new_password, req.confirm_password, ip_address)

@app.post("/api/auth/refresh")
@app.post("/auth/refresh")
def refresh_token(req: RefreshTokenRequest, request: Request, db: Session = Depends(get_db)):
    user_agent = request.headers.get("User-Agent", "Unknown")
    device_id = req.device_id or "default"
    return AuthService.refresh_access_token(
        db=db,
        raw_refresh_token=req.refresh_token,
        device_id=device_id,
        user_agent=user_agent
    )

@app.post("/api/auth/logout")
@app.post("/auth/logout")
def logout_current_device(req: LogoutRequest, db: Session = Depends(get_db)):
    if req.refresh_token:
        return AuthService.logout_current_device(db, req.refresh_token)
    return {"message": "Logged out successfully from current device."}

@app.post("/api/auth/logout-all")
@app.post("/auth/logout-all")
def logout_all_devices(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AuthService.logout_all_devices(db, current_user.id)

# ==========================================================================
# Dashboard Statistics Endpoint
# ==========================================================================

@app.get("/api/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    today_str = date.today().isoformat()
    start_of_month = date.today().replace(day=1).isoformat()
    
    total_members = db.query(Member).filter(Member.deleted_at.is_(None), Member.user_id == current_user.id).count()
    active_members = db.query(Member).filter(Member.deleted_at.is_(None), Member.user_id == current_user.id, Member.status == STATUS_ACTIVE).count()
    pending_fee_members = db.query(Member).filter(Member.deleted_at.is_(None), Member.user_id == current_user.id, Member.status == STATUS_PENDING).count()
    expired_memberships = db.query(Member).filter(Member.deleted_at.is_(None), Member.user_id == current_user.id, Member.status == STATUS_EXPIRED).count()

    today_new = db.query(Member).filter(
        Member.deleted_at.is_(None),
        Member.user_id == current_user.id,
        Member.joining_date == today_str
    ).count()
    
    revenue_sum = db.query(Payment).join(Member).filter(Payment.payment_date >= start_of_month, Member.user_id == current_user.id).all()
    monthly_revenue = sum(p.amount for p in revenue_sum)

    target_expiry = date.today() + timedelta(days=3)
    target_expiry_str = target_expiry.isoformat()
    expiring_soon_memberships = db.query(MemberMembership).join(Member).filter(
        Member.user_id == current_user.id,
        Member.deleted_at.is_(None),
        MemberMembership.status == STATUS_ACTIVE,
        MemberMembership.expiry_date <= target_expiry_str,
        MemberMembership.expiry_date >= today_str
    ).all()
    
    expiring_soon_list = [{
        "member_id": ms.member.id,
        "full_name": ms.member.full_name,
        "plan_name": ms.plan.plan_name,
        "expiry_date": ms.expiry_date
    } for ms in expiring_soon_memberships]

    recent_members = db.query(Member).filter(Member.deleted_at.is_(None), Member.user_id == current_user.id).order_by(Member.id.desc()).limit(5).all()
    recent_payments = db.query(Payment).join(Member).filter(Member.user_id == current_user.id).order_by(Payment.id.desc()).limit(5).all()
    
    activities = []
    for m in recent_members:
        activities.append({
            "type": "registration",
            "message": f"New member registered: {m.full_name}",
            "timestamp": m.created_at.isoformat()
        })
    for p in recent_payments:
        activities.append({
            "type": "payment",
            "message": f"Payment of PKR {p.amount:,.0f} received from {p.member.full_name if p.member else 'Unknown'}",
            "timestamp": p.payment_date + "T12:00:00"
        })
    
    activities.sort(key=lambda x: x["timestamp"], reverse=True)
    
    plans = db.query(MembershipPlan).all()
    plan_stats = []
    for pl in plans:
        count = db.query(MemberMembership).join(Member).filter(
            Member.user_id == current_user.id,
            Member.deleted_at.is_(None),
            MemberMembership.plan_id == pl.id,
            MemberMembership.status == STATUS_ACTIVE
        ).count()
        plan_stats.append({"plan_name": pl.plan_name, "count": count})

    revenue_summary = RevenueService.get_revenue_history_summary(db, current_user.id)
    attendance_stats = AttendanceRepo.get_dashboard_attendance_stats(db, current_user.id)

    return {
        "total_members": total_members,
        "active_members": active_members,
        "pending_fee_members": pending_fee_members,
        "expired_memberships": expired_memberships,
        "today_new_members": today_new,
        "monthly_revenue": revenue_summary["current_month_live_revenue"],
        "previous_month_revenue": revenue_summary["previous_month_revenue"],
        "previous_month_name": revenue_summary["previous_month_name"],
        "revenue_history": revenue_summary["rolling_history"],
        "attendance_stats": attendance_stats,
        "recent_activities": activities[:8],
        "expiring_soon": expiring_soon_list[:5],
        "plan_stats": plan_stats
    }

@app.get("/api/revenue/history")
def get_revenue_history(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return RevenueService.get_revenue_history_summary(db, current_user.id)

@app.post("/api/admin/trigger-revenue-archival")
def trigger_revenue_archival(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    RevenueService.run_monthly_archival_job(db)
    return {"message": "Monthly revenue archival job executed successfully."}

class CheckInRequest(BaseModel):
    member_id: int
    allow_override: bool = False

class CheckOutRequest(BaseModel):
    member_id: int

# ==========================================================================
# Attendance Management Endpoints
# ==========================================================================

@app.get("/api/attendance/stats")
def get_attendance_stats(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AttendanceRepo.get_dashboard_attendance_stats(db, current_user.id)

@app.get("/api/attendance/search")
def search_attendance_members(q: str = "", db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AttendanceService.search_members_for_receptionist(db, current_user.id, q)

@app.post("/api/attendance/check-in")
def attendance_check_in(req: CheckInRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AttendanceService.check_in(db, req.member_id, current_user.id, current_user.id, current_user.name, req.allow_override)

@app.post("/api/attendance/check-out")
def attendance_check_out(req: CheckOutRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AttendanceService.check_out(db, req.member_id, current_user.id, current_user.id, current_user.name)

@app.get("/api/attendance/history")
def get_attendance_history(
    preset: Optional[str] = None,
    custom_from: Optional[str] = None,
    custom_to: Optional[str] = None,
    member_id: Optional[int] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_auth)
):
    return AttendanceService.get_history_with_presets(db, current_user.id, preset, custom_from, custom_to, member_id, q)

@app.get("/api/attendance/member/{member_id}")
def get_member_attendance(member_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AttendanceService.get_member_profile_attendance(db, member_id)

@app.get("/api/attendance/reports")
def get_attendance_reports(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AttendanceService.generate_attendance_reports(db, current_user.id)

@app.get("/api/attendance/reports/export-csv")
def export_attendance_csv(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    csv_content = AttendanceService.export_csv_report(db, current_user.id)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=attendance_report.csv"}
    )

# ==========================================================================
# Membership Plans Management Endpoints (Secured by RBAC require_admin)
# ==========================================================================

class PlanCreateEdit(BaseModel):
    plan_name: str
    price: float
    registration_fee: float = 1000.0
    duration_days: int = 30
    is_active: bool = True

@app.get("/api/plans")
def get_plans(db: Session = Depends(get_db)):
    plans = db.query(MembershipPlan).all()
    return [{
        "id": p.id,
        "plan_name": p.plan_name,
        "price": p.price,
        "registration_fee": p.registration_fee,
        "duration_days": p.duration_days,
        "is_active": p.is_active
    } for p in plans]

@app.post("/api/plans")
def create_plan(plan: PlanCreateEdit, request: Request, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    existing = db.query(MembershipPlan).filter(MembershipPlan.plan_name == plan.plan_name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Plan name already exists.")
        
    new_plan = MembershipPlan(**plan.dict())
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)
    
    AuditService.log_action(db, current_user.id, current_user.name, "PLAN_CREATED", f"Created plan: {new_plan.plan_name} at PKR {new_plan.price}", request.client.host)
    return new_plan

@app.put("/api/plans/{plan_id}")
def update_plan(plan_id: int, plan: PlanCreateEdit, request: Request, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    p = db.query(MembershipPlan).filter(MembershipPlan.id == plan_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plan not found.")
        
    p.plan_name = plan.plan_name
    p.price = plan.price
    p.registration_fee = plan.registration_fee
    p.duration_days = plan.duration_days
    p.is_active = plan.is_active
    db.commit()
    
    AuditService.log_action(db, current_user.id, current_user.name, "PLAN_UPDATED", f"Updated plan ID {p.id}: {p.plan_name}", request.client.host)
    return p

# ==========================================================================
# Member Management Endpoints (With Pagination Support)
# ==========================================================================

class MemberCreate(BaseModel):
    full_name: str
    email: str
    whatsapp_number: str
    plan_id: Optional[int] = None
    notes: Optional[str] = None
    include_registration_fee: bool = True
    payment_method: str = "Cash"

class MemberEdit(BaseModel):
    full_name: str
    email: str
    whatsapp_number: str
    status: Optional[str] = None
    notes: Optional[str] = None

@app.get("/api/members")
def list_members(
    search: Optional[str] = None, 
    status_filter: Optional[str] = None, 
    archived: bool = False, 
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user_auth)
):
    members, total = MemberRepo.list_paginated(db, current_user.id, search, status_filter, archived, page, limit)
    
    result = []
    for m in members:
        active_membership = db.query(MemberMembership).filter(
            MemberMembership.member_id == m.id,
            MemberMembership.status == STATUS_ACTIVE
        ).order_by(MemberMembership.id.desc()).first()
        
        if not active_membership:
            active_membership = db.query(MemberMembership).filter(
                MemberMembership.member_id == m.id
            ).order_by(MemberMembership.id.desc()).first()

        days_left_in_archive = 60
        if m.deleted_at:
            deleted_date = datetime.fromisoformat(m.deleted_at).date()
            elapsed = (date.today() - deleted_date).days
            days_left_in_archive = max(0, 60 - elapsed)

        result.append({
            "id": m.id,
            "member_code": m.member_code,
            "full_name": m.full_name,
            "email": m.email,
            "whatsapp_number": m.whatsapp_number,
            "joining_date": m.joining_date,
            "registration_date": m.registration_date,
            "status": m.status,
            "deleted_at": m.deleted_at,
            "days_left_in_archive": days_left_in_archive,
            "notes": m.notes,
            "plan_name": active_membership.plan.plan_name if active_membership and active_membership.plan else "None",
            "due_date": active_membership.next_due_date if active_membership else "None",
            "expiry_date": active_membership.expiry_date if active_membership else "None"
        })
        
    return result

@app.post("/api/members")
def create_member(member: MemberCreate, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
    if idempotency_key:
        cached = IdempotencyService.get_existing_response(db, idempotency_key, current_user.id, request.url.path)
        if cached:
            status_code, body = cached
            return JSONResponse(status_code=status_code, content=body)

    try:
        latest = apply_for_update(db.query(Member).order_by(Member.id.desc()), db).first()
        next_id = (latest.id + 1) if latest else 1
        year = date.today().year
        member_code = f"FM-{year}-{next_id:04d}"
        
        today_str = date.today().isoformat()
        
        new_member = Member(
            user_id=current_user.id,
            member_code=member_code,
            full_name=member.full_name,
            email=member.email,
            whatsapp_number=member.whatsapp_number,
            joining_date=today_str,
            registration_date=today_str,
            status=STATUS_PENDING,
            notes=member.notes
        )
        
        db.add(new_member)
        db.flush()

        if member.plan_id:
            plan = db.query(MembershipPlan).filter(MembershipPlan.id == member.plan_id).first()
            if plan:
                today = date.today()
                expiry = today + timedelta(days=plan.duration_days)
                new_ms = MemberMembership(
                    member_id=new_member.id,
                    plan_id=plan.id,
                    start_date=today.isoformat(),
                    expiry_date=expiry.isoformat(),
                    next_due_date=expiry.isoformat(),
                    status=STATUS_ACTIVE
                )
                db.add(new_ms)
                db.flush()  # get new_ms.id before creating Payment
                new_member.status = STATUS_ACTIVE

                # Auto-record initial payment at registration
                reg_fee = plan.registration_fee if member.include_registration_fee else 0.0
                latest_p = apply_for_update(db.query(Payment).order_by(Payment.id.desc()), db).first()
                last_id = latest_p.id if latest_p else 0
                receipt_num = PaymentService.generate_receipt_number(last_id, today_str)
                initial_payment = Payment(
                    member_id=new_member.id,
                    membership_id=new_ms.id,
                    amount=plan.price,
                    registration_fee=reg_fee,
                    payment_method=member.payment_method,
                    payment_date=today_str,
                    receipt_number=receipt_num,
                    payment_type="Registration",
                    status="Paid"
                )
                db.add(initial_payment)
                db.flush()
                RevenueService.record_payment_in_platform_history(db, initial_payment.payment_date, initial_payment.amount)
        
        db.add(Notification(
            user_id=current_user.id,
            member_id=new_member.id,
            title="New Member Registered",
            message=f"🔔 New Member Registered: {new_member.full_name} has joined the gym.",
            type="New Member"
        ))
        
        # Add AuditLog entry to transaction (deferred commit)
        AuditService.log_action(
            db, 
            current_user.id, 
            current_user.name, 
            "MEMBER_CREATED", 
            f"Registered member: {new_member.full_name} ({new_member.member_code})", 
            request.client.host,
            commit=False
        )

        # Single atomic commit for Member + Membership + Payment + Notification + AuditLog
        db.commit()
        payment_obj = db.query(Payment).filter(Payment.member_id == new_member.id).order_by(Payment.id.desc()).first()
        
        response_payload = {
            "id": new_member.id,
            "member_code": new_member.member_code,
            "full_name": new_member.full_name,
            "email": new_member.email,
            "whatsapp_number": new_member.whatsapp_number,
            "joining_date": new_member.joining_date,
            "registration_date": new_member.registration_date,
            "status": new_member.status,
            "payment_id": payment_obj.id if payment_obj else None,
            "receipt_number": payment_obj.receipt_number if payment_obj else None
        }

        if idempotency_key:
            IdempotencyService.save_response(db, idempotency_key, current_user.id, request.url.path, member.dict(), 200, response_payload)

        return response_payload
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create member: {str(e)}")

@app.get("/api/members/{member_id}")
def view_member(member_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    m = db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
        
    memberships = db.query(MemberMembership).filter(MemberMembership.member_id == m.id).order_by(MemberMembership.id.desc()).all()
    payments = db.query(Payment).filter(Payment.member_id == m.id).order_by(Payment.id.desc()).all()
    
    active_membership = None
    for ms in memberships:
        if ms.status == STATUS_ACTIVE:
            active_membership = ms
            break
    if not active_membership and len(memberships) > 0:
        active_membership = memberships[0]
        
    payment_count = len(payments)
    next_due = active_membership.next_due_date if active_membership else None
    reg_fee = PaymentService.calculate_registration_fee(m.status, payment_count, next_due)
    
    membership_history = [{
        "id": ms.id,
        "plan_name": ms.plan.plan_name,
        "price": ms.plan.price,
        "start_date": ms.start_date,
        "expiry_date": ms.expiry_date,
        "next_due_date": ms.next_due_date,
        "status": ms.status
    } for ms in memberships]
    
    payment_history = [{
        "id": p.id,
        "amount": float(p.amount),
        "registration_fee": float(p.registration_fee or 0),
        "payment_method": p.payment_method,
        "payment_date": p.payment_date,
        "receipt_number": p.receipt_number,
        "payment_type": p.payment_type or "Renewal",
        "status": p.status,
        "plan_name": p.membership.plan.plan_name if (p.membership and p.membership.plan) else "Membership Plan"
    } for p in payments]

    return {
        "id": m.id,
        "member_code": m.member_code,
        "full_name": m.full_name,
        "email": m.email,
        "whatsapp_number": m.whatsapp_number,
        "joining_date": m.joining_date,
        "registration_date": m.registration_date,
        "status": m.status,
        "notes": m.notes,
        "registration_fee_required": reg_fee > 0,
        "registration_fee_amount": reg_fee,
        "current_membership": {
            "plan_id": active_membership.plan_id if active_membership else None,
            "plan_name": active_membership.plan.plan_name if active_membership else "None",
            "price": active_membership.plan.price if active_membership else 0,
            "start_date": active_membership.start_date if active_membership else None,
            "expiry_date": active_membership.expiry_date if active_membership else None,
            "next_due_date": active_membership.next_due_date if active_membership else None,
            "status": active_membership.status if active_membership else "None"
        } if active_membership else None,
        "memberships": membership_history,
        "payments": payment_history
    }

@app.put("/api/members/{member_id}")
def edit_member(member_id: int, data: MemberEdit, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    m = db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id, Member.deleted_at.is_(None)).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
        
    m.full_name = data.full_name
    m.email = data.email
    m.whatsapp_number = data.whatsapp_number
    if data.status:
        m.status = data.status
    if data.notes:
        m.notes = data.notes
    m.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(m)
    
    AuditService.log_action(db, current_user.id, current_user.name, "MEMBER_EDITED", f"Edited member profile: {m.full_name}", request.client.host)
    return m

@app.delete("/api/members/{member_id}")
def archive_member(member_id: int, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    m = db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id, Member.deleted_at.is_(None)).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
        
    m.deleted_at = datetime.now().isoformat()
    m.status = STATUS_ARCHIVED
    
    active_m = db.query(MemberMembership).filter(
        MemberMembership.member_id == m.id,
        MemberMembership.status == STATUS_ACTIVE
    ).all()
    for ms in active_m:
        ms.status = STATUS_EXPIRED
        
    db.commit()
    AuditService.log_action(db, current_user.id, current_user.name, "MEMBER_ARCHIVED", f"Soft-deleted member to archive: {m.full_name}", request.client.host)
    return {"message": "Member archived successfully for 60 days.", "member_id": member_id}

@app.post("/api/members/{member_id}/restore")
def restore_member(member_id: int, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    m = db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id, Member.deleted_at.isnot(None)).first()
    if not m:
        raise HTTPException(status_code=404, detail="Archived member not found")
        
    m.deleted_at = None
    
    latest_membership = db.query(MemberMembership).filter(
        MemberMembership.member_id == m.id
    ).order_by(MemberMembership.id.desc()).first()
    
    if latest_membership:
        today_str = date.today().isoformat()
        if latest_membership.expiry_date >= today_str:
            latest_membership.status = STATUS_ACTIVE
            m.status = STATUS_ACTIVE
        else:
            latest_membership.status = STATUS_EXPIRED
            m.status = STATUS_EXPIRED
    else:
        m.status = STATUS_PENDING
        
    m.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(m)
    
    AuditService.log_action(db, current_user.id, current_user.name, "MEMBER_RESTORED", f"Restored member from archive: {m.full_name}", request.client.host)
    return {"message": "Member restored successfully.", "member_id": member_id}

@app.delete("/api/members/{member_id}/permanent")
def delete_member_permanent(member_id: int, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    m = db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
        
    name = m.full_name
    code = m.member_code
    db.delete(m)
    db.commit()
    AuditService.log_action(db, current_user.id, current_user.name, "MEMBER_PERMANENTLY_DELETED", f"Permanently deleted member: {name} ({code})", request.client.host)
    return {"message": f"Member '{name}' permanently deleted.", "member_id": member_id}

# ==========================================================================
# Membership Assignment & Transactional Payment Endpoints
# ==========================================================================

class AssignmentRequest(BaseModel):
    plan_id: int

class PaymentRecordRequest(BaseModel):
    amount: float
    payment_method: str
    payment_date: str  # YYYY-MM-DD
    registration_fee: Optional[float] = 0.0

@app.post("/api/members/{member_id}/assign-plan")
def assign_plan(member_id: int, req: AssignmentRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
    if idempotency_key:
        cached = IdempotencyService.get_existing_response(db, idempotency_key, current_user.id, request.url.path)
        if cached:
            status_code, body = cached
            return JSONResponse(status_code=status_code, content=body)

    m = apply_for_update(db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id, Member.deleted_at.is_(None)), db).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
        
    plan = db.query(MembershipPlan).filter(MembershipPlan.id == req.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Membership plan not found")
        
    active_memberships = db.query(MemberMembership).filter(
        MemberMembership.member_id == m.id,
        MemberMembership.status == STATUS_ACTIVE
    ).all()
    for ms in active_memberships:
        ms.status = STATUS_EXPIRED
        
    today = date.today()
    expiry = today + timedelta(days=plan.duration_days)
    
    new_ms = MemberMembership(
        member_id=m.id,
        plan_id=plan.id,
        start_date=today.isoformat(),
        expiry_date=expiry.isoformat(),
        next_due_date=today.isoformat(),
        status=STATUS_PENDING
    )
    
    db.add(new_ms)
    
    # Add AuditLog entry to transaction (deferred commit)
    AuditService.log_action(
        db, 
        current_user.id, 
        current_user.name, 
        "PLAN_ASSIGNED", 
        f"Assigned {plan.plan_name} to {m.full_name}", 
        request.client.host,
        commit=False
    )
    
    db.commit()
    db.refresh(new_ms)

    response_payload = {
        "message": "Plan assigned successfully. Membership pending payment.",
        "membership": {
            "id": new_ms.id,
            "plan_name": plan.plan_name,
            "price": plan.price,
            "start_date": new_ms.start_date,
            "expiry_date": new_ms.expiry_date,
            "next_due_date": new_ms.next_due_date,
            "status": new_ms.status
        }
    }

    if idempotency_key:
        IdempotencyService.save_response(db, idempotency_key, current_user.id, request.url.path, req.dict(), 200, response_payload)

    return response_payload

@app.post("/api/members/{member_id}/pay")
def record_payment(member_id: int, req: PaymentRecordRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
    if idempotency_key:
        cached = IdempotencyService.get_existing_response(db, idempotency_key, current_user.id, request.url.path)
        if cached:
            status_code, body = cached
            return JSONResponse(status_code=status_code, content=body)

    m = apply_for_update(db.query(Member).filter(Member.id == member_id, Member.user_id == current_user.id, Member.deleted_at.is_(None)), db).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")

    eligible, msg = StatusService.validate_payment_eligibility(m)
    if not eligible:
        raise HTTPException(status_code=400, detail=msg)
        
    ms = apply_for_update(db.query(MemberMembership).filter(
        MemberMembership.member_id == m.id
    ).order_by(MemberMembership.id.desc()), db).first()
    
    if not ms:
        raise HTTPException(status_code=400, detail="No membership assigned to this member. Please assign a plan first.")

    reg_fee_amount = float(req.registration_fee) if (req.registration_fee is not None and float(req.registration_fee) >= 0) else 0.0
    
    # Query latest global payment ID with row lock to guarantee receipt number uniqueness
    latest_p = apply_for_update(db.query(Payment).order_by(Payment.id.desc()), db).first()
    last_id = latest_p.id if latest_p else 0
    receipt_number = PaymentService.generate_receipt_number(last_id, req.payment_date)
    
    try:
        new_payment = Payment(
            member_id=m.id,
            membership_id=ms.id,
            amount=req.amount,
            registration_fee=reg_fee_amount,
            payment_method=req.payment_method,
            payment_date=req.payment_date,
            receipt_number=receipt_number,
            payment_type="Renewal" if m.status == STATUS_ACTIVE else "Fee Payment",
            status="Paid"
        )
        db.add(new_payment)
        db.flush()
        RevenueService.record_payment_in_platform_history(db, new_payment.payment_date, new_payment.amount + reg_fee_amount, commit=False)
        
        ms.status = STATUS_ACTIVE
        payment_date_obj = datetime.strptime(req.payment_date, "%Y-%m-%d").date()
        plan = ms.plan
        duration = plan.duration_days if plan else 30
        
        # Calculate new expiry date: extend from current expiry if in the future, else from payment date
        current_expiry_date = None
        if ms.expiry_date and ms.expiry_date != "None":
            try:
                current_expiry_date = datetime.strptime(ms.expiry_date.split("T")[0], "%Y-%m-%d").date()
            except Exception:
                current_expiry_date = None

        if current_expiry_date and current_expiry_date > payment_date_obj:
            new_expiry = current_expiry_date + timedelta(days=duration)
        else:
            new_expiry = payment_date_obj + timedelta(days=duration)

        ms.start_date = req.payment_date
        ms.expiry_date = new_expiry.isoformat()
        ms.next_due_date = new_expiry.isoformat()
        
        m.status = STATUS_ACTIVE
        m.updated_at = datetime.utcnow()
        
        pending_notifs = db.query(Notification).filter(
            Notification.member_id == m.id,
            Notification.is_read == False
        ).all()
        for n in pending_notifs:
            n.is_read = True
            
        db.add(Notification(
            user_id=current_user.id,
            member_id=m.id,
            title="Payment Received",
            message=f"✅ Membership payment of PKR {req.amount:,.0f} received successfully from {m.full_name}.",
            type="Payment"
        ))
        
        # Add AuditLog entry to transaction (deferred commit)
        AuditService.log_action(
            db, 
            current_user.id, 
            current_user.name, 
            "PAYMENT_RECORDED", 
            f"Recorded payment PKR {req.amount} ({receipt_number}) for {m.full_name}", 
            request.client.host,
            commit=False
        )

        # Single atomic commit for Payment + Membership Update + Notification + AuditLog
        db.commit()
        db.refresh(new_payment)
        
        response_payload = {
            "message": "Payment recorded successfully.",
            "payment": {
                "id": new_payment.id,
                "amount": new_payment.amount,
                "registration_fee": new_payment.registration_fee,
                "payment_method": new_payment.payment_method,
                "payment_date": new_payment.payment_date,
                "receipt_number": new_payment.receipt_number,
                "status": new_payment.status
            }
        }

        if idempotency_key:
            IdempotencyService.save_response(db, idempotency_key, current_user.id, request.url.path, req.dict(), 200, response_payload)

        return response_payload
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to record payment: {str(e)}")

# ==========================================================================
# Receipt Printing & Plan Change Endpoints
# ==========================================================================

class ChangePlanRequest(BaseModel):
    new_plan_id: int
    payment_method: str = "Cash"
    amount: Optional[float] = None
    registration_fee: Optional[float] = 0.0
    pay_now: bool = False

@app.get("/api/payments/{payment_id}/receipt")
def get_payment_receipt(payment_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return PaymentService.get_receipt_details(db, payment_id, current_user.id)

@app.post("/api/members/{member_id}/change-plan")
def change_member_plan(member_id: int, req: ChangePlanRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return PlanService.change_member_plan(
        db=db,
        member_id=member_id,
        new_plan_id=req.new_plan_id,
        payment_method=req.payment_method,
        amount=req.amount,
        registration_fee=req.registration_fee,
        pay_now=req.pay_now,
        user_id=current_user.id,
        user_name=current_user.name,
        client_ip=request.client.host if request.client else "127.0.0.1"
    )

# ==========================================================================
# Reports & Audit Logs Endpoints (Audit logs secured by require_admin)
# ==========================================================================

@app.get("/api/reports/summary")
def get_report_summary(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return ReportService.get_financial_summary(db, current_user.id)

@app.get("/api/reports/export-members")
def export_members_csv(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    csv_data = ReportService.export_members_csv(db, current_user.id)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=members_export_{date.today().isoformat()}.csv"}
    )

@app.get("/api/audit-logs")
def list_audit_logs(
    page: int = 1, 
    limit: int = 50, 
    user_id: Optional[int] = None, 
    scope: Optional[str] = "me",
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user_auth)
):
    query = db.query(AuditLog)
    
    if current_user.role == "admin":
        if user_id:
            query = query.filter(AuditLog.user_id == user_id)
        elif scope == "me":
            query = query.filter(AuditLog.user_id == current_user.id)
        elif scope == "all":
            pass  # Admin viewing all accounts combined
        else:
            query = query.filter(AuditLog.user_id == current_user.id)
    else:
        # Non-admin accounts only see their own account's activity logs
        query = query.filter(AuditLog.user_id == current_user.id)

    total = query.count()
    offset = (page - 1) * limit
    logs = query.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
    
    users_list = []
    if current_user.role == "admin":
        users = db.query(User).all()
        users_list = [{"id": u.id, "name": u.name, "email": u.email, "role": u.role} for u in users]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "current_user": {
            "id": current_user.id,
            "name": current_user.name,
            "role": current_user.role
        },
        "logs": [{
            "id": l.id,
            "user_id": l.user_id,
            "user_name": l.user_name or "System",
            "action": l.action,
            "details": l.details,
            "ip_address": l.ip_address,
            "created_at": l.created_at.isoformat()
        } for l in logs],
        "users": users_list
    }

# ==========================================================================
# Notification Endpoints
# ==========================================================================

@app.get("/api/notifications")
def list_notifications(page: int = 1, limit: int = 30, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    total = query.count()
    offset = (page - 1) * limit
    notifs = query.order_by(Notification.id.desc()).offset(offset).limit(limit).all()
    return [{
        "id": n.id,
        "member_id": n.member_id,
        "title": n.title,
        "message": n.message,
        "type": n.type,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat()
    } for n in notifs]

@app.post("/api/notifications/read-all")
def read_all_notifications(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    db.query(Notification).filter(Notification.user_id == current_user.id, Notification.is_read == False).update({Notification.is_read: True}, synchronize_session=False)
    db.commit()
    return {"message": "All notifications marked as read."}

@app.post("/api/notifications/{notif_id}/read")
def read_notification(notif_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    n = db.query(Notification).filter(Notification.id == notif_id, Notification.user_id == current_user.id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    db.commit()
    return {"message": "Notification marked as read."}

@app.post("/api/admin/trigger-cron")
async def trigger_cron(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    await run_all_cron_jobs(db)
    return {"message": "Cron jobs triggered manually and executed successfully."}

# ==========================================================================
# Settings Module Endpoints (Profile, Password, Security, Branding, System)
# ==========================================================================

class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: str
    phone: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

class GymBrandingUpdateRequest(BaseModel):
    gym_name: str
    gym_address: str
    gym_phone: str
    gym_email: str
    gym_website: Optional[str] = ""
    gym_footer_text: Optional[str] = ""

class SystemSettingsUpdateRequest(BaseModel):
    currency: str = "PKR"
    timezone: str = "Asia/Karachi"
    date_format: str = "YYYY-MM-DD"
    receipt_prefix: str = "REC"
    default_registration_fee: float = 1000.0
    default_membership_duration: int = 30

@app.get("/api/settings/profile")
def get_profile(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return SettingsService.get_user_profile(db, current_user.id)

@app.put("/api/settings/profile")
def update_profile(data: ProfileUpdateRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return SettingsService.update_user_profile(db, current_user.id, data.name, data.email, data.phone, request.client.host)

@app.post("/api/settings/profile/avatar")
async def upload_avatar(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return await SettingsService.upload_user_avatar(db, current_user.id, file, request.client.host if request else "127.0.0.1")

@app.delete("/api/settings/profile/avatar")
def remove_avatar(request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return SettingsService.remove_user_avatar(db, current_user.id, request.client.host)

@app.post("/api/settings/change-password")
def change_password(data: ChangePasswordRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return SettingsService.change_password(db, current_user.id, data.current_password, data.new_password, data.confirm_password, request.client.host)

@app.get("/api/settings/security")
def get_security_info(db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    user = db.query(User).filter(User.id == current_user.id).first()
    return {
        "last_login_at": user.last_login_at.isoformat() if user and user.last_login_at else None,
        "last_login_ip": user.last_login_ip if user else "127.0.0.1",
        "active_sessions_count": 1,
        "current_device": "Current Web Browser Session"
    }

@app.post("/api/settings/logout-all-devices")
def logout_all_devices(request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    AuditService.log_action(db, current_user.id, current_user.name, "LOGOUT_ALL_DEVICES", "Logged out from all active sessions.", request.client.host)
    return {"message": "Logged out from all devices successfully."}

@app.get("/api/settings/branding")
@app.get("/settings/branding")
def get_gym_branding(db: Session = Depends(get_db)):
    return SettingsService.get_gym_branding(db)

@app.put("/api/settings/branding")
def update_gym_branding(data: GymBrandingUpdateRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return SettingsService.update_gym_branding(db, current_user.id, data.dict(), request.client.host)

@app.post("/api/settings/branding/logo")
async def upload_gym_logo(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return await SettingsService.upload_gym_logo(db, current_user.id, file, request.client.host if request else "127.0.0.1")

@app.delete("/api/settings/branding/logo")
def remove_gym_logo(request: Request, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return SettingsService.remove_gym_logo(db, current_user.id, request.client.host)

class CreateGymRequest(BaseModel):
    owner_name: str
    gym_name: str
    email: str
    phone: str
    cnic: str
    address: Optional[str] = None
    subscription_plan: str
    subscription_expiry: Optional[str] = None
    custom_password: Optional[str] = None
    amount_paid: Optional[float] = 0.0

    @model_validator(mode="before")
    @classmethod
    def auto_calc_expiry(cls, data):
        """Auto-calculate subscription_expiry from plan if not provided."""
        if not data.get("subscription_expiry"):
            from datetime import date, timedelta
            plan = data.get("subscription_plan", "Monthly")
            plan_days = {
                "Monthly": 30, "Quarterly": 90, "6 Months": 180,
                "Yearly": 365, "1 Year": 365, "2 Years": 730
            }
            days = plan_days.get(plan, 30)
            data["subscription_expiry"] = (date.today() + timedelta(days=days)).isoformat()
        return data

class EditGymRequest(BaseModel):
    owner_name: str
    gym_name: str
    phone: str
    cnic: str
    address: Optional[str] = None
    subscription_plan: str
    subscription_expiry: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def auto_calc_edit_expiry(cls, data):
        """Auto-calculate subscription_expiry from plan if not provided."""
        if not data.get("subscription_expiry"):
            from datetime import date, timedelta
            plan = data.get("subscription_plan", "Monthly")
            plan_days = {
                "Monthly": 30, "Quarterly": 90, "6 Months": 180,
                "Yearly": 365, "1 Year": 365, "2 Years": 730
            }
            days = plan_days.get(plan, 30)
            data["subscription_expiry"] = (date.today() + timedelta(days=days)).isoformat()
        return data

class ResetOwnerPasswordRequest(BaseModel):
    new_password: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

# ==========================================================================
# Super Admin Gym Management Endpoints
# ==========================================================================

@app.get("/api/super-admin/stats")
def get_super_admin_stats(months: int = 12, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.get_super_admin_stats(db, months=months)

@app.get("/api/super-admin/revenue-analytics")
def get_super_admin_revenue_analytics(
    filter_type: str = "last_30_days",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return RevenueService.get_platform_revenue_analytics(
        db=db,
        filter_type=filter_type,
        start_date=start_date,
        end_date=end_date,
        search=search,
        page=page,
        limit=limit
    )

@app.get("/api/super-admin/gyms")
def list_gyms(
    search: Optional[str] = None,
    status: Optional[str] = None,
    plan: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return GymService.list_gyms(db, search=search, status_filter=status, plan_filter=plan)

@app.post("/api/super-admin/gyms")
def create_gym(req: CreateGymRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.create_gym(
        db=db,
        admin_user_id=current_user.id,
        admin_user_name=current_user.name,
        owner_name=req.owner_name,
        gym_name=req.gym_name,
        email=req.email,
        phone=req.phone,
        cnic=req.cnic,
        subscription_plan=req.subscription_plan,
        subscription_expiry=req.subscription_expiry,
        address=req.address,
        custom_password=req.custom_password,
        amount_paid=req.amount_paid or 0.0,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.get("/api/super-admin/gyms/{gym_id}")
def get_gym_details(gym_id: int, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.get_gym_details(db, gym_id)

@app.put("/api/super-admin/gyms/{gym_id}")
def update_gym(gym_id: int, req: EditGymRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.update_gym(
        db=db,
        admin_user_id=current_user.id,
        admin_user_name=current_user.name,
        gym_id=gym_id,
        owner_name=req.owner_name,
        gym_name=req.gym_name,
        phone=req.phone,
        cnic=req.cnic,
        subscription_plan=req.subscription_plan,
        subscription_expiry=req.subscription_expiry,
        address=req.address,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.post("/api/super-admin/gyms/{gym_id}/suspend")
def suspend_gym(gym_id: int, request: Request, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.suspend_gym(db, current_user.id, current_user.name, gym_id, request.client.host if request.client else "127.0.0.1")

@app.post("/api/super-admin/gyms/{gym_id}/activate")
def activate_gym(gym_id: int, request: Request, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.activate_gym(db, current_user.id, current_user.name, gym_id, request.client.host if request.client else "127.0.0.1")

@app.post("/api/super-admin/gyms/{gym_id}/reset-password")
def reset_gym_owner_password(gym_id: int, req: ResetOwnerPasswordRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.reset_owner_password(db, current_user.id, current_user.name, gym_id, req.new_password, request.client.host if request.client else "127.0.0.1")

class SendDirectMessageRequest(BaseModel):
    title: str
    message: str

class SendWhatsAppMessageRequest(BaseModel):
    message: Optional[str] = None

class PlatformAnnouncementRequest(BaseModel):
    message: str
    banner_style: Optional[str] = "danger"
    banner_type: Optional[str] = None



@app.post("/api/super-admin/gyms/bulk-message-pending")
def send_bulk_pending_fee_reminders(
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return GymService.send_bulk_pending_fee_reminders(
        db=db,
        admin_user_id=current_user.id,
        admin_user_name=current_user.name,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

class PaySubscriptionRequest(BaseModel):
    subscription_plan: str
    amount_paid: float
    payment_method: Optional[str] = "Cash"
    notes: Optional[str] = None

@app.post("/api/super-admin/gyms/{gym_id}/pay-subscription")
def pay_gym_subscription(
    gym_id: int,
    req: PaySubscriptionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return GymService.pay_subscription_and_change_plan(
        db=db,
        admin_user_id=current_user.id,
        admin_user_name=current_user.name,
        gym_id=gym_id,
        subscription_plan=req.subscription_plan,
        amount_paid=req.amount_paid,
        payment_method=req.payment_method or "Cash",
        notes=req.notes,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.post("/api/super-admin/gyms/{gym_id}/message")
def send_direct_message_to_gym(
    gym_id: int,
    req: SendDirectMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return GymService.send_direct_message(
        db=db,
        admin_user_id=current_user.id,
        admin_user_name=current_user.name,
        gym_id=gym_id,
        title=req.title,
        message=req.message,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.post("/api/super-admin/gyms/{gym_id}/whatsapp")
def generate_whatsapp_link(
    gym_id: int,
    req: SendWhatsAppMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return GymService.generate_whatsapp_link(
        db=db,
        admin_user_id=current_user.id,
        admin_user_name=current_user.name,
        gym_id=gym_id,
        message=req.message,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.get("/api/super-admin/gyms/{gym_id}/messages")
def get_gym_direct_messages(gym_id: int, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return GymService.get_gym_messages(db, gym_id)

@app.post("/api/super-admin/announcements")
def publish_platform_announcement(req: PlatformAnnouncementRequest, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    import json
    from datetime import datetime

    msg_text = req.message.strip()
    if not msg_text:
        raise HTTPException(status_code=400, detail="Announcement message cannot be empty.")

    b_style = req.banner_style or req.banner_type or "danger"
    now_dt = datetime.utcnow()
    now_str = now_dt.strftime("%b %d, %Y %I:%M %p")

    announcement_obj = {
        "id": int(now_dt.timestamp() * 1000),
        "message": msg_text,
        "banner_style": b_style,
        "banner_type": b_style,
        "created_by": current_user.name or "Super Admin",
        "created_at": now_str,
        "published_at": now_str,
        "disabled_at": None,
        "is_published": True,
        "is_active": True
    }

    val = json.dumps(announcement_obj)

    # 1. Save currently active announcement
    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_announcement").first()
    if setting:
        setting.value = val
    else:
        setting = SystemSetting(key="global_announcement", value=val)
        db.add(setting)

    # 2. Append to announcement history log
    history_setting = db.query(SystemSetting).filter(SystemSetting.key == "announcement_history").first()
    history = []
    if history_setting and history_setting.value:
        try:
            history = json.loads(history_setting.value)
        except Exception:
            history = []

    # Mark all previous announcements as unpublished
    for item in history:
        if item.get("is_published") or item.get("is_active"):
            item["is_published"] = False
            item["is_active"] = False
            item["disabled_at"] = now_str

    history.insert(0, announcement_obj)

    if history_setting:
        history_setting.value = json.dumps(history)
    else:
        db.add(SystemSetting(key="announcement_history", value=json.dumps(history)))

    db.commit()
    return {"message": "Platform announcement published successfully!", "announcement": announcement_obj}

@app.post("/api/super-admin/announcements/disable")
def disable_platform_announcement(db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    import json
    from datetime import datetime

    now_dt = datetime.utcnow()
    now_str = now_dt.strftime("%b %d, %Y %I:%M %p")

    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_announcement").first()
    if not setting or not setting.value:
        return {"message": "No active announcement to disable.", "announcement": {"is_published": False, "is_active": False}}

    try:
        ann = json.loads(setting.value)
    except Exception:
        ann = {}

    ann["is_published"] = False
    ann["is_active"] = False
    ann["disabled_at"] = now_str

    setting.value = json.dumps(ann)

    # Update history log
    history_setting = db.query(SystemSetting).filter(SystemSetting.key == "announcement_history").first()
    if history_setting and history_setting.value:
        try:
            history = json.loads(history_setting.value)
            for item in history:
                if item.get("id") == ann.get("id") or item.get("is_published"):
                    item["is_published"] = False
                    item["is_active"] = False
                    item["disabled_at"] = now_str
            history_setting.value = json.dumps(history)
        except Exception:
            pass

    db.commit()
    return {"message": "Platform announcement disabled successfully!", "announcement": ann}

@app.get("/api/announcement")
def get_platform_announcement(response: Response, db: Session = Depends(get_db)):
    import json
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_announcement").first()
    if not setting or not setting.value:
        return {"is_published": False, "is_active": False, "message": "", "banner_style": "danger", "banner_type": "danger"}
    try:
        data = json.loads(setting.value)
        data.setdefault("is_published", data.get("is_active", False))
        data.setdefault("banner_style", data.get("banner_type", "danger"))
        return data
    except Exception:
        return {"is_published": False, "is_active": False, "message": "", "banner_style": "danger", "banner_type": "danger"}

@app.get("/api/super-admin/export/gyms")
def export_gyms_csv(db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Gym Name", "Owner Name", "Email", "Phone", "CNIC", "Plan", "Expiry Date", "Status", "Address"])
    gyms = db.query(Gym).order_by(Gym.id.desc()).all()
    for g in gyms:
        writer.writerow([g.id, g.gym_name, g.owner_name, g.email, g.phone, g.cnic, g.subscription_plan, g.subscription_expiry, g.status, g.address or ""])
    
    filename = f"sthx_gym_tenants_{date.today().isoformat()}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/super-admin/export/revenue")
def export_revenue_csv(db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Year-Month", "Year", "Month", "Total Revenue (PKR)", "Payment Count"])
    summary = RevenueService.get_revenue_history_summary(db, user_id=None, months_limit=24)
    for r in summary["rolling_history"]:
        writer.writerow([r["year_month_str"], r["year"], r["month_name"], r["total_revenue"], r["payment_count"]])
    
    filename = f"sthx_platform_revenue_{date.today().isoformat()}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/super-admin/audit-logs")
def get_super_admin_audit_logs(page: int = 1, limit: int = 100, db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    return AuditService.get_audit_logs(db, page=page, limit=limit)

class SuperAdminProfileUpdateRequest(BaseModel):
    full_name: str
    username: Optional[str] = None
    email: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None
    confirm_password: Optional[str] = None

@app.put("/api/super-admin/profile")
def update_super_admin_profile(
    req: SuperAdminProfileUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    return AuthService.update_super_admin_profile(
        db=db,
        user_id=current_user.id,
        full_name=req.full_name,
        username=req.username,
        email=req.email,
        current_password=req.current_password,
        new_password=req.new_password,
        confirm_password=req.confirm_password,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.post("/api/super-admin/profile/avatar")
async def upload_super_admin_avatar(
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower().strip(".")
    allowed_exts = {"jpg", "jpeg", "png", "webp"}
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image format. Allowed formats: {', '.join(allowed_exts).upper()}."
        )

    # Read content & check size limit (5MB)
    contents = await file.read()
    max_size_bytes = 5 * 1024 * 1024
    if len(contents) > max_size_bytes:
        raise HTTPException(
            status_code=400,
            detail="File size exceeds maximum allowed limit of 5 MB."
        )

    avatars_dir = os.path.join("static", "uploads", "avatars")
    os.makedirs(avatars_dir, exist_ok=True)

    filename = f"avatar_{current_user.id}_{int(datetime.utcnow().timestamp())}.{ext}"
    filepath = os.path.join(avatars_dir, filename)

    with open(filepath, "wb") as f:
        f.write(contents)

    avatar_url = f"/static/uploads/avatars/{filename}"
    current_user.avatar_url = avatar_url
    
    ip_addr = request.client.host if (request and request.client) else "127.0.0.1"
    AuditService.log_action(
        db, current_user.id, current_user.name, "SUPER_ADMIN_AVATAR_UPDATED",
        f"Uploaded profile image: {filename}", ip_addr, commit=False
    )

    db.commit()
    db.refresh(current_user)

    return {
        "message": "Profile image uploaded successfully!",
        "avatar_url": avatar_url,
        "user": {
            "id": current_user.id,
            "name": current_user.name,
            "username": getattr(current_user, "username", None),
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url
        }
    }

@app.post("/api/settings/change-password")
def change_password(req: ChangePasswordRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user_auth)):
    return AuthService.change_password(
        db=db,
        user_id=current_user.id,
        current_password=req.current_password,
        new_password=req.new_password,
        confirm_password=req.confirm_password,
        ip_address=request.client.host if request.client else "127.0.0.1"
    )

@app.get("/api/super-admin/company-logo")
def get_company_logo(db: Session = Depends(get_db), current_user = Depends(require_super_admin)):
    from src.repositories.settings_repo import SettingsRepo
    logo_url = SettingsRepo.get_setting(db, "company_logo_url") or "/static/sthx_technologies_logo.png"
    return {"company_logo_url": logo_url}

@app.post("/api/super-admin/company-logo")
async def upload_company_logo(
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user = Depends(require_super_admin)
):
    ext = os.path.splitext(file.filename)[1].lower().strip(".")
    allowed_exts = {"jpg", "jpeg", "png", "webp"}
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image format. Allowed formats: JPG, JPEG, PNG, WEBP."
        )

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds the 5 MB limit.")

    logos_dir = os.path.join("static", "uploads", "logos")
    os.makedirs(logos_dir, exist_ok=True)

    filename = f"company_logo_{int(datetime.utcnow().timestamp())}.{ext}"
    filepath = os.path.join(logos_dir, filename)
    with open(filepath, "wb") as f:
        f.write(contents)

    logo_url = f"/static/uploads/logos/{filename}"

    from src.repositories.settings_repo import SettingsRepo
    SettingsRepo.set_setting(db, "company_logo_url", logo_url)

    ip_addr = request.client.host if (request and request.client) else "127.0.0.1"
    AuditService.log_action(
        db, current_user.id, current_user.name,
        "COMPANY_LOGO_UPLOADED", f"Uploaded company logo: {filename}", ip_addr
    )

    return {"message": "Company logo uploaded successfully!", "company_logo_url": logo_url}

# Mount static folders
os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def redirect_to_index():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting STHX Technologies Gym App on http://{host}:{port}")
    uvicorn.run("main:app", host=host, port=port, reload=False)

