"""
SIQ Tracking API Router

提供跟踪与预警的RESTful API端点
"""

from datetime import datetime
from fastapi import APIRouter, HTTPException
from typing import Optional

from agents.tracking.agent import TrackingAgent
from agents.tracking.paths import tracking_base_path
from agents.tracking.schemas import (
    TrackingDashboard,
    SentimentDailyReport,
    MetricTrackingPanel,
    AlertReport,
)

router = APIRouter(prefix="/tracking", tags=["tracking"])


_tracking_agent: TrackingAgent | None = None


def _get_tracking_agent() -> TrackingAgent:
    global _tracking_agent
    if _tracking_agent is None:
        _tracking_agent = TrackingAgent()
    return _tracking_agent


def _company_tracking_dirs(stock_code: str) -> list[str]:
    import glob
    import os

    pattern = os.path.join(str(tracking_base_path()), f"{stock_code}-*", "tracking")
    return glob.glob(pattern)


@router.post("/process", response_model=TrackingDashboard)
async def process_report(
    stock_code: str,
    company_name: str,
    report_text: str,
    metrics_data: Optional[dict] = None,
    previous_metrics: Optional[dict] = None,
    year_ago_metrics: Optional[dict] = None,
):
    """
    处理分析报告，生成完整跟踪面板

    - 提取跟踪事项
    - 收集舆情数据
    - 追踪指标变化
    - 触发预警
    """
    try:
        dashboard = await _get_tracking_agent().process_report(
            stock_code=stock_code,
            company_name=company_name,
            report_text=report_text,
            metrics_data=metrics_data,
            previous_metrics=previous_metrics,
            year_ago_metrics=year_ago_metrics,
        )
        return dashboard
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/{stock_code}", response_model=TrackingDashboard)
async def get_dashboard(stock_code: str, company_name: Optional[str] = None):
    """获取指定股票的跟踪面板"""
    if not company_name:
        company_name = stock_code

    dashboard = _get_tracking_agent().get_dashboard(stock_code, company_name)
    if not dashboard:
        raise HTTPException(status_code=404, detail="跟踪面板不存在")

    return dashboard


@router.post("/sentiment/refresh", response_model=SentimentDailyReport)
async def refresh_sentiment(stock_code: str, company_name: str):
    """刷新舆情数据并生成日报"""
    try:
        report = await _get_tracking_agent().refresh_sentiment(stock_code, company_name)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metrics/refresh", response_model=MetricTrackingPanel)
async def refresh_metrics(
    stock_code: str,
    company_name: str,
    report_period: str,
    current_data: dict,
    previous_data: Optional[dict] = None,
    year_ago_data: Optional[dict] = None,
):
    """刷新指标追踪面板"""
    try:
        panel = _get_tracking_agent().refresh_metrics(
            stock_code=stock_code,
            company_name=company_name,
            report_period=report_period,
            current_data=current_data,
            previous_data=previous_data,
            year_ago_data=year_ago_data,
        )
        return panel
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts/{stock_code}")
async def get_alerts(stock_code: str, level: Optional[str] = None):
    """获取预警列表"""
    # 从文件系统读取预警记录
    import os

    dirs = _company_tracking_dirs(stock_code)

    if not dirs:
        return {"stock_code": stock_code, "alerts": []}

    alerts_dir = os.path.join(dirs[0], "alerts")
    if not os.path.exists(alerts_dir):
        return {"stock_code": stock_code, "alerts": []}

    alert_files = sorted(os.listdir(alerts_dir), reverse=True)
    alerts = []

    for f in alert_files[:20]:  # 最近20条
        if f.endswith(".md"):
            parts = f.replace(".md", "").split("-")
            if len(parts) >= 3:
                alert_level = parts[2].upper() if len(parts) > 2 else "UNKNOWN"
                if level and alert_level != level.upper():
                    continue

                alerts.append({
                    "file": f,
                    "date": parts[0],
                    "level": alert_level,
                    "path": os.path.join(alerts_dir, f),
                })

    return {
        "stock_code": stock_code,
        "count": len(alerts),
        "alerts": alerts,
    }


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """确认预警已处理"""
    # 实际应更新数据库状态
    return {"alert_id": alert_id, "status": "acknowledged", "acknowledged_at": datetime.now().isoformat()}


@router.get("/items/{stock_code}")
async def get_tracking_items(stock_code: str, category: Optional[str] = None):
    """获取跟踪事项列表"""
    import os

    dirs = _company_tracking_dirs(stock_code)

    if not dirs:
        return {"stock_code": stock_code, "items": []}

    items_path = os.path.join(dirs[0], "tracking-items.md")
    if not os.path.exists(items_path):
        return {"stock_code": stock_code, "items": []}

    with open(items_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 简化返回
    return {
        "stock_code": stock_code,
        "path": items_path,
        "content_preview": content[:1000] + "..." if len(content) > 1000 else content,
    }
