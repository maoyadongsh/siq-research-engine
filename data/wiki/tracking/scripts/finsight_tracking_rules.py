#!/usr/bin/env python3
"""
finsight_tracking 工作规则引擎
固化所有工作规则，提供统一的规则校验和执行接口。

规则列表:
    1. 工作目录规则: 每个公司的跟踪数据存放在 companies/<stock>-<name>/tracking/
    2. 脚本位置规则: 五大模块脚本保留在 tracking/scripts/
    3. 报告命名规则: <stock>-<name>-跟踪报告-<date>.html
    4. 单报告原则: 不生成单独HTML，只生成合并报告
    5. 前置检查规则: 只跟踪 finsight_analysis 已完成分析的公司
    6. 目录结构规则: 标准化 tracking/ 子目录结构
    7. HTML报告规则: 必须通过模块6生成合并HTML报告，禁止手工创建HTML
    8. HTML样式规则: 白色背景主题CSS，确保高可读性；包含统计卡片、可折叠区块、响应式布局
    9. HTML内容规则: 必须包含跟踪事项、指标追踪、舆情监控、预警状态、更新记录五大板块
   10. HTML可读性规则: 背景色必须为白色(#ffffff)或极浅灰(#fafafa)，文字对比度符合WCAG AA标准，禁止暗色主题
   11. HTML白色背景固化规则: module6_html_reporter.py 必须使用白色背景CSS变量(--bg-primary:#ffffff)，禁止任何暗色/深色主题色值
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict

_SCRIPT_PATH = Path(__file__).resolve()
_PROJECT_ROOT = _SCRIPT_PATH.parents[4]
_DEFAULT_WIKI_BASE = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or _SCRIPT_PATH.parents[2]
).expanduser().resolve()
WIKISET_DIR = Path(
    os.environ.get("SIQ_WIKISET_ROOT")
    or os.environ.get("WIKISET_ROOT")
    or _PROJECT_ROOT / "scripts" / "wiki" / "wikiset"
).expanduser().resolve()
if str(WIKISET_DIR) not in sys.path:
    sys.path.insert(0, str(WIKISET_DIR))

from company_identity import (
    company_dir_name,
    generated_report_archive_path,
    generated_report_filename,
    safe_slug_part,
)


# ═══════════════════════════════════════════════════════════════
# 常量定义
# ═══════════════════════════════════════════════════════════════

WIKI_BASE = str(_DEFAULT_WIKI_BASE)
COMPANIES_DIR = os.path.join(WIKI_BASE, "companies")
TRACKING_SCRIPTS_DIR = os.path.join(WIKI_BASE, "tracking", "scripts")
META_DIR = os.path.join(WIKI_BASE, "tracking", "_meta")


def configure_wiki_base(wiki_base: str | os.PathLike[str] | None = None) -> str:
    """Set the wiki root used by rule helpers.

    The tracking scripts live inside the project wiki, so their default must
    follow the current checkout instead of the historical /home/maoyd/wiki copy.
    """
    global WIKI_BASE, COMPANIES_DIR, TRACKING_SCRIPTS_DIR, META_DIR
    base = Path(
        wiki_base
        or os.environ.get("SIQ_WIKI_ROOT")
        or os.environ.get("WIKI_ROOT")
        or _DEFAULT_WIKI_BASE
    ).expanduser().resolve()
    WIKI_BASE = str(base)
    COMPANIES_DIR = os.path.join(WIKI_BASE, "companies")
    TRACKING_SCRIPTS_DIR = os.path.join(WIKI_BASE, "tracking", "scripts")
    META_DIR = os.path.join(WIKI_BASE, "tracking", "_meta")
    return WIKI_BASE

# 标准化子目录结构
TRACKING_SUBDIRS = ["sentiment", "metrics", "alerts", "updates", "reports"]

# 报告命名模板
REPORT_NAME_TEMPLATE = "{stock}-{name}-跟踪报告-{date}.html"
REPORT_NAME_PATTERN = re.compile(r"^(\d{6})-(.+)-跟踪报告-(\d{4}-\d{2}-\d{2})\.html$")
REPORT_MANIFEST_NAME = "report_manifest.json"
LATEST_REPORT_LINK = "latest.html"

# 股票代码格式
STOCK_CODE_PATTERN = re.compile(r"^\d{6}$")

# HTML报告必需包含的板块（规则9）
HTML_REQUIRED_SECTIONS = [
    "跟踪事项", "指标追踪", "舆情监控", "预警状态", "更新记录"
]

# HTML样式校验标记（规则8+10）
HTML_STYLE_MARKERS = [
    "background-color", "#ffffff", "#fafafa", "color:", "font-family",
    "color-scheme: light", "stat-card", "section-header", "toggleSection", "@media (max-width"
]

# HTML可读性校验标记（规则10）
HTML_READABILITY_MARKERS = [
    "background-color:#ffffff", "background-color: #ffffff",
    "background-color:#fafafa", "background-color: #fafafa",
    "background: #ffffff", "background:#ffffff",
    "background: var(--bg-primary)",  # 变量方式，在:root中定义白色
]

# 禁止的暗色主题标记（规则10）
HTML_DARK_THEME_FORBIDDEN = [
    "background-color:#1a", "background-color: #1a",
    "background-color:#0d", "background-color: #0d",
    "background-color:#121", "background-color: #121",
    "background-color:#000", "background-color: #000",
    "--bg-primary: #0d", "--bg-primary:#0d",
    "--bg-primary: #1a", "--bg-primary:#1a",
    "background: #0f172a", "background:#0f172a",
    "background: #111827", "background:#111827",
    "linear-gradient(135deg,#0", "linear-gradient(135deg, #0",
    "linear-gradient(135deg,#1", "linear-gradient(135deg, #1",
    "dark-theme", "dark mode", "dark-mode",
]


# ═══════════════════════════════════════════════════════════════
# 规则1: 工作目录规则
# ═══════════════════════════════════════════════════════════════

def get_company_dir(stock_code: str, company_name: str) -> str:
    """获取公司主目录路径"""
    return os.path.join(COMPANIES_DIR, company_dir_name(stock_code, company_name))


def get_tracking_dir(stock_code: str, company_name: str) -> str:
    """获取公司跟踪数据目录路径 (规则1)"""
    return os.path.join(get_company_dir(stock_code, company_name), "tracking")


def ensure_tracking_dir(stock_code: str, company_name: str) -> str:
    """确保跟踪目录存在，不存在则创建"""
    tracking_dir = get_tracking_dir(stock_code, company_name)
    os.makedirs(tracking_dir, exist_ok=True)
    return tracking_dir


def resolve_tracking_path(stock_code: str, company_name: str, *subpaths: str) -> str:
    """解析跟踪数据下的完整路径"""
    return os.path.join(get_tracking_dir(stock_code, company_name), *subpaths)


# ═══════════════════════════════════════════════════════════════
# 规则2: 脚本位置规则
# ═══════════════════════════════════════════════════════════════

def get_script_dir() -> str:
    """获取脚本目录路径 (规则2)"""
    return TRACKING_SCRIPTS_DIR


def resolve_script_path(script_name: str) -> str:
    """解析脚本完整路径"""
    return os.path.join(TRACKING_SCRIPTS_DIR, script_name)


# ═══════════════════════════════════════════════════════════════
# 规则3: 报告命名规则
# ═══════════════════════════════════════════════════════════════

def generate_report_name(stock_code: str, company_name: str, date: Optional[str] = None) -> str:
    """生成标准报告文件名 (规则3)"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    return REPORT_NAME_TEMPLATE.format(stock=stock_code, name=safe_slug_part(company_name), date=date)


