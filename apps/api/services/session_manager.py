"""
多租户会话管理服务
支持基于Redis的用户隔离会话存储
"""
import json
import os
import uuid
from typing import Optional, Dict, Any
from datetime import datetime
import redis
from fastapi import HTTPException


ADMIN_SESSION_ROLES = {"admin", "super_admin"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _prefixed_int_env(siq_name: str, legacy_name: str, default: int) -> int:
    try:
        return int(os.getenv(siq_name) or os.getenv(legacy_name) or str(default))
    except ValueError:
        return default


def _role_value(user_role: Any | None) -> str:
    return str(getattr(user_role, "value", user_role) or "").strip().lower()


def keeps_sessions_forever(user_role: Any | None) -> bool:
    return _role_value(user_role) in ADMIN_SESSION_ROLES


class SessionManager:
    """多租户会话管理器"""

    def __init__(self, redis_url: str = "redis://localhost:16379/0"):
        self.redis_client = None
        self.memory_sessions: dict[str, dict[str, Any]] = {}
        self.memory_user_sessions: dict[str, list[str]] = {}
        self.memory_current_sessions: dict[str, str] = {}
        try:
            client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=2,
                health_check_interval=30,
            )
            client.ping()
            self.redis_client = client
        except Exception:
            self.redis_client = None
        # 默认不按时间过期；普通用户按数量保留，管理员永久保留。
        self.session_ttl = _prefixed_int_env("SIQ_SESSION_TTL_SECONDS", "SIQ_SESSION_TTL_SECONDS", 0)
        self.default_keep_count = _prefixed_int_env("SIQ_USER_SESSION_KEEP_COUNT", "SIQ_USER_SESSION_KEEP_COUNT", 100)

    def _user_sessions_key(self, user_id: str, profile: str) -> str:
        return f"user:{user_id}:sessions:{profile}"

    def _memory_key(self, user_id: str, profile: str) -> str:
        return f"{user_id}:{profile}"

    def _current_session_key(self, user_id: str, profile: str) -> str:
        return f"user:{user_id}:current_session:{profile}"

    def _store_session(self, session_id: str, session_data: dict[str, Any], *, persistent: bool = False) -> None:
        if self.redis_client is None:
            self.memory_sessions[session_id] = session_data
            return

        payload = json.dumps(session_data)
        if self.session_ttl > 0 and not persistent:
            self.redis_client.setex(f"session:{session_id}", self.session_ttl, payload)
        else:
            self.redis_client.set(f"session:{session_id}", payload)

    def create_session(
        self,
        user_id: str,
        profile: str = "assistant",
        *,
        user_role: Any | None = None,
        keep_count: int | None = None,
        return_deleted: bool = False,
    ):
        """为用户创建新会话"""
        session_id = f"user-{user_id}-{profile}-{uuid.uuid4().hex[:8]}"
        keep_forever = keeps_sessions_forever(user_role)
        effective_keep_count = self.default_keep_count if keep_count is None else keep_count

        # 存储会话元数据
        session_data = {
            "user_id": user_id,
            "profile": profile,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "user_role": _role_value(user_role),
            "message_count": 0
        }

        deleted_session_ids: list[str] = []
        if self.redis_client is None:
            self._store_session(session_id, session_data, persistent=keep_forever)
            key = self._memory_key(user_id, profile)
            sessions = [sid for sid in self.memory_user_sessions.get(key, []) if sid != session_id]
            self.memory_user_sessions[key] = [session_id, *sessions]
            self.memory_current_sessions[key] = session_id
            if not keep_forever:
                deleted_session_ids = self.cleanup_old_sessions(user_id, profile, effective_keep_count)
            return (session_id, deleted_session_ids) if return_deleted else session_id

        self._store_session(session_id, session_data, persistent=keep_forever)

        # 添加到用户的会话列表
        user_sessions_key = self._user_sessions_key(user_id, profile)
        self.redis_client.lrem(user_sessions_key, 0, session_id)
        self.redis_client.lpush(user_sessions_key, session_id)
        self.redis_client.set(self._current_session_key(user_id, profile), session_id)
        if self.session_ttl > 0 and not keep_forever:
            self.redis_client.expire(user_sessions_key, self.session_ttl)
            self.redis_client.expire(self._current_session_key(user_id, profile), self.session_ttl)

        if not keep_forever:
            deleted_session_ids = self.cleanup_old_sessions(user_id, profile, effective_keep_count)

        return (session_id, deleted_session_ids) if return_deleted else session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        if self.redis_client is None:
            return self.memory_sessions.get(session_id)

        session_key = f"session:{session_id}"
        data = self.redis_client.get(session_key)

        if not data:
            return None

        return json.loads(data)

    def set_current_session(self, user_id: str, profile: str, session_id: str) -> str:
        """设置当前会话，并把它移动到用户会话列表前面。"""
        self.validate_session(session_id, user_id, profile)

        if self.redis_client is None:
            key = self._memory_key(user_id, profile)
            sessions = [sid for sid in self.memory_user_sessions.get(key, []) if sid != session_id]
            self.memory_user_sessions[key] = [session_id, *sessions]
            self.memory_current_sessions[key] = session_id
            return session_id

        current_key = self._current_session_key(user_id, profile)
        user_sessions_key = self._user_sessions_key(user_id, profile)
        self.redis_client.set(current_key, session_id)
        self.redis_client.lrem(user_sessions_key, 0, session_id)
        self.redis_client.lpush(user_sessions_key, session_id)
        return session_id

    def restore_session(
        self,
        user_id: str,
        profile: str,
        session_id: str,
        *,
        user_role: Any | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        message_count: int = 0,
    ) -> str:
        """Restore a DB-backed chat session into the runtime session index."""
        session_data = {
            "user_id": user_id,
            "profile": profile,
            "created_at": created_at or datetime.utcnow().isoformat(),
            "updated_at": updated_at or datetime.utcnow().isoformat(),
            "user_role": _role_value(user_role),
            "message_count": int(message_count or 0),
        }
        keep_forever = keeps_sessions_forever(user_role)
        self._store_session(session_id, session_data, persistent=keep_forever)

        if self.redis_client is None:
            key = self._memory_key(user_id, profile)
            sessions = [sid for sid in self.memory_user_sessions.get(key, []) if sid != session_id]
            self.memory_user_sessions[key] = [session_id, *sessions]
            self.memory_current_sessions[key] = session_id
            return session_id

        user_sessions_key = self._user_sessions_key(user_id, profile)
        self.redis_client.lrem(user_sessions_key, 0, session_id)
        self.redis_client.lpush(user_sessions_key, session_id)
        self.redis_client.set(self._current_session_key(user_id, profile), session_id)
        if self.session_ttl > 0 and not keep_forever:
            self.redis_client.expire(user_sessions_key, self.session_ttl)
            self.redis_client.expire(self._current_session_key(user_id, profile), self.session_ttl)
        return session_id

    def get_current_session_id(self, user_id: str, profile: str = "assistant") -> Optional[str]:
        """获取用户当前会话；若当前会话失效，则回退到最近一条。"""
        if self.redis_client is None:
            key = self._memory_key(user_id, profile)
            session_id = self.memory_current_sessions.get(key)
            if session_id and self.memory_sessions.get(session_id):
                return session_id
            self.memory_current_sessions.pop(key, None)
            sessions = self.memory_user_sessions.get(key, [])
            for sid in sessions:
                if sid in self.memory_sessions:
                    self.memory_current_sessions[key] = sid
                    return sid
            return None

        current_key = self._current_session_key(user_id, profile)
        session_id = self.redis_client.get(current_key)
        if session_id and self.get_session(session_id):
            return session_id
        if session_id:
            self.redis_client.delete(current_key)

        sessions = self.list_user_sessions(user_id, profile, limit=1)
        if not sessions:
            return None
        latest = str(sessions[0].get("session_id") or "")
        if latest:
            self.redis_client.set(current_key, latest)
        return latest or None

    def validate_session(self, session_id: str, user_id: str, profile: str | None = None) -> bool:
        """验证会话属于该用户"""
        session_data = self.get_session(session_id)

        if not session_data:
            raise HTTPException(404, "Session not found or expired")

        if session_data["user_id"] != user_id:
            raise HTTPException(403, "Session does not belong to this user")

        if profile is not None and session_data.get("profile") != profile:
            raise HTTPException(404, "Session not found for this agent")

        return True

    def delete_session(self, session_id: str, user_id: str):
        """删除会话"""
        self.validate_session(session_id, user_id)

        session_data = self.get_session(session_id)
        if session_data:
            profile = session_data["profile"]

            if self.redis_client is None:
                self.memory_sessions.pop(session_id, None)
                key = self._memory_key(user_id, profile)
                self.memory_user_sessions[key] = [
                    sid for sid in self.memory_user_sessions.get(key, []) if sid != session_id
                ]
                if self.memory_current_sessions.get(key) == session_id:
                    self.memory_current_sessions.pop(key, None)
                return

            # 从Redis删除
            self.redis_client.delete(f"session:{session_id}")

            # 从用户会话列表移除
            user_sessions_key = self._user_sessions_key(user_id, profile)
            self.redis_client.lrem(user_sessions_key, 0, session_id)
            current_key = self._current_session_key(user_id, profile)
            if self.redis_client.get(current_key) == session_id:
                self.redis_client.delete(current_key)

    def list_user_sessions(self, user_id: str, profile: str = "assistant", limit: int | None = 100) -> list:
        """列出用户的所有会话"""
        if self.redis_client is None:
            session_ids = self.memory_user_sessions.get(self._memory_key(user_id, profile), [])
            if limit and limit > 0:
                session_ids = session_ids[:limit]
            return [
                {"session_id": sid, **self.memory_sessions[sid]}
                for sid in session_ids
                if sid in self.memory_sessions
            ]

        user_sessions_key = self._user_sessions_key(user_id, profile)
        end = -1 if limit is None or limit <= 0 else limit - 1
        session_ids = self.redis_client.lrange(user_sessions_key, 0, end)

        sessions = []
        seen: set[str] = set()
        for sid in session_ids:
            if sid in seen:
                continue
            seen.add(sid)
            session_data = self.get_session(sid)
            if session_data:
                sessions.append({
                    "session_id": sid,
                    **session_data
                })
            else:
                self.redis_client.lrem(user_sessions_key, 0, sid)

        return sessions

    def get_latest_session_id(self, user_id: str, profile: str = "assistant") -> Optional[str]:
        """获取用户最近的会话ID。"""
        return self.get_current_session_id(user_id, profile)

    def increment_message_count(self, session_id: str):
        """增加消息计数"""
        session_data = self.get_session(session_id)
        if session_data:
            session_data["message_count"] += 1
            session_data["updated_at"] = datetime.utcnow().isoformat()
            if self.redis_client is None:
                self.memory_sessions[session_id] = session_data
                return

            persistent = self.session_ttl <= 0 or keeps_sessions_forever(session_data.get("user_role"))
            self._store_session(session_id, session_data, persistent=persistent)

    def cleanup_old_sessions(self, user_id: str, profile: str, keep_count: int | None = 100) -> list[str]:
        """清理用户的旧会话，保留最近的N条"""
        if keep_count is None or keep_count <= 0:
            return []

        if self.redis_client is None:
            key = self._memory_key(user_id, profile)
            all_sessions = self.memory_user_sessions.get(key, [])
            deleted_session_ids = all_sessions[keep_count:]
            for sid in all_sessions[keep_count:]:
                self.memory_sessions.pop(sid, None)
            self.memory_user_sessions[key] = all_sessions[:keep_count]
            if self.memory_current_sessions.get(key) in deleted_session_ids:
                self.memory_current_sessions.pop(key, None)
            return deleted_session_ids

        user_sessions_key = self._user_sessions_key(user_id, profile)

        # 获取所有会话ID
        all_sessions = self.redis_client.lrange(user_sessions_key, 0, -1)

        if len(all_sessions) > keep_count:
            # 删除超出限制的旧会话
            sessions_to_delete = all_sessions[keep_count:]

            for sid in sessions_to_delete:
                self.redis_client.delete(f"session:{sid}")

            # 修剪列表
            self.redis_client.ltrim(user_sessions_key, 0, keep_count - 1)
            current_key = self._current_session_key(user_id, profile)
            if self.redis_client.get(current_key) in sessions_to_delete:
                self.redis_client.delete(current_key)
            return sessions_to_delete

        return []


# 全局实例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取会话管理器单例"""
    global _session_manager

    if _session_manager is None:
        import os
        redis_url = os.getenv("REDIS_URL", "redis://localhost:16379/0")
        _session_manager = SessionManager(redis_url)

    return _session_manager
