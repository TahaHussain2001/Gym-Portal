from typing import Dict, Optional
from sqlalchemy.orm import Session
from src.models.db_models import User, SystemSetting

class SettingsRepo:
    @staticmethod
    def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        return setting.value if setting else default

    @staticmethod
    def get_all_settings(db: Session) -> Dict[str, str]:
        settings = db.query(SystemSetting).all()
        return {s.key: s.value for s in settings}

    @staticmethod
    def set_setting(db: Session, key: str, value: str):
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            setting = SystemSetting(key=key, value=str(value))
            db.add(setting)
        db.commit()

    @staticmethod
    def set_multiple_settings(db: Session, settings_dict: Dict[str, str]):
        for key, val in settings_dict.items():
            if val is not None:
                setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
                if setting:
                    setting.value = str(val)
                else:
                    db.add(SystemSetting(key=key, value=str(val)))
        db.commit()

    @staticmethod
    def update_user_profile(db: Session, user_id: int, name: str, email: str, phone: Optional[str] = None, avatar_url: Optional[str] = None) -> User:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.name = name
            user.email = email
            if phone is not None:
                user.phone = phone
            if avatar_url is not None:
                user.avatar_url = avatar_url
            db.commit()
            db.refresh(user)
        return user

    @staticmethod
    def update_user_avatar(db: Session, user_id: int, avatar_url: Optional[str]) -> User:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.avatar_url = avatar_url
            db.commit()
            db.refresh(user)
        return user