def generate_agent_report_name(
    stock_code: str,
    company_name: str,
    report_type: str,
    date: Optional[str] = None,
    suffix: str = ".md",
) -> str:
    """生成智能体报告标准文件名。"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    return generated_report_filename(stock_code, company_name, report_type, date, suffix)


def get_agent_report_archive_path(
    stock_code: str,
    company_name: str,
    report_type: str,
    date: Optional[str] = None,
    suffix: str = ".md",
) -> str:
    """获取智能体生成报告的标准归档路径。"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    return str(generated_report_archive_path(WIKI_BASE, stock_code, company_name, report_type, date, suffix))


def parse_report_name(filename: str) -> Optional[Tuple[str, str, str]]:
    """解析报告文件名，返回 (stock_code, company_name, date)"""
    match = REPORT_NAME_PATTERN.match(filename)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None


def get_report_path(stock_code: str, company_name: str, date: Optional[str] = None) -> str:
    """获取标准报告文件完整路径"""
    report_name = generate_report_name(stock_code, company_name, date)
    return resolve_tracking_path(stock_code, company_name, report_name)


def _report_sort_key(filename: str) -> str:
    parsed = parse_report_name(filename)
    return parsed[2] if parsed else ""


def list_html_reports(stock_code: str, company_name: str) -> List[str]:
    """列出跟踪目录下符合标准命名的 HTML 综合报告。"""
    tracking_dir = get_tracking_dir(stock_code, company_name)
    if not os.path.exists(tracking_dir):
        return []
    reports = [
        f for f in os.listdir(tracking_dir)
        if f.endswith(".html") and parse_report_name(f)
    ]
    return sorted(reports, key=_report_sort_key)


