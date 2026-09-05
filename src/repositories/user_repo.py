from sqlalchemy import or_
from sqlalchemy.orm import Session
from src.models.db_models import User

class UserRepo:
    @staticmethod
    def get_by_email(db: Session, email: str) -> User:
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User:
        return db.query(User).filter(User.id == user_id).first()

    @staticmethod
    def get_by_identifier(db: Session, identifier: str) -> User:
        """Lookup user by email, username, phone number, or CNIC."""
        identifier = identifier.strip()
        return db.query(User).filter(
            or_(
                User.email == identifier.lower(),
                User.username == identifier,
                User.phone == identifier,
                User.cnic == identifier,
            )
        ).first()

    @staticmethod
    def create(db: Session, name: str, email: str, password_hash: str, role: str) -> User:
        user = User(
            name=name,
            email=email,
            password=hashed_password if (hashed_password := password_hash) else password_hash,
            role=role
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
