"""
认证与权限管理路由
提供用户登录、注册、权限验证等API
"""
from datetime import datetime, timedelta, timezone
from html import unescape
import json
import os
import re
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session, get_session
from services.auth_service import (
    User, AuditLog, ReportReview,
    AuthService, PermissionChecker, AuditLogger, ReportSignature,
    LoginRequest, LoginResponse, UserCreate, UserUpdate, UserBatchUpdate, ReportReviewCreate,
    UserRole,
)
from services.usage_service import (
    AGENT_QUESTION_EVENT,
    PARSE_EVENT,
    UsageEvent,
    UserArtifact,
    WorkspaceProject,
    usage_response_payload,
)

router = APIRouter(tags=["authentication"])
security = HTTPBearer(auto_error=False)

REPORT_GENERATOR_METADATA_KEYS = {"generated_by", "generatedby", "generator", "siq:generated_by", "siq:generator"}


def _clean_report_generated_by(value: object) -> str:
    text = str(value or "").strip()
    return text[:100] if text else ""


def _report_generated_by_from_metadata(content: str) -> str:
    """Best-effort report generator metadata extraction with a stable fallback."""
    head = content[:8192]

    for match in re.finditer(r"<meta\s+([^>]+)>", head, flags=re.IGNORECASE):
        attrs = {
            key.lower(): unescape(value).strip()
            for key, value in re.findall(r"""([:\w-]+)\s*=\s*["']([^"']*)["']""", match.group(1))
        }
        name = attrs.get("name", "").lower()
        if name in REPORT_GENERATOR_METADATA_KEYS:
            generated_by = _clean_report_generated_by(attrs.get("content"))
            if generated_by:
                return generated_by

    if head.startswith("---"):
        front_matter = head.split("---", 2)
        if len(front_matter) >= 3:
            for line in front_matter[1].splitlines():
                if ":" not in line:
                    continue
                key, raw_value = line.split(":", 1)
                if key.strip().lower() in REPORT_GENERATOR_METADATA_KEYS:
                    generated_by = _clean_report_generated_by(raw_value.strip().strip("\"'"))
                    if generated_by:
                        return generated_by

    for key in ("generated_by", "generatedBy", "generator"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', head)
        if match:
            generated_by = _clean_report_generated_by(match.group(1))
            if generated_by:
                return generated_by

    return "system"


def _parse_report_generated_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _nested_metadata_value(payload: dict, *paths: tuple[str, ...]) -> object:
    for path in paths:
        current: object = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if current not in (None, ""):
            return current
    return None


def _read_sibling_report_metadata(report_path: Path) -> dict:
    metadata_path = report_path.with_suffix(".json")
    if not metadata_path.is_file():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _report_generation_metadata(report_path: Path, content: str) -> tuple[str, datetime]:
    metadata = _read_sibling_report_metadata(report_path)
    generated_by = _clean_report_generated_by(
        _nested_metadata_value(
            metadata,
            ("report_meta", "generator"),
            ("report_meta", "generated_by"),
            ("quality_report", "generated_by"),
            ("generator",),
            ("generated_by",),
        )
    )
    if not generated_by:
        generated_by = _report_generated_by_from_metadata(content)

    generated_at = _parse_report_generated_at(
        _nested_metadata_value(
            metadata,
            ("report_meta", "generated_at"),
            ("quality_report", "generated_at"),
            ("generated_at",),
        )
    ) or datetime.utcnow()
    return generated_by or "system", generated_at


def _demo_mode_enabled() -> bool:
    return (os.getenv("SIQ_DEMO_MODE") or os.getenv("SIQ_DEMO_MODE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _registration_enabled() -> bool:
    return (os.getenv("SIQ_ALLOW_REGISTRATION") or os.getenv("SIQ_ALLOW_REGISTRATION", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _login_response_for_user(user: User) -> LoginResponse:
    access_token = AuthService.create_access_token(
        data={"sub": user.username, "role": user.role}
    )
    return LoginResponse(
        access_token=access_token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "approval_status": user.approval_status,
            "is_active": user.is_active,
        }
    )


def _set_access_cookie(response: Response, token: str) -> None:
    if not AuthService.cookie_mode_enabled():
        return
    same_site = AuthService.access_cookie_samesite()
    secure = AuthService.access_cookie_secure() or same_site == "none"
    response.set_cookie(
        key=AuthService.ACCESS_COOKIE_NAME,
        value=token,
        max_age=AuthService.access_cookie_max_age_seconds(),
        path=AuthService.ACCESS_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite=same_site,
    )


def _clear_access_cookie(response: Response) -> None:
    if not AuthService.cookie_mode_enabled():
        return
    same_site = AuthService.access_cookie_samesite()
    secure = AuthService.access_cookie_secure() or same_site == "none"
    response.delete_cookie(
        key=AuthService.ACCESS_COOKIE_NAME,
        path=AuthService.ACCESS_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite=same_site,
    )


def _role_value(role) -> str:
    return role.value if hasattr(role, "value") else str(role)


def _is_super_admin(user: User) -> bool:
    return _role_value(user.role) == UserRole.SUPER_ADMIN.value


def _validate_user_update(current_user: User, target_user: User, user_data: UserUpdate) -> None:
    if not _is_super_admin(current_user):
        if _is_super_admin(target_user) or user_data.role == UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="只有超级管理员可以管理超级管理员账户")

    if current_user.id == target_user.id:
        if any([
            user_data.role is not None,
            user_data.approval_status is not None,
            user_data.is_active is not None,
        ]):
            raise HTTPException(status_code=400, detail="不能修改当前登录账户的角色、审批状态或启用状态")


def _apply_user_update_fields(target_user: User, user_data: UserUpdate, current_user: User) -> None:
    if user_data.email is not None:
        target_user.email = user_data.email
    if user_data.full_name is not None:
        target_user.full_name = user_data.full_name
    if user_data.role is not None:
        target_user.role = user_data.role
    if user_data.approval_status is not None:
        approval_status_value = user_data.approval_status.strip().lower()
        if approval_status_value not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=400, detail="审批状态必须是 pending、approved 或 rejected")
        target_user.approval_status = approval_status_value
        if approval_status_value == "approved":
            target_user.is_active = True
            target_user.approved_by = current_user.id
            target_user.approved_at = datetime.utcnow()
        elif approval_status_value == "rejected":
            target_user.is_active = False
        elif approval_status_value == "pending":
            target_user.is_active = False
    if user_data.approval_note is not None:
        target_user.approval_note = user_data.approval_note
    if user_data.is_active is not None:
        target_user.is_active = user_data.is_active

# ============ 依赖注入 ============

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    session: AsyncSession = Depends(get_async_session),
    access_token_cookie: str | None = Cookie(default=None, alias=AuthService.ACCESS_COOKIE_NAME),
) -> User:
    """获取当前登录用户"""
    token = credentials.credentials if credentials is not None else access_token_cookie
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = AuthService.decode_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌格式错误",
        )

    result = await session.exec(select(User).where(User.username == username))
    user = result.first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )

    approval_status = getattr(user, "approval_status", "approved")
    if approval_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户待管理员审核，通过后即可登录",
        )
    if approval_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=user.approval_note or "账户申请未通过，请联系管理员",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户已被禁用",
        )

    return user


