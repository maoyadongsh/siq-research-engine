"""用户认证依赖函数。"""
from fastapi import Depends, HTTPException, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from database import get_async_session
from services.auth_service import AuthService, PermissionChecker, User


security = HTTPBearer()


def create_access_token(data: dict, expires_delta=None) -> str:
    """Compatibility wrapper for older imports."""
    return AuthService.create_access_token(data, expires_delta)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_async_session)
) -> User:
    """
    从JWT token获取当前用户

    使用方式：
    @router.get("/protected")
    async def protected_route(current_user: User = Depends(get_current_user)):
        return {"user_id": current_user.id}
    """
    token = credentials.credentials

    payload = AuthService.decode_token(token)
    if payload is None:
        raise HTTPException(401, "Invalid or expired token")

    subject = str(payload.get("sub") or "").strip()
    if not subject:
        raise HTTPException(401, "Invalid token: missing subject")

    if subject.isdigit():
        result = await session.exec(select(User).where(User.id == int(subject)))
    else:
        result = await session.exec(select(User).where(User.username == subject))

    user = result.first()

    if user is None:
        raise HTTPException(401, "User not found")

    approval_status = getattr(user, "approval_status", "approved")
    if approval_status == "pending":
        raise HTTPException(403, "User account is pending administrator approval")
    if approval_status == "rejected":
        raise HTTPException(403, getattr(user, "approval_note", None) or "User account request was rejected")

    if not user.is_active:
        raise HTTPException(403, "User account is disabled")

    return user


def require_permission(permission: str):
    """Require a named RBAC permission for an API route."""
    async def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        if not PermissionChecker.has_permission(current_user, permission):
            raise HTTPException(403, f"Permission denied: {permission} required")
        return current_user

    return permission_checker
