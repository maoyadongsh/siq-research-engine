"""
安全工具函数
包含路径验证、表名验证、输入清理等
"""
import re
from pathlib import Path
from typing import Optional
from fastapi import HTTPException


# 允许的数据库表名白名单
ALLOWED_TABLES = frozenset({
    "three_statement_metrics",
    "validation_anomalies",
    "reports",
    "companies",
    "agentstate",
    "chatmessage",
    "achievement",
    "interactionlog",
    "tracking_items",
    "sentiment_records",
    "metric_snapshots",
    "alert_records",
    "report_updates",
    "users",
    "audit_logs",
    "report_reviews",
    "chatsessionmemory"
})


def validate_table_name(table: str) -> str:
    """
    验证表名是否在白名单中

    Args:
        table: 表名

    Returns:
        验证通过的表名

    Raises:
        HTTPException: 表名不在白名单中
    """
    if table not in ALLOWED_TABLES:
        raise HTTPException(400, f"Invalid table name: {table}")
    return table


def safe_task_id(task_id: str) -> str:
    """
    严格验证UUID格式的task_id

    Args:
        task_id: 任务ID

    Returns:
        验证通过的task_id

    Raises:
        HTTPException: task_id格式不合法
    """
    task_id = task_id.strip()

    # 严格的UUID格式验证（带连字符）
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

    if not re.match(uuid_pattern, task_id, re.IGNORECASE):
        raise HTTPException(400, f"Invalid task_id format: must be a valid UUID")

    return task_id


def safe_path_join(base: Path, *parts: str) -> Path:
    """
    安全的路径拼接，防止路径遍历攻击

    Args:
        base: 基础目录
        parts: 路径组成部分

    Returns:
        解析后的安全路径

    Raises:
        HTTPException: 路径遍历检测
    """
    # 解析为绝对路径
    result = (base / Path(*parts)).resolve()
    base_resolved = base.resolve()

    # 验证结果路径在基础目录内
    try:
        result.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(
            403,
            f"Access denied: path traversal detected"
        )

    return result


def validate_company_dir(company_dir: str) -> str:
    """
    验证公司目录名

    Args:
        company_dir: 公司目录名（如 "000001-平安银行"）

    Returns:
        验证通过的目录名

    Raises:
        HTTPException: 格式不合法
    """
    # 允许：数字、字母、中文、连字符、下划线
    if not re.match(r'^[\w一-龥-]+$', company_dir):
        raise HTTPException(400, f"Invalid company_dir format")

    # 防止路径遍历字符
    if '..' in company_dir or '/' in company_dir or '\\' in company_dir:
        raise HTTPException(400, f"Invalid characters in company_dir")

    return company_dir


def validate_file_extension(filename: str, allowed_extensions: set) -> str:
    """
    验证文件扩展名

    Args:
        filename: 文件名
        allowed_extensions: 允许的扩展名集合（如 {'.html', '.json'}）

    Returns:
        验证通过的文件名

    Raises:
        HTTPException: 扩展名不允许
    """
    ext = Path(filename).suffix.lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            400,
            f"File extension {ext} not allowed. Allowed: {allowed_extensions}"
        )

    return filename


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除危险字符

    Args:
        filename: 原始文件名

    Returns:
        清理后的文件名
    """
    # 移除路径分隔符和其他危险字符
    filename = re.sub(r'[/\\:*?"<>|]', '', filename)

    # 移除前后空格和点号
    filename = filename.strip('. ')

    # 限制长度
    if len(filename) > 255:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        filename = name[:250] + ('.' + ext if ext else '')

    return filename


def validate_page_number(page_number: int, max_pages: int = 10000) -> int:
    """
    验证页码范围

    Args:
        page_number: 页码
        max_pages: 最大页数

    Returns:
        验证通过的页码

    Raises:
        HTTPException: 页码超出范围
    """
    if page_number < 1 or page_number > max_pages:
        raise HTTPException(400, f"Page number must be between 1 and {max_pages}")

    return page_number


def validate_table_index(table_index: int, max_tables: int = 10000) -> int:
    """
    验证表格索引范围

    Args:
        table_index: 表格索引
        max_tables: 最大表格数

    Returns:
        验证通过的索引

    Raises:
        HTTPException: 索引超出范围
    """
    if table_index < 0 or table_index >= max_tables:
        raise HTTPException(400, f"Table index must be between 0 and {max_tables-1}")

    return table_index