def require_permission(permission: str):
    """权限依赖工厂"""
    async def permission_checker(current_user: User = Depends(get_current_user)):
        if not PermissionChecker.has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {permission} 权限",
            )
        return current_user
    return permission_checker


# ============ 认证接口 ============

@router.post("/login", response_model=LoginResponse)
def login(
    login_data: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """用户登录"""
    # 查找用户
    user = session.exec(select(User).where(User.username == login_data.username)).first()

    if not user or not AuthService.verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    approval_status = getattr(user, "approval_status", "approved")
    if approval_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户待管理员审核，通过后即可登录",
        )
    if approval_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=user.approval_note or "账户申请未通过，请联系管理员",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    # 更新最后登录时间
    user.last_login = datetime.utcnow()
    session.add(user)
    session.commit()

    # 记录审计日志
    AuditLogger.log_action(
        session=session,
        user_id=user.id,
        action="LOGIN",
        resource_type="auth",
        resource_id=str(user.id),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    login_response = _login_response_for_user(user)
    _set_access_cookie(response, login_response.access_token)
    return login_response


@router.post("/demo-login", response_model=LoginResponse)
def demo_login(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """演示模式自动登录。生产环境可通过 SIQ_DEMO_MODE=0 关闭。"""
    if not _demo_mode_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="演示登录未启用")

    username = os.getenv("SIQ_DEMO_USERNAME", "").strip()
    if not username:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="演示用户未配置")
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="演示用户不可用")

    user.last_login = datetime.utcnow()
    session.add(user)
    session.commit()

    AuditLogger.log_action(
        session=session,
        user_id=user.id,
        action="DEMO_LOGIN",
        resource_type="auth",
        resource_id=str(user.id),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    login_response = _login_response_for_user(user)
    _set_access_cookie(response, login_response.access_token)
    return login_response


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """用户登出"""
    AuditLogger.log_action(
        session=session,
        user_id=current_user.id,
        action="LOGOUT",
        resource_type="auth",
        resource_id=str(current_user.id),
        ip_address=request.client.host if request.client else None,
    )

    _clear_access_cookie(response)
    return {"message": "登出成功"}


@router.get("/me")
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "approval_status": current_user.approval_status,
        "approval_note": current_user.approval_note,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "last_login": current_user.last_login,
    }




