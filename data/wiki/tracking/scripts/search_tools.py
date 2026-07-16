#!/usr/bin/env python3
"""
FinSight Tracking - 搜索工具封装模块
集成 Tavily 和 Exa 搜索 API，为持续跟踪任务提供网络搜索能力。

使用方式:
    from search_tools import SearchTools

    search = SearchTools()

    # Tavily 深度搜索
    results = search.tavily_search("宁德时代 2025年财报", max_results=5)

    # Exa 语义搜索
    results = search.exa_search("宁德时代 固态电池技术进展", num_results=5)

    # 智能搜索（自动选择后端）
    results = search.search("公司最新公告", backend="auto")
"""

import os
import sys
import json
import time
from typing import List, Dict, Optional, Literal
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════

def _load_env_from_file(filepath: str) -> Dict[str, str]:
    """从 .env 文件加载环境变量"""
    env_vars = {}
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


# 加载 profile 级别的 .env
PROFILE_ENV_PATH = "/home/maoyd/.hermes/profiles/finsight_tracking/.env"
profile_env = _load_env_from_file(PROFILE_ENV_PATH)

# API Keys
TAVILY_API_KEY = profile_env.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY", "")
EXA_API_KEY = profile_env.get("EXA_API_KEY") or os.environ.get("EXA_API_KEY", "")

# ═══════════════════════════════════════════════════════════════
# Tavily 搜索封装
# ═══════════════════════════════════════════════════════════════

class TavilySearch:
    """Tavily 搜索客户端封装"""

    API_BASE = "https://api.tavily.com"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or TAVILY_API_KEY
        self.available = bool(self.api_key)
        if not self.available:
            print("⚠️ Tavily API Key 未配置，Tavily 搜索不可用")

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "advanced",
        include_answer: bool = True,
        include_raw_content: bool = False,
        include_images: bool = False,
        days: Optional[int] = None,  # 限制搜索时间范围（天）
    ) -> Dict:
        """
        执行 Tavily 搜索

        Args:
            query: 搜索查询
            max_results: 最大结果数
            search_depth: 搜索深度 (basic/advanced)
            include_answer: 是否包含 AI 生成的答案摘要
            include_raw_content: 是否包含网页原始内容
            include_images: 是否包含图片
            days: 限制搜索最近 N 天的内容

        Returns:
            {
                "success": bool,
                "query": str,
                "answer": str,  # AI 摘要（如果 include_answer=True）
                "results": [
                    {
                        "title": str,
                        "url": str,
                        "content": str,
                        "raw_content": str,  # 原始内容（如果 include_raw_content=True）
                        "score": float,
                        "published_date": str,
                    }
                ],
                "response_time": float,
            }
        """
        if not self.available:
            return {"success": False, "error": "Tavily API Key 未配置", "results": []}

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests 库未安装", "results": []}

        start_time = time.time()

        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
            "include_images": include_images,
        }

        if days:
            payload["time_range"] = f"{days}d"

        try:
            response = requests.post(
                f"{self.API_BASE}/search",
                json=payload,
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "raw_content": r.get("raw_content", "") if include_raw_content else "",
                    "score": r.get("score", 0),
                    "published_date": r.get("published_date", ""),
                })

            return {
                "success": True,
                "query": query,
                "answer": data.get("answer", ""),
                "results": results,
                "response_time": round(time.time() - start_time, 2),
            }

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"请求失败: {str(e)}",
                "results": [],
                "response_time": round(time.time() - start_time, 2),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"未知错误: {str(e)}",
                "results": [],
                "response_time": round(time.time() - start_time, 2),
            }

    def extract(
        self,
        urls: List[str],
        extract_depth: Literal["basic", "advanced"] = "advanced",
        include_images: bool = False,
    ) -> Dict:
        """
        从指定 URL 提取内容

        Args:
            urls: URL 列表
            extract_depth: 提取深度
            include_images: 是否包含图片

        Returns:
            {
                "success": bool,
                "results": [
                    {
                        "url": str,
                        "raw_content": str,
                        "images": List[str],
                    }
                ]
            }
        """
        if not self.available:
            return {"success": False, "error": "Tavily API Key 未配置", "results": []}

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests 库未安装", "results": []}

        payload = {
            "api_key": self.api_key,
            "urls": urls,
            "extract_depth": extract_depth,
            "include_images": include_images,
        }

        try:
            response = requests.post(
                f"{self.API_BASE}/extract",
                json=payload,
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "url": r.get("url", ""),
                    "raw_content": r.get("raw_content", ""),
                    "images": r.get("images", []),
                })

            return {
                "success": True,
                "results": results,
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"提取失败: {str(e)}",
                "results": [],
            }


