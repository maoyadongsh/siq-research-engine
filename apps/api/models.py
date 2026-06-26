from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime


class PetState(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    name: str = Field(default="Taiyi")
    level: int = Field(default=1)
    xp: int = Field(default=0)
    hunger: int = Field(default=80, ge=0, le=100)
    mood: int = Field(default=80, ge=0, le=100)
    energy: int = Field(default=80, ge=0, le=100)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(default="default", index=True)
    role: str  # "user" or "assistant"
    content: str
    attachments_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatSessionMemory(SQLModel, table=True):
    profile: str = Field(primary_key=True)
    session_id: str = Field(primary_key=True, index=True)
    summary: str = Field(default="")
    last_message_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Achievement(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    description: str
    icon: str = Field(default="star")
    unlocked_at: Optional[datetime] = None
    progress: int = Field(default=0)
    target: int = Field(default=1)


class InteractionLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    action: str  # "chat", "feed", "play", "rest"
    created_at: datetime = Field(default_factory=datetime.utcnow)