@router.post("/register")
def register(
    user_data: UserCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    """公开用户注册
    
    新用户可以自助注册账户，默认角色为普通用户。
    可通过环境变量 SIQ_ALLOW_REGISTRATION=0 关闭公开注册。
    """
    # 检查是否允许公开注册
    if not _registration_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="公开注册已关闭，请联系管理员创建账户"
        )
    
    # 检查用户名是否已存在
    existing = session.exec(select(User).where(User.username == user_data.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    # 检查邮箱是否已存在
    existing = session.exec(select(User).where(User.email == user_data.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="邮箱已存在")
    
    # 创建新用户（强制为只读用户角色，防止权限提升），等待管理员审批。
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=AuthService.hash_password(user_data.password),
        full_name=user_data.full_name or user_data.username,
        role=UserRole.VIEWER,
        approval_status="pending",
        is_active=False,
    )
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    
    # 记录审计日志
    AuditLogger.log_action(
        session=session,
        user_id=new_user.id,
        action="REGISTER",
        resource_type="user",
        resource_id=str(new_user.id),
        details={"username": new_user.username, "email": new_user.email},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    
    return {
        "message": "注册申请已提交，请等待管理员审核",
        "status": "pending",
        "user": {
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "role": new_user.role,
            "approval_status": new_user.approval_status,
        },
    }


# ============ 用户管理接口（需要管理员权限） ============
# ============ 用户管理接口（需要管理员权限） ============

@router.post("/users", dependencies=[Depends(require_permission("user.manage"))])
def create_user(
    user_data: UserCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """创建用户"""
    # 检查用户名是否已存在
    existing = session.exec(select(User).where(User.username == user_data.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 检查邮箱是否已存在
    existing = session.exec(select(User).where(User.email == user_data.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="邮箱已存在")

    # 创建用户
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=AuthService.hash_password(user_data.password),
        full_name=user_data.full_name,
        role=user_data.role,
        approval_status="approved",
        approved_by=current_user.id,
        approved_at=datetime.utcnow(),
        is_active=True,
    )
    session.add(new_user)
    session.commit()
    session.refresh(new_user)

    # 记录审计日志
    AuditLogger.log_action(
        session=session,
        user_id=current_user.id,
        action="CREATE_USER",
        resource_type="user",
        resource_id=str(new_user.id),
        details={"username": new_user.username, "role": new_user.role},
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "用户创建成功", "user_id": new_user.id}


@router.get("/users", dependencies=[Depends(require_permission("user.manage"))])
def list_users(
    skip: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """列出所有用户"""
    users = session.exec(select(User).offset(skip).limit(limit)).all()
    return [
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "approval_status": user.approval_status,
            "approval_note": user.approval_note,
            "is_active": user.is_active,
            "created_at": user.created_at,
            "last_login": user.last_login,
        }
        for user in users
    ]


@router.post("/users/batch", dependencies=[Depends(require_permission("user.manage"))])
def batch_update_users(
    user_data: UserBatchUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """批量更新用户"""
    if not user_data.user_ids:
        raise HTTPException(status_code=400, detail="请先选择用户")
    if user_data.approval_status is not None:
        approval_status_value = user_data.approval_status.strip().lower()
        if approval_status_value not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=400, detail="审批状态必须是 pending、approved 或 rejected")
        user_data.approval_status = approval_status_value

    updated = 0
    skipped = 0
    for user_id in user_data.user_ids:
        user = session.get(User, user_id)
        if not user:
            skipped += 1
            continue

        update_payload = UserUpdate(
            role=user_data.role,
            approval_status=user_data.approval_status,
            approval_note=user_data.approval_note,
            is_active=user_data.is_active,
        )

        try:
            _validate_user_update(current_user, user, update_payload)
            _apply_user_update_fields(user, update_payload, current_user)
            session.add(user)
            updated += 1
        except HTTPException:
            skipped += 1

    session.commit()

    AuditLogger.log_action(
        session=session,
        user_id=current_user.id,
        action="BATCH_UPDATE_USERS",
        resource_type="user",
        resource_id=",".join(str(user_id) for user_id in user_data.user_ids),
        details=user_data.dict(exclude_unset=True),
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "批量更新完成", "updated": updated, "skipped": skipped}


@router.get("/users/{user_id}/detail", dependencies=[Depends(require_permission("user.manage"))])
def get_user_detail(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """获取用户详情汇总"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if _is_super_admin(user) and not _is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以查看超级管理员账户详情")

    projects = session.exec(
        select(WorkspaceProject).where(WorkspaceProject.user_id == user_id).order_by(WorkspaceProject.updated_at.desc())
    ).all()
    artifacts = session.exec(
        select(UserArtifact).where(UserArtifact.user_id == user_id).order_by(UserArtifact.created_at.desc())
    ).all()
    audit_logs = session.exec(
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    ).all()
    usage_rows = session.exec(
        select(UsageEvent).where(UsageEvent.user_id == user_id).order_by(UsageEvent.created_at.desc())
    ).all()

    usage_by_type: dict[str, int] = {}
    for row in usage_rows:
        usage_by_type[row.event_type] = usage_by_type.get(row.event_type, 0) + int(row.count or 0)

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "approval_status": user.approval_status,
            "approval_note": user.approval_note,
            "approved_by": user.approved_by,
            "approved_at": user.approved_at,
            "is_active": user.is_active,
            "created_at": user.created_at,
            "last_login": user.last_login,
        },
        "usage": {
            "agentQuestion": usage_response_payload(session, user_id=user_id, user_role=_role_value(user.role), event_type=AGENT_QUESTION_EVENT),
            "parseJob": usage_response_payload(session, user_id=user_id, user_role=_role_value(user.role), event_type=PARSE_EVENT),
            "totals": usage_by_type,
        },
        "workspace": {
            "projects": len(projects),
            "artifacts": len(artifacts),
            "recentProjects": [
                {
                    "id": item.id,
                    "name": item.name,
                    "company_code": item.company_code,
                    "company_name": item.company_name,
                    "status": item.status,
                    "updated_at": item.updated_at,
                }
                for item in projects[:5]
            ],
            "recentArtifacts": [
                {
                    "id": item.id,
                    "type": item.artifact_type,
                    "title": item.title,
                    "path": item.path,
                    "source": item.source,
                    "created_at": item.created_at,
                }
                for item in artifacts[:8]
            ],
        },
        "audit": {
            "recentLogs": [
                {
                    "id": item.id,
                    "action": item.action,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "details": item.details,
                    "ip_address": item.ip_address,
                    "created_at": item.created_at,
                }
                for item in audit_logs
            ],
        },
    }


@router.patch("/users/{user_id}", dependencies=[Depends(require_permission("user.manage"))])
def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """更新用户信息"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    _validate_user_update(current_user, user, user_data)
    _apply_user_update_fields(user, user_data, current_user)

    session.add(user)
    session.commit()

    # 记录审计日志
    AuditLogger.log_action(
        session=session,
        user_id=current_user.id,
        action="UPDATE_USER",
        resource_type="user",
        resource_id=str(user_id),
        details=user_data.dict(exclude_unset=True),
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "用户更新成功"}




# ============ 报告审核接口 ============

@router.post("/reports/review", dependencies=[Depends(require_permission("report.review"))])
def create_report_review(
    review_data: ReportReviewCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """创建报告审核记录"""
    # 读取报告内容计算哈希
    report_path = Path(review_data.report_path)
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")

    content = report_path.read_text(encoding="utf-8")
    content_hash = ReportSignature.calculate_hash(content)
    signature = ReportSignature.sign_report(content, current_user.id)
    generated_by, generated_at = _report_generation_metadata(report_path, content)

    # 创建审核记录
    review = ReportReview(
        report_path=review_data.report_path,
        company_id=review_data.company_id,
        report_year=review_data.report_year,
        report_type=review_data.report_type,
        reviewer_id=current_user.id,
        status=review_data.status,
        review_result=json.dumps(review_data.review_result, ensure_ascii=False) if review_data.review_result else None,
        reviewed_at=datetime.utcnow(),
        generated_by=generated_by,
        generated_at=generated_at,
        content_hash=content_hash,
        signature=signature,
    )
    session.add(review)
    session.commit()
    session.refresh(review)

    # 记录审计日志
    AuditLogger.log_action(
        session=session,
        user_id=current_user.id,
        action="REVIEW_REPORT",
        resource_type="report",
        resource_id=review_data.report_path,
        details={"status": review_data.status, "review_id": review.id},
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "审核记录创建成功", "review_id": review.id}


@router.get("/reports/reviews")
def list_report_reviews(
    company_id: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """列出报告审核记录"""
    query = select(ReportReview)

    if company_id:
        query = query.where(ReportReview.company_id == company_id)
    if status:
        query = query.where(ReportReview.status == status)

    # 非超级管理员只能看自己的审核记录
    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.where(ReportReview.reviewer_id == current_user.id)

    reviews = session.exec(query.offset(skip).limit(limit)).all()

    return [
        {
            "id": review.id,
            "report_path": review.report_path,
            "company_id": review.company_id,
            "report_year": review.report_year,
            "report_type": review.report_type,
            "status": review.status,
            "reviewer": review.reviewer.full_name if review.reviewer else None,
            "reviewed_at": review.reviewed_at,
            "generated_at": review.generated_at,
        }
        for review in reviews
    ]


# ============ 审计日志接口 ============

@router.get("/audit-logs", dependencies=[Depends(require_permission("audit.view"))])
def list_audit_logs(
    action: Optional[str] = None,
    user_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """列出审计日志"""
    query = select(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)

    logs = session.exec(query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)).all()

    return [
        {
            "id": log.id,
            "user": log.user.username if log.user else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details": log.details,
            "ip_address": log.ip_address,
            "created_at": log.created_at,
        }
        for log in logs
    ]