# ═══════════════════════════════════════════════════════════════
# Exa 搜索封装
# ═══════════════════════════════════════════════════════════════

class ExaSearch:
    """Exa 搜索客户端封装"""

    API_BASE = "https://api.exa.ai"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or EXA_API_KEY
        self.available = bool(self.api_key)
        if not self.available:
            print("⚠️ Exa API Key 未配置，Exa 搜索不可用")

    def search(
        self,
        query: str,
        num_results: int = 5,
        use_autoprompt: bool = True,
        type: Literal["neural", "keyword", "auto"] = "auto",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        start_published_date: Optional[str] = None,
        end_published_date: Optional[str] = None,
        include_text: Optional[List[str]] = None,
        exclude_text: Optional[List[str]] = None,
    ) -> Dict:
        """
        执行 Exa 语义搜索

        Args:
            query: 搜索查询（支持自然语言描述）
            num_results: 结果数量
            use_autoprompt: 是否使用自动提示优化
            type: 搜索类型 (neural/keyword/auto)
            include_domains: 仅搜索指定域名
            exclude_domains: 排除指定域名
            start_published_date: 开始日期 (ISO 格式)
            end_published_date: 结束日期 (ISO 格式)
            include_text: 结果必须包含的文本
            exclude_text: 结果必须排除的文本

        Returns:
            {
                "success": bool,
                "query": str,
                "autoprompt": str,  # 优化后的查询
                "results": [
                    {
                        "title": str,
                        "url": str,
                        "published_date": str,
                        "author": str,
                        "score": float,
                    }
                ],
                "response_time": float,
            }
        """
        if not self.available:
            return {"success": False, "error": "Exa API Key 未配置", "results": []}

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests 库未安装", "results": []}

        start_time = time.time()

        payload = {
            "query": query,
            "numResults": num_results,
            "useAutoprompt": use_autoprompt,
            "type": type,
        }

        if include_domains:
            payload["includeDomains"] = include_domains
        if exclude_domains:
            payload["excludeDomains"] = exclude_domains
        if start_published_date:
            payload["startPublishedDate"] = start_published_date
        if end_published_date:
            payload["endPublishedDate"] = end_published_date
        if include_text:
            payload["includeText"] = include_text
        if exclude_text:
            payload["excludeText"] = exclude_text

        try:
            response = requests.post(
                f"{self.API_BASE}/search",
                json=payload,
                timeout=30,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                }
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "published_date": r.get("publishedDate", ""),
                    "author": r.get("author", ""),
                    "score": r.get("score", 0),
                })

            return {
                "success": True,
                "query": query,
                "autoprompt": data.get("autopromptString", ""),
                "results": results,
                "response_time": round(time.time() - start_time, 2),
            }

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"请求失败: {str(e)}",
                "results": [],
                "response_time": round(time.time() - start_time, 2),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"未知错误: {str(e)}",
                "results": [],
                "response_time": round(time.time() - start_time, 2),
            }

    def find_similar(
        self,
        url: str,
        num_results: int = 5,
        exclude_source_domain: bool = True,
    ) -> Dict:
        """
        查找与指定 URL 相似的内容

        Args:
            url: 参考 URL
            num_results: 结果数量
            exclude_source_domain: 是否排除源域名

        Returns:
            {
                "success": bool,
                "results": [...]
            }
        """
        if not self.available:
            return {"success": False, "error": "Exa API Key 未配置", "results": []}

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests 库未安装", "results": []}

        payload = {
            "url": url,
            "numResults": num_results,
            "excludeSourceDomain": exclude_source_domain,
        }

        try:
            response = requests.post(
                f"{self.API_BASE}/findSimilar",
                json=payload,
                timeout=30,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                }
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "published_date": r.get("publishedDate", ""),
                    "score": r.get("score", 0),
                })

            return {
                "success": True,
                "results": results,
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"查找失败: {str(e)}",
                "results": [],
            }

    def get_contents(
        self,
        urls: List[str],
        text: bool = True,
        highlights: bool = False,
    ) -> Dict:
        """
        获取 URL 的详细内容

        Args:
            urls: URL 列表
            text: 是否获取全文
            highlights: 是否获取高亮片段

        Returns:
            {
                "success": bool,
                "results": [
                    {
                        "url": str,
                        "title": str,
                        "text": str,
                        "highlights": List[str],
                    }
                ]
            }
        """
        if not self.available:
            return {"success": False, "error": "Exa API Key 未配置", "results": []}

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests 库未安装", "results": []}

        payload = {
            "ids": urls,
            "text": text,
            "highlights": {"numSentences": 3, "highlightsPerUrl": 3} if highlights else False,
        }

        try:
            response = requests.post(
                f"{self.API_BASE}/contents",
                json=payload,
                timeout=30,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                }
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "text": r.get("text", ""),
                    "highlights": r.get("highlights", []),
                })

            return {
                "success": True,
                "results": results,
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"获取内容失败: {str(e)}",
                "results": [],
            }


