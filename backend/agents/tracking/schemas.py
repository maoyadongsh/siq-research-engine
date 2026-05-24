"""
FinSight Tracking Schemas (Pydantic)
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TrackingItemCreate(BaseModel):
    stock_code: str
    company_name: str
    category: str
    title: str
    description: str
    due_date: Optional[datetime] = None
    threshold_value: Optional[str] = None
    verification_method: Optional[str] = None
    source_report: Optional[str] = None


class TrackingItemResponse(BaseModel):
    id: int
    stock_code: str
    company_name: str
    category: str
    title: str
    description: str
    due_date: Optional[datetime]
    threshold_value: Optional[str]
    verification_method: Optional[str]
    status: str
    source_report: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SentimentRecordResponse(BaseModel):
    id: int
    stock_code: str
    source: str
    title: str
    url: Optional[str]
    polarity: str
    score: float
    summary: Optional[str]
    published_at: Optional[datetime]
    collected_at: datetime

    class Config:
        from_attributes = True


class MetricSnapshotResponse(BaseModel):
    id: int
    stock_code: str
    report_period: str
    metric_name: str
    metric_value: float
    unit: Optional[str]
    yoy_change: Optional[float]
    qoq_change: Optional[float]
    deviation: Optional[float]
    recorded_at: datetime

    class Config:
        from_attributes = True


class AlertRecordResponse(BaseModel):
    id: int
    stock_code: str
    alert_level: str
    alert_type: str
    title: str
    description: str
    triggered_by: Optional[str]
    metric_value: Optional[str]
    threshold_value: Optional[str]
    is_acknowledged: bool
    acknowledged_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class SentimentDailyReport(BaseModel):
    """舆情日报"""
    stock_code: str
    company_name: str
    report_date: str
    total_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    avg_score: float
    highlights: list[dict]
    risk_signals: list[dict]
    markdown: str


class MetricTrackingPanel(BaseModel):
    """指标追踪面板"""
    stock_code: str
    company_name: str
    report_period: str
    metrics: list[dict]
    changes_summary: dict
    abnormal_flags: list[dict]
    markdown: str


class AlertReport(BaseModel):
    """预警报告"""
    stock_code: str
    company_name: str
    alert_level: str
    alert_type: str
    title: str
    description: str
    recommendation: str
    triggered_at: datetime
    markdown: str


class TrackingDashboard(BaseModel):
    """跟踪面板综合视图"""
    stock_code: str
    company_name: str
    active_items: list[TrackingItemResponse]
    latest_sentiment: Optional[SentimentDailyReport]
    latest_metrics: Optional[MetricTrackingPanel]
    recent_alerts: list[AlertRecordResponse]
    summary: str
