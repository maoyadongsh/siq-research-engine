"""
FinSight Tracking Agent - 智能跟踪与预警系统

职责：提取跟踪事项、监控舆情、追踪指标、触发预警
模型：kimi-for-coding（监控任务）+ kimi-k2-thinking（预警分析）
输入：已生成的分析报告 / 用户查询
输出：跟踪面板 + 舆情日报 + 预警报告
"""

from .agent import TrackingAgent

__all__ = ["TrackingAgent"]
