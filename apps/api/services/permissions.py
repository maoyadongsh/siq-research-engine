from fastapi import HTTPException
from fastapi.params import Depends as DependsParam

from services.auth_service import PermissionChecker, User


def role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role).strip().lower()


def is_admin(user: User) -> bool:
    return role_value(user) in {"admin", "super_admin"}


def require_user_permission(user: User, permission: str) -> None:
    if isinstance(user, DependsParam):
        return
    if not PermissionChecker.has_permission(user, permission):
        raise HTTPException(status_code=403, detail=f"权限不足：需要 {permission} 权限")


def require_admin(user: User) -> None:
    if isinstance(user, DependsParam):
        return
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="仅管理员可访问系统平台数据")
