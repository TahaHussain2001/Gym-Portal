import re
import secrets
import hashlib
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from src.config.app_config import PASSWORD_REGEX
from src.models.db_models import User, Gym, RefreshToken, LoginHistory, OTPCode
from src.repositories.user_repo import UserRepo
from src.repositories.otp_repo import OTPRepo
from src.utils.helpers import hash_password, verify_password, create_access_token, create_refresh_token, decode_access_token
from src.services.email_service import EmailService
from src.services.audit_service import AuditService
from src.constants.business_rules import ROLE_RECEPTIONIST

MAX_OTP_ATTEMPTS = 5
MAX_RESEND_HOURLY = 5
OTP_EXPIRY_MINUTES = 10

class AuthService:
    @staticmethod
    def register_user(
        db: Session, 
        name: str, 
        email: str, 
        password: str, 
        confirm_password: str, 
        gym_name: str = "", 
        role: str = ROLE_RECEPTIONIST
    ) -> dict:
        email = email.lower().strip()
        
        if password != confirm_password:
            raise HTTPException(status_code=400, detail="Passwords do not match.")

        if not re.match(PASSWORD_REGEX, password):
            raise HTTPException(
                status_code=400,
                detail="Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, one number, and one special character."
            )

        try:
            existing_user = UserRepo.get_by_email(db, email)
            if existing_user:
                if existing_user.is_verified:
                    raise HTTPException(status_code=400, detail="A user with this email address already exists. Please log in.")
                else:
                    # User exists but unverified: Update name, gym_name and password
                    existing_user.name = name
                    existing_user.gym_name = gym_name or ""
                    existing_user.password = hash_password(password)
                    existing_user.role = role or ROLE_RECEPTIONIST
                    target_user = existing_user
            else:
                # Create new unverified user
                target_user = User(
                    name=name,
                    email=email,
                    password=hash_password(password),
                    gym_name=gym_name or "",
                    role=role or ROLE_RECEPTIONIST,
                    is_verified=False
                )
                db.add(target_user)
            
            db.flush()

            # Generate secure 6-digit OTP using secrets module
            otp_code = f"{secrets.randbelow(900000) + 100000}"

            # Store OTP in database (deferred commit)
            OTPRepo.create_otp(
                db=db,
                email=email,
                otp=otp_code,
                purpose="EMAIL_VERIFICATION",
                user_id=target_user.id,
                expires_in_minutes=OTP_EXPIRY_MINUTES,
                commit=False
            )

            # Single atomic commit for User + OTP transaction
            db.commit()
            db.refresh(target_user)

            # Send email via Brevo Email API
            EmailService.send_otp_email(email, otp_code, purpose="EMAIL_VERIFICATION")

            return {
                "message": f"Registration initiated. Verification code sent to {email}. Valid for {OTP_EXPIRY_MINUTES} minutes.",
                "email": email
            }
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

    @staticmethod
    def verify_email(db: Session, email: str, otp: str) -> dict:
        email = email.lower().strip()
        user = UserRepo.get_by_email(db, email)
        if not user:
            raise HTTPException(status_code=404, detail="User account not found. Please register first.")

        if user.is_verified:
            return {"message": "Email is already verified. Please log in."}

        active_otp = OTPRepo.get_active_otp(db, email, "EMAIL_VERIFICATION")
        if not active_otp:
            raise HTTPException(status_code=400, detail="No pending verification code found or code has expired. Please request a new code.")

        if datetime.utcnow() > active_otp.expires_at:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Verification code has expired. Please request a new code.")

        if active_otp.attempts >= MAX_OTP_ATTEMPTS:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Maximum verification attempts (5) exceeded. Please request a new code.")

        # Constant-time comparison to prevent timing attacks
        if not secrets.compare_digest(active_otp.otp.strip(), otp.strip()):
            OTPRepo.increment_attempts(db, active_otp)
            remaining = MAX_OTP_ATTEMPTS - active_otp.attempts
            raise HTTPException(status_code=400, detail=f"Invalid verification code. {remaining} attempt(s) remaining.")

        # Verification successful: mark OTP used and user as verified
        OTPRepo.mark_as_used(db, active_otp)
        user.is_verified = True
        db.commit()

        return {
            "message": "Email verified successfully! You can now log in.",
            "user": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "is_verified": user.is_verified
            }
        }

    @staticmethod
    def resend_verification(db: Session, email: str) -> dict:
        email = email.lower().strip()
        user = UserRepo.get_by_email(db, email)
        if not user:
            raise HTTPException(status_code=404, detail="User account not found. Please register first.")

        if user.is_verified:
            raise HTTPException(status_code=400, detail="Email is already verified. Please log in.")

        recent_resends = OTPRepo.get_recent_resends_count_hourly(db, email, "EMAIL_VERIFICATION")
        if recent_resends >= MAX_RESEND_HOURLY:
            raise HTTPException(status_code=400, detail="Maximum resend attempts (5 per hour) exceeded. Please try again later.")

        otp_code = f"{secrets.randbelow(900000) + 100000}"
        OTPRepo.create_otp(
            db=db,
            email=email,
            otp=otp_code,
            purpose="EMAIL_VERIFICATION",
            user_id=user.id,
            expires_in_minutes=OTP_EXPIRY_MINUTES
        )

        EmailService.send_otp_email(email, otp_code, purpose="EMAIL_VERIFICATION")

        return {
            "message": f"Verification code resent to {email}. Code is valid for {OTP_EXPIRY_MINUTES} minutes."
        }

    @staticmethod
    def login(db: Session, email: str, password: str, device_id: str = "default", ip_address: str = "127.0.0.1", user_agent: str = "Unknown") -> dict:
        identifier = email.strip()  # may be email, username, phone, or CNIC
        user = UserRepo.get_by_identifier(db, identifier)

        if not user:
            db.add(LoginHistory(email=identifier, status="FAILED", ip_address=ip_address, user_agent=user_agent))
            db.commit()
            raise HTTPException(status_code=400, detail="Invalid credentials. Please check your email / username / phone / CNIC and password.")

        # Account Suspension Check for Gym Owners / Non-Super-Admins
        if user.role != "super_admin":
            linked_gym = None
            if user.gym_id:
                linked_gym = db.query(Gym).filter(Gym.id == user.gym_id).first()
            if not linked_gym:
                linked_gym = db.query(Gym).filter(Gym.email == user.email).first()

            if linked_gym and linked_gym.status == "Suspended":
                raise HTTPException(
                    status_code=403,
                    detail="Your account has been suspended. Please contact the Super Admin."
                )

        # 1. Account Lockout Check (5 failed attempts -> 15 min lock)
        if user.lockout_until:
            if datetime.utcnow() < user.lockout_until:
                remaining = max(1, int((user.lockout_until - datetime.utcnow()).total_seconds() // 60))
                db.add(LoginHistory(user_id=user.id, email=email, status="LOCKED", ip_address=ip_address, user_agent=user_agent))
                db.commit()
                raise HTTPException(
                    status_code=400, 
                    detail=f"Account locked due to 5 consecutive failed login attempts. Please try again in {remaining} minute(s)."
                )
            else:
                user.failed_login_attempts = 0
                user.lockout_until = None

        # 2. Password Verification
        if not verify_password(password, user.password):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 5:
                user.lockout_until = datetime.utcnow() + timedelta(minutes=15)
                AuditService.log_action(
                    db, 
                    user_id=user.id, 
                    user_name=user.name, 
                    action="ACCOUNT_LOCKED", 
                    details="Account locked for 15 minutes due to 5 consecutive failed login attempts.", 
                    ip_address=ip_address,
                    commit=False
                )
            db.add(LoginHistory(user_id=user.id, email=email, status="FAILED", ip_address=ip_address, user_agent=user_agent))
            db.commit()
            raise HTTPException(status_code=400, detail="Invalid email or password.")

        # Enforcement: User cannot login until email is verified
        if not user.is_verified:
            raise HTTPException(status_code=400, detail="Please verify your email before logging in.")

        # 3. Successful Login Housekeeping
        user.failed_login_attempts = 0
        user.lockout_until = None
        user.last_login_at = datetime.utcnow()
        user.last_login_ip = ip_address

        db.add(LoginHistory(user_id=user.id, email=email, status="SUCCESS", ip_address=ip_address, user_agent=user_agent))

        # 4. Refresh Token Rotation & Hashed Storage
        raw_refresh_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_refresh_token.encode("utf-8")).hexdigest()

        # Revoke previous active tokens for this device
        existing_device_tokens = db.query(RefreshToken).filter(
            RefreshToken.user_id == user.id,
            RefreshToken.device_id == device_id,
            RefreshToken.is_revoked == False
        ).all()
        for old_t in existing_device_tokens:
            old_t.is_revoked = True

        # Add new RefreshToken record
        expires_at = datetime.utcnow() + timedelta(days=7)
        db.add(RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            device_id=device_id,
            device_info=user_agent[:255] if user_agent else "Unknown",
            expires_at=expires_at,
            is_revoked=False
        ))

        db.commit()

        access_token = create_access_token({"sub": user.email, "role": user.role})

        return {
            "access_token": access_token,
            "refresh_token": raw_refresh_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "name": user.name,
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "gym_name": user.gym_name or "",
                "avatar_url": user.avatar_url,
                "phone": user.phone,
                "is_verified": user.is_verified
            }
        }

    @staticmethod
    def refresh_access_token(db: Session, raw_refresh_token: str, device_id: str = "default", user_agent: str = "Unknown") -> dict:
        if not raw_refresh_token or not raw_refresh_token.strip():
            raise HTTPException(status_code=401, detail="Refresh token required.")

        token_hash = hashlib.sha256(raw_refresh_token.strip().encode("utf-8")).hexdigest()
        token_record = db.query(RefreshToken).filter(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked == False
        ).first()

        if not token_record or datetime.utcnow() > token_record.expires_at:
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token. Please log in again.")

        user = token_record.user
        if not user or not user.is_verified:
            raise HTTPException(status_code=401, detail="User account inactive or unverified.")

        # Refresh Token Rotation: Revoke current token
        token_record.is_revoked = True

        # Generate new raw refresh token and store hash
        new_raw_refresh = secrets.token_urlsafe(48)
        new_token_hash = hashlib.sha256(new_raw_refresh.encode("utf-8")).hexdigest()
        new_expires_at = datetime.utcnow() + timedelta(days=7)

        db.add(RefreshToken(
            user_id=user.id,
            token_hash=new_token_hash,
            device_id=device_id,
            device_info=user_agent[:255] if user_agent else "Unknown",
            expires_at=new_expires_at,
            is_revoked=False
        ))
        db.commit()

        new_access_token = create_access_token({"sub": user.email, "role": user.role})

        return {
            "access_token": new_access_token,
            "refresh_token": new_raw_refresh,
            "token_type": "bearer"
        }

    @staticmethod
    def logout_current_device(db: Session, raw_refresh_token: str) -> dict:
        if raw_refresh_token:
            token_hash = hashlib.sha256(raw_refresh_token.strip().encode("utf-8")).hexdigest()
            token_record = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
            if token_record:
                token_record.is_revoked = True
                db.commit()
        return {"message": "Logged out successfully from current device."}

    @staticmethod
    def logout_all_devices(db: Session, user_id: int) -> dict:
        tokens = db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked == False
        ).all()
        for t in tokens:
            t.is_revoked = True
        db.commit()
        return {"message": "Logged out successfully from all active devices."}

    @staticmethod
    def forgot_password(db: Session, email: str) -> dict:
        email = email.lower().strip()
        user = UserRepo.get_by_email(db, email)
        
        # Always return generic message to prevent account enumeration
        generic_message = "If an account exists, a verification code has been sent."

        if user:
            recent_resends = OTPRepo.get_recent_resends_count_hourly(db, email, "PASSWORD_RESET")
            if recent_resends < MAX_RESEND_HOURLY:
                otp_code = f"{secrets.randbelow(900000) + 100000}"
                OTPRepo.create_otp(
                    db=db,
                    email=email,
                    otp=otp_code,
                    purpose="PASSWORD_RESET",
                    user_id=user.id,
                    expires_in_minutes=OTP_EXPIRY_MINUTES
                )
                EmailService.send_otp_email(email, otp_code, purpose="PASSWORD_RESET")

        return {"message": generic_message}

    @staticmethod
    def verify_reset_otp(db: Session, email: str, otp: str) -> dict:
        email = email.lower().strip()
        active_otp = OTPRepo.get_active_otp(db, email, "PASSWORD_RESET")

        if not active_otp:
            raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

        if datetime.utcnow() > active_otp.expires_at:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Verification code has expired. Please request a new one.")

        if active_otp.attempts >= MAX_OTP_ATTEMPTS:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Maximum attempts (5) exceeded. Please request a new code.")

        # Constant-time comparison
        if not secrets.compare_digest(active_otp.otp.strip(), otp.strip()):
            OTPRepo.increment_attempts(db, active_otp)
            remaining = MAX_OTP_ATTEMPTS - active_otp.attempts
            raise HTTPException(status_code=400, detail=f"Invalid verification code. {remaining} attempt(s) remaining.")

        return {"message": "Verification code verified successfully."}

    @staticmethod
    def resend_reset_otp(db: Session, email: str) -> dict:
        return AuthService.forgot_password(db, email)

    @staticmethod
    def reset_password(db: Session, email: str, otp: str, new_password: str, confirm_password: str) -> dict:
        email = email.lower().strip()

        if new_password != confirm_password:
            raise HTTPException(status_code=400, detail="Passwords do not match.")

        if not re.match(PASSWORD_REGEX, new_password):
            raise HTTPException(
                status_code=400,
                detail="Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, one number, and one special character."
            )

        active_otp = OTPRepo.get_active_otp(db, email, "PASSWORD_RESET")
        if not active_otp:
            raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

        if datetime.utcnow() > active_otp.expires_at:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Verification code has expired. Please request a new one.")

        if active_otp.attempts >= MAX_OTP_ATTEMPTS:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Maximum attempts (5) exceeded. Please request a new code.")

        # Constant-time comparison
        if not secrets.compare_digest(active_otp.otp.strip(), otp.strip()):
            OTPRepo.increment_attempts(db, active_otp)
            remaining = MAX_OTP_ATTEMPTS - active_otp.attempts
            raise HTTPException(status_code=400, detail=f"Invalid verification code. {remaining} attempt(s) remaining.")

        user = UserRepo.get_by_email(db, email)
        if not user:
            raise HTTPException(status_code=404, detail="User account not found.")

        # Reset password and revoke all active refresh tokens for all devices
        user.password = hash_password(new_password)
        OTPRepo.mark_as_used(db, active_otp, commit=False)
        AuthService.logout_all_devices(db, user.id)

        AuditService.log_action(db, user.id, user.name, "PASSWORD_RESET", "Account password reset completed via OTP verification.", commit=False)
        db.commit()

        return {"message": "Password reset successfully! All existing active sessions have been logged out. Please log in with your new password."}

    @staticmethod
    def super_admin_forgot_password(db: Session, email: str, ip_address: str = "127.0.0.1") -> dict:
        email = email.lower().strip()
        user = UserRepo.get_by_email(db, email)

        # Enforce that self-service recovery is available ONLY for Super Admin accounts
        if not user or user.role != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Self-service password recovery is available for STHX Super Admin accounts only. Gym Owners must contact Super Admin for assistance."
            )

        recent_resends = OTPRepo.get_recent_resends_count_hourly(db, email, "SUPER_ADMIN_PASSWORD_RESET")
        if recent_resends >= MAX_RESEND_HOURLY:
            raise HTTPException(status_code=400, detail="Maximum resend attempts (5 per hour) exceeded. Please try again later.")

        # Cryptographically secure 6-digit OTP
        otp_code = f"{secrets.randbelow(900000) + 100000}"
        
        # create_otp automatically invalidates any previously issued active OTPs for this email and purpose
        OTPRepo.create_otp(
            db=db,
            email=email,
            otp=otp_code,
            purpose="SUPER_ADMIN_PASSWORD_RESET",
            user_id=user.id,
            expires_in_minutes=OTP_EXPIRY_MINUTES
        )

        EmailService.send_otp_email(email, otp_code, purpose="SUPER_ADMIN_PASSWORD_RESET")
        AuditService.log_action(db, user.id, user.name, "SUPER_ADMIN_FORGOT_PASSWORD_REQUEST", f"Super Admin password reset OTP requested for: {email}", ip_address)

        return {"message": "Verification code sent to Super Admin recovery email."}

    @staticmethod
    def super_admin_verify_reset_otp(db: Session, email: str, code: str) -> dict:
        email = email.lower().strip()
        user = UserRepo.get_by_email(db, email)

        if not user or user.role != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Self-service password recovery is available for STHX Super Admin accounts only."
            )

        active_otp = OTPRepo.get_active_otp(db, email, "SUPER_ADMIN_PASSWORD_RESET")
        if not active_otp:
            raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

        if datetime.utcnow() > active_otp.expires_at:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Verification code has expired. Please request a new code.")

        if active_otp.attempts >= MAX_OTP_ATTEMPTS:
            OTPRepo.mark_as_used(db, active_otp)
            raise HTTPException(status_code=400, detail="Maximum attempts (5) exceeded. Please request a new code.")

        # Constant-time comparison
        if not secrets.compare_digest(active_otp.otp.strip(), code.strip()):
            OTPRepo.increment_attempts(db, active_otp)
            remaining = MAX_OTP_ATTEMPTS - active_otp.attempts
            raise HTTPException(status_code=400, detail=f"Invalid verification code. {remaining} attempt(s) remaining.")

        # ATOMIC SINGLE-USE ENFORCEMENT: Consume OTP immediately upon successful verification
        OTPRepo.mark_as_used(db, active_otp)

        # Issue dedicated short-lived single-use reset token valid for 10 minutes
        reset_token_payload = {
            "sub": str(user.id),
            "email": user.email,
            "purpose": "super_admin_reset_token",
            "otp_id": active_otp.id
        }
        reset_token = create_access_token(reset_token_payload, expires_delta=timedelta(minutes=10))

        return {
            "message": "OTP verified successfully. Submit your new password with the provided reset token.",
            "reset_token": reset_token
        }

    @staticmethod
    def super_admin_reset_password(db: Session, reset_token: str, new_password: str, confirm_password: str, ip_address: str = "127.0.0.1") -> dict:
        if new_password != confirm_password:
            raise HTTPException(status_code=400, detail="Passwords do not match.")

        if not re.match(PASSWORD_REGEX, new_password):
            raise HTTPException(
                status_code=400,
                detail="Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, one number, and one special character."
            )

        try:
            payload = decode_access_token(reset_token)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid or expired reset token: {type(e).__name__} - {str(e)}")

        if payload.get("purpose") != "super_admin_reset_token":
            raise HTTPException(status_code=400, detail="Invalid reset token purpose.")

        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=400, detail="Invalid reset token payload.")

        user = db.query(User).filter(User.id == int(user_id_str), User.role == "super_admin").first()
        if not user:
            raise HTTPException(status_code=403, detail="Reset token does not belong to a valid Super Admin account.")

        otp_id = payload.get("otp_id")
        if otp_id:
            otp_record = db.query(OTPCode).filter(OTPCode.id == otp_id).first()
            if otp_record and otp_record.resend_count == 999:
                raise HTTPException(status_code=400, detail="Reset token has already been used.")
            if otp_record:
                otp_record.resend_count = 999

        # Update password and revoke all active sessions/refresh tokens
        user.password = hash_password(new_password)
        AuthService.logout_all_devices(db, user.id)

        AuditService.log_action(db, user.id, user.name, "SUPER_ADMIN_PASSWORD_RESET_SUCCESS", f"Super Admin password reset completed successfully.", ip_address, commit=False)
        db.commit()

        return {"message": "Super Admin password reset successfully! All existing active sessions have been logged out. Please log in with your new password."}

    @staticmethod
    def change_password(db: Session, user_id: int, current_password: str, new_password: str, confirm_password: str, ip_address: str = "127.0.0.1") -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User account not found.")

        if not verify_password(current_password, user.password):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        if new_password != confirm_password:
            raise HTTPException(status_code=400, detail="New password and confirmation do not match.")

        if not re.match(PASSWORD_REGEX, new_password):
            raise HTTPException(
                status_code=400,
                detail="New password must be at least 8 characters long and contain uppercase, lowercase, number, and special character."
            )

        user.password = hash_password(new_password)
        AuditService.log_action(db, user.id, user.name, "PASSWORD_CHANGED", "User changed account password from Settings.", ip_address, commit=False)
        db.commit()

        return {"message": "Password changed successfully!"}

    @staticmethod
    def update_super_admin_profile(
        db: Session,
        user_id: int,
        full_name: str,
        username: Optional[str] = None,
        email: Optional[str] = None,
        current_password: Optional[str] = None,
        new_password: Optional[str] = None,
        confirm_password: Optional[str] = None,
        ip_address: str = "127.0.0.1"
    ) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User account not found.")

        clean_name = (full_name or "").strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Full name cannot be empty.")
        user.name = clean_name

        email_changed = False
        if email:
            clean_email = email.strip().lower()
            if clean_email != user.email:
                if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", clean_email):
                    raise HTTPException(status_code=400, detail="Please enter a valid email address.")
                existing = db.query(User).filter(User.email == clean_email, User.id != user.id).first()
                if existing:
                    raise HTTPException(status_code=400, detail="Email is already taken. Please choose another email.")
                user.email = clean_email
                email_changed = True

        if username:
            clean_username = username.strip().lower()
            existing = db.query(User).filter(User.username == clean_username, User.id != user.id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Username is already taken. Please choose another username.")
            user.username = clean_username

        if new_password and new_password.strip():
            if not current_password:
                raise HTTPException(status_code=400, detail="Current password is required to change password.")
            if not verify_password(current_password, user.password):
                raise HTTPException(status_code=400, detail="Current password is incorrect.")
            if new_password != confirm_password:
                raise HTTPException(status_code=400, detail="New passwords do not match.")
            if not re.match(PASSWORD_REGEX, new_password):
                raise HTTPException(
                    status_code=400,
                    detail="Password must be at least 8 characters long and contain uppercase, lowercase, number, and special character."
                )
            user.password = hash_password(new_password)

        AuditService.log_action(
            db, user.id, user.name, "SUPER_ADMIN_PROFILE_UPDATED",
            f"Super Admin updated profile details (Username: {user.username or 'N/A'}).", ip_address, commit=False
        )

        db.commit()
        db.refresh(user)

        response = {
            "message": "Super Admin profile updated successfully!",
            "user": {
                "id": user.id,
                "name": user.name,
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "avatar_url": user.avatar_url
            }
        }
        if email_changed:
            from src.utils.helpers import create_access_token
            response["access_token"] = create_access_token(data={"sub": user.email, "type": "access"})

        return response
