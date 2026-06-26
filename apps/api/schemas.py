from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime


class PetStateResponse(BaseModel):
    name: str
    level: int
    xp: int
    xp_to_next: int
    hunger: int
    mood: int
    energy: int

    class Config:
        from_attributes = True


class ActionResponse(BaseModel):
    pet: PetStateResponse
    new_achievements: list["AchievementResponse"]


class ChatContextCompany(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    dir: Optional[str] = None


class ChatContextReport(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    mtime: Optional[str] = None


class ChatContextPage(BaseModel):
    title: Optional[str] = None


class ChatContext(BaseModel):
    company: Optional[ChatContextCompany] = None
    report: Optional[ChatContextReport] = None
    page: Optional[ChatContextPage] = None


class ChatAttachmentUpload(BaseModel):
    filename: str
    content_type: str
    data_url: str


class ChatAttachment(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int
    path: str
    url: Optional[str] = None
    kind: str = "image"
    metadata: Optional[dict[str, Any]] = None


class ChatAttachmentUploadRequest(BaseModel):
    files: list[ChatAttachmentUpload]


class ChatAttachmentUploadResponse(BaseModel):
    attachments: list[ChatAttachment]


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    display_message: Optional[str] = None
    context: Optional[ChatContext] = None
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    new_achievements: list["AchievementResponse"]


class AchievementResponse(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    unlocked_at: Optional[datetime] = None
    progress: int
    target: int

    class Config:
        from_attributes = True


ActionResponse.model_rebuild()
ChatResponse.model_rebuild()
