import os
import re
import uuid
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, UploadFile, status

from src.models.db_models import User, Gym, Notification
from src.constants.business_rules import ROLE_SUPER_ADMIN
from src.repositories.settings_repo import SettingsRepo
from src.repositories.user_repo import UserRepo
from src.utils.helpers import hash_password, verify_password
from src.config.app_config import PASSWORD_REGEX
from src.services.audit_service import AuditService

logger = logging.getLogger("sthxtechnologies-settings")

try:
    os.makedirs(os.path.join("static", "uploads"), exist_ok=True)
except Exception:
    pass

class SettingsService:
    @staticmethod
    def _notify_super_admins(db: Session, title: str, message: str, notif_type: str = "OWNER_PROFILE_UPDATED"):
        try:
            super_admins = db.query(User).filter(User.role == ROLE_SUPER_ADMIN).all()
            for sa in super_admins:
                db.add(Notification(
                    user_id=sa.id,
                    title=title,
                    message=message,
                    type=notif_type
                ))
            db.commit()
        except Exception as e:
            logger.error(f"Error sending notification to Super Admin: {e}")

    @staticmethod
    def get_user_profile(db: Session, user_id: int) -> Dict[str, Any]:
        user = UserRepo.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        phone = user.phone or ""
        gym_address = ""
        gym_email = user.email or ""

        if user.gym_id:
            gym = db.query(Gym).filter(Gym.id == user.gym_id).first()
            if gym:
                phone = user.phone or gym.phone or ""
                gym_address = gym.address or ""
                gym_email = gym.email or user.email or ""

        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": phone,
            "cnic": user.cnic or "",
            "role": user.role,
            "gym_name": user.gym_name or "",
            "gym_address": gym_address,
            "avatar_url": user.avatar_url,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "last_login_ip": user.last_login_ip or "127.0.0.1"
        }

    @staticmethod
    def update_user_profile(db: Session, user_id: int, name: Optional[str], email: str, phone: Optional[str] = None, ip_address: str = "127.0.0.1") -> Dict[str, Any]:
        user = UserRepo.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # RULE: Owner Name & CNIC cannot be changed by Gym Owners (non-superadmins)
        target_name = user.name
        if user.role == ROLE_SUPER_ADMIN and name:
            target_name = name

        # Check email uniqueness if email changed
        if email and email.lower() != user.email.lower():
            existing = db.query(User).filter(User.email == email.lower(), User.id != user_id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Email address is already in use by another account.")

        changes = []
        if email and user.email != email:
            changes.append(f"Email: '{user.email}' -> '{email}'")
            user.email = email
        if phone is not None and user.phone != phone:
            changes.append(f"Phone: '{user.phone or 'N/A'}' -> '{phone}'")
            user.phone = phone

        # Sync updated Email and Phone to linked Gym database record
        if user.gym_id:
            gym = db.query(Gym).filter(Gym.id == user.gym_id).first()
            if gym:
                if email:
                    gym.email = email
                if phone is not None:
                    gym.phone = phone

        db.commit()
        db.refresh(user)

        AuditService.log_action(db, user_id, user.name, "PROFILE_UPDATED", f"Updated profile details ({', '.join(changes) if changes else 'saved'})", ip_address)

        # Notify Super Admin if user is a Gym Owner
        if user.role != ROLE_SUPER_ADMIN and changes:
            SettingsService._notify_super_admins(
                db,
                title="Gym Owner Profile Updated",
                message=f"Gym Owner '{user.name}' ({user.gym_name or 'Gym'}) updated profile details: {', '.join(changes)}.",
                notif_type="GYM_OWNER_UPDATE"
            )

        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone or "",
            "cnic": user.cnic or "",
            "role": user.role,
            "gym_name": user.gym_name or "",
            "avatar_url": user.avatar_url
        }

    @staticmethod
    def change_password(db: Session, user_id: int, current_password: str, new_password: str, confirm_password: str, ip_address: str = "127.0.0.1") -> dict:
        if new_password != confirm_password:
            raise HTTPException(status_code=400, detail="New password and confirm password do not match.")

        user = UserRepo.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify current password
        if not verify_password(current_password, user.password):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        # Enforce password complexity policy
        if not re.match(PASSWORD_REGEX, new_password):
            raise HTTPException(
                status_code=400,
                detail="New password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, one number, and one special character."
            )

        new_hash = hash_password(new_password)
        user.password = new_hash
        db.commit()

        AuditService.log_action(db, user_id, user.name, "PASSWORD_CHANGED", "Successfully changed account password.", ip_address)

        # Notify Super Admin if user is a Gym Owner
        if user.role != ROLE_SUPER_ADMIN:
            SettingsService._notify_super_admins(
                db,
                title="Gym Owner Password Changed",
                message=f"Gym Owner '{user.name}' ({user.gym_name or 'Gym'}) changed their account password.",
                notif_type="GYM_OWNER_UPDATE"
            )

        return {"message": "Password changed successfully."}

    @staticmethod
    async def upload_user_avatar(db: Session, user_id: int, file: UploadFile, ip_address: str = "127.0.0.1") -> dict:
        user = UserRepo.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        old_avatar = user.avatar_url
        from src.services.file_service import FileSecurityService
        avatar_url = await FileSecurityService.validate_and_upload_scoped(file, "profile-avatars", str(user_id))

        if old_avatar:
            FileSecurityService.delete_managed_object(old_avatar)

        SettingsRepo.update_user_avatar(db, user_id, avatar_url)
        AuditService.log_action(db, user_id, user.name, "AVATAR_UPLOADED", f"Uploaded new profile avatar: {avatar_url}", ip_address)

        # Notify Super Admin if user is a Gym Owner
        if user.role != ROLE_SUPER_ADMIN:
            SettingsService._notify_super_admins(
                db,
                title="Gym Owner Profile Picture Updated",
                message=f"Gym Owner '{user.name}' ({user.gym_name or 'Gym'}) uploaded a new profile picture.",
                notif_type="GYM_OWNER_UPDATE"
            )

        return {"message": "Profile picture uploaded successfully.", "avatar_url": avatar_url}

    @staticmethod
    def remove_user_avatar(db: Session, user_id: int, ip_address: str = "127.0.0.1") -> dict:
        user = UserRepo.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        old_avatar = user.avatar_url
        if old_avatar:
            from src.services.file_service import FileSecurityService
            FileSecurityService.delete_managed_object(old_avatar)

        SettingsRepo.update_user_avatar(db, user_id, None)
        AuditService.log_action(db, user_id, user.name, "AVATAR_REMOVED", "Removed profile avatar.", ip_address)

        if user.role != ROLE_SUPER_ADMIN:
            SettingsService._notify_super_admins(
                db,
                title="Gym Owner Profile Picture Removed",
                message=f"Gym Owner '{user.name}' ({user.gym_name or 'Gym'}) removed their profile picture.",
                notif_type="GYM_OWNER_UPDATE"
            )

        return {"message": "Profile picture removed successfully.", "avatar_url": None}

    # ==========================================================================
    # Gym Branding Services
    # ==========================================================================

    @staticmethod
    def get_gym_branding(db: Session) -> Dict[str, Any]:
        settings = SettingsRepo.get_all_settings(db)
        return {
            "gym_name": settings.get("gym_name", ""),
            "gym_logo_url": settings.get("gym_logo_url", ""),
            "gym_address": settings.get("gym_address", ""),
            "gym_phone": settings.get("gym_phone", ""),
            "gym_email": settings.get("gym_email", ""),
            "gym_website": settings.get("gym_website", ""),
            "gym_footer_text": settings.get("gym_footer_text", "")
        }

    @staticmethod
    def update_gym_branding(db: Session, user_id: int, branding_data: Dict[str, str], ip_address: str = "127.0.0.1") -> Dict[str, Any]:
        user = UserRepo.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # RULE: Gym Name cannot be changed by Gym Owners (non-superadmins)
        if user.role != ROLE_SUPER_ADMIN and user.gym_name:
            branding_data["gym_name"] = user.gym_name

        SettingsRepo.set_multiple_settings(db, branding_data)

        # Sync changes to linked Gym table
        changes = []
        if user.gym_id:
            gym = db.query(Gym).filter(Gym.id == user.gym_id).first()
            if gym:
                if branding_data.get("gym_address") and gym.address != branding_data["gym_address"]:
                    changes.append(f"Address: '{branding_data['gym_address']}'")
                    gym.address = branding_data["gym_address"]
                if branding_data.get("gym_phone") and gym.phone != branding_data["gym_phone"]:
                    changes.append(f"Phone: '{branding_data['gym_phone']}'")
                    gym.phone = branding_data["gym_phone"]
                    user.phone = branding_data["gym_phone"]
                if branding_data.get("gym_email") and gym.email != branding_data["gym_email"]:
                    changes.append(f"Email: '{branding_data['gym_email']}'")
                    gym.email = branding_data["gym_email"]
                    user.email = branding_data["gym_email"]
                db.commit()

        AuditService.log_action(db, user_id, user.name if user else "Admin", "BRANDING_UPDATED", "Updated Gym Branding configuration.", ip_address)

        # Notify Super Admin if user is a Gym Owner
        if user.role != ROLE_SUPER_ADMIN and changes:
            SettingsService._notify_super_admins(
                db,
                title="Gym Branding Details Updated",
                message=f"Gym Owner '{user.name}' ({user.gym_name or 'Gym'}) updated gym details: {', '.join(changes)}.",
                notif_type="GYM_OWNER_UPDATE"
            )

        return SettingsService.get_gym_branding(db)

    @staticmethod
    async def upload_gym_logo(db: Session, user_id: int, file: UploadFile, ip_address: str = "127.0.0.1") -> dict:
        user = UserRepo.get_by_id(db, user_id)
        gym_id = user.gym_id if (user and user.gym_id) else user_id
        
        old_logo = SettingsRepo.get_setting(db, "gym_logo_url")
        from src.services.file_service import FileSecurityService
        logo_url = await FileSecurityService.validate_and_upload_scoped(file, "gym-logos", str(gym_id))

        if old_logo:
            FileSecurityService.delete_managed_object(old_logo)

        SettingsRepo.set_setting(db, "gym_logo_url", logo_url)
        AuditService.log_action(db, user_id, user.name if user else "Admin", "LOGO_UPLOADED", f"Uploaded new gym logo: {logo_url}", ip_address)

        # Notify Super Admin if user is a Gym Owner
        if user and user.role != ROLE_SUPER_ADMIN:
            SettingsService._notify_super_admins(
                db,
                title="Gym Profile Picture/Logo Updated",
                message=f"Gym Owner '{user.name}' ({user.gym_name or 'Gym'}) uploaded a new gym profile logo.",
                notif_type="GYM_OWNER_UPDATE"
            )

        return {"message": "Gym logo uploaded successfully.", "gym_logo_url": logo_url}

    @staticmethod
    def remove_gym_logo(db: Session, user_id: int, ip_address: str = "127.0.0.1") -> dict:
        user = UserRepo.get_by_id(db, user_id)
        old_logo = SettingsRepo.get_setting(db, "gym_logo_url")
        if old_logo:
            from src.services.file_service import FileSecurityService
            FileSecurityService.delete_managed_object(old_logo)

        SettingsRepo.set_setting(db, "gym_logo_url", "/static/sthx_technologies_logo.png")
        AuditService.log_action(db, user_id, user.name if user else "Admin", "LOGO_REMOVED", "Reset Gym logo to default.", ip_address)
        return {"message": "Gym logo reset to default.", "gym_logo_url": "/static/sthx_technologies_logo.png"}

    # ==========================================================================
    # System Settings Services (Admin Only)
    # ==========================================================================

    @staticmethod
    def get_system_settings(db: Session) -> Dict[str, Any]:
        settings = SettingsRepo.get_all_settings(db)
        return {
            "currency": settings.get("currency", "PKR"),
            "timezone": settings.get("timezone", "Asia/Karachi"),
            "date_format": settings.get("date_format", "YYYY-MM-DD"),
            "receipt_prefix": settings.get("receipt_prefix", "REC"),
            "default_registration_fee": float(settings.get("default_registration_fee", "1000.0")),
            "default_membership_duration": int(settings.get("default_membership_duration", "30"))
        }

    @staticmethod
    def update_system_settings(db: Session, user_id: int, system_data: Dict[str, Any], ip_address: str = "127.0.0.1") -> Dict[str, Any]:
        user = UserRepo.get_by_id(db, user_id)
        SettingsRepo.set_multiple_settings(db, {
            "currency": system_data.get("currency"),
            "timezone": system_data.get("timezone"),
            "date_format": system_data.get("date_format"),
            "receipt_prefix": system_data.get("receipt_prefix"),
            "default_registration_fee": str(system_data.get("default_registration_fee")),
            "default_membership_duration": str(system_data.get("default_membership_duration"))
        })
        AuditService.log_action(db, user_id, user.name if user else "Admin", "SYSTEM_SETTINGS_UPDATED", "Updated System Configuration settings.", ip_address)
        return SettingsService.get_system_settings(db)
