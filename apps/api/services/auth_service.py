#!/usr/bin/env python3
"""
用户认证与权限管理系统
为SIQ添加企业级用户体系和权限控制
"""
import hashlib
import hmac
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional

import jwt
from pydantic import BaseModel, EmailStr
from sqlmodel import Field, Relationship, SQLModel

from services.path_config import ARTIFACTS_ROOT

# ============ 用户角色定义 ============

class UserRole(str, Enum):
    """用户角色"""
    SUPER_ADMIN = "super_admin"      # 超级管理员：全部可见，系统配置
    ADMIN = "admin"                   # 管理员：用户管理，配置管理
    ANALYST = "analyst"               # 分析师：生成报告，查看数据
    REVIEWER = "reviewer"             # 复核员：审核报告，标注问题
    VIEWER = "viewer"                 # 查看者：只读访问

# 角色权限映射
ROLE_PERMISSIONS = {
    UserRole.SUPER_ADMIN: [
        "system.config",
        "user.manage",
        "tracking.read", "tracking.write",
        "report.create", "report.edit", "report.delete", "report.view", "report.review",
        "company.create", "company.edit", "company.delete", "company.view",
        "audit.view",
        "cost.view",
    ],
    UserRole.ADMIN: [
        "system.config",
        "user.manage",
        "tracking.read", "tracking.write",
        "report.create", "report.edit", "report.delete", "report.view", "report.review",
        "company.create", "company.edit", "company.view",
        "audit.view",
    ],
    UserRole.ANALYST: [
        "tracking.read", "tracking.write",
        "report.create", "report.edit", "report.view",
        "company.view",
    ],
    UserRole.REVIEWER: [
        "report.view", "report.review",
        "company.view",
    ],
    UserRole.VIEWER: [
        "report.view",
        "company.view",
    ],
}

# ============ 数据模型 ============

class User(SQLModel, table=True):
    """用户表"""
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=50)
    email: EmailStr = Field(unique=True, index=True)
    hashed_password: str = Field(max_length=255)
    full_name: str = Field(max_length=100)
    role: UserRole = Field(default=UserRole.VIEWER)
    approval_status: str = Field(default="approved", max_length=20, index=True)
    approval_note: Optional[str] = Field(default=None, max_length=500)
    approved_by: Optional[int] = Field(default=None, index=True)
    approved_at: Optional[datetime] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None

    # 关系
    audit_logs: List["AuditLog"] = Relationship(back_populates="user")
    report_reviews: List["ReportReview"] = Relationship(back_populates="reviewer")


class AuditLog(SQLModel, table=True):
    """审计日志表"""
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    action: str = Field(max_length=50, index=True)  # CREATE_REPORT, DELETE_REPORT, etc.
    resource_type: str = Field(max_length=50)       # report, company, user
    resource_id: str = Field(max_length=255)
    details: Optional[str] = None                   # JSON格式详细信息
    ip_address: Optional[str] = Field(max_length=45)
    user_agent: Optional[str] = Field(max_length=500)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # 关系
    user: Optional[User] = Relationship(back_populates="audit_logs")


class ReportReview(SQLModel, table=True):
    """报告审核记录表"""
    __tablename__ = "report_reviews"

    id: Optional[int] = Field(default=None, primary_key=True)
    report_path: str = Field(max_length=500, index=True)  # 报告文件路径
    company_id: str = Field(max_length=100, index=True)
    report_year: int
    report_type: str = Field(max_length=50)  # analysis, factcheck, tracking, legal

    # 审核信息
    reviewer_id: int = Field(foreign_key="users.id")
    status: str = Field(max_length=20, index=True)  # pending, approved, rejected, revision_required
    review_result: Optional[str] = None  # JSON格式审核意见
    reviewed_at: Optional[datetime] = None

    # 报告元数据
    generated_by: str = Field(max_length=100)  # 生成者用户名或"system"
    generated_at: datetime
    version: int = Field(default=1)

    # 内容指纹和服务端 HMAC；保留字段名以兼容已有数据库/API。
    content_hash: str = Field(max_length=64)  # SHA256哈希
    signature: Optional[str] = Field(max_length=500)  # 服务端 HMAC 完整性签名

    # 关系
    reviewer: Optional[User] = Relationship(back_populates="report_reviews")


# ============ 认证服务 ============

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _siq_env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def _siq_int_env(name: str, default: int) -> int:
    try:
        return int(_siq_env(name, str(default)))
    except ValueError:
        return default


def _siq_bool_env(name: str, default: bool = False) -> bool:
    raw = _siq_env(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _auth_secret_from_env() -> str:
    secret = _siq_env("SIQ_AUTH_SECRET_KEY").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "SIQ_AUTH_SECRET_KEY must be set to a non-empty secret of at least 32 characters."
        )
    return secret


