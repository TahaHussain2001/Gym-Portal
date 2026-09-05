from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from src.models.db_models import UserPreference

DEFAULT_PREFS = {
    "theme_name": "Default Blue",
    "primary_color": "#2563EB",
    "secondary_color": "#1E40AF",
    "accent_color": "#60A5FA",
    "hover_color": "#3B82F6",
    "border_color": "rgba(37,99,235,0.3)",
    "background_image": None,
    "background_opacity": 0,
    "background_blur": 0,
    "background_overlay": "medium",
    "dark_mode": "dark",
    "card_style": "glassmorphism",
    "sidebar_style": "expanded",
    "layout_type": "full_width",
}

def _pref_to_dict(p: UserPreference) -> dict:
    return {
        "theme_name": p.theme_name,
        "primary_color": p.primary_color,
        "secondary_color": p.secondary_color,
        "accent_color": p.accent_color,
        "hover_color": p.hover_color,
        "border_color": p.border_color,
        "background_image": p.background_image,
        "background_opacity": p.background_opacity,
        "background_blur": p.background_blur,
        "background_overlay": p.background_overlay,
        "dark_mode": p.dark_mode,
        "card_style": p.card_style,
        "sidebar_style": p.sidebar_style,
        "layout_type": p.layout_type,
    }

class PreferencesRepo:

    @staticmethod
    def get(db: Session, user_id: int) -> dict:
        p = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
        if not p:
            return dict(DEFAULT_PREFS)
        return _pref_to_dict(p)

    @staticmethod
    def save(db: Session, user_id: int, **kwargs) -> dict:
        p = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
        if not p:
            p = UserPreference(user_id=user_id)
            db.add(p)

        allowed = {
            "theme_name", "primary_color", "secondary_color", "accent_color",
            "hover_color", "border_color", "background_image",
            "background_opacity", "background_blur", "background_overlay",
            "dark_mode", "card_style", "sidebar_style", "layout_type",
        }
        for key, val in kwargs.items():
            if key in allowed and val is not None:
                setattr(p, key, val)

        p.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(p)
        return _pref_to_dict(p)

    @staticmethod
    def remove_background(db: Session, user_id: int) -> dict:
        p = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
        if p:
            p.background_image = None
            p.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(p)
            return _pref_to_dict(p)
        return dict(DEFAULT_PREFS)

    @staticmethod
    def reset_to_defaults(db: Session, user_id: int) -> dict:
        p = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
        if p:
            db.delete(p)
            db.commit()
        return dict(DEFAULT_PREFS)
