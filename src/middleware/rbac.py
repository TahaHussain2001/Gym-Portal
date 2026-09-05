from typing import List
from fastapi import HTTPException, status
from src.constants.business_rules import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_GYM_OWNER

def check_role(current_user, allowed_roles: List[str]):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=403, 
            detail=f"Access forbidden: requires one of roles {allowed_roles}, but user has role '{current_user.role}'."
        )
    return current_user
