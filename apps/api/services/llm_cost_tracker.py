#!/usr/bin/env python3
"""
LLM成本监控服务
追踪所有LLM调用的Token消耗和费用
"""
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.path_config import LLM_COST_LOG_ROOT

class LLMCostTracker:
    """LLM成本追踪器"""

    # 价格配置（每1M tokens，单位：人民币）
    PRICING = {
        "kimi": {
            "input": 12.0,   # Kimi input: ¥12/M tokens
            "output": 12.0,  # Kimi output: ¥12/M tokens
        },
        "minimax": {
            "input": 15.0,
            "output": 15.0,
        },
        "qwen": {
            "input": 0.0,    # 本地模型无成本
            "output": 0.0,
        },
        "gemma": {
            "input": 0.0,    # 本地模型无成本
            "output": 0.0,
        }
    }

    def __init__(self, log_dir: str | Path | None = None):
        self.log_dir = Path(log_dir) if log_dir else LLM_COST_LOG_ROOT
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_log = self.log_dir / f"llm_costs_{datetime.now().strftime('%Y%m')}.jsonl"

    def log_usage(
        self,
        model: str,
        profile: str,
        input_tokens: int,
        output_tokens: int,
        request_id: Optional[str] = None,
        user_query: Optional[str] = None,
    ):
        """记录LLM使用"""
        provider = self._detect_provider(model)

        # 计算成本
        input_cost = (input_tokens / 1_000_000) * self.PRICING.get(provider, {}).get("input", 0)
        output_cost = (output_tokens / 1_000_000) * self.PRICING.get(provider, {}).get("output", 0)
        total_cost = input_cost + output_cost

        # 记录日志
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id or "",
            "profile": profile,
            "model": model,
            "provider": provider,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            "cost_cny": {
                "input": round(input_cost, 4),
                "output": round(output_cost, 4),
                "total": round(total_cost, 4),
            },
            "user_query_preview": user_query[:100] if user_query else "",
        }

        # 写入日志文件
        with open(self.current_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return log_entry

    def _detect_provider(self, model: str) -> str:
        """检测模型提供商"""
        model_lower = model.lower()
        if "kimi" in model_lower or "moonshot" in model_lower:
            return "kimi"
        elif "minimax" in model_lower or "abab" in model_lower:
            return "minimax"
        elif "qwen" in model_lower:
            return "qwen"
        elif "gemma" in model_lower:
            return "gemma"
        else:
            return "unknown"

    def get_monthly_summary(self, year_month: Optional[str] = None):
        """获取月度成本汇总"""
        if not year_month:
            year_month = datetime.now().strftime('%Y%m')

        log_file = self.log_dir / f"llm_costs_{year_month}.jsonl"
        if not log_file.exists():
            return {"error": f"No data for {year_month}"}

        # 统计
        stats = {
            "total_requests": 0,
            "total_tokens": 0,
            "total_cost_cny": 0.0,
            "by_profile": {},
            "by_provider": {},
        }

        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                stats["total_requests"] += 1
                stats["total_tokens"] += entry["tokens"]["total"]
                stats["total_cost_cny"] += entry["cost_cny"]["total"]

                # 按profile统计
                profile = entry["profile"]
                if profile not in stats["by_profile"]:
                    stats["by_profile"][profile] = {"requests": 0, "tokens": 0, "cost_cny": 0.0}
                stats["by_profile"][profile]["requests"] += 1
                stats["by_profile"][profile]["tokens"] += entry["tokens"]["total"]
                stats["by_profile"][profile]["cost_cny"] += entry["cost_cny"]["total"]

                # 按provider统计
                provider = entry["provider"]
                if provider not in stats["by_provider"]:
                    stats["by_provider"][provider] = {"requests": 0, "tokens": 0, "cost_cny": 0.0}
                stats["by_provider"][provider]["requests"] += 1
                stats["by_provider"][provider]["tokens"] += entry["tokens"]["total"]
                stats["by_provider"][provider]["cost_cny"] += entry["cost_cny"]["total"]

        # 四舍五入
        stats["total_cost_cny"] = round(stats["total_cost_cny"], 2)
        for profile_stats in stats["by_profile"].values():
            profile_stats["cost_cny"] = round(profile_stats["cost_cny"], 2)
        for provider_stats in stats["by_provider"].values():
            provider_stats["cost_cny"] = round(provider_stats["cost_cny"], 2)

        return stats

cost_tracker = LLMCostTracker()
