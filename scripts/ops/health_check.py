#!/usr/bin/env python3
"""
统一健康检查脚本 - 检查所有 SIQ 服务状态
"""
import sys
import requests
from typing import Dict, List, Tuple

# 服务配置
SERVICES = [
    ("前端", "http://localhost:15173", "GET"),
    ("后端API", "http://localhost:18081/health", "GET"),
    ("PDF下载服务", "http://localhost:18000/health", "GET"),
    ("PDF解析服务", "http://localhost:15000/api/health", "GET"),
    ("Hermes-助手", "http://localhost:18642/health", "GET"),
    ("Hermes-分析", "http://localhost:18651/health", "GET"),
    ("Hermes-核查", "http://localhost:18649/health", "GET"),
    ("Hermes-跟踪", "http://localhost:18650/health", "GET"),
    ("Hermes-法务", "http://localhost:18652/health", "GET"),
]

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
