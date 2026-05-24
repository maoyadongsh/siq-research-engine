"""
FinSight Tracking 数据模型
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


class TrackingItemCategory(str, Enum):
    """跟踪事项分类"""
    FINANCIAL_COMMITMENT = "财务承诺"
    RISK_SIGNAL = "风险信号"
    ABNORMAL_METRIC = "异常指标"
    RELATED_TRANSACTION = "关联交易"
    ACCOUNTING_CHANGE = "会计变更"
    REGULATORY_DYNAMIC = "监管动态"
    MAJOR_EVENT = "重大事项"
    INDUSTRY_CHANGE = "行业变化"


class AlertLevel(str, Enum):
    """预警级别"""
    INFO = "INFO"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class SentimentPolarity(str, Enum):
    """舆情极性"""
    POSITIVE = "正面"
    NEGATIVE = "负面"
    NEUTRAL = "中性"


class TrackingItem(SQLModel, table=True):
    """跟踪事项"""
    __tablename__ = "tracking_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True, description="股票代码")
    company_name: str = Field(description="公司名称")
    category: str = Field(description="事项分类")
    title: str = Field(description="事项标题")
    description: str = Field(description="详细描述")
    due_date: Optional[datetime] = Field(default=None, description="到期日")
    threshold_value: Optional[str] = Field(default=None, description="阈值")
    verification_method: Optional[str] = Field(default=None, description="验证方式")
    status: str = Field(default="active", description="状态: active/resolved/expired")
    source_report: Optional[str] = Field(default=None, description="来源报告路径")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = Field(default=None)


class SentimentRecord(SQLModel, table=True):
    """舆情记录"""
    __tablename__ = "sentiment_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True)
    source: str = Field(description="数据来源")
    title: str = Field(description="标题")
    url: Optional[str] = Field(default=None)
    polarity: str = Field(description="正面/负面/中性")
    score: float = Field(default=0.0, description="情感得分 -1~1")
    summary: Optional[str] = Field(default=None)
    published_at: Optional[datetime] = Field(default=None)
    collected_at: datetime = Field(default_factory=datetime.utcnow)


class MetricSnapshot(SQLModel, table=True):
    """指标快照"""
    __tablename__ = "metric_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True)
    report_period: str = Field(description="报告期，如 2025-Q1")
    metric_name: str = Field(description="指标名称")
    metric_value: float = Field(description="指标值")
    unit: Optional[str] = Field(default=None)
    yoy_change: Optional[float] = Field(default=None, description="同比变化%")
    qoq_change: Optional[float] = Field(default=None, description="环比变化%")
    deviation: Optional[float] = Field(default=None, description="偏离度%")
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class AlertRecord(SQLModel, table=True):
    """预警记录"""
    __tablename__ = "alert_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True)
    alert_level: str = Field(description="INFO/WATCH/WARNING/CRITICAL")
    alert_type: str = Field(description="阈值突破/负面舆情/监管处罚/其他")
    title: str = Field(description="预警标题")
    description: str = Field(description="详细说明")
    triggered_by: Optional[str] = Field(default=None, description="触发源")
    metric_value: Optional[str] = Field(default=None)
    threshold_value: Optional[str] = Field(default=None)
    is_acknowledged: bool = Field(default=False)
    acknowledged_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReportUpdate(SQLModel, table=True):
    """报告更新记录"""
    __tablename__ = "report_updates"

    id: Optional[int] = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True)
    original_report_path: str = Field(description="原始报告路径")
    update_type: str = Field(description="跟踪更新/指标更新/预警更新")
    content: str = Field(description="更新内容")
    update_date: datetime = Field(default_factory=datetime.utcnow)
    archived_path: Optional[str] = Field(default=None, description="归档路径")
