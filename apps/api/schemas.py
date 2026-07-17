from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    level: int
    xp: int
    xp_to_next: int
    hunger: int
    mood: int
    energy: int


class AgentActionResponse(BaseModel):
    agent: AgentStateResponse
    new_achievements: list["AchievementResponse"]


class ChatContextCompany(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: Optional[str] = None
    name: Optional[str] = None
    dir: Optional[str] = None
    market: Optional[str] = None
    company_id: Optional[str] = None
    filing_id: Optional[str] = None
    parse_run_id: Optional[str] = None
    company_key: Optional[str] = None


class ChatContextReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    mtime: Optional[str] = None
    market: Optional[str] = None
    company_id: Optional[str] = None
    filing_id: Optional[str] = None
    parse_run_id: Optional[str] = None
    artifact_id: Optional[str] = None
    report_id: Optional[str] = None
    source_report_id: Optional[str] = None
    source_family: Optional[str] = None


class ChatContextPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: Optional[str] = None


class ResearchIdentity(BaseModel):
    market: Optional[str] = None
    company_id: Optional[str] = None
    filing_id: Optional[str] = None
    parse_run_id: Optional[str] = None


class ChatContextSourceReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_id: Optional[str] = None
    market: Optional[str] = None
    company_id: Optional[str] = None
    filing_id: Optional[str] = None
    parse_run_id: Optional[str] = None
    source_family: Optional[str] = None
    document_format: Optional[str] = None
    report_type: Optional[str] = None
    form_type: Optional[str] = None
    fiscal_year: Optional[int] = None
    period_end: Optional[str] = None
    published_at: Optional[str] = None
    accounting_standard: Optional[str] = None
    reporting_currency: Optional[str] = None
    quality_status: Optional[str] = None
    filename: Optional[str] = None
    baseline_analysis_artifact_id: Optional[str] = None


class ChatContextResearchTarget(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Optional[str] = None
    company_key: Optional[str] = None
    company_wiki_id: Optional[str] = None
    display_code: Optional[str] = None
    display_name: Optional[str] = None
    research_identity: Optional[ResearchIdentity] = None
    source_report: Optional[ChatContextSourceReport] = None


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    domain: Optional[str] = None
    deal_id: Optional[str] = None
    profile_id: Optional[str] = None
    retrieval_query: Optional[str] = None
    company: Optional[ChatContextCompany] = None
    report: Optional[ChatContextReport] = None
    page: Optional[ChatContextPage] = None
    research_identity: Optional[ResearchIdentity] = None
    source_report: Optional[ChatContextSourceReport] = None
    research_target: Optional[ChatContextResearchTarget] = None
    market: Optional[str] = None
    company_key: Optional[str] = None
    report_id: Optional[str] = None
    upstream_analysis_artifact_id: Optional[str] = None
    company_id: Optional[str] = None
    filing_id: Optional[str] = None
    parse_run_id: Optional[str] = None


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


class ChatVoiceTranscriptionResponse(BaseModel):
    text: str
    duration: float
    language: str
    provider: str
    attachment: ChatAttachment


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    display_message: Optional[str] = None
    context: Optional[ChatContext] = None
    attachments: list[ChatAttachment] = Field(default_factory=list)
    runtime_target: Optional[Literal["host", "openshell"]] = None


class ChatResponse(BaseModel):
    reply: str
    new_achievements: list["AchievementResponse"]
    audit_trace_id: Optional[str] = None
    artifact: Optional[dict[str, Any]] = None


class ChatHistoryMessageResponse(BaseModel):
    id: Optional[int] = None
    session_id: str
    role: str
    content: str
    created_at: Optional[datetime] = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    audit_trace_id: Optional[str] = None
    research_identity: Optional[ResearchIdentity] = None


class ChatHistoryResponse(BaseModel):
    messages: list[ChatHistoryMessageResponse]
    session_id: str


class AnswerAuditTraceResponse(BaseModel):
    trace_id: str
    trace: dict[str, Any]


class AchievementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    icon: str
    unlocked_at: Optional[datetime] = None
    progress: int
    target: int


AgentActionResponse.model_rebuild()
ChatResponse.model_rebuild()
