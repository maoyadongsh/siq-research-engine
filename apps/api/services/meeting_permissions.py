"""Meeting-domain RBAC constants and object-owner helpers.

This is deliberately separate from the existing global role map so the new
domain can be deployed behind its feature flag without changing old routes.
"""

from __future__ import annotations

from fastapi import HTTPException

from services.auth_service import User, UserRole

MEETING_READ = "meeting.read"
MEETING_CREATE = "meeting.create"
MEETING_UPDATE = "meeting.update"
MEETING_DELETE = "meeting.delete"
MEETING_EXPORT = "meeting.export"
MEETING_VOICEPRINT_MANAGE = "meeting.voiceprint.manage"
MEETING_ADMIN = "meeting.admin"

ALL_MEETING_PERMISSIONS = frozenset(
    {
        MEETING_READ,
        MEETING_CREATE,
        MEETING_UPDATE,
        MEETING_DELETE,
        MEETING_EXPORT,
        MEETING_VOICEPRINT_MANAGE,
        MEETING_ADMIN,
    }
)

ROLE_MEETING_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.SUPER_ADMIN: ALL_MEETING_PERMISSIONS,
    UserRole.ADMIN: ALL_MEETING_PERMISSIONS,
    UserRole.ANALYST: frozenset(
        {
            MEETING_READ,
            MEETING_CREATE,
            MEETING_UPDATE,
            MEETING_DELETE,
            MEETING_EXPORT,
            MEETING_VOICEPRINT_MANAGE,
        }
    ),
    UserRole.REVIEWER: frozenset({MEETING_READ, MEETING_CREATE, MEETING_UPDATE, MEETING_EXPORT}),
    UserRole.VIEWER: frozenset({MEETING_READ}),
}


def meeting_user_id(user: User) -> int:
    user_id = getattr(user, "id", None)
    if user_id is None:
        raise HTTPException(status_code=401, detail={"code": "AUTH_USER_ID_MISSING"})
    return int(user_id)


def has_meeting_permission(user: User, permission: str) -> bool:
    if not getattr(user, "is_active", False):
        return False
    try:
        role = UserRole(user.role)
    except ValueError:
        return False
    return permission in ROLE_MEETING_PERMISSIONS.get(role, frozenset())


def require_meeting_permission(user: User, permission: str) -> None:
    if not has_meeting_permission(user, permission):
        raise HTTPException(
            status_code=403,
            detail={"code": "MEETING_PERMISSION_DENIED", "permission": permission},
        )


def hide_cross_owner_resource(owner_user_id: int, user: User) -> None:
    """Return a uniform 404 for object identifiers outside the owner scope."""

    if int(owner_user_id) != meeting_user_id(user):
        raise HTTPException(status_code=404, detail={"code": "MEETING_RESOURCE_NOT_FOUND"})