# ═══════════════════════════════════════════════════════════════
# 统一搜索接口
# ═══════════════════════════════════════════════════════════════

class SearchTools:
    """
    FinSight Tracking 统一搜索工具

    封装 Tavily 和 Exa 搜索，提供统一的搜索接口。
    根据查询类型自动选择最适合的搜索后端。
    """

    def __init__(self):
        self.tavily = TavilySearch()
        self.exa = ExaSearch()
        self._last_search_time = 0
        self._min_interval = 0.5  # 最小请求间隔（秒）

    def _rate_limit(self):
        """简单的速率限制"""
        elapsed = time.time() - self._last_search_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_search_time = time.time()

    @property
    def available_backends(self) -> List[str]:
        """返回可用的搜索后端列表"""
        backends = []
        if self.tavily.available:
            backends.append("tavily")
        if self.exa.available:
            backends.append("exa")
        return backends

    def check_availability(self) -> Dict[str, bool]:
        """检查各搜索后端的可用性"""
        return {
            "tavily": self.tavily.available,
            "exa": self.exa.available,
            "any": self.tavily.available or self.exa.available,
        }

    def search(
        self,
        query: str,
        backend: Literal["auto", "tavily", "exa"] = "auto",
        max_results: int = 5,
        **kwargs
    ) -> Dict:
        """
        统一搜索接口

        Args:
            query: 搜索查询
            backend: 搜索后端 (auto/tavily/exa)
            max_results: 最大结果数
            **kwargs: 传递给具体搜索后端的额外参数

        Returns:
            {
                "success": bool,
                "backend": str,  # 实际使用的后端
                "query": str,
                "results": [...],
                ...
            }
        """
        self._rate_limit()

        # 确定后端
        if backend == "auto":
            if self.tavily.available:
                backend = "tavily"
            elif self.exa.available:
                backend = "exa"
            else:
                return {
                    "success": False,
                    "error": "没有可用的搜索后端",
                    "backend": None,
                    "query": query,
                    "results": [],
                }

        # 执行搜索
        if backend == "tavily":
            result = self.tavily.search(query, max_results=max_results, **kwargs)
            result["backend"] = "tavily"
            return result
        elif backend == "exa":
            result = self.exa.search(query, num_results=max_results, **kwargs)
            result["backend"] = "exa"
            return result
        else:
            return {
                "success": False,
                "error": f"未知的搜索后端: {backend}",
                "backend": backend,
                "query": query,
                "results": [],
            }

    def search_company_news(
        self,
        company_name: str,
        stock_code: str,
        days: int = 7,
        max_results: int = 10,
    ) -> Dict:
        """
        搜索公司最新新闻和公告

        适合 module2 舆情监控使用

        Args:
            company_name: 公司简称
            stock_code: 股票代码
            days: 搜索最近几天的内容
            max_results: 最大结果数

        Returns:
            搜索结果字典
        """
        query = f"{company_name} {stock_code} 最新公告 新闻"
        # Tavily time_range 只支持特定格式，不使用 days 参数
        return self.search(
            query=query,
            backend="tavily",
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
        )

    def search_company_risks(
        self,
        company_name: str,
        stock_code: str,
        max_results: int = 5,
    ) -> Dict:
        """
        搜索公司风险相关信息

        适合 module1 跟踪事项提取和 module4 预警触发使用

        Args:
            company_name: 公司简称
            stock_code: 股票代码
            max_results: 最大结果数

        Returns:
            搜索结果字典
        """
        query = f"{company_name} {stock_code} 风险 监管 处罚 问询 立案调查"
        return self.search(
            query=query,
            backend="tavily",
            max_results=max_results,
            search_depth="advanced",
            include_answer=True,
        )

    def search_industry_trends(
        self,
        industry: str,
        max_results: int = 5,
    ) -> Dict:
        """
        搜索行业趋势和政策变化

        适合跟踪事项中的行业变化维度

        Args:
            industry: 行业名称
            max_results: 最大结果数

        Returns:
            搜索结果字典
        """
        query = f"{industry} 行业政策 趋势 2025"
        return self.search(
            query=query,
            backend="exa",
            max_results=max_results,
            type="neural",
        )

    def search_similar_content(
        self,
        url: str,
        max_results: int = 5,
    ) -> Dict:
        """
        查找与指定内容相似的信息

        适合 Exa 的语义相似搜索

        Args:
            url: 参考 URL
            max_results: 最大结果数

        Returns:
            搜索结果字典
        """
        if not self.exa.available:
            return {"success": False, "error": "Exa 不可用", "results": []}

        self._rate_limit()
        result = self.exa.find_similar(url, num_results=max_results)
        result["backend"] = "exa"
        return result

    def extract_url_content(
        self,
        urls: List[str],
        backend: Literal["tavily", "exa"] = "tavily",
    ) -> Dict:
        """
        从 URL 提取详细内容

        Args:
            urls: URL 列表
            backend: 使用的后端

        Returns:
            提取结果字典
        """
        self._rate_limit()

        if backend == "tavily":
            result = self.tavily.extract(urls)
        else:
            result = self.exa.get_contents(urls)

        result["backend"] = backend
        return result


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def get_search_tools() -> SearchTools:
    """获取 SearchTools 实例（单例模式）"""
    if not hasattr(get_search_tools, "_instance"):
        get_search_tools._instance = SearchTools()
    return get_search_tools._instance