def get_latest_report_path(stock_code: str, company_name: str) -> Optional[str]:
    """获取最新标准 HTML 综合报告路径。"""
    reports = list_html_reports(stock_code, company_name)
    if not reports:
        return None
    return resolve_tracking_path(stock_code, company_name, reports[-1])


def write_report_manifest(stock_code: str, company_name: str, report_path: str) -> str:
    """写入最新报告 manifest，供校验和前端稳定读取。"""
    tracking_dir = get_tracking_dir(stock_code, company_name)
    os.makedirs(tracking_dir, exist_ok=True)
    report_name = os.path.basename(report_path)
    parsed = parse_report_name(report_name)
    manifest = {
        "stock_code": stock_code,
        "company_name": company_name,
        "latest_report": report_name,
        "latest_report_path": report_path,
        "report_date": parsed[2] if parsed else None,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    manifest_path = resolve_tracking_path(stock_code, company_name, REPORT_MANIFEST_NAME)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    latest_path = resolve_tracking_path(stock_code, company_name, LATEST_REPORT_LINK)
    try:
        if os.path.lexists(latest_path):
            os.remove(latest_path)
        os.symlink(report_name, latest_path)
    except OSError:
        shutil.copy2(report_path, latest_path)
    return manifest_path


def resolve_report_path(stock_code: str, company_name: str, date: Optional[str] = None) -> Optional[str]:
    """按日期或 manifest/latest 解析 HTML 综合报告路径。"""
    if date:
        report_path = get_report_path(stock_code, company_name, date)
        return report_path if os.path.exists(report_path) else None

    manifest_path = resolve_tracking_path(stock_code, company_name, REPORT_MANIFEST_NAME)
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            latest_name = manifest.get("latest_report")
            if latest_name:
                candidate = resolve_tracking_path(stock_code, company_name, latest_name)
                if os.path.exists(candidate):
                    return candidate
        except (OSError, json.JSONDecodeError):
            pass
    return get_latest_report_path(stock_code, company_name)


# ═══════════════════════════════════════════════════════════════
# 规则4: 单报告原则
# ═══════════════════════════════════════════════════════════════

def validate_single_report_policy(stock_code: str, company_name: str, date: Optional[str] = None) -> bool:
    """
    校验单报告原则 (规则4)
    返回 True 表示符合规则（只有一个合并报告），False 表示违反规则
    """
    tracking_dir = get_tracking_dir(stock_code, company_name)
    if not os.path.exists(tracking_dir):
        return True  # 目录不存在，视为符合

    for f in os.listdir(tracking_dir):
        if not f.endswith('.html') or f == LATEST_REPORT_LINK:
            continue
        parsed = parse_report_name(f)
        if not parsed:
            return False
        if date and parsed[2] != date:
            return False
    return True


def enforce_single_report_policy(stock_code: str, company_name: str, date: Optional[str] = None, archive: bool = True) -> List[str]:
    """
    强制执行单报告原则：归档非目标日期的标准 HTML，删除/归档非标准 HTML。
    """
    tracking_dir = get_tracking_dir(stock_code, company_name)
    if not os.path.exists(tracking_dir):
        return []

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    standard_name = generate_report_name(stock_code, company_name, date)
    archived = []
    archive_dir = os.path.join(tracking_dir, "reports", "archive")
    os.makedirs(archive_dir, exist_ok=True)
    for f in os.listdir(tracking_dir):
        if not f.endswith('.html') or f in {standard_name, LATEST_REPORT_LINK}:
            continue
        src = os.path.join(tracking_dir, f)
        if archive:
            dst = os.path.join(archive_dir, f)
            shutil.move(src, dst)
            archived.append(dst)
            print(f"  📦 归档HTML: {f}")
        else:
            print(f"  ⚠️ 发现非当前HTML: {f}")
    return archived


# ═══════════════════════════════════════════════════════════════
# 规则5: 前置检查规则
# ═══════════════════════════════════════════════════════════════

def check_analysis_completed(stock_code: str, company_name: str) -> bool:
    """
    检查 finsight_analysis 是否已完成分析 (规则5)
    检查 companies/<stock>-<name>/analysis/ 目录是否存在且有内容
    """
    analysis_dir = os.path.join(get_company_dir(stock_code, company_name), "analysis")
    if not os.path.exists(analysis_dir):
        return False
    # 检查目录是否有内容（至少一个文件或子目录）
    try:
        return len(os.listdir(analysis_dir)) > 0
    except OSError:
        return False


def validate_stock_code(stock_code: str) -> bool:
    """校验股票代码格式"""
    return bool(STOCK_CODE_PATTERN.match(stock_code))


def preflight_check(stock_code: str, company_name: str) -> Tuple[bool, List[str]]:
    """
    运行所有前置检查
    返回 (是否通过, 错误信息列表)
    """
    errors = []

    # 检查股票代码格式
    if not validate_stock_code(stock_code):
        errors.append(f"股票代码格式错误: {stock_code} (应为6位数字)")

    # 检查公司名称
    if not company_name or not company_name.strip():
        errors.append("公司名称不能为空")

    # 检查分析是否完成
    if not check_analysis_completed(stock_code, company_name):
        errors.append(f"finsight_analysis 尚未完成: {stock_code}-{company_name}/analysis/ 不存在或为空")

    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════════
# 规则6: 目录结构规则
# ═══════════════════════════════════════════════════════════════

def ensure_tracking_structure(stock_code: str, company_name: str) -> Dict[str, str]:
    """
    确保标准化跟踪目录结构存在 (规则6)
    返回创建的目录路径字典
    """
    tracking_dir = ensure_tracking_dir(stock_code, company_name)
    created = {"tracking": tracking_dir}

    for subdir in TRACKING_SUBDIRS:
        path = os.path.join(tracking_dir, subdir)
        os.makedirs(path, exist_ok=True)
        created[subdir] = path

    return created


def validate_tracking_structure(stock_code: str, company_name: str) -> Tuple[bool, List[str]]:
    """
    校验跟踪目录结构是否完整
    返回 (是否完整, 缺失目录列表)
    """
    tracking_dir = get_tracking_dir(stock_code, company_name)
    missing = []

    if not os.path.exists(tracking_dir):
        missing.append("tracking")
        return False, missing

    for subdir in TRACKING_SUBDIRS:
        path = os.path.join(tracking_dir, subdir)
        if not os.path.exists(path):
            missing.append(subdir)

    return len(missing) == 0, missing


# ═══════════════════════════════════════════════════════════════
# 规则7-10: HTML报告规则
# ═══════════════════════════════════════════════════════════════

def validate_html_report_exists(stock_code: str, company_name: str, date: Optional[str] = None) -> bool:
    """
    检查HTML报告是否存在 (规则7)
    """
    return resolve_report_path(stock_code, company_name, date) is not None


def validate_html_content(stock_code: str, company_name: str, date: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    校验HTML报告内容合规性 (规则8+9+10)
    返回 (是否合规, 违规信息列表)
    """
    report_path = resolve_report_path(stock_code, company_name, date)
    issues = []

    if not report_path or not os.path.exists(report_path):
        issues.append("HTML报告不存在")
        return False, issues

    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 规则8: 检查样式标记
    for marker in HTML_STYLE_MARKERS:
        if marker not in content:
            issues.append(f"缺少样式标记: {marker}")

    # 规则10: 检查白色背景（可读性）
    has_light_bg = any(marker in content for marker in HTML_READABILITY_MARKERS)
    if not has_light_bg:
        issues.append("缺少白色/浅色背景样式，不符合可读性要求(规则10)")

    # 规则10: 检查是否包含暗色主题（禁止）
    for forbidden in HTML_DARK_THEME_FORBIDDEN:
        if forbidden in content:
            issues.append(f"检测到暗色主题标记，违反可读性规则(规则10): {forbidden}")
            break  # 只报告一次

    # 规则9: 检查必需板块
    for section in HTML_REQUIRED_SECTIONS:
        if section not in content:
            issues.append(f"缺少必需板块: {section}")

    return len(issues) == 0, issues


def enforce_html_rules(stock_code: str, company_name: str, date: Optional[str] = None) -> Dict[str, any]:
    """
    强制执行HTML报告规则
    返回执行结果字典
    """
    result = {
        "stock_code": stock_code,
        "company_name": company_name,
        "passed": False,
        "errors": [],
        "warnings": [],
    }

    # 规则7: 检查HTML存在
    if not validate_html_report_exists(stock_code, company_name, date):
        result["errors"].append("HTML报告不存在，必须通过模块6生成")
        return result

    # 规则8+9: 内容校验
    ok, issues = validate_html_content(stock_code, company_name, date)
    if not ok:
        result["errors"].extend(issues)
        return result

    result["passed"] = True
    return result


def delete_manual_html_files(stock_code: str, company_name: str, archive: bool = True) -> List[str]:
    """
    删除手工创建的HTML文件（非模块6生成的标准报告）
    返回删除的文件列表
    """
    tracking_dir = get_tracking_dir(stock_code, company_name)
    handled = []

    if not os.path.exists(tracking_dir):
        return handled

    for f in os.listdir(tracking_dir):
        if f.endswith('.html'):
            # 检查是否符合命名规范
            parsed = parse_report_name(f)
            if not parsed:
                # 不符合命名规范，视为手工创建
                src = os.path.join(tracking_dir, f)
                if archive:
                    archive_dir = os.path.join(tracking_dir, "reports", "manual")
                    os.makedirs(archive_dir, exist_ok=True)
                    dst = os.path.join(archive_dir, f)
                    shutil.move(src, dst)
                    handled.append(dst)
                else:
                    os.remove(src)
                    handled.append(f)

    return handled


# ═══════════════════════════════════════════════════════════════
# 统一规则引擎接口
# ═══════════════════════════════════════════════════════════════

class TrackingRulesEngine:
    """finsight_tracking 规则引擎"""

    def __init__(self, wiki_base: str = WIKI_BASE):
        self.wiki_base = configure_wiki_base(wiki_base)
        self.companies_dir = os.path.join(self.wiki_base, "companies")

    def setup_company(self, stock_code: str, company_name: str) -> Dict[str, any]:
        """
        为一家公司初始化完整的跟踪环境
        执行所有规则检查并创建必要目录
        """
        result = {
            "stock_code": stock_code,
            "company_name": company_name,
            "passed": False,
            "errors": [],
            "warnings": [],
            "created_paths": {},
        }

        # 规则5: 前置检查
        passed, errors = preflight_check(stock_code, company_name)
        if not passed:
            result["errors"].extend(errors)
            return result

        # 规则1+6: 创建跟踪目录结构
        try:
            created = ensure_tracking_structure(stock_code, company_name)
            result["created_paths"] = created
        except Exception as e:
            result["errors"].append(f"创建目录失败: {e}")
            return result

        # 规则4: 检查单报告原则
        if not validate_single_report_policy(stock_code, company_name):
            result["warnings"].append("发现额外的HTML报告文件，建议清理")

        result["passed"] = True
        return result

    def get_all_tracking_companies(self) -> List[Tuple[str, str]]:
        """获取所有已配置跟踪的公司列表"""
        companies = []
        if not os.path.exists(self.companies_dir):
            return companies

        for entry in os.listdir(self.companies_dir):
            entry_path = os.path.join(self.companies_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            # 解析 stock-name 格式
            parts = entry.split('-', 1)
            if len(parts) == 2 and validate_stock_code(parts[0]):
                stock, name = parts
                tracking_dir = os.path.join(entry_path, "tracking")
                if os.path.exists(tracking_dir):
                    companies.append((stock, name))

        return sorted(companies)

    def validate_all(self) -> Dict[str, any]:
        """验证所有跟踪公司的规则合规性"""
        results = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "details": [],
        }

        companies = self.get_all_tracking_companies()
        results["total"] = len(companies)

        for stock, name in companies:
            detail = {"stock": stock, "name": name, "issues": []}

            # 检查目录结构
            ok, missing = validate_tracking_structure(stock, name)
            if not ok:
                detail["issues"].append(f"目录结构不完整，缺失: {missing}")

            # 检查单报告原则
            if not validate_single_report_policy(stock, name):
                detail["issues"].append("违反单报告原则，存在额外HTML文件")

            # 检查HTML报告规则
            if not validate_html_report_exists(stock, name):
                detail["issues"].append("缺少HTML报告（规则7）")
            else:
                ok, html_issues = validate_html_content(stock, name)
                if not ok:
                    detail["issues"].extend([f"HTML内容违规: {i}" for i in html_issues])

            if detail["issues"]:
                results["failed"] += 1
            else:
                results["passed"] += 1

            results["details"].append(detail)

        return results


# ═══════════════════════════════════════════════════════════════
# CLI 接口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="finsight_tracking 规则引擎")
    parser.add_argument("--wiki-base", default=WIKI_BASE, help="wiki 根目录")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # setup 命令
    setup_parser = subparsers.add_parser("setup", help="初始化公司跟踪环境")
    setup_parser.add_argument("--stock", required=True, help="股票代码")
    setup_parser.add_argument("--company", required=True, help="公司简称")

    # validate 命令
    validate_parser = subparsers.add_parser("validate", help="验证规则合规性")
    validate_parser.add_argument("--stock", help="股票代码（不指定则验证全部）")
    validate_parser.add_argument("--company", help="公司简称")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出所有跟踪公司")

    # path 命令
    path_parser = subparsers.add_parser("path", help="获取路径信息")
    path_parser.add_argument("--stock", required=True, help="股票代码")
    path_parser.add_argument("--company", required=True, help="公司简称")
    path_parser.add_argument("--type", choices=["tracking", "report", "sentiment", "metrics", "alerts", "updates"],
                            default="tracking", help="路径类型")

    args = parser.parse_args()

    engine = TrackingRulesEngine(args.wiki_base)

    if args.command == "setup":
        result = engine.setup_company(args.stock, args.company)
        print(f"\n{'='*50}")
        print(f"设置结果: {args.stock}-{args.company}")
        print(f"{'='*50}")
        print(f"状态: {'✅ 通过' if result['passed'] else '❌ 失败'}")
        if result["errors"]:
            print(f"错误: {', '.join(result['errors'])}")
        if result["warnings"]:
            print(f"警告: {', '.join(result['warnings'])}")
        if result["created_paths"]:
            print(f"创建目录:")
            for k, v in result["created_paths"].items():
                print(f"  {k}: {v}")

    elif args.command == "validate":
        if args.stock and args.company:
            ok, errors = preflight_check(args.stock, args.company)
            ok2, missing = validate_tracking_structure(args.stock, args.company)
            html_exists = validate_html_report_exists(args.stock, args.company)
            html_ok, html_issues = validate_html_content(args.stock, args.company) if html_exists else (False, ["HTML报告不存在"])
            print(f"\n{'='*50}")
            print(f"验证结果: {args.stock}-{args.company}")
            print(f"{'='*50}")
            print(f"前置检查: {'✅ 通过' if ok else '❌ 失败'}")
            if errors:
                for e in errors:
                    print(f"  - {e}")
            print(f"目录结构: {'✅ 完整' if ok2 else '❌ 不完整'}")
            if missing:
                print(f"  缺失: {missing}")
            print(f"单报告原则: {'✅ 符合' if validate_single_report_policy(args.stock, args.company) else '⚠️ 不符合'}")
            print(f"HTML报告存在: {'✅ 是' if html_exists else '❌ 否'}")
            if html_exists:
                print(f"HTML内容合规: {'✅ 是' if html_ok else '❌ 否'}")
                if html_issues:
                    for issue in html_issues:
                        print(f"  - {issue}")
        else:
            results = engine.validate_all()
            print(f"\n{'='*50}")
            print(f"全量验证结果")
            print(f"{'='*50}")
            print(f"总计: {results['total']}, 通过: {results['passed']}, 失败: {results['failed']}")
            for d in results["details"]:
                if d["issues"]:
                    print(f"\n❌ {d['stock']}-{d['name']}:")
                    for issue in d["issues"]:
                        print(f"   - {issue}")

    elif args.command == "list":
        companies = engine.get_all_tracking_companies()
        print(f"\n{'='*50}")
        print(f"已配置跟踪的公司 ({len(companies)}家)")
        print(f"{'='*50}")
        for stock, name in companies:
            print(f"  {stock} - {name}")

    elif args.command == "path":
        if args.type == "tracking":
            print(get_tracking_dir(args.stock, args.company))
        elif args.type == "report":
            print(get_report_path(args.stock, args.company))
        else:
            print(resolve_tracking_path(args.stock, args.company, args.type))

    else:
        parser.print_help()
