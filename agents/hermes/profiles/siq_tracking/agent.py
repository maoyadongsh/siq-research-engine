"""SIQ_tracking profile wrapper.

生产级执行入口在 SIQ_WIKI_ROOT/tracking/scripts。本文件只保留薄封装，
避免 profile 原型模块与真实生产链路形成两个真相源。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.environ.get("SIQ_PROJECT_ROOT") or Path(__file__).resolve().parents[5])
WIKI_ROOT = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_DIR")
    or PROJECT_ROOT / "data" / "wiki"
)
SCRIPT_DIR = WIKI_ROOT / "tracking" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from finsight_tracking_rules import TrackingRulesEngine, resolve_report_path  # noqa: E402
from run_all import run_all  # noqa: E402


class TrackingAgent:
    """Thin wrapper around the production siq_tracking scripts."""

    def __init__(self, wiki_base_path: str | None = None):
        wiki_base_path = wiki_base_path or str(WIKI_ROOT)
        self.wiki_base = wiki_base_path
        self.rules = TrackingRulesEngine(wiki_base_path)

    def setup_company(self, stock_code: str, company_name: str) -> dict[str, Any]:
        """初始化公司 tracking 目录。"""
        return self.rules.setup_company(stock_code, company_name)

    def run(
        self,
        stock_code: str,
        company_name: str,
        *,
        skip_sentiment: bool = False,
        use_search: bool = True,
        allow_simulated_sentiment: bool = False,
        strict: bool = False,
        update_analysis: bool = False,
    ) -> dict[str, Any]:
        """运行完整持续跟踪链路。"""
        return run_all(
            stock_code,
            company_name,
            self.wiki_base,
            skip_sentiment=skip_sentiment,
            use_search=use_search,
            allow_simulated_sentiment=allow_simulated_sentiment,
            strict=strict,
            update_analysis=update_analysis,
        )

    def latest_report(self, stock_code: str, company_name: str) -> str | None:
        """返回最新综合 HTML 报告路径。"""
        return resolve_report_path(stock_code, company_name)