def quick_search(query: str, max_results: int = 5) -> List[Dict]:
    """
    快速搜索，返回结果列表

    Args:
        query: 搜索查询
        max_results: 最大结果数

    Returns:
        搜索结果列表
    """
    search = get_search_tools()
    result = search.search(query, max_results=max_results)
    return result.get("results", [])


# ═══════════════════════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FinSight Tracking 搜索工具测试")
    parser.add_argument("--query", default="宁德时代 2025年财报", help="搜索查询")
    parser.add_argument("--backend", choices=["auto", "tavily", "exa"], default="auto", help="搜索后端")
    parser.add_argument("--max-results", type=int, default=5, help="最大结果数")
    parser.add_argument("--test-company", default="宁德时代", help="测试公司名称")
    parser.add_argument("--test-stock", default="300750", help="测试股票代码")
    args = parser.parse_args()

    print("=" * 60)
    print("FinSight Tracking 搜索工具测试")
    print("=" * 60)

    search = SearchTools()

    # 检查可用性
    print("\n📊 搜索后端可用性:")
    availability = search.check_availability()
    for backend, available in availability.items():
        status = "✅ 可用" if available else "❌ 不可用"
        print(f"  {backend}: {status}")

    if not availability["any"]:
        print("\n❌ 没有可用的搜索后端，请检查 API Key 配置")
        sys.exit(1)

    # 测试1: 通用搜索
    print(f"\n🔍 测试1: 通用搜索")
    print(f"  查询: {args.query}")
    print(f"  后端: {args.backend}")
    result = search.search(args.query, backend=args.backend, max_results=args.max_results)

    if result["success"]:
        print(f"  ✅ 成功 (后端: {result.get('backend')}, 耗时: {result.get('response_time', 'N/A')}s)")
        print(f"  结果数: {len(result.get('results', []))}")
        for i, r in enumerate(result.get("results", [])[:3], 1):
            print(f"  [{i}] {r.get('title', 'N/A')[:60]}...")
            print(f"      {r.get('url', 'N/A')[:80]}...")
    else:
        print(f"  ❌ 失败: {result.get('error', '未知错误')}")

    # 测试2: 公司新闻搜索
    print(f"\n📰 测试2: 公司新闻搜索")
    print(f"  公司: {args.test_company} ({args.test_stock})")
    result = search.search_company_news(args.test_company, args.test_stock, days=7, max_results=5)

    if result["success"]:
        print(f"  ✅ 成功 (结果数: {len(result.get('results', []))})")
        for i, r in enumerate(result.get("results", [])[:3], 1):
            print(f"  [{i}] {r.get('title', 'N/A')[:60]}...")
    else:
        print(f"  ❌ 失败: {result.get('error', '未知错误')}")

    # 测试3: 风险搜索
    print(f"\n⚠️ 测试3: 风险信息搜索")
    result = search.search_company_risks(args.test_company, args.test_stock, max_results=3)

    if result["success"]:
        print(f"  ✅ 成功 (结果数: {len(result.get('results', []))})")
        if result.get("answer"):
            print(f"  AI摘要: {result['answer'][:100]}...")
    else:
        print(f"  ❌ 失败: {result.get('error', '未知错误')}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
