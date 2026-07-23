"""用户认证依赖函数。"""
import hmac
from urllib.parse import urlparse

from database import get_async_session
from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.auth_service import AuthService, PermissionChecker, User

security = HTTPBearer(auto_error=False)
SAFE_CSRF_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def create_access_token(data: dict, expires_delta=None) -> str:
    """Compatibility wrapper for older imports."""
    return AuthService.create_access_token(data, expires_delta)


def _origin_from_referer(referer: str) -> str:
    try:
        parsed = urlparse(referer)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _request_origin(request: Request) -> str:
    origin = str(request.headers.get("origin") or "").strip().rstrip("/")
    if origin:
        return origin
    return _origin_from_referer(str(request.headers.get("referer") or "").strip())


def _allowed_csrf_origins(request: Request) -> set[str]:
    allowed = set(AuthService.csrf_allowed_origins())
    host = str(request.headers.get("host") or "").strip()
    if host:
        allowed.add(f"{request.url.scheme}://{host}".rstrip("/"))
        forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
        if forwarded_proto in {"http", "https"}:
            allowed.add(f"{forwarded_proto}://{host}".rstrip("/"))
    return allowed


def _header_csrf_token(request: Request) -> str:
    return str(
        request.headers.get("x-csrf-token")
        or request.headers.get("x-siq-csrf-token")
        or ""
    ).strip()


def _validate_cookie_csrf(request: Request) -> None:
    if request is None or not AuthService.cookie_mode_enabled():
        return
    if request.method.upper() in SAFE_CSRF_METHODS:
        return

    csrf_cookie = str(request.cookies.get(AuthService.CSRF_COOKIE_NAME) or "").strip()
    csrf_header = _header_csrf_token(request)
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(403, "CSRF token missing or invalid")

    origin = _request_origin(request)
    if not origin:
        raise HTTPException(403, "CSRF origin missing")
    if origin not in _allowed_csrf_origins(request):
        raise HTTPException(403, "CSRF origin not allowed")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    session: AsyncSession = Depends(get_async_session),
    access_token_cookie: str | None = Cookie(default=None, alias=AuthService.ACCESS_COOKIE_NAME),
    request: Request = None,
) -> User:
    """
    从JWT token获取当前用户

    使用方式：
    @router.get("/protected")
    async def protected_route(current_user: User = Depends(get_current_user)):
        return {"user_id": current_user.id}
    """
    bearer_token = credentials.credentials if credentials is not None else None
    token = bearer_token or access_token_cookie
    if not token:
        raise HTTPException(401, "Invalid or expired token")

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

    # Bind every newly issued token to the current account security version.
    # Legacy tokens are accepted only in explicitly non-production profiles;
    # this keeps local clients compatible while production fails closed.
    if not AuthService.token_version_matches_user(payload, user):
        raise HTTPException(401, "Invalid or expired token")

    approval_status = getattr(user, "approval_status", "approved")
    if approval_status == "pending":
        raise HTTPException(403, "User account is pending administrator approval")
    if approval_status == "rejected":
        raise HTTPException(403, getattr(user, "approval_note", None) or "User account request was rejected")

    if not user.is_active:
        raise HTTPException(403, "User account is disabled")

    if request is not None:
        request.state.siq_auth_source = "bearer" if bearer_token else "cookie"
        if not bearer_token and access_token_cookie:
            _validate_cookie_csrf(request)

    return user


def require_permission(permission: str):
    """Require a named RBAC permission for an API route."""
    async def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        if not PermissionChecker.has_permission(current_user, permission):
            raise HTTPException(403, f"Permission denied: {permission} required")
        return current_user

    return permission_checker
