#!/usr/bin/env python3
"""
统一健康检查脚本 - 检查所有 SIQ 服务状态
"""
import os
import sys
from typing import Tuple

import requests


def env_port(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# 服务配置
SERVICES = [
    ("前端", "http://localhost:15173", "GET"),
    ("后端API", "http://localhost:18081/health", "GET"),
    ("PDF下载服务", "http://localhost:18000/health", "GET"),
    ("PDF解析服务", os.getenv("SIQ_PDF2MD_HEALTH_URL", "http://localhost:15000/api/ready"), "GET"),
    ("文档解析服务", os.getenv("SIQ_DOCUMENT_PARSER_HEALTH_URL", "http://localhost:15010/api/ready"), "GET"),
    ("Hermes-助手", f"http://localhost:{env_port('SIQ_HERMES_ASSISTANT_PORT', 18642)}/health", "GET"),
    ("Hermes-分析", f"http://localhost:{env_port('SIQ_HERMES_ANALYSIS_PORT', 18651)}/health", "GET"),
    ("Hermes-核查", f"http://localhost:{env_port('SIQ_HERMES_FACTCHECKER_PORT', 18649)}/health", "GET"),
    ("Hermes-跟踪", f"http://localhost:{env_port('SIQ_HERMES_TRACKING_PORT', 18650)}/health", "GET"),
    ("Hermes-法务", f"http://localhost:{env_port('SIQ_HERMES_LEGAL_PORT', 18652)}/health", "GET"),
]
if env_bool("SIQ_ENABLE_IC_HERMES", True):
    SERVICES.extend(
        [
            ("Hermes-IC总协调", f"http://localhost:{env_port('SIQ_HERMES_IC_MASTER_PORT', 18660)}/health", "GET"),
            ("Hermes-IC主席", f"http://localhost:{env_port('SIQ_HERMES_IC_CHAIRMAN_PORT', 18661)}/health", "GET"),
            ("Hermes-IC策略", f"http://localhost:{env_port('SIQ_HERMES_IC_STRATEGIST_PORT', 18662)}/health", "GET"),
            ("Hermes-IC行业", f"http://localhost:{env_port('SIQ_HERMES_IC_SECTOR_PORT', 18663)}/health", "GET"),
            ("Hermes-IC财务", f"http://localhost:{env_port('SIQ_HERMES_IC_FINANCE_PORT', 18664)}/health", "GET"),
            ("Hermes-IC法务", f"http://localhost:{env_port('SIQ_HERMES_IC_LEGAL_PORT', 18665)}/health", "GET"),
            ("Hermes-IC风控", f"http://localhost:{env_port('SIQ_HERMES_IC_RISK_PORT', 18666)}/health", "GET"),
        ]
    )

def check_service(name: str, url: str, method: str = "GET") -> Tuple[bool, str]:
    """检查单个服务状态"""
    try:
        response = requests.request(method, url, timeout=5)
        if response.status_code == 200:
            return True, f"✅ {name:20s} - 正常运行"
        else:
            return False, f"❌ {name:20s} - HTTP {response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, f"❌ {name:20s} - 无法连接"
    except requests.exceptions.Timeout:
        return False, f"❌ {name:20s} - 连接超时"
    except Exception as e:
        return False, f"❌ {name:20s} - 错误: {str(e)}"

def main():
    print("=" * 60)
    print("SIQ 服务健康检查")
    print("=" * 60)

    results = []
    for name, url, method in SERVICES:
        status, message = check_service(name, url, method)
        results.append((status, message))
        print(message)

    print("=" * 60)
    healthy_count = sum(1 for status, _ in results if status)
    total_count = len(results)

    print(f"健康服务: {healthy_count}/{total_count}")

    if healthy_count == total_count:
        print("✅ 所有服务正常运行")
        sys.exit(0)
    else:
        print(f"⚠️  {total_count - healthy_count} 个服务异常")
        sys.exit(1)

if __name__ == "__main__":
    main()