class AuthService:
    """认证服务"""

    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = _siq_int_env("SIQ_ACCESS_TOKEN_EXPIRE_MINUTES", 480)
    ACCESS_COOKIE_NAME = _siq_env("SIQ_AUTH_ACCESS_COOKIE_NAME", "siq_access_token")
    CSRF_COOKIE_NAME = _siq_env("SIQ_AUTH_CSRF_COOKIE_NAME", "siq_csrf_token")
    ACCESS_COOKIE_PATH = _siq_env("SIQ_AUTH_COOKIE_PATH", "/")
    PASSWORD_HASH_ITERATIONS = _siq_int_env("SIQ_PASSWORD_HASH_ITERATIONS", 100000)

    @staticmethod
    def secret_key() -> str:
        return _auth_secret_from_env()

    @staticmethod
    def validate_runtime_config() -> None:
        AuthService.secret_key()

    @staticmethod
    def cookie_mode_enabled() -> bool:
        return _siq_bool_env("SIQ_AUTH_COOKIE_MODE")

    @staticmethod
    def access_cookie_max_age_seconds() -> int:
        return max(60, AuthService.ACCESS_TOKEN_EXPIRE_MINUTES * 60)

    @staticmethod
    def access_cookie_secure() -> bool:
        return _siq_bool_env("SIQ_AUTH_COOKIE_SECURE")

    @staticmethod
    def access_cookie_samesite() -> str:
        value = _siq_env("SIQ_AUTH_COOKIE_SAMESITE", "lax").strip().lower()
        return value if value in {"lax", "strict", "none"} else "lax"

    @staticmethod
    def create_csrf_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def csrf_allowed_origins() -> set[str]:
        raw = _siq_env("SIQ_AUTH_CSRF_ALLOWED_ORIGINS", "")
        configured = {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}
        return configured | {
            "http://localhost:15173",
            "http://127.0.0.1:15173",
            "tauri://localhost",
            "https://tauri.localhost",
        }

    @staticmethod
    def hash_password(password: str) -> str:
        """密码哈希"""
        salt = secrets.token_hex(32)
        pwd_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            salt.encode(),
            AuthService.PASSWORD_HASH_ITERATIONS,
        )
        return f"{salt}${pwd_hash.hex()}"

    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        """验证密码"""
        try:
            salt, pwd_hash = hashed_password.split('$', 1)
            new_hash = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode(),
                salt.encode(),
                AuthService.PASSWORD_HASH_ITERATIONS,
            )
            return hmac.compare_digest(new_hash.hex(), pwd_hash)
        except Exception:
            return False

    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建JWT访问令牌"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=AuthService.ACCESS_TOKEN_EXPIRE_MINUTES)

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, AuthService.secret_key(), algorithm=AuthService.ALGORITHM)
        return encoded_jwt

    @staticmethod
    def decode_token(token: str) -> Optional[dict]:
        """解码JWT令牌"""
        try:
            payload = jwt.decode(token, AuthService.secret_key(), algorithms=[AuthService.ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except (getattr(jwt, "JWTError", jwt.PyJWTError), jwt.PyJWTError):
            return None


# ============ 权限检查 ============

class PermissionChecker:
    """权限检查器"""

    @staticmethod
    def has_permission(user: User, permission: str) -> bool:
        """检查用户是否有指定权限"""
        if not user.is_active:
            return False

        user_permissions = ROLE_PERMISSIONS.get(user.role, [])
        return permission in user_permissions

    @staticmethod
    def check_report_access(user: User, action: str) -> bool:
        """检查报告访问权限"""
        permission_map = {
            "create": "report.create",
            "edit": "report.edit",
            "delete": "report.delete",
            "view": "report.view",
            "review": "report.review",
        }

        permission = permission_map.get(action)
        if not permission:
            return False

        return PermissionChecker.has_permission(user, permission)


# ============ 审计日志服务 ============

class AuditLogger:
    """审计日志记录器"""

    @staticmethod
    def log_action(
        session,
        user_id: int,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Optional[dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        """记录审计日志"""
        import json

        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=json.dumps(details, ensure_ascii=False) if details else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        session.add(log)
        session.commit()

        return log


# ============ 报告审核 artifact 与完整性服务 ============


class ReportArtifactError(ValueError):
    """A report artifact failed the server-side path or content policy."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReportArtifact:
    root: Path
    path: Path
    identity: str
    content: str


class ReportArtifactPolicy:
    """Resolve caller input to a bounded, regular UTF-8 artifact."""

    DEFAULT_MAX_BYTES = 10 * 1024 * 1024

    @staticmethod
    def root() -> Path:
        configured = os.getenv("SIQ_REPORT_REVIEW_ROOT", "").strip()
        return Path(configured).expanduser().resolve() if configured else ARTIFACTS_ROOT.resolve()

    @classmethod
    def max_bytes(cls) -> int:
        try:
            return max(1, int(os.getenv("SIQ_REPORT_REVIEW_MAX_BYTES", str(cls.DEFAULT_MAX_BYTES))))
        except ValueError:
            return cls.DEFAULT_MAX_BYTES

    @staticmethod
    def _reject_symlink_components(root: Path, relative_path: Path) -> None:
        current = root
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise ReportArtifactError("invalid_path")

    @classmethod
    def resolve_and_read(cls, raw_path: str, *, max_bytes: int | None = None) -> ReportArtifact:
        raw_value = str(raw_path or "").strip()
        if not raw_value or "\x00" in raw_value or "\\" in raw_value:
            raise ReportArtifactError("invalid_path")

        requested = Path(raw_value).expanduser()
        if ".." in requested.parts:
            raise ReportArtifactError("invalid_path")

        root = cls.root()
        candidate = requested if requested.is_absolute() else root / requested
        resolved = candidate.resolve(strict=False)
        try:
            relative_path = resolved.relative_to(root)
        except ValueError as exc:
            raise ReportArtifactError("invalid_path") from exc
        if not relative_path.parts:
            raise ReportArtifactError("invalid_path")

        cls._reject_symlink_components(root, relative_path)
        try:
            file_stat = resolved.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ReportArtifactError("not_found") from exc
        except OSError as exc:
            raise ReportArtifactError("invalid_path") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise ReportArtifactError("invalid_path")

        max_bytes = cls.max_bytes() if max_bytes is None else max(1, max_bytes)
        if file_stat.st_size > max_bytes:
            raise ReportArtifactError("too_large")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(resolved, flags)
        except OSError as exc:
            raise ReportArtifactError("invalid_path") from exc
        try:
            with os.fdopen(descriptor, "rb") as report_file:
                current_stat = os.fstat(report_file.fileno())
                if not stat.S_ISREG(current_stat.st_mode):
                    raise ReportArtifactError("invalid_path")
                content_bytes = report_file.read(max_bytes + 1)
        except ReportArtifactError:
            raise
        except OSError as exc:
            raise ReportArtifactError("invalid_path") from exc
        if len(content_bytes) > max_bytes:
            raise ReportArtifactError("too_large")
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReportArtifactError("unsupported_encoding") from exc

        return ReportArtifact(
            root=root,
            path=resolved,
            identity=relative_path.as_posix(),
            content=content,
        )

class ReportSignature:
    """报告内容指纹与服务端 HMAC 完整性签名。"""

    SIGNATURE_PREFIX = "hmac-sha256:v1"

    @staticmethod
    def calculate_hash(content: str) -> str:
        """计算可公开比较的内容指纹。"""
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def _signature_digest(content_hash: str, user_id: int) -> str:
        message = f"siq-report-review:v1\n{user_id}\n{content_hash}".encode()
        return hmac.new(AuthService.secret_key().encode(), message, hashlib.sha256).hexdigest()

    @staticmethod
    def sign_report(content: str, user_id: int) -> str:
        """使用仅服务端持有的认证密钥签名报告内容和审核人身份。"""
        content_hash = ReportSignature.calculate_hash(content)
        digest = ReportSignature._signature_digest(content_hash, user_id)
        return f"{ReportSignature.SIGNATURE_PREFIX}:{user_id}:{digest}"

    @staticmethod
    def verify_signature(
        content: str,
        content_hash: str,
        signature: str | None = None,
        *,
        user_id: int | None = None,
    ) -> bool:
        """验证内容指纹以及服务端 HMAC；旧的无签名调用安全地返回 False。"""
        current_hash = ReportSignature.calculate_hash(content)
        if not hmac.compare_digest(current_hash, str(content_hash or "")) or not signature:
            return False
        try:
            prefix, version, signed_user_id, supplied_digest = signature.split(":", 3)
            if f"{prefix}:{version}" != ReportSignature.SIGNATURE_PREFIX:
                return False
            parsed_user_id = int(signed_user_id)
        except (TypeError, ValueError):
            return False
        if user_id is not None and parsed_user_id != user_id:
            return False
        expected_digest = ReportSignature._signature_digest(content_hash, parsed_user_id)
        return hmac.compare_digest(supplied_digest, expected_digest)


# ============ Pydantic模型（API接口） ============

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: str
    role: UserRole = UserRole.VIEWER


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    approval_status: Optional[str] = None
    approval_note: Optional[str] = None
    is_active: Optional[bool] = None


class UserBatchUpdate(BaseModel):
    user_ids: List[int]
    role: Optional[UserRole] = None
    approval_status: Optional[str] = None
    approval_note: Optional[str] = None
    is_active: Optional[bool] = None


class ReportReviewCreate(BaseModel):
    report_path: str
    company_id: str
    report_year: int
    report_type: str
    status: str
    review_result: Optional[dict] = None


if __name__ == "__main__":
    # 示例：创建超级管理员
    auth = AuthService()

    # 生成密码哈希
    password = os.getenv("SIQ_SAMPLE_PASSWORD", "")
    if not password:
        raise RuntimeError("Set SIQ_SAMPLE_PASSWORD before running this module directly.")
    hashed = auth.hash_password(password)
    print(f"超级管理员密码哈希: {hashed}")

    # 创建访问令牌
    token = auth.create_access_token({"sub": "admin", "role": "super_admin"})
    print(f"访问令牌: {token}")

    # 验证密码
    is_valid = auth.verify_password(password, hashed)
    print(f"密码验证: {is_valid}")
