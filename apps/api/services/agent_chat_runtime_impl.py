import asyncio
import base64
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import httpx
from fastapi import Request
from sqlalchemy import text as sql_text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import async_engine
from models import ChatMessage, ChatSessionMemory
from services.citation_links import append_missing_pdf_source_links
from services.hermes_client import HermesProfile, collect_run_result, create_run, stop_run, stream_run
from services import agent_runtime_dedupe
from services.agent_runtime_loop_guard import (
    CONSECUTIVE_TOOL_ERROR_LIMIT,
    HISTORY_LOOP_SANITIZED_MESSAGE,
    IDLE_TIMEOUT_MESSAGE,
    LEGACY_HISTORY_LOOP_SANITIZED_PREFIX,
    ORPHANED_RUN_MESSAGE,
    OUTPUT_LOOP_STOP_MESSAGE,
    REPEATED_TOOL_CALL_LIMIT,
    REPEATED_TOOL_CALL_STOP_MESSAGE,
    RUN_CANCELLED_MESSAGE,
    RUN_FAILED_MESSAGE,
    STOPPED_MESSAGE,
    TIMEOUT_MESSAGE,
    TOOL_FAILURE_STOP_MESSAGE,
    _assistant_reply_for_display,
    _detect_output_loop,
    _detect_stream_output_loop,
    _failed_run_reply_for_history,
    _is_loop_polluted_assistant_message,
    _sanitize_assistant_history_reply,
)
from services import agent_runtime_progress
from services import agent_runtime_citations
from services import agent_runtime_catalog
from services import agent_runtime_parse_only
from services import agent_runtime_display
from services import agent_runtime_memory
from services import agent_runtime_history
from services import agent_runtime_context
from services import agent_runtime_financial_guard
from services import agent_runtime_financial_format
from services import agent_runtime_fallback_contexts
from services import agent_runtime_postgres_fallback
from services import agent_runtime_statement_context
from services.agent_runtime_streaming import (
    ACTIVE_RUNS,
    ActiveRunState,
    PROGRESS_BAR_RE,
    PROGRESS_LINE_RE,
    _active_key,
    _append_completed_active_run,
    _append_progress_event,
    _append_reasoning_active_run,
    _append_state_event,
    _append_user_stopped_active_run,
    _clear_active_run,
    _extract_progress_from_text,
    _progress_payload,
    _progress_signature,
    _runtime_profile,
    get_active_run_snapshot as _streaming_get_active_run_snapshot,
    has_active_run,
    stop_active_run as _streaming_stop_active_run,
    stream_active_run_events as _streaming_stream_active_run_events,
)
from services.agent_runtime_fallback_contexts import (
    _markdown_table_cell,
    _postgres_row_md_line,
    _postgres_row_metric_name,
    _postgres_row_payload,
    _postgres_row_pdf_page,
    _postgres_row_source,
    _postgres_row_table_index,
    _postgres_row_unit,
    _postgres_row_value,
)
from services.agent_runtime_tool_output import normalize_tool_output as _normalize_tool_output
from services.path_config import (
    ASSISTANT_WIKI_ROOT as CONFIG_ASSISTANT_WIKI_ROOT,
    DB_PROGRAM_ROOT,
    FINANCIAL_CALCULATOR_SCRIPT,
    FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT,
    HERMES_PROFILE_ROOTS,
    HERMES_SHARED_SCRIPTS_ROOT,
    PDF_OUTPUT_ROOT_CANDIDATES,
    PDF_RESULT_ROOT_CANDIDATES,
    PROJECT_ROOT,
    BACKEND_DATA_ROOT,
    WIKI_ROOT as CONFIG_WIKI_ROOT,
)


FINANCIAL_CALCULATOR_PATH = FINANCIAL_CALCULATOR_SCRIPT
FINANCIAL_RECONCILIATION_VALIDATOR_PATH = FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT
FINANCIAL_CALCULATOR_PATH_TEXT = str(FINANCIAL_CALCULATOR_PATH)
FINANCIAL_RECONCILIATION_VALIDATOR_PATH_TEXT = str(FINANCIAL_RECONCILIATION_VALIDATOR_PATH)
_FINANCIAL_CALCULATOR_MODULE: Any | None = None


def _load_financial_calculator_module() -> Any | None:
    global _FINANCIAL_CALCULATOR_MODULE
    if _FINANCIAL_CALCULATOR_MODULE is not None:
        return _FINANCIAL_CALCULATOR_MODULE
    if not FINANCIAL_CALCULATOR_PATH.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("siq_financial_calculator", FINANCIAL_CALCULATOR_PATH)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("siq_financial_calculator", module)
        spec.loader.exec_module(module)
    except Exception:
        return None
    _FINANCIAL_CALCULATOR_MODULE = module
    return module


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_int_any(names: tuple[str, ...], default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = next((os.getenv(name) for name in names if os.getenv(name) is not None), None)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_bool_any(names: tuple[str, ...], default: bool = True) -> bool:
    raw = next((os.getenv(name) for name in names if os.getenv(name) is not None), None)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_path_list(names: str | tuple[str, ...], defaults: tuple[Path, ...]) -> tuple[Path, ...]:
    if isinstance(names, str):
        names = (names,)
    raw = next((os.getenv(name) for name in names if os.getenv(name)), None)
    values = re.split(rf"[{re.escape(os.pathsep)},]", raw) if raw else [str(item) for item in defaults]
    output: list[Path] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return tuple(output)


HISTORY_LIMIT = _env_int_any(("SIQ_CHAT_HISTORY_LIMIT", "SIQ_CHAT_HISTORY_LIMIT"), 24, minimum=4, maximum=120)
LOCAL_MEMORY_ENABLED = _env_bool_any(("SIQ_LOCAL_MEMORY_ENABLED", "SIQ_LOCAL_MEMORY_ENABLED"), True)
LOCAL_MEMORY_ENABLED_PROFILES = {
    item.strip()
    for item in (os.getenv("SIQ_LOCAL_MEMORY_PROFILES") or "siq_assistant").split(",")
    if item.strip()
}
LOCAL_MEMORY_RECENT_LIMIT = _env_int_any(("SIQ_LOCAL_MEMORY_RECENT_LIMIT", "SIQ_LOCAL_MEMORY_RECENT_LIMIT"), HISTORY_LIMIT, minimum=4, maximum=160)
LOCAL_MEMORY_MAX_CHARS = _env_int_any(("SIQ_LOCAL_MEMORY_MAX_CHARS", "SIQ_LOCAL_MEMORY_MAX_CHARS"), 5000, minimum=800, maximum=20000)
LOCAL_MEMORY_MAX_BULLETS = _env_int_any(("SIQ_LOCAL_MEMORY_MAX_BULLETS", "SIQ_LOCAL_MEMORY_MAX_BULLETS"), 18, minimum=4, maximum=80)
LOCAL_MEMORY_SNIPPET_CHARS = _env_int_any(("SIQ_LOCAL_MEMORY_SNIPPET_CHARS", "SIQ_LOCAL_MEMORY_SNIPPET_CHARS"), 360, minimum=120, maximum=1200)
PROFILE_SESSION_PREFIXES: dict[str, str] = {
    "siq_assistant": "siq-assistant",
    "siq_analysis": "siq-analysis",
    "siq_factchecker": "siq-factchecker",
    "siq_tracking": "siq-tracking",
    "siq_legal": "siq-legal",
}
CHAT_UPLOAD_ROOT = BACKEND_DATA_ROOT / "chat_uploads"
CHAT_PDF_PARSE_ROOT = CHAT_UPLOAD_ROOT / "pdf_parses"
CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS = _env_int(
    os.getenv("SIQ_CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS") and "SIQ_CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS" or "SIQ_CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS",
    150,
    minimum=0,
    maximum=600,
)
CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS = _env_int(
    os.getenv("SIQ_CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS") and "SIQ_CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS" or "SIQ_CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS",
    3,
    minimum=1,
    maximum=30,
)
_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY = False
ATTACHMENT_FOLLOWUP_RE = re.compile(
    r"(继续|前面|刚才|上[一个轮条张份次]|这张|那张|这份|那份|图片|照片|附件|手写|ocr|OCR)",
    re.IGNORECASE,
)
IMAGE_MODEL_BASE_URL = (
    os.getenv("SIQ_IMAGE_MODEL_BASE_URL")
    or os.getenv("SIQ_IMAGE_MODEL_URL")
    or os.getenv("SIQ_IMAGE_MODEL_BASE_URL")
    or os.getenv("SIQ_IMAGE_MODEL_URL")
    or "http://127.0.0.1:8004/v1"
).rstrip("/")
IMAGE_MODEL_NAME = (os.getenv("SIQ_IMAGE_MODEL") or os.getenv("SIQ_IMAGE_MODEL", "")).strip()
IMAGE_MODEL_ENABLED = _env_bool_any(("SIQ_IMAGE_MODEL_ENABLED", "SIQ_IMAGE_MODEL_ENABLED"), True)
IMAGE_MODEL_TIMEOUT_SECONDS = _env_int_any(("SIQ_IMAGE_MODEL_TIMEOUT_SECONDS", "SIQ_IMAGE_MODEL_TIMEOUT_SECONDS"), 90, minimum=5, maximum=600)
MAX_DOCUMENT_CONTEXT_CHARS = 16000
STREAM_TIMEOUT_SECONDS = 1800
READ_TIMEOUT_SECONDS = 1800
ASSISTANT_STREAM_IDLE_TIMEOUT_SECONDS = _env_int(
    os.getenv("SIQ_ASSISTANT_STREAM_IDLE_TIMEOUT_SECONDS") and "SIQ_ASSISTANT_STREAM_IDLE_TIMEOUT_SECONDS" or "SIQ_ASSISTANT_STREAM_IDLE_TIMEOUT_SECONDS",
    900,
    minimum=30,
    maximum=1800,
)
SPECIALIST_STREAM_IDLE_TIMEOUT_SECONDS = _env_int(
    os.getenv("SIQ_SPECIALIST_STREAM_IDLE_TIMEOUT_SECONDS") and "SIQ_SPECIALIST_STREAM_IDLE_TIMEOUT_SECONDS" or "SIQ_SPECIALIST_STREAM_IDLE_TIMEOUT_SECONDS",
    600,
    minimum=120,
    maximum=1800,
)
STREAM_EVENT_HEARTBEAT_SECONDS = _env_int(
    os.getenv("SIQ_STREAM_EVENT_HEARTBEAT_SECONDS") and "SIQ_STREAM_EVENT_HEARTBEAT_SECONDS" or "SIQ_STREAM_EVENT_HEARTBEAT_SECONDS",
    8,
    minimum=5,
    maximum=120,
)
ANALYSIS_IDEMPOTENCY_WINDOW_SECONDS = 300
ANALYSIS_DUPLICATE_MESSAGE = (
    "[已完成] 检测到这是刚刚处理过的同一条分析请求，系统没有再次创建后台 run。"
    "请查看上一条回复或已生成的报告；如需强制重建，请明确说明“强制重建”。"
)
RECENT_DUPLICATE_MESSAGE = (
    "[已处理] 检测到这是刚刚处理过的同一条请求，系统没有再次创建后台 run。"
    "请查看上一条回复；如需重新执行，请换一种明确的新指令。"
)
ANALYSIS_COMPLETED_MESSAGE = (
    "[已完成] 检测到当前公司年度分析报告已经通过验收，系统没有重复创建后台 run。"
    "如需覆盖重建，请明确说明“强制重建/覆盖重建”。"
)
_IMAGE_MODEL_NAME_CACHE: str | None = None
CONTEXT_HEADER = (
    "以下是本会话的默认上下文，只用于用户没有明确指定公司、证券代码、报告或主题时补全指代。"
    "如果用户问题或会话历史里指定了其他公司/代码/报告/行业/主题，或明显是在问通用问题，必须优先按用户问题和会话历史回答，不要强行套用默认公司。"
)
CHAT_OUTPUT_CONTRACT = (
    "回答格式要求：\n"
    "- 除非用户明确要求一段式回复，问答默认优先使用 Markdown 列表、紧凑小表格或短分节展示，不要整段纯文字堆叠。\n"
    "- 默认先给可读简版：除非用户明确要求“详细/完整/展开/生成报告”，普通问答控制在约 800-1500 中文字，优先列关键结论和关键数据，不要展开成长报告。\n"
    "- 默认报告期口径：当前已入库 Wiki 财报必须以实时 catalog/company.json 的 `primary_report_id` 为准；用户明确指定年报/季报、截止日、年份或 `report_id` 时必须匹配 company.json.reports 或 _meta/report_catalog.json，不要在默认回答、功能介绍或提问示例中写死任何年份。\n"
    "- 财报问答建议结构：先给 `## 结论` 列表，再给 `## 依据/数据` 列表或表格，最后保留 `## 引用来源`。\n"
    "- 财报事实问答中，正文出现的主要数值、比例、金额、员工数、销量、市占率或派生指标，必须在唯一的 `## 引用来源` 中逐项映射到 PDF 页、表格/文本块和来源链接，不要另起 `主要数据溯源补充`、`主要数据引用来源` 等重复章节。\n"
    f"- 人均、每股、同比、增长率、占比、CAGR、外币折人民币和金额单位归一等派生计算，必须使用 `{FINANCIAL_CALCULATOR_PATH_TEXT}` 或后端确定性脚本校验；不要心算后直接输出。\n"
    "- 图片识别、按钮文本、键盘符号、单位和普通指标名一律使用普通文本；不要用 `$...$` 包裹。仅在用户明确要求公式或 LaTeX 时才输出 LaTeX 分隔符。\n"
    "- 涉及数据表证据时，引用行必须保留 `table_index` 或表格来源链接，便于前端展示可打开表格入口。\n"
    "- `## 引用来源` 内的 `source_type/file/task_id/pdf_page/table_index/md_line` 字段必须保持机器可解析，不要改写成散文。\n"
)
FINANCIAL_CALCULATION_RUNTIME_CONTRACT = (
    "财务派生计算硬约束：\n"
    f"- 人均、每股、同比、增长率、占比、CAGR、外币折人民币和金额单位归一，必须使用 `{FINANCIAL_CALCULATOR_PATH_TEXT}` 或后端同源函数；最终答案应保留 `financial_calculator.py`、`## 计算器校验` 或等价计算器痕迹。\n"
    f"- 商誉、坏账准备、存货跌价准备、资产减值准备等涉及原值/准备/净额的口径，必须使用 `{FINANCIAL_RECONCILIATION_VALIDATOR_PATH_TEXT}` 或后端同源函数勾稽；商誉主表值是账面净额，不得把附注账面原值当成主表余额。\n"
    "- 中国上市公司商誉口径必须区分账面原值、减值准备余额、账面价值、当期减值损失和准备变动；若附注写明“本年/本期计入当期损益”或减值准备由 `-`/0 增加为正数，不能表述为“本期未新增减值”。\n"
    "- 上期值为 0 或负数时，普通同比/增长率默认 `not_applicable`，应描述扭亏/亏损扩大/亏损收窄和绝对变动，不能硬写普通增长百分比。\n"
    "- `(1,016)`、`（1,016）` 这类括号金额按负数处理；`HKD`、`HK$` 是港元币种，不是 `K=千` 单位。\n"
    "- `fx_required`、`division_by_zero`、`not_applicable` 是受控业务状态，不等于工具失败；必须解释状态和缺口，不能改写成确定数值。\n"
)
GENERAL_ASSISTANT_CONTEXT = (
    "当前用户是在询问智能体自身简介、能力范围、使用方式或提问示例。"
    "这是 profile 元信息请求，不是公司分析、公司跟踪或 Wiki 公司目录查询任务。"
    "回答必须遵循当前 Hermes profile 自身的角色设定，围绕当前智能体的职责、能力、边界、适合提问方式和输出形式；"
    "不要把页面默认公司、会话默认公司、本地记忆里的公司、历史 session 示例或测试样例当作当前工作集。"
    "除非用户在本条消息中明确指定公司，否则不要声称当前工作集/默认分析对象/默认跟踪对象是某家公司。"
    "不要套用其他智能体身份，特别是不要把所有角色都写成全局财报问答助手。"
    "若需要举公司示例，优先使用当前 profile 可访问的实时 Wiki catalog；无法读取时使用“某个已入库公司”。"
    "默认财报口径必须以实时 catalog/company.json 的 primary_report_id 说明，不要写死任何年份或 report_id。"
)
NOTE_DETAIL_SCRIPT_DIR = HERMES_SHARED_SCRIPTS_ROOT
STATEMENT_QUERY_TERMS = (
    "营业收入",
    "营收",
    "营业成本",
    "营业利润",
    "利润总额",
    "净利润",
    "归母净利润",
    "扣非归母",
    "扣非净利润",
    "每股收益",
    "净资产收益率",
    "现金流",
    "现金流量表",
    "经营活动现金",
    "投资活动现金",
    "筹资活动现金",
    "资产负债表",
    "资产负债",
    "资产构成",
    "资产结构",
    "负债结构",
    "负债与权益",
    "负债权益",
    "所有者权益",
    "股东权益",
    "偿债",
    "总资产",
    "总负债",
    "净资产",
    "利润表",
    "损益表",
)
NOTE_DETAIL_QUERY_TERMS = (
    "明细",
    "构成",
    "分布",
    "组成",
    "附注",
    "详情",
    "减值",
    "准备",
    "账龄",
    "前五名",
    "资产组",
    "可收回",
    "变动",
)
NOTE_DETAIL_EXCLUDE_TERMS = (
    "生成报告",
    "分析报告",
    "完整报告",
    "重建报告",
    "覆盖重建",
    "强制重建",
)
NOTE_DETAIL_DIRECT_TERMS = (
    "是什么",
    "查询",
    "查一下",
    "给我",
    "给我一下",
    "看一下",
    "有哪些",
    "列出",
    "展示",
    "显示",
    "明细",
    "构成",
    "分布",
    "组成",
    "详情",
    "附注",
    "账龄",
    "前五名",
)
NOTE_DETAIL_ANALYSIS_TERMS = (
    "分析",
    "评价",
    "判断",
    "影响",
    "风险",
    "原因",
    "趋势",
    "对比",
    "预测",
    "建议",
    "异常",
    "合理",
    "解释",
    "为什么",
    "怎么看",
)
FINANCIAL_NOTE_METRIC_TERMS = (
    "商誉",
    "应收账款",
    "其他应收款",
    "预付款项",
    "存货",
    "合同资产",
    "固定资产",
    "在建工程",
    "无形资产",
    "开发支出",
    "长期股权投资",
    "投资性房地产",
    "递延所得税资产",
    "短期借款",
    "长期借款",
    "应付账款",
    "合同负债",
    "预计负债",
    "营业收入",
    "营业成本",
    "销售费用",
    "管理费用",
    "研发费用",
    "财务费用",
    "资产减值损失",
    "信用减值损失",
)
FINANCIAL_EVIDENCE_ACTION_TERMS = (
    *NOTE_DETAIL_QUERY_TERMS,
    *NOTE_DETAIL_ANALYSIS_TERMS,
    "多少",
    "金额",
    "余额",
    "账面价值",
    "账面余额",
    "原值",
    "计提",
    "转回",
    "转销",
    "处置",
    "同比",
    "变化",
    "占比",
    "数据",
    "情况",
)
RUNTIME_STATUS_PREFIXES = ("[已停止]", "[失败]", "[已取消]", "[错误]")
STATEMENT_DIRECT_TERMS = (
    "是什么",
    "有哪些",
    "列出",
    "展示",
    "显示",
    "核心数据",
    "数据",
    "摘要",
    "情况",
    "多少",
    "概览",
    "构成",
    "结构",
    "明细",
)
GENERAL_ASSISTANT_REQUEST_TERMS = (
    "智能体简介",
    "自我介绍",
    "你是谁",
    "你能做什么",
    "能解决哪些",
    "能力范围",
    "典型场景",
    "工作方式",
    "提问示例",
    "输出边界",
    "最佳使用方式",
    "注意事项",
    "可追溯链接",
    "如何使用",
    "怎么使用",
    "怎么提问",
    "如何提问",
    "适合用户怎样提问",
)
GENERAL_ASSISTANT_SUBJECT_TERMS = (
    "你",
    "智能体",
    "助手",
    "问答助手",
    "agent",
    "siq",
)
HUMAN_CAPITAL_QUERY_TERMS = (
    "人才结构",
    "人才构成",
    "人员结构",
    "人员构成",
    "员工结构",
    "员工构成",
    "员工情况",
    "专业构成",
    "教育程度",
    "学历结构",
    "人力结构",
    "人力资源结构",
)
HUMAN_EFFICIENCY_QUERY_TERMS = (
    "人效",
    "人均营收",
    "人均收入",
    "人均产出",
    "人均创收",
    "人均利润",
    "人均成本",
    "人均人力成本",
    "人力成本",
    "人员成本",
    "人员费用",
    "员工效率",
    "劳动效率",
    "员工生产率",
    "员工人数",
    "平均员工",
)
HUMAN_CAPITAL_TABLE_TERMS = (
    "母公司在职员工的数量",
    "主要子公司在职员工的数量",
    "在职员工的数量合计",
    "专业构成",
    "教育程度",
)
CORE_KEY_METRIC_TERMS = (
    "营业收入",
    "营收",
    "收入",
    "毛利",
    "毛利率",
    "净利率",
    "利润总额",
    "净利润",
    "归母净利润",
    "扣非归母",
    "扣非净利润",
    "研发投入",
    "研发费用",
    "销售费用",
    "管理费用",
    "财务费用",
    "经营现金流",
    "经营活动现金流量净额",
    "现金流量净额",
    "基本每股收益",
    "稀释每股收益",
    "每股收益",
    "eps",
    "roe",
    "净资产收益率",
    "加权平均净资产收益率",
    "总资产",
    "总负债",
    "净资产",
    "资产负债率",
    "每股净资产",
)
CORE_KEY_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "营业收入": ("operating_revenue", "营业收入", "营收", "收入"),
    "利润总额": ("total_profit", "利润总额"),
    "净利润": ("net_profit", "净利润"),
    "归母净利润": ("parent_net_profit", "归属于上市公司股东的净利润", "归属于本行股东的净利润", "归母净利润"),
    "扣非归母净利润": (
        "deducted_parent_net_profit",
        "归属于上市公司股东的扣除非经常性损益的净利润",
        "扣除非经常性损益后归属于本行股东的净利润",
        "扣非归母净利润",
        "扣非净利润",
        "扣非归母",
    ),
    "经营活动现金流量净额": ("operating_cash_flow_net", "经营活动产生的现金流量净额", "经营现金流", "经营活动现金流量净额"),
    "基本每股收益": ("basic_eps", "基本每股收益", "每股收益", "eps"),
    "稀释每股收益": ("diluted_eps", "稀释每股收益"),
    "扣非基本每股收益": ("deducted_basic_eps", "扣除非经常性损益后的基本每股收益", "扣非基本每股收益"),
    "加权平均净资产收益率": ("weighted_avg_roe", "加权平均净资产收益率", "净资产收益率", "roe"),
    "扣非加权平均净资产收益率": (
        "deducted_weighted_avg_roe",
        "扣除非经常性损益后的加权平均净资产收益率",
        "扣非加权平均净资产收益率",
    ),
    "总资产": ("total_assets", "总资产"),
    "总负债": ("total_liabilities", "总负债"),
    "归属于母公司股东权益": ("equity_attributable_parent", "归属于上市公司股东的净资产", "归属于本行股东权益", "净资产"),
    "每股净资产": ("parent_nav_per_share", "每股净资产"),
}
THREE_STATEMENT_CORE_KEYS: dict[str, tuple[str, ...]] = {
    "income_statement": (
        "operating_revenue",
        "total_operating_revenue",
        "operating_cost",
        "gross_profit",
        "operating_profit",
        "total_profit",
        "net_profit",
        "parent_net_profit",
    ),
    "cash_flow_statement": (
        "operating_cash_inflow_total",
        "operating_cash_outflow_total",
        "operating_cash_flow_net",
        "investing_cash_flow_net",
        "financing_cash_flow_net",
        "cash_and_cash_equivalents_net_increase",
        "ending_cash_and_cash_equivalents",
    ),
    "balance_sheet": (
        "current_assets",
        "non_current_assets",
        "total_assets",
        "current_liabilities",
        "non_current_liabilities",
        "total_liabilities",
        "equity_attributable_parent",
        "total_equity",
        "total_liabilities_and_equity",
    ),
}
THREE_STATEMENT_CORE_NAME_TERMS: dict[str, tuple[str, ...]] = {
    "income_statement": (
        "营业收入",
        "营业总收入",
        "营业成本",
        "营业利润",
        "利润总额",
        "净利润",
        "归属于母公司",
        "归属于本行股东",
    ),
    "cash_flow_statement": (
        "经营活动现金流入小计",
        "经营活动现金流出小计",
        "经营活动产生的现金流量净额",
        "投资活动产生",
        "投资活动使用",
        "筹资活动产生",
        "筹资活动使用",
        "现金及现金等价物净增加额",
        "期末现金及现金等价物余额",
    ),
    "balance_sheet": (
        "流动资产合计",
        "非流动资产合计",
        "资产总计",
        "资产合计",
        "流动负债合计",
        "非流动负债合计",
        "负债合计",
        "归属于母公司股东权益",
        "归属于本行股东权益",
        "所有者权益合计",
        "股东权益合计",
        "负债和股东权益总计",
        "负债和所有者权益总计",
    ),
}
THREE_STATEMENT_LABELS = {
    "income_statement": "利润表",
    "cash_flow_statement": "现金流量表",
    "balance_sheet": "资产负债表",
}
HERMES_PROFILE_DIRS: dict[HermesProfile, Path] = {
    "siq_assistant": HERMES_PROFILE_ROOTS["siq_assistant"],
    "siq_analysis": HERMES_PROFILE_ROOTS["siq_analysis"],
    "siq_factchecker": HERMES_PROFILE_ROOTS["siq_factchecker"],
    "siq_tracking": HERMES_PROFILE_ROOTS["siq_tracking"],
    "siq_legal": HERMES_PROFILE_ROOTS["siq_legal"],
}
DEFAULT_WIKI_ROOT = str(CONFIG_WIKI_ROOT)
PROJECT_WIKI_ROOT = CONFIG_WIKI_ROOT
ASSISTANT_WIKI_ROOT = CONFIG_ASSISTANT_WIKI_ROOT
os.environ.setdefault("SIQ_WIKI_ROOT", str(PROJECT_WIKI_ROOT))
os.environ.setdefault("SIQ_WIKI_ROOT", str(PROJECT_WIKI_ROOT))
os.environ.setdefault(
    "SIQ_DEFAULT_SOURCE_TYPE",
    "wiki_metrics",
)
os.environ.setdefault("SIQ_DEFAULT_SOURCE_TYPE", os.environ.get("SIQ_DEFAULT_SOURCE_TYPE", "wiki_metrics"))
os.environ.setdefault("SIQ_ASSISTANT_DEFAULT_SOURCE_TYPE", "wiki_metrics")
os.environ.setdefault("SIQ_ASSISTANT_DEFAULT_SOURCE_TYPE", os.environ.get("SIQ_ASSISTANT_DEFAULT_SOURCE_TYPE", "wiki_metrics"))
_CURRENT_PROFILE: ContextVar[HermesProfile | None] = ContextVar("siq_current_profile", default=None)


class _ProfileWikiRoot:
    def _path(self) -> Path:
        profile = _CURRENT_PROFILE.get()
        if profile == "siq_assistant":
            return ASSISTANT_WIKI_ROOT
        return PROJECT_WIKI_ROOT

    def __fspath__(self) -> str:
        return os.fspath(self._path())

    def __str__(self) -> str:
        return str(self._path())

    def __repr__(self) -> str:
        return repr(self._path())

    def __truediv__(self, key: str) -> Path:
        return self._path() / key

    def resolve(self) -> Path:
        return self._path().resolve()

    def expanduser(self) -> Path:
        return self._path().expanduser()


WIKI_ROOT = _ProfileWikiRoot()


def _current_default_source_type() -> str:
    profile = _CURRENT_PROFILE.get()
    if profile == "siq_assistant":
        return os.environ.get("SIQ_ASSISTANT_DEFAULT_SOURCE_TYPE") or os.environ.get("SIQ_ASSISTANT_DEFAULT_SOURCE_TYPE", "wiki_metrics")
    return os.environ.get("SIQ_DEFAULT_SOURCE_TYPE") or os.environ.get("SIQ_DEFAULT_SOURCE_TYPE", "wiki_metrics")


def _current_source_type(kind: str) -> str:
    default_source_type = _current_default_source_type()
    prefix = default_source_type.split("_", 1)[0] if default_source_type.startswith("wiki_") else "wiki"
    return f"{prefix}_{kind}"


def _configure_wiki_module(module: Any | None) -> Any | None:
    if module is None:
        return None
    try:
        module.WIKI_BASE = WIKI_ROOT._path()
    except Exception:
        pass
    try:
        module.DEFAULT_SOURCE_TYPE = _current_default_source_type()
    except Exception:
        pass
    return module


@contextmanager
def _profile_wiki_context(profile: HermesProfile):
    token = _CURRENT_PROFILE.set(profile)
    try:
        yield
    finally:
        _CURRENT_PROFILE.reset(token)
FINANCIAL_QUERY_API_DIR = DB_PROGRAM_ROOT
PDF2MD_RESULTS_ROOTS = _env_path_list(
    ("SIQ_PDF2MD_RESULTS_DIRS", "SIQ_PDF2MD_RESULTS_DIRS"),
    PDF_RESULT_ROOT_CANDIDATES,
)
PDF2MD_OUTPUT_ROOTS = _env_path_list(
    ("SIQ_PDF2MD_OUTPUT_DIRS", "SIQ_PDF2MD_OUTPUT_DIRS"),
    PDF_OUTPUT_ROOT_CANDIDATES,
)
PDF2MD_PARSE_ONLY_CONTEXT_LIMIT = _env_int_any(("SIQ_PDF2MD_PARSE_ONLY_CONTEXT_LIMIT", "SIQ_PDF2MD_PARSE_ONLY_CONTEXT_LIMIT"), 3, minimum=1, maximum=8)
TASK_ID_FIELD_RE = re.compile(r"\btask_id=([0-9a-fA-F-]{32,36})\b")
API_TASK_ID_RE = re.compile(r"/api/(?:pdf_page|source)/([0-9a-fA-F-]{32,36})(?:[/?#]|$)")
POSTGRES_FALLBACK_ROW_LIMIT = int(os.environ.get("SIQ_PG_FALLBACK_ROW_LIMIT") or os.environ.get("SIQ_PG_FALLBACK_ROW_LIMIT", "20"))
COMPANY_ALIAS_OVERRIDES: dict[str, tuple[str, ...]] = {
    "BASF-BASF": ("巴斯夫", "巴斯夫集团", "BASF Group"),
}
POSTGRES_FALLBACK_TERMS = (
    *STATEMENT_QUERY_TERMS,
    *CORE_KEY_METRIC_TERMS,
    "财务",
    "业绩",
    "表现",
    "基本面",
    "经营情况",
    "主要数据",
    "核心数据",
    "指标",
    "金额",
    "余额",
    "同比",
    "变化",
    "占比",
    "比例",
    "率",
)
PRIMARY_DATA_EVIDENCE_MARKERS = (
    "## 主要数据溯源",
    "## 主要数据溯源补充",
    "## 财务指标溯源补充",
    "## 指标级引用来源",
)
AUTO_EVIDENCE_SECTION_TITLES = {
    "主要数据溯源",
    "主要数据溯源补充",
    "财务指标溯源补充",
    "主要数据引用来源",
    "指标级引用来源",
    "PostgreSQL 引用",
}
PRIMARY_DATA_SUPPLEMENT_MAX_ROWS = _env_int("SIQ_PRIMARY_DATA_SUPPLEMENT_MAX_ROWS", 28, minimum=6, maximum=80)
REPORT_FULLTEXT_FALLBACK_TERMS = (
    *POSTGRES_FALLBACK_TERMS,
    *FINANCIAL_NOTE_METRIC_TERMS,
    *HUMAN_CAPITAL_QUERY_TERMS,
    *HUMAN_EFFICIENCY_QUERY_TERMS,
    "年报",
    "年度报告",
    "报告",
    "全文",
    "原文",
    "披露",
    "说明",
    "业务",
    "经营",
    "市场",
    "市场占有率",
    "占有率",
    "销量",
    "销售",
    "新能源",
    "出口",
    "海外",
    "研发投入",
    "研发",
    "员工",
    "客户",
    "供应商",
    "订单",
    "产能",
    "产量",
    "分红",
    "股东",
    "前十名",
    "治理",
    "处罚",
    "诉讼",
    "担保",
    "质押",
)
REPORT_FULLTEXT_GENERIC_TERMS = {
    "年报",
    "年度报告",
    "报告",
    "全文",
    "原文",
    "披露",
    "说明",
    "业务",
    "经营",
    "数据",
    "情况",
    "分析",
    "财务",
    "业绩",
    "表现",
    "基本面",
    "主要数据",
    "核心数据",
}
REPORT_ANNUAL_TERMS = ("年报", "年度报告", "年度报", "annual", "2025年报", "2025年度")
REPORT_QUARTERLY_TERMS = ("季报", "季度报告", "一季报", "三季报", "半年报", "半年度报告", "quarter", "quarterly", "2025q")
REPORT_FULLTEXT_MAX_SNIPPETS = _env_int("SIQ_REPORT_FULLTEXT_MAX_SNIPPETS", 6, minimum=1, maximum=20)
REPORT_FULLTEXT_SNIPPET_CHARS = _env_int("SIQ_REPORT_FULLTEXT_SNIPPET_CHARS", 900, minimum=240, maximum=2200)
PROFILE_LABELS: dict[HermesProfile, str] = {
    "siq_assistant": "通用助手",
    "siq_analysis": "智能分析助手",
    "siq_factchecker": "事实核查助手",
    "siq_tracking": "跟踪助手",
    "siq_legal": "法务助手",
}
DIAGNOSTIC_MAX_AGE_SECONDS = 30 * 60
FORCE_REBUILD_TERMS = ("强制重建", "覆盖重建", "强制重新生成", "重新计算", "--force")
ANALYSIS_REPORT_TERMS = (
    "分析报告",
    "年度分析",
    "年报分析",
    "财务诊断报告",
    "年度报告",
    "完整报告",
    "html",
    "markdown",
    ".html",
    ".md",
)
ANALYSIS_GENERATION_TERMS = (
    "生成",
    "重新生成",
    "重生成",
    "重建",
    "覆盖",
    "创建",
    "新建",
    "撰写",
    "编写",
    "写一份",
    "做一份",
    "出一份",
    "跑",
    "跑一遍",
    "执行",
    "启动",
    "开始",
    "续跑",
    "恢复",
    "渲染",
)
ANALYSIS_STATUS_TERMS = (
    "完成了吗",
    "完成了么",
    "是否完成",
    "生成了吗",
    "生成了么",
    "报告路径",
    "验收结果",
    "验收了吗",
    "进度",
)
WIKI_CATALOG_COUNT_TERMS = (
    "多少家",
    "几家",
    "总数",
    "数量",
    "规模",
    "count",
)
WIKI_CATALOG_LIST_TERMS = (
    "清单",
    "列表",
    "名单",
    "列出",
    "展示",
    "看看",
    "有哪些",
    "都有谁",
    "list",
)
WIKI_CATALOG_SUBJECT_TERMS = (
    "已入库",
    "入库",
    "wiki",
    "Wiki",
    "公司",
    "财报",
    "工作集",
    "知识库",
)

@dataclass
class RecentRunRecord:
    message_hash: str
    reply: str
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ChatRequestEnvelope:
    all_attachments: list[dict[str, Any]]
    message_hash: str
    user_display_message: str


@dataclass(frozen=True)
class ChatRunPreflightContext:
    history: list[dict[str, Any]]
    local_memory_context: str | None
    attachments: list[dict[str, Any]]

    @property
    def allow_initialize(self) -> bool:
        return not self.history


SESSION_DEFAULT_CONTEXTS: dict[tuple[HermesProfile, str], str] = {}
RECENT_COMPLETED_RUNS: dict[tuple[HermesProfile, str], RecentRunRecord] = {}


def _read_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_task_id_like(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{32,36}", str(value or "").strip()))


def _pdf2md_task_result_dir(task_id: str) -> Path | None:
    if not _is_task_id_like(task_id):
        return None
    for root in PDF2MD_RESULTS_ROOTS:
        candidate = root / task_id
        if not candidate.is_dir():
            continue
        if any((candidate / name).exists() for name in ("result.md", "result_complete.md", "document_full.json", "content_list.json")):
            return candidate
    return None


def _pdf2md_task_output_dir(task_id: str) -> Path | None:
    if not _is_task_id_like(task_id):
        return None
    for root in PDF2MD_OUTPUT_ROOTS:
        candidate = root / task_id
        if candidate.exists():
            return candidate
    return None


def _file_contains_bytes(path: Path, needle: bytes) -> bool:
    if not needle:
        return False
    try:
        with path.open("rb") as handle:
            overlap = max(len(needle) - 1, 0)
            previous = b""
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                haystack = previous + chunk
                if needle in haystack:
                    return True
                previous = haystack[-overlap:] if overlap else b""
    except Exception:
        return False


def _company_wiki_contains_task_id(company_dir: Path, task_id: str) -> bool:
    if not company_dir.exists() or not _is_task_id_like(task_id):
        return False
    needle = task_id.encode("utf-8")
    preferred_files: list[Path] = [
        company_dir / "company.json",
        *(company_dir / "reports").glob("*/artifact_manifest.json"),
        *(company_dir / "reports").glob("*/report.json"),
        *(company_dir / "reports").glob("*/document_full.json"),
        *(company_dir / "metrics").glob("**/*.json"),
        *(company_dir / "evidence").glob("*.json"),
        *(company_dir / "semantic").glob("*.json"),
    ]
    seen: set[Path] = set()
    for path in preferred_files:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if _file_contains_bytes(path, needle):
            return True
    return False


def _wiki_task_id_exists(task_id: str, message: str = "", context: Any | None = None) -> bool:
    if not _is_task_id_like(task_id):
        return False
    company_dirs = _resolve_company_dirs(message, context, limit=6) if message or context else []
    for company_dir in company_dirs:
        if _company_wiki_contains_task_id(company_dir, task_id):
            return True

    companies_dir = WIKI_ROOT / "companies"
    if not companies_dir.exists():
        return False
    needle = task_id.encode("utf-8")
    try:
        manifests = list(companies_dir.glob("*/company.json"))
        manifests.extend(companies_dir.glob("*/reports/*/artifact_manifest.json"))
        manifests.extend(companies_dir.glob("*/reports/*/report.json"))
    except Exception:
        return False
    return any(path.is_file() and _file_contains_bytes(path, needle) for path in manifests[:3000])


def _task_id_exists(task_id: str, message: str = "", context: Any | None = None) -> bool:
    task_id = str(task_id or "").strip()
    if not _is_task_id_like(task_id):
        return False
    return bool(
        _pdf2md_task_result_dir(task_id)
        or _pdf2md_task_output_dir(task_id)
        or _wiki_task_id_exists(task_id, message, context)
    )


def _extract_task_ids_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    task_ids = [match.group(1) for match in TASK_ID_FIELD_RE.finditer(text)]
    task_ids.extend(match.group(1) for match in API_TASK_ID_RE.finditer(text))
    return sorted(dict.fromkeys(task_id.strip() for task_id in task_ids if _is_task_id_like(task_id)))


def _invalid_task_ids_in_reply(message: str, context: Any | None, reply: str) -> list[str]:
    return [
        task_id
        for task_id in _extract_task_ids_from_text(reply)
        if not _task_id_exists(task_id, message, context)
    ]


def _infer_stock_code_from_text(text: str) -> str:
    return agent_runtime_parse_only.infer_stock_code_from_text(text)


def _infer_company_name_from_filename(filename: str) -> str:
    return agent_runtime_parse_only.infer_company_name_from_filename(filename)


def _pdf2md_task_info_from_dir(result_dir: Path) -> dict[str, Any] | None:
    task_id = result_dir.name
    if not _is_task_id_like(task_id):
        return None
    info: dict[str, Any] = {
        "task_id": task_id,
        "result_dir": result_dir,
    }
    for file_name in ("artifact_manifest.json", "financial_data.json", "quality_report.json", "result_payload_summary.json"):
        payload = _read_json_file(result_dir / file_name)
        if not isinstance(payload, dict):
            continue
        for key in ("filename", "result_file", "file_name", "original_filename"):
            value = payload.get(key)
            if value and not info.get("filename"):
                info["filename"] = str(value)
        if file_name == "artifact_manifest.json":
            core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
            info["ready"] = bool(core.get("ready") or core.get("status") == "ready")
            artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
            if artifacts:
                info["artifacts"] = artifacts
    filename = str(info.get("filename") or "")
    info["stock_code"] = _infer_stock_code_from_text(filename)
    info["company_name"] = _infer_company_name_from_filename(filename)

    result_md = result_dir / "result.md"
    if result_md.is_file():
        info["result_md"] = result_md
    for name in ("result_complete.md", "document_full.json", "content_list_enhanced.json", "content_list.json", "table_index.json", "financial_data.json"):
        path = result_dir / name
        if path.exists():
            info[name.replace(".", "_")] = path
    if not any(key in info for key in ("result_md", "document_full_json", "content_list_json", "financial_data_json")):
        return None
    return info


def _iter_pdf2md_task_infos() -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in PDF2MD_RESULTS_ROOTS:
        try:
            children = sorted(root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
        except Exception:
            continue
        for child in children:
            if not child.is_dir() or child.name in seen:
                continue
            seen.add(child.name)
            info = _pdf2md_task_info_from_dir(child)
            if info:
                infos.append(info)
    return infos


def _pdf2md_task_aliases(info: dict[str, Any]) -> list[str]:
    return agent_runtime_parse_only.pdf2md_task_aliases(info)


def _pdf2md_info_matches_message(info: dict[str, Any], message: str, context: Any | None = None) -> bool:
    return agent_runtime_parse_only.pdf2md_info_matches_message(
        info,
        message,
        context,
        normalize_text=_normalize_financial_text,
        context_company_hint=_context_company_hint,
    )


def _wiki_company_exists_for_pdf2md_info(info: dict[str, Any]) -> bool:
    stock_code = str(info.get("stock_code") or "").strip()
    if stock_code:
        try:
            if any((WIKI_ROOT / "companies").glob(f"{stock_code}-*")):
                return True
        except Exception:
            pass
    catalog = _read_json_file(WIKI_ROOT / "_meta" / "company_catalog.json")
    companies = catalog.get("companies") if isinstance(catalog, dict) else None
    if not isinstance(companies, list):
        return False
    aliases = {_normalize_financial_text(alias) for alias in _pdf2md_task_aliases(info) if alias}
    aliases.discard("")
    for company in companies:
        if not isinstance(company, dict):
            continue
        company_aliases = {
            _normalize_financial_text(value)
            for value in (
                company.get("company_id"),
                company.get("stock_code"),
                company.get("company_short_name"),
                company.get("company_full_name"),
                *((company.get("aliases") or []) if isinstance(company.get("aliases"), list) else []),
            )
            if value
        }
        if aliases & company_aliases:
            return True
    return False


def _pdf2md_parse_only_matches(message: str, context: Any | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    return agent_runtime_parse_only._pdf2md_parse_only_matches(
        message,
        context,
        limit=limit,
        iter_pdf2md_task_infos=_iter_pdf2md_task_infos,
        pdf2md_info_matches_message=_pdf2md_info_matches_message,
        wiki_company_exists_for_pdf2md_info=_wiki_company_exists_for_pdf2md_info,
        is_general_assistant_request=_is_general_assistant_request,
        resolve_company_dir=_resolve_company_dir,
    )


def _should_consider_pdf2md_parse_only_context(message: str, context: Any | None = None) -> bool:
    return agent_runtime_parse_only._should_consider_pdf2md_parse_only_context(
        message,
        context,
        pdf2md_parse_only_matches=_pdf2md_parse_only_matches,
        is_general_assistant_request=_is_general_assistant_request,
        resolve_company_dir=_resolve_company_dir,
        report_fulltext_fallback_terms=REPORT_FULLTEXT_FALLBACK_TERMS,
        context_company_hint=_context_company_hint,
    )


def build_pdf2md_parse_only_context(message: str, context: Any | None = None) -> str | None:
    return agent_runtime_parse_only.build_pdf2md_parse_only_context(
        message,
        context,
        pdf2md_parse_only_matches=_pdf2md_parse_only_matches,
        parse_only_context_limit=PDF2MD_PARSE_ONLY_CONTEXT_LIMIT,
    )


def _is_wiki_catalog_query(message: str) -> bool:
    return agent_runtime_catalog.is_wiki_catalog_query(
        message,
        is_general_assistant_request=_is_general_assistant_request,
        count_terms=WIKI_CATALOG_COUNT_TERMS,
        list_terms=WIKI_CATALOG_LIST_TERMS,
        subject_terms=WIKI_CATALOG_SUBJECT_TERMS,
    )


def _wiki_catalog_path() -> Path:
    return agent_runtime_catalog.wiki_catalog_path(wiki_root=WIKI_ROOT)


def _load_wiki_catalog_companies() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return agent_runtime_catalog.load_wiki_catalog_companies(
        wiki_root=WIKI_ROOT,
        read_json_file=_read_json_file,
    )


def _format_catalog_company_line(index: int, company: dict[str, Any]) -> str:
    return agent_runtime_catalog.format_catalog_company_line(index, company)


def build_wiki_catalog_reply(message: str) -> str | None:
    return agent_runtime_catalog.build_wiki_catalog_reply(
        message,
        wiki_root=WIKI_ROOT,
        is_general_assistant_request=_is_general_assistant_request,
        read_json_file=_read_json_file,
        count_terms=WIKI_CATALOG_COUNT_TERMS,
        list_terms=WIKI_CATALOG_LIST_TERMS,
        subject_terms=WIKI_CATALOG_SUBJECT_TERMS,
    )


def _latest_hermes_session(profile: HermesProfile) -> Path | None:
    recent = _recent_hermes_sessions(profile, limit=1)
    return recent[0] if recent else None


def _profile_diagnostic_context(profile: HermesProfile, session_file: Path | None = None) -> dict[str, Any]:
    profile = _runtime_profile(profile)
    return {
        "scope": "profile",
        "profile": profile,
        "profile_label": PROFILE_LABELS.get(profile, profile),
        "session_file": str(session_file) if session_file else None,
    }


def _session_age_seconds(path: Path) -> float:
    return max(0.0, (datetime.utcnow() - datetime.utcfromtimestamp(path.stat().st_mtime)).total_seconds())


def _is_recent_diagnostic_session(path: Path) -> bool:
    return _session_age_seconds(path) <= DIAGNOSTIC_MAX_AGE_SECONDS


def _recent_hermes_sessions(profile: HermesProfile, *, limit: int = 20) -> list[Path]:
    profile = _runtime_profile(profile)
    profile_dir = HERMES_PROFILE_DIRS.get(profile)
    sessions_dir = profile_dir / "sessions" if profile_dir else None
    if not sessions_dir or not sessions_dir.exists():
        return []

    candidates = [path for path in sessions_dir.glob("*.json") if path.is_file()]
    if not candidates:
        return []
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _hash_text(text: str) -> str:
    return agent_runtime_dedupe._hash_text(text)


def normalize_plain_inline_latex(content: str | None) -> str:
    return agent_runtime_citations.normalize_plain_inline_latex(content)


def _force_rebuild_requested(message: str) -> bool:
    return agent_runtime_context.force_rebuild_requested(message, FORCE_REBUILD_TERMS)


def _analysis_completed_guard_applies(message: str) -> bool:
    return agent_runtime_context.analysis_completed_guard_applies(
        message,
        status_terms=ANALYSIS_STATUS_TERMS,
        report_terms=ANALYSIS_REPORT_TERMS,
        generation_terms=ANALYSIS_GENERATION_TERMS,
    )


def _should_use_analysis_completion_guard(message: str) -> bool:
    return agent_runtime_context.should_use_analysis_completion_guard(
        message,
        force_rebuild_terms=FORCE_REBUILD_TERMS,
        status_terms=ANALYSIS_STATUS_TERMS,
        report_terms=ANALYSIS_REPORT_TERMS,
        generation_terms=ANALYSIS_GENERATION_TERMS,
    )


def _dedupe_hash(message: str, context: Any | None) -> str:
    return agent_runtime_dedupe._dedupe_hash(message, context)


def _attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return agent_runtime_context.attachment_dicts(attachments)


def _image_attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return agent_runtime_context.image_attachment_dicts(attachments)


def _document_attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return agent_runtime_context.document_attachment_dicts(attachments)


def _should_reuse_recent_attachments(message: str) -> bool:
    return agent_runtime_context.should_reuse_recent_attachments(message, ATTACHMENT_FOLLOWUP_RE)


def _attachment_reference_context(attachments: Any | None) -> str:
    items: list[str] = []
    for index, item in enumerate(_attachment_dicts(attachments), start=1):
        path = _safe_uploaded_path(item)
        if path is None:
            continue
        kind = str(item.get("kind") or "image")
        label = "图片" if kind == "image" else "文档"
        filename = str(item.get("filename") or path.name or f"attachment-{index}").strip()
        content_type = str(item.get("content_type") or "application/octet-stream").strip()
        lines = [
            f"- {label}附件 {index}: {filename}",
            f"  - 本地路径: {path}",
            f"  - 类型: {content_type}",
            f"  - 大小: {item.get('size', 0)} bytes",
        ]
        url = str(item.get("url") or "").strip()
        if url:
            lines.append(f"  - 前端链接: {url}")
        items.append("\n".join(lines))
    if not items:
        return ""
    return (
        "历史附件上下文：以下附件已由 SIQ 后端保存。继续/重试时请使用本地路径读取；"
        "`/api/chat/attachments/...` 是前端后端路由，不是 Hermes 8642 网关接口。\n"
        + "\n".join(items)
    )


def _dedupe_hash_with_attachments(message: str, context: Any | None, attachments: Any | None) -> str:
    return agent_runtime_dedupe._dedupe_hash_with_attachments(message, context, attachments)


def _image_attachment_data_url(item: dict[str, Any]) -> str | None:
    path = Path(str(item.get("path") or ""))
    try:
        resolved = path.resolve()
        root = CHAT_UPLOAD_ROOT.resolve()
        if root not in resolved.parents:
            return None
        raw = resolved.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    content_type = str(item.get("content_type") or "image/png").strip() or "image/png"
    if not content_type.startswith("image/"):
        content_type = "image/png"
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


async def _resolve_image_model_name() -> str | None:
    global _IMAGE_MODEL_NAME_CACHE
    if IMAGE_MODEL_NAME:
        return IMAGE_MODEL_NAME
    if _IMAGE_MODEL_NAME_CACHE:
        return _IMAGE_MODEL_NAME_CACHE
    if not IMAGE_MODEL_ENABLED or not IMAGE_MODEL_BASE_URL:
        return None
    try:
        timeout = httpx.Timeout(10.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{IMAGE_MODEL_BASE_URL}/models")
        if not response.is_success:
            return None
        payload = response.json() if response.content else {}
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                _IMAGE_MODEL_NAME_CACHE = str(item["id"])
                return _IMAGE_MODEL_NAME_CACHE
    except Exception:
        return None
    return None


def _extract_openai_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content") or first.get("text") or ""
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, dict):
                value = part.get("text") or part.get("content")
                if value:
                    pieces.append(str(value))
        return "\n".join(pieces).strip()
    return str(content or "").strip()


async def _analyze_single_image_with_primary_model(
    client: httpx.AsyncClient,
    *,
    model: str,
    message: str,
    item: dict[str, Any],
    index: int,
    total: int,
) -> str:
    data_url = _image_attachment_data_url(item)
    if not data_url:
        raise RuntimeError("image file is unavailable")
    filename = str(item.get("filename") or Path(str(item.get("path") or "")).name or f"image-{index}")
    prompt = (
        "用户在聊天对话框上传了一张图片。请用中文客观分析这张图片，优先提取可见文字、数字、表格、图表结构、"
        "关键对象和可能影响财务/合规判断的信息；无法确定的内容明确说明不确定。"
        f"\n\n图片: {filename} ({index}/{total})"
        f"\n用户问题: {(message or '').strip() or '请分析图片内容'}"
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    response = await client.post(f"{IMAGE_MODEL_BASE_URL}/chat/completions", json=payload)
    if not response.is_success:
        raise RuntimeError(f"image model HTTP {response.status_code}: {response.text[:300]}")
    text = _extract_openai_message_text(response.json() if response.content else {})
    if not text:
        raise RuntimeError("image model returned empty content")
    return f"### 图片 {index}: {filename}\n{text}"


async def analyze_images_with_primary_model(
    message: str,
    attachments: Any | None,
) -> tuple[str, bool]:
    images = _image_attachment_dicts(attachments)
    if not images or not IMAGE_MODEL_ENABLED:
        return "", False
    model = await _resolve_image_model_name()
    if not model:
        return "", False
    blocks: list[str] = []
    try:
        timeout = httpx.Timeout(IMAGE_MODEL_TIMEOUT_SECONDS, connect=10.0, read=IMAGE_MODEL_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, item in enumerate(images, start=1):
                blocks.append(
                    await _analyze_single_image_with_primary_model(
                        client,
                        model=model,
                        message=message,
                        item=item,
                        index=index,
                        total=len(images),
                    )
                )
    except Exception as exc:
        print(f"[chat-attachments] primary image model unavailable, falling back to Hermes: {exc}")
        return "", False
    return (
        "图片已优先由本机多模态模型处理。下面是模型初步分析，回答时应结合用户问题使用；"
        "如需复核细节，可继续读取图片本地路径。\n\n" + "\n\n".join(blocks),
        True,
    )


def _safe_chat_path(raw_path: str, *, must_be_file: bool = True) -> Path | None:
    if not raw_path:
        return None
    try:
        resolved = Path(raw_path).resolve()
        root = CHAT_UPLOAD_ROOT.resolve()
        if root not in resolved.parents:
            return None
        if must_be_file and not resolved.is_file():
            return None
        return resolved
    except Exception:
        return None


def _safe_uploaded_path(item: dict[str, Any]) -> Path | None:
    return _safe_chat_path(str(item.get("path") or ""))


def _attachment_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    merged = dict(metadata or {})
    parse_dir = _safe_chat_path(str(merged.get("parse_dir") or ""), must_be_file=False)
    if parse_dir is None:
        return merged
    metadata_path = parse_dir / "metadata.json"
    if metadata_path.is_file():
        try:
            parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                merged.update(parsed)
        except Exception:
            pass
    merged["parse_dir"] = str(parse_dir)
    merged["metadata_path"] = str(metadata_path)
    return merged


def _pdf_attachment_parse_dirs(attachments: Any | None) -> list[Path]:
    parse_dirs: list[Path] = []
    seen: set[Path] = set()
    for item in _document_attachment_dicts(attachments):
        content_type = str(item.get("content_type") or "").lower()
        path = Path(str(item.get("path") or ""))
        if content_type != "application/pdf" and path.suffix.lower() != ".pdf":
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        parse_dir = _safe_chat_path(str(metadata.get("parse_dir") or ""), must_be_file=False)
        if parse_dir and parse_dir not in seen:
            seen.add(parse_dir)
            parse_dirs.append(parse_dir)
    return parse_dirs


def _pdf_parse_is_terminal(metadata: dict[str, Any]) -> bool:
    status = str(
        metadata.get("document_parser_status")
        or metadata.get("mineru_parse_status")
        or metadata.get("mineru_submit_status")
        or ""
    ).lower()
    if status in {"completed_with_warnings"}:
        return True
    if status in {"completed", "completed_without_markdown", "failed", "error", "failure", "cancelled", "timeout"}:
        return True
    if status in {"completed_result_fetch_failed", "status_failed", "poll_failed"}:
        return True
    return False


async def wait_for_pdf_attachment_parses(
    attachments: Any | None,
    *,
    timeout_seconds: int = CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS,
    poll_seconds: int = CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS,
) -> list[dict[str, Any]]:
    parse_dirs = _pdf_attachment_parse_dirs(attachments)
    if not parse_dirs or timeout_seconds <= 0:
        return []

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_statuses: list[dict[str, Any]] = []
    while True:
        pending = False
        statuses: list[dict[str, Any]] = []
        for parse_dir in parse_dirs:
            metadata_path = parse_dir / "metadata.json"
            metadata: dict[str, Any] = {"parse_dir": str(parse_dir), "metadata_path": str(metadata_path)}
            if metadata_path.is_file():
                try:
                    parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        metadata.update(parsed)
                except Exception as exc:
                    metadata["mineru_parse_status"] = "metadata_read_failed"
                    metadata["mineru_parse_error"] = str(exc)
            else:
                metadata["mineru_parse_status"] = "metadata_missing"
            if not _pdf_parse_is_terminal(metadata):
                pending = True
            statuses.append(metadata)
        last_statuses = statuses
        if not pending or asyncio.get_running_loop().time() >= deadline:
            return last_statuses
        await asyncio.sleep(max(1, poll_seconds))


def _attachments_with_fresh_metadata(attachments: Any | None) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    for item in _attachment_dicts(attachments):
        updated = dict(item)
        metadata = _attachment_metadata(updated)
        if metadata:
            updated["metadata"] = metadata
        refreshed.append(updated)
    return refreshed


async def _ensure_chatmessage_attachments_column(async_session: AsyncSession) -> None:
    global _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY
    if _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY:
        return
    try:
        bind = async_session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""
        if dialect == "sqlite":
            result = await async_session.exec(sql_text("PRAGMA table_info(chatmessage)"))
            columns = {str(row[1]) for row in result.all()}
            if "attachments_json" not in columns:
                await async_session.exec(sql_text("ALTER TABLE chatmessage ADD COLUMN attachments_json TEXT"))
                await async_session.commit()
        else:
            await async_session.exec(sql_text("ALTER TABLE chatmessage ADD COLUMN IF NOT EXISTS attachments_json TEXT"))
            await async_session.commit()
        _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY = True
    except Exception:
        await async_session.rollback()
        raise


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding, errors="replace")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{ns}p"):
            pieces = [node.text or "" for node in paragraph.iter(f"{ns}t")]
            text = "".join(pieces).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)
    except Exception as exc:
        return f"[DOCX 文本抽取失败: {exc}]"


def _read_pdf_text_with_pdftotext(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-f", "1", "-l", "8", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError:
        return "[PDF 快速文本抽取跳过: 系统未安装 pdftotext；请使用 MinerU 解析任务结果。]"
    except Exception as exc:
        return f"[PDF 快速文本抽取失败: {exc}]"
    text = (result.stdout or "").strip()
    if text:
        return text
    err = (result.stderr or "").strip()
    return f"[PDF 快速文本抽取无可用文本{f': {err}' if err else ''}；请使用 MinerU 解析任务结果。]"


def _document_text_preview(item: dict[str, Any]) -> str:
    path = _safe_uploaded_path(item)
    if path is None:
        return "[文档文件不可读取或路径不在允许目录内]"
    content_type = str(item.get("content_type") or "").lower()
    suffix = path.suffix.lower()
    if content_type == "application/pdf" or suffix == ".pdf":
        metadata = _attachment_metadata(item)
        markdown_path = _safe_chat_path(str(metadata.get("markdown_path") or ""))
        if markdown_path and markdown_path.is_file():
            return _read_text_file(markdown_path)
        return _read_pdf_text_with_pdftotext(path)
    if suffix == ".docx" or content_type.endswith("wordprocessingml.document"):
        return _read_docx_text(path)
    if suffix == ".doc":
        return "[旧版 .doc 已保存，但当前环境未配置稳定的 .doc 文本抽取器；如需精读，请先转换为 .docx/PDF/Markdown。]"
    return _read_text_file(path)


def _truncate_document_text(text: str, limit: int = MAX_DOCUMENT_CONTEXT_CHARS) -> str:
    cleaned = re.sub(r"\n{4,}", "\n\n\n", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + f"\n\n[文档预览已截断，仅展示前 {limit} 字符。请用文件路径或 MinerU 任务结果继续精读。]"


def _document_attachment_context(attachments: Any | None) -> str:
    docs = _document_attachment_dicts(attachments)
    if not docs:
        return ""
    blocks = [
        "用户本轮上传了以下文档附件。请优先基于附件内容回答；需要全文、表格或版面证据时，使用给出的本地路径或 MinerU/PDF 解析任务信息继续读取。"
    ]
    for index, item in enumerate(docs, start=1):
        filename = str(item.get("filename") or Path(str(item.get("path") or "")).name or f"document-{index}")
        path = str(item.get("path") or "")
        content_type = str(item.get("content_type") or "application/octet-stream")
        metadata = _attachment_metadata(item)
        lines = [
            f"### 文档附件 {index}: {filename}",
            f"- 本地路径: {path}",
            f"- 类型: {content_type}",
            f"- 大小: {item.get('size', 0)} bytes",
        ]
        task_id = metadata.get("mineru_task_id") if metadata else None
        if task_id:
            lines.extend(
                [
                    f"- 解析任务: {task_id}",
                    f"- MinerU 直连解析任务: {task_id}",
                    f"- 通用文档解析任务: {metadata.get('document_parser_task_id') or task_id}",
                    f"- 状态接口: {metadata.get('document_parser_status_url') or metadata.get('mineru_status_url')}",
                    f"- 结果接口: {metadata.get('document_parser_result_url') or metadata.get('mineru_result_url')}",
                    f"- 工作台页面: {metadata.get('document_parser_page_url') or ''}",
                    f"- 独立解析目录: {metadata.get('parse_dir')}",
                    f"- 元数据文件: {metadata.get('metadata_path')}",
                    f"- 当前解析状态: {metadata.get('document_parser_status') or metadata.get('mineru_parse_status') or metadata.get('mineru_submit_status')}",
                    "- 该 PDF 走通用 document-parser，不进入财报解析前端队列。",
                    "- 如用户询问 PDF 版面、表格或长文档细节，应优先读取独立解析目录中的 result.md，或使用通用文档解析 source map / blocks / tables 产物。",
                ]
            )
            if metadata.get("document_parser_source_map_url"):
                lines.append(f"- Source map: {metadata.get('document_parser_source_map_url')}")
            if metadata.get("document_parser_blocks_url"):
                lines.append(f"- Blocks: {metadata.get('document_parser_blocks_url')}")
            if metadata.get("document_parser_tables_url"):
                lines.append(f"- Tables: {metadata.get('document_parser_tables_url')}")
            if metadata.get("document_parser_source_page_url_template"):
                lines.append(f"- 页来源模板: {metadata.get('document_parser_source_page_url_template')}")
            if metadata.get("document_parser_source_block_url_template"):
                lines.append(f"- 块来源模板: {metadata.get('document_parser_source_block_url_template')}")
            if metadata.get("document_parser_source_table_url_template"):
                lines.append(f"- 表格来源模板: {metadata.get('document_parser_source_table_url_template')}")
            lines.append("- 引用来源如需给出可点击链接，优先使用 `/api/documents/source/<task_id>/page/<page_number>`、`/api/documents/source/<task_id>/block/<block_id>` 或 `/api/documents/source/<task_id>/table/<table_id>`。")
            if not metadata.get("document_parser_task_id"):
                lines.append("- 该 PDF 没有进入财报解析前端队列，也不会写入任何公司 Wiki/入库解析产物目录。")
            if metadata.get("markdown_path"):
                lines.append(f"- Markdown: {metadata.get('markdown_path')}")
            if metadata.get("content_list_path"):
                lines.append(f"- content_list: {metadata.get('content_list_path')}")
        elif metadata:
            if metadata.get("parse_dir"):
                lines.append(f"- 独立解析目录: {metadata.get('parse_dir')}")
            if metadata.get("document_parser_submit_status"):
                lines.append(f"- 文档解析提交状态: {metadata.get('document_parser_submit_status')}")
            else:
                lines.append(f"- MinerU 提交状态: {metadata.get('mineru_submit_status')}")
            if metadata.get("document_parser_status"):
                lines.append(f"- 文档解析状态: {metadata.get('document_parser_status')}")
            if metadata.get("mineru_parse_status"):
                lines.append(f"- MinerU 解析状态: {metadata.get('mineru_parse_status')}")
            if (
                metadata.get("queue_policy") == "direct_mineru_no_pdf2md_frontend_queue"
                or metadata.get("queue_policy") == "document_parser_chat_attachment"
                or metadata.get("submitted_to_project_queue") is False
            ):
                if metadata.get("queue_policy") == "direct_mineru_no_pdf2md_frontend_queue":
                    lines.append("- 该 PDF 没有进入财报解析前端队列，也不会写入任何公司 Wiki/入库解析产物目录。")
                else:
                    lines.append("- 该 PDF 没有进入财报解析前端队列。")
            if metadata.get("document_parser_submit_error"):
                lines.append(f"- 文档解析提交错误: {metadata.get('document_parser_submit_error')}")
            if metadata.get("document_parser_error"):
                lines.append(f"- 文档解析错误: {metadata.get('document_parser_error')}")
            if metadata.get("mineru_submit_error"):
                lines.append(f"- MinerU 提交错误: {metadata.get('mineru_submit_error')}")
            if metadata.get("mineru_parse_error"):
                lines.append(f"- MinerU 解析错误: {metadata.get('mineru_parse_error')}")
        preview = _truncate_document_text(_document_text_preview(item))
        if preview:
            lines.extend(["", "```text", preview, "```"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _display_message_with_attachments(message: str, attachments: Any | None) -> str:
    return agent_runtime_display._display_message_with_attachments(message, _attachment_dicts(attachments))


def _recent_duplicate_reply(profile: HermesProfile, session_id: str, message_hash: str) -> str | None:
    record = RECENT_COMPLETED_RUNS.get(_active_key(profile, session_id))
    if not record or record.message_hash != message_hash:
        return None
    age = datetime.utcnow() - record.created_at
    if age > timedelta(seconds=ANALYSIS_IDEMPOTENCY_WINDOW_SECONDS):
        return None
    fallback = ANALYSIS_DUPLICATE_MESSAGE if profile == "siq_analysis" else RECENT_DUPLICATE_MESSAGE
    return record.reply or fallback


def _forget_recent_completed_run(profile: HermesProfile, session_id: str, message_hash: str | None = None) -> None:
    record = RECENT_COMPLETED_RUNS.get(_active_key(profile, session_id))
    if not record:
        return
    if message_hash and record.message_hash != message_hash:
        return
    RECENT_COMPLETED_RUNS.pop(_active_key(profile, session_id), None)


def _remember_completed_run(profile: HermesProfile, session_id: str, message_hash: str | None, reply: str) -> None:
    if not message_hash:
        return
    RECENT_COMPLETED_RUNS[_active_key(profile, session_id)] = RecentRunRecord(message_hash=message_hash, reply=reply)


def normalize_evidence_trace_for_display(content: str | None) -> str:
    return agent_runtime_citations.normalize_evidence_trace_for_display(content)


def _diagnose_latest_hermes_session(profile: HermesProfile) -> dict[str, Any] | None:
    profile_dir = HERMES_PROFILE_DIRS.get(profile)
    if not profile_dir:
        return None

    profile_label = PROFILE_LABELS.get(profile, profile)
    gateway_state = _read_json_file(profile_dir / "gateway_state.json") or {}
    active_agents = int(gateway_state.get("active_agents") or 0)
    recent_sessions = _recent_hermes_sessions(profile)
    latest_session = recent_sessions[0] if recent_sessions else None
    if active_agents > 0:
        return {
            **_profile_diagnostic_context(profile, latest_session),
            "severity": "info",
            "issue": "external_run_active",
            "title": "后台仍在运行",
            "detail": f"{profile_label} 的 Hermes profile 显示仍有 {active_agents} 个活跃 agent，网页连接可能已断开，可稍后刷新或重新接入。",
            "recovery_action": "等待后台 run 完成，或通过停止按钮结束后重新发起任务。",
            "active_agents": active_agents,
        }

    if profile == "siq_analysis":
        recovery = _latest_successful_analysis_recovery()
        if recovery:
            return recovery
    if not recent_sessions:
        return None

    for latest_session in recent_sessions:
        if not _is_recent_diagnostic_session(latest_session):
            continue
        session_data = _read_json_file(latest_session)
        if not isinstance(session_data, dict):
            continue

        messages = session_data.get("messages")
        if not isinstance(messages, list):
            continue

        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text_loop = _detect_output_loop(str(message.get("content") or ""))
            if text_loop:
                return {
                    **_profile_diagnostic_context(profile, latest_session),
                    "severity": "warning",
                    "issue": "text_output_loop_no_progress",
                    "title": "检测到输出循环",
                    "detail": (
                        f"最近一次 {profile_label} 回复在“{text_loop['sample']}”附近反复输出或逐页扫描，"
                        f"命中行 {text_loop['repeated_lines']} 行、不同形态 "
                        f"{text_loop['unique_lines']} 个；说明模型停留在检索过程，没有继续产生可验证结论。"
                    ),
                    "recovery_action": "从 .work 检查点或已生成文件续跑；必要时使用确定性渲染/验收脚本，而不是继续让模型重复叙述。",
                    "active_agents": active_agents,
                    "last_updated": session_data.get("last_updated"),
                }
            break

        tool_events: list[tuple[str, str | None, str]] = []
        max_tool_iteration_notice = False
        for message in messages[-40:]:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "tool":
                status, output = _normalize_tool_output(message.get("content"))
                tool_events.append((str(message.get("name") or "unknown"), status, output))
            elif message.get("role") == "user" and "maximum number of tool-calling iterations" in str(message.get("content") or ""):
                max_tool_iteration_notice = True

        repeated_count = 0
        repeated_tool = ""
        repeated_output = ""
        if tool_events:
            repeated_tool, last_status, repeated_output = tool_events[-1]
            for tool_name, status, output in reversed(tool_events):
                if tool_name == repeated_tool and status == last_status and output == repeated_output:
                    repeated_count += 1
                else:
                    break

        if repeated_count >= 3 or max_tool_iteration_notice:
            output_hash = _hash_text(repeated_output) if repeated_output else None
            return {
                **_profile_diagnostic_context(profile, latest_session),
                "severity": "warning",
                "issue": "tool_loop_no_progress",
                "title": "工具循环已中断",
                "detail": (
                    f"最近一次 {profile_label} run 没有活跃进程，且 {repeated_tool or '工具'} 连续 "
                    f"{repeated_count or '多'} 次返回相同结果，系统随后触发工具调用上限。"
                ),
                "recovery_action": "从 .work 检查点续跑，或直接进入渲染、溯源修复和质量验收阶段。",
                "active_agents": active_agents,
                "last_repeated_tool": repeated_tool or None,
                "last_repeated_output_hash": output_hash,
                "last_updated": session_data.get("last_updated"),
            }

        # The diagnostic endpoint is meant to describe the latest run state.
        # Once the newest valid Hermes session has no loop/failure signal, do
        # not surface stale warnings from older session snapshots.
        return None

    return None


def _latest_successful_analysis_recovery() -> dict[str, Any] | None:
    analysis_root = WIKI_ROOT / "companies"
    if not analysis_root.exists():
        return None
    candidates = [
        path
        for path in analysis_root.glob("*/analysis/.work/*/recovery_result.json")
        if path.is_file()
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    payload = _read_json_file(latest)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    metrics = validation.get("metrics") if isinstance(validation.get("metrics"), dict) else {}
    return {
        "scope": "profile",
        "profile": "siq_analysis",
        "profile_label": PROFILE_LABELS["siq_analysis"],
        "severity": "info",
        "issue": "last_recovery_completed",
        "title": "最近一次恢复已完成",
        "detail": (
            f"确定性恢复流程已通过验收：json_sections={metrics.get('json_sections', '未返回')}，"
            f"markdown_h2={metrics.get('markdown_h2', '未返回')}，"
            f"html_h2={metrics.get('html_h2', '未返回')}，"
            f"api_pdf_links={metrics.get('api_pdf_links', '未返回')}。"
        ),
        "recovery_action": "可以打开恢复生成的 HTML/MD/JSON；后续分析任务若遇到检查点不完整，应继续使用 recover_report_from_workdir.py。",
        "recovery_result": str(latest),
        "html": files.get("html"),
        "last_updated": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
    }


def _trim_tool_preview(value: Any, limit: int = 280) -> str:
    return agent_runtime_progress.trim_tool_preview(value, limit=limit)


def _is_file_search_tool_invocation(tool: str | None, preview: str | None = None) -> bool:
    return agent_runtime_progress.is_file_search_tool_invocation(
        tool,
        preview,
        project_wiki_root=PROJECT_WIKI_ROOT,
        wiki_root=WIKI_ROOT,
    )


def _display_tool_label(tool: str | None, preview: str | None = None) -> str:
    return agent_runtime_progress.display_tool_label(
        tool,
        preview,
        project_wiki_root=PROJECT_WIKI_ROOT,
        wiki_root=WIKI_ROOT,
    )


def get_active_run_snapshot(profile: HermesProfile, session_id: str) -> dict[str, Any]:
    return _streaming_get_active_run_snapshot(
        profile,
        session_id,
        diagnose_latest_hermes_session=_diagnose_latest_hermes_session,
    )


async def stream_active_run_events(
    request: Request,
    *,
    profile: HermesProfile,
    session_id: str,
    offset: int = 0,
) -> AsyncGenerator[dict[str, str], None]:
    async for event in _streaming_stream_active_run_events(
        request,
        profile=profile,
        session_id=session_id,
        offset=offset,
        heartbeat_seconds=STREAM_EVENT_HEARTBEAT_SECONDS,
    ):
        yield event


def normalize_history(messages: list[ChatMessage], limit: int = HISTORY_LIMIT) -> list[dict]:
    return agent_runtime_history.normalize_history(
        messages,
        limit=limit,
        chat_message_has_visible_payload=chat_message_has_visible_payload,
        message_attachments=_message_attachments,
        attachment_reference_context=_attachment_reference_context,
        is_loop_polluted_assistant_message=_is_loop_polluted_assistant_message,
        normalize_evidence_trace_for_display=normalize_evidence_trace_for_display,
        sanitize_assistant_history_reply=_sanitize_assistant_history_reply,
    )


async def load_history(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> list[dict]:
    return await agent_runtime_history.load_history(
        async_session,
        session_id,
        limit=limit,
        normalize_messages=lambda messages: normalize_history(messages, limit=limit),
    )


async def load_recent_session_attachments(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(max(1, limit))
    )
    for message in result.all():
        attachments = _attachments_with_fresh_metadata(_message_attachments(message))
        if attachments:
            return attachments
    return []


def _message_attachments(message: ChatMessage) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    if getattr(message, "attachments_json", None):
        try:
            parsed = json.loads(message.attachments_json or "[]")
            if isinstance(parsed, list):
                attachments = [
                    item for item in parsed
                    if isinstance(item, dict) and str(item.get("path") or "").strip()
                ]
        except Exception:
            attachments = []
    return attachments


def chat_message_has_visible_payload(message: ChatMessage) -> bool:
    if str(message.content or "").strip():
        return True
    return bool(_message_attachments(message))


async def save_message(
    async_session: AsyncSession,
    role: str,
    content: str,
    session_id: str,
    attachments: Any | None = None,
) -> None:
    if role == "assistant":
        content = normalize_evidence_trace_for_display(content)
    attachment_items = _attachments_with_fresh_metadata(attachments)
    if attachment_items:
        await _ensure_chatmessage_attachments_column(async_session)
    msg = ChatMessage(
        role=role,
        content=content,
        session_id=session_id,
        attachments_json=json.dumps(attachment_items, ensure_ascii=False) if attachment_items else None,
    )
    async_session.add(msg)
    await async_session.commit()


async def chat_history_response(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    fetch_limit = max(int(limit or HISTORY_LIMIT), 1)
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(fetch_limit * 3)
    )
    visible_messages = [
        message
        for message in reversed(result.all())
        if chat_message_has_visible_payload(message)
    ]
    return [_chat_message_payload(message) for message in visible_messages[-fetch_limit:]]


def _chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    content = message.content or ""
    if message.role == "assistant":
        content = normalize_evidence_trace_for_display(_assistant_reply_for_display(content))
    attachments = _message_attachments(message)
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": content,
        "created_at": message.created_at,
        "attachments": attachments,
    }


def _session_id_matches_profile(profile: HermesProfile, session_id: str) -> bool:
    prefix = PROFILE_SESSION_PREFIXES.get(profile)
    return bool(prefix and session_id.startswith(prefix))


def hermes_runs_session_id(profile: HermesProfile, session_id: str) -> str:
    return f"siq:{profile}:{session_id}"


def _strip_local_memory_blocks(text: str) -> str:
    return agent_runtime_memory._strip_local_memory_blocks(text)


def _compact_memory_content(role: str, content: str, *, max_chars: int = LOCAL_MEMORY_SNIPPET_CHARS) -> str:
    return agent_runtime_memory._compact_memory_content(
        role,
        content,
        max_chars=max_chars,
        is_loop_polluted_assistant_message=_is_loop_polluted_assistant_message,
        sanitize_assistant_history_reply=_sanitize_assistant_history_reply,
    )


def _local_memory_turn_line(user_text: str, assistant_text: str | None) -> str:
    return agent_runtime_memory._local_memory_turn_line(user_text, assistant_text)


def build_local_memory_summary(
    messages: list[ChatMessage],
    *,
    max_bullets: int = LOCAL_MEMORY_MAX_BULLETS,
    max_chars: int = LOCAL_MEMORY_MAX_CHARS,
) -> str:
    return agent_runtime_memory.build_local_memory_summary(
        messages,
        max_bullets=max_bullets,
        max_chars=max_chars,
        snippet_chars=LOCAL_MEMORY_SNIPPET_CHARS,
        is_loop_polluted_assistant_message=_is_loop_polluted_assistant_message,
        sanitize_assistant_history_reply=_sanitize_assistant_history_reply,
    )


def build_local_memory_context(summary: str | None) -> str | None:
    return agent_runtime_memory.build_local_memory_context(summary)


async def _load_session_memory_record(
    async_session: AsyncSession,
    profile: HermesProfile,
    session_id: str,
) -> ChatSessionMemory | None:
    result = await async_session.exec(
        select(ChatSessionMemory).where(
            ChatSessionMemory.profile == profile,
            ChatSessionMemory.session_id == session_id,
        )
    )
    return result.first()


async def refresh_session_memory(
    async_session: AsyncSession,
    profile: HermesProfile,
    session_id: str,
    *,
    recent_limit: int = LOCAL_MEMORY_RECENT_LIMIT,
) -> None:
    if (
        not LOCAL_MEMORY_ENABLED
        or profile not in LOCAL_MEMORY_ENABLED_PROFILES
        or not _session_id_matches_profile(profile, session_id)
    ):
        return

    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    )
    messages = list(result.all())
    older_messages = agent_runtime_memory.select_local_memory_source_messages(
        messages,
        recent_limit=recent_limit,
    )
    summary = build_local_memory_summary(older_messages)
    last_message_id = older_messages[-1].id if older_messages else None
    record = await _load_session_memory_record(async_session, profile, session_id)

    if record is None:
        if not summary:
            return
        record = ChatSessionMemory(
            profile=profile,
            session_id=session_id,
            summary=summary,
            last_message_id=last_message_id,
        )
        async_session.add(record)
    else:
        record.summary = summary
        record.last_message_id = last_message_id
        record.updated_at = datetime.utcnow()
        async_session.add(record)
    await async_session.commit()


async def load_local_memory_context(
    async_session: AsyncSession,
    profile: HermesProfile,
    session_id: str,
) -> str | None:
    if (
        not LOCAL_MEMORY_ENABLED
        or profile not in LOCAL_MEMORY_ENABLED_PROFILES
        or not _session_id_matches_profile(profile, session_id)
    ):
        return None
    record = await _load_session_memory_record(async_session, profile, session_id)
    return build_local_memory_context(record.summary if record else None)


async def ensure_local_memory_context(
    async_session: AsyncSession,
    profile: HermesProfile,
    session_id: str,
) -> str | None:
    await refresh_session_memory(async_session, profile, session_id)
    return await load_local_memory_context(async_session, profile, session_id)


async def save_message_in_background(
    role: str,
    content: str,
    session_id: str,
    *,
    profile: HermesProfile | None = None,
) -> None:
    async with AsyncSession(async_engine) as async_session:
        await save_message(async_session, role, content, session_id)
        if role == "assistant" and profile:
            await refresh_session_memory(async_session, profile, session_id)


def _clean_context_value(value: Any) -> str:
    return agent_runtime_context.clean_context_value(value)


def _context_dict(context: Any | None) -> dict[str, Any]:
    return agent_runtime_context.context_dict(context)


def _context_company(context: Any | None) -> dict[str, Any]:
    return agent_runtime_context.context_company(context)


def _analysis_completed_artifacts(context: Any | None) -> dict[str, str] | None:
    return agent_runtime_context.analysis_completed_artifacts(
        context,
        read_json_file=_read_json_file,
        wiki_root=WIKI_ROOT,
    )


def _analysis_completion_reply(context: Any | None) -> str | None:
    return agent_runtime_context.analysis_completion_reply(
        context,
        analysis_completed_artifacts=_analysis_completed_artifacts,
        analysis_completed_message=ANALYSIS_COMPLETED_MESSAGE,
    )


def _analysis_completion_guard_input(message: str, artifacts: dict[str, str]) -> str:
    return agent_runtime_context.analysis_completion_guard_input(message, artifacts)


def _is_general_assistant_request(message: str) -> bool:
    return agent_runtime_context.is_general_assistant_request(
        message,
        request_terms=GENERAL_ASSISTANT_REQUEST_TERMS,
        subject_terms=GENERAL_ASSISTANT_SUBJECT_TERMS,
    )


def _is_note_detail_query(message: str) -> bool:
    return agent_runtime_context.note_detail_query_applies(
        message,
        note_detail_query_terms=NOTE_DETAIL_QUERY_TERMS,
        note_detail_exclude_terms=NOTE_DETAIL_EXCLUDE_TERMS,
        financial_note_metric_terms=FINANCIAL_NOTE_METRIC_TERMS,
        statement_terms=STATEMENT_QUERY_TERMS,
        is_general_assistant_request=_is_general_assistant_request,
    )


def _should_direct_answer_note_detail(message: str) -> bool:
    return agent_runtime_context.direct_note_detail_answer_applies(
        message,
        note_detail_query_terms=NOTE_DETAIL_QUERY_TERMS,
        note_detail_exclude_terms=NOTE_DETAIL_EXCLUDE_TERMS,
        note_detail_direct_terms=NOTE_DETAIL_DIRECT_TERMS,
        note_detail_analysis_terms=NOTE_DETAIL_ANALYSIS_TERMS,
        financial_note_metric_terms=FINANCIAL_NOTE_METRIC_TERMS,
        statement_terms=STATEMENT_QUERY_TERMS,
        is_general_assistant_request=_is_general_assistant_request,
    )


def _is_financial_note_metric_query(message: str) -> bool:
    return agent_runtime_context.financial_note_metric_query_applies(
        message,
        note_detail_query_terms=NOTE_DETAIL_QUERY_TERMS,
        note_detail_exclude_terms=NOTE_DETAIL_EXCLUDE_TERMS,
        financial_note_metric_terms=FINANCIAL_NOTE_METRIC_TERMS,
        financial_evidence_action_terms=FINANCIAL_EVIDENCE_ACTION_TERMS,
        statement_terms=STATEMENT_QUERY_TERMS,
        is_general_assistant_request=_is_general_assistant_request,
    )


def _should_inject_note_detail_context(message: str) -> bool:
    return agent_runtime_context.note_detail_context_applies(
        message,
        note_detail_query_terms=NOTE_DETAIL_QUERY_TERMS,
        note_detail_exclude_terms=NOTE_DETAIL_EXCLUDE_TERMS,
        financial_note_metric_terms=FINANCIAL_NOTE_METRIC_TERMS,
        financial_evidence_action_terms=FINANCIAL_EVIDENCE_ACTION_TERMS,
        statement_terms=STATEMENT_QUERY_TERMS,
        is_general_assistant_request=_is_general_assistant_request,
    )


def _is_statement_query(message: str) -> bool:
    return agent_runtime_context.statement_query_applies(
        message,
        statement_terms=STATEMENT_QUERY_TERMS,
        is_general_assistant_request=_is_general_assistant_request,
    )


def _should_direct_answer_statement_query(message: str) -> bool:
    return agent_runtime_context.direct_statement_answer_applies(
        message,
        statement_terms=STATEMENT_QUERY_TERMS,
        statement_direct_terms=STATEMENT_DIRECT_TERMS,
        note_detail_analysis_terms=NOTE_DETAIL_ANALYSIS_TERMS,
        is_general_assistant_request=_is_general_assistant_request,
    )


def _context_company_hint(context: Any | None) -> str:
    return agent_runtime_context.context_company_hint(context)


def _forced_context_company_dir(context: Any | None) -> Path | None:
    return agent_runtime_context.forced_context_company_dir(context, wiki_root=WIKI_ROOT)


def _normalize_financial_text(value: Any) -> str:
    return re.sub(r"[\s（）()_\-：:、,，;；/]+", "", str(value or "").lower())


def _load_local_citation_module() -> Any | None:
    script_path = str(NOTE_DETAIL_SCRIPT_DIR)
    if script_path not in sys.path:
        sys.path.insert(0, script_path)
    try:
        return _configure_wiki_module(importlib.import_module("local_citations"))
    except Exception:
        return None


def _resolve_company_dir(message: str, context: Any | None = None) -> Path | None:
    if _is_general_assistant_request(message):
        return None
    forced_company_dir = _forced_context_company_dir(context)
    if forced_company_dir:
        return forced_company_dir
    module = _load_local_citation_module()
    finder = getattr(module, "find_company_dir_from_text", None) if module else None
    if not callable(finder):
        return None

    company_hint = _context_company_hint(context)
    candidates = [message]
    if company_hint:
        candidates.extend([company_hint, f"{message}\n{company_hint}"])
    for candidate in candidates:
        try:
            company_dir = finder(candidate, WIKI_ROOT)
        except Exception:
            continue
        if company_dir and Path(company_dir).exists():
            return Path(company_dir)
    return _resolve_company_dir_from_catalog(message, context)


def _resolve_company_dir_from_catalog(message: str, context: Any | None = None) -> Path | None:
    catalog = _read_json_file(WIKI_ROOT / "_meta" / "company_catalog.json")
    companies = catalog.get("companies") if isinstance(catalog, dict) else None
    if not isinstance(companies, list):
        return None

    haystack = _normalize_financial_text(f"{message}\n{_context_company_hint(context)}")
    if not haystack:
        return None

    for company in companies:
        if not isinstance(company, dict):
            continue
        aliases = [
            company.get("company_id"),
            company.get("stock_code"),
            company.get("company_short_name"),
            company.get("company_full_name"),
            *((company.get("aliases") or []) if isinstance(company.get("aliases"), list) else []),
            *COMPANY_ALIAS_OVERRIDES.get(str(company.get("company_id") or ""), ()),
        ]
        normalized_aliases = [
            _normalize_financial_text(alias)
            for alias in aliases
            if alias not in (None, "")
        ]
        if not any(alias and alias in haystack for alias in normalized_aliases):
            continue
        rel_path = company.get("company_path") or company.get("path") or f"companies/{company.get('company_id') or ''}"
        company_dir = WIKI_ROOT / str(rel_path)
        if company_dir.exists():
            return company_dir
    return None


def _resolve_company_dirs_from_catalog(message: str, context: Any | None = None, *, limit: int = 4) -> list[Path]:
    catalog = _read_json_file(WIKI_ROOT / "_meta" / "company_catalog.json")
    companies = catalog.get("companies") if isinstance(catalog, dict) else None
    if not isinstance(companies, list):
        return []

    haystack = _normalize_financial_text(f"{message}\n{_context_company_hint(context)}")
    if not haystack:
        return []

    matches: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()
    for company in companies:
        if not isinstance(company, dict):
            continue
        aliases = [
            company.get("company_id"),
            company.get("stock_code"),
            company.get("company_short_name"),
            company.get("company_full_name"),
            *((company.get("aliases") or []) if isinstance(company.get("aliases"), list) else []),
            *COMPANY_ALIAS_OVERRIDES.get(str(company.get("company_id") or ""), ()),
        ]
        normalized_aliases = [
            _normalize_financial_text(alias)
            for alias in aliases
            if alias not in (None, "")
        ]
        matched_aliases = [alias for alias in normalized_aliases if alias and alias in haystack]
        if not matched_aliases:
            continue
        rel_path = company.get("company_path") or company.get("path") or f"companies/{company.get('company_id') or ''}"
        company_dir = WIKI_ROOT / str(rel_path)
        if not company_dir.exists() or company_dir in seen:
            continue
        seen.add(company_dir)
        best_alias = max(matched_aliases, key=len)
        matches.append((len(best_alias), best_alias, company_dir))
    matches.sort(key=lambda item: (-item[0], item[1], str(item[2])))
    return [company_dir for _score, _alias, company_dir in matches[:limit]]


def _resolve_company_dirs(message: str, context: Any | None = None, *, limit: int = 4) -> list[Path]:
    if _is_general_assistant_request(message):
        return []
    dirs = _resolve_company_dirs_from_catalog(message, context, limit=limit)
    first = _resolve_company_dir(message, context)
    if first and first not in dirs:
        dirs.insert(0, first)
    output: list[Path] = []
    seen: set[Path] = set()
    for company_dir in dirs:
        if company_dir in seen:
            continue
        seen.add(company_dir)
        output.append(company_dir)
        if len(output) >= limit:
            break
    return output


def _company_query_prefix(company_dir: Path) -> str:
    company = _read_json_file(company_dir / "company.json") or {}
    return (
        company.get("company_short_name")
        or company.get("company_full_name")
        or (company_dir.name.split("-", 1)[1] if "-" in company_dir.name else company_dir.name)
    )


def _context_for_company_dir(company_dir: Path) -> dict[str, Any]:
    company = _read_json_file(company_dir / "company.json") or {}
    return {
        "force_company": True,
        "company": {
            "name": company.get("company_short_name") or company.get("company_full_name") or _company_query_prefix(company_dir),
            "code": company.get("stock_code") or company_dir.name.split("-", 1)[0],
            "dir": str(company_dir),
        }
    }


def _message_for_company(message: str, company_dir: Path) -> str:
    return f"{_company_query_prefix(company_dir)} {message}"


def _report_text_blob(report: dict[str, Any]) -> str:
    metadata = report.get("source_filename_metadata") if isinstance(report.get("source_filename_metadata"), dict) else {}
    values = [
        report.get("report_id"),
        report.get("report_kind"),
        report.get("source_filename"),
        metadata.get("report_type"),
        metadata.get("report_end"),
    ]
    return " ".join(str(item or "") for item in values).lower()


def _report_is_annual(report: dict[str, Any]) -> bool:
    text = _report_text_blob(report)
    return "annual" in text or "年报" in text or "年度报告" in text or "2025-annual" in text


def _report_is_quarterly(report: dict[str, Any]) -> bool:
    text = _report_text_blob(report)
    return any(term in text for term in ("quarter", "quarterly", "季报", "季度", "半年报", "半年度"))


def _select_report_from_company_json(company: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    reports = [item for item in (company.get("reports") or []) if isinstance(item, dict)]
    if not reports:
        return {}

    text = re.sub(r"\s+", "", message or "").lower()
    wants_quarterly = any(term.lower() in text for term in REPORT_QUARTERLY_TERMS)
    wants_annual = any(term.lower() in text for term in REPORT_ANNUAL_TERMS)

    if wants_quarterly:
        quarterly = next((item for item in reports if _report_is_quarterly(item)), None)
        if quarterly:
            return quarterly
    if wants_annual or not wants_quarterly:
        annual = next((item for item in reports if item.get("report_id") == "2025-annual"), None)
        if annual:
            return annual
        annual = next((item for item in reports if _report_is_annual(item)), None)
        if annual:
            return annual

    requested_report_id = company.get("primary_report_id")
    report = next((item for item in reports if item.get("report_id") == requested_report_id), None)
    return report or reports[0]


def _primary_report_for_company(company_dir: Path, message: str | None = None) -> dict[str, Any]:
    module = _load_local_citation_module()
    primary = getattr(module, "primary_report", None) if module else None
    if callable(primary):
        try:
            report = primary(company_dir, query_text=message)
            if isinstance(report, dict):
                return report
        except TypeError:
            try:
                report = primary(company_dir)
                if isinstance(report, dict) and not message:
                    return report
            except Exception:
                pass
        except Exception:
            pass
    company = _read_json_file(company_dir / "company.json") or {}
    report = _select_report_from_company_json(company, message) if isinstance(company, dict) else {}
    report_id = (
        report.get("report_id")
        or (company.get("primary_report_id") if isinstance(company, dict) else None)
        or "2025-annual"
    )
    return {
        "report_id": report_id,
        "task_id": report.get("task_id") or company.get("task_id"),
        "document_full": company_dir / (report.get("document_full") or f"reports/{report_id}/document_full.json"),
    }


def _existing_company_file(company_dir: Path, rel_candidates: list[str | None]) -> Path | None:
    for rel in rel_candidates:
        if not rel:
            continue
        path = company_dir / rel
        if path.exists():
            return path
    return None


def _company_artifact_paths(company_dir: Path, report_id: str) -> dict[str, Path]:
    company = _read_json_file(company_dir / "company.json") or {}
    metrics = company.get("metrics") if isinstance(company, dict) else {}
    by_report = (metrics.get("by_report") or {}).get(report_id) if isinstance(metrics, dict) else {}
    latest = metrics.get("latest") if isinstance(metrics, dict) else {}
    evidence = company.get("evidence") if isinstance(company, dict) else {}

    candidates: dict[str, list[str | None]] = {
        "three_statements": [
            by_report.get("three_statements") if isinstance(by_report, dict) else None,
            f"metrics/reports/{report_id}/three_statements.json",
            latest.get("three_statements") if isinstance(latest, dict) else None,
            "metrics/latest/three_statements.json",
            metrics.get("three_statements") if isinstance(metrics, dict) else None,
            "metrics/three_statements.json",
        ],
        "key_metrics": [
            by_report.get("key_metrics") if isinstance(by_report, dict) else None,
            f"metrics/reports/{report_id}/key_metrics.json",
            latest.get("key_metrics") if isinstance(latest, dict) else None,
            "metrics/latest/key_metrics.json",
            metrics.get("key_metrics") if isinstance(metrics, dict) else None,
            "metrics/key_metrics.json",
        ],
        "validation": [
            by_report.get("validation") if isinstance(by_report, dict) else None,
            f"metrics/reports/{report_id}/validation.json",
            latest.get("validation") if isinstance(latest, dict) else None,
            "metrics/latest/validation.json",
            metrics.get("validation") if isinstance(metrics, dict) else None,
            "metrics/validation.json",
        ],
        "evidence_index": [
            evidence.get("evidence_index") if isinstance(evidence, dict) else None,
            "evidence/evidence_index.json",
        ],
        "pdf_refs": [
            evidence.get("pdf_refs") if isinstance(evidence, dict) else None,
            "evidence/pdf_refs.json",
        ],
        "report_md": [f"reports/{report_id}/report.md"],
        "report_json": [f"reports/{report_id}/report.json"],
        "document_full": [f"reports/{report_id}/document_full.json"],
        "evidence_semantic": ["semantic/evidence_semantic.json"],
        "retrieval_index": ["semantic/retrieval_index.json"],
        "document_links": ["semantic/document_links.json"],
        "note_links": ["semantic/note_links.json"],
    }
    return {
        key: path
        for key, rels in candidates.items()
        if (path := _existing_company_file(company_dir, rels))
    }


def _report_relpath(path: Path, company_dir: Path) -> str:
    try:
        return str(path.relative_to(company_dir))
    except ValueError:
        return str(path)


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _nearest_report_pdf_page(lines: list[str], line_number: int | None) -> int | None:
    return agent_runtime_fallback_contexts._nearest_report_pdf_page(lines, line_number)


def _html_to_text(value: str) -> str:
    return agent_runtime_fallback_contexts._html_to_text(value)


def _normalize_search_text(value: Any) -> str:
    return agent_runtime_fallback_contexts._normalize_search_text(value)


def _remove_company_aliases(text: str, company_dir: Path) -> str:
    company = _read_json_file(company_dir / "company.json") or {}
    aliases = agent_runtime_fallback_contexts._company_aliases(company_dir.name, company)
    return agent_runtime_fallback_contexts._remove_company_aliases(text, aliases)


def _fallback_search_terms(message: str, company_dir: Path) -> list[str]:
    company = _read_json_file(company_dir / "company.json") or {}
    aliases = agent_runtime_fallback_contexts._company_aliases(company_dir.name, company)
    return agent_runtime_fallback_contexts._fallback_search_terms(
        message,
        aliases,
        REPORT_FULLTEXT_FALLBACK_TERMS,
    )


def _specific_fulltext_terms(terms: list[str]) -> list[str]:
    return agent_runtime_fallback_contexts._specific_fulltext_terms(terms, REPORT_FULLTEXT_GENERIC_TERMS)


def _line_match_score(line: str, terms: list[str]) -> int:
    return agent_runtime_fallback_contexts._line_match_score(line, terms)


def _line_matches_any_term(line: str, terms: list[str]) -> bool:
    return agent_runtime_fallback_contexts._line_matches_any_term(line, terms)


def _snippet_window(lines: list[str], line_number: int, *, radius: int = 2) -> str:
    return agent_runtime_fallback_contexts._snippet_window(
        lines,
        line_number,
        radius=radius,
        snippet_chars=REPORT_FULLTEXT_SNIPPET_CHARS,
    )


def _table_meta_by_line(company_dir: Path, report_id: str) -> list[dict[str, Any]]:
    report_json = _read_json_file(company_dir / "reports" / report_id / "report.json") or {}
    tables = report_json.get("tables") if isinstance(report_json, dict) else []
    if isinstance(tables, list) and tables:
        return [table for table in tables if isinstance(table, dict)]
    document_full = _read_json_file(company_dir / "reports" / report_id / "document_full.json") or {}
    enhanced = document_full.get("content_list_enhanced") if isinstance(document_full, dict) else {}
    tables = enhanced.get("tables") if isinstance(enhanced, dict) else []
    return [table for table in tables if isinstance(table, dict)] if isinstance(tables, list) else []


def _nearest_table_meta(tables: list[dict[str, Any]], line_number: int | None, *, max_distance: int = 3) -> dict[str, Any] | None:
    return agent_runtime_fallback_contexts._nearest_table_meta(
        tables,
        line_number,
        max_distance=max_distance,
    )


def _document_full_text_items(document_full: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    content_list = document_full.get("content_list") if isinstance(document_full, dict) else []
    if isinstance(content_list, list):
        for index, item in enumerate(content_list):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            score = _line_match_score(text, terms)
            if score <= 0:
                continue
            page_idx = _safe_int(item.get("page_idx"))
            items.append(
                {
                    "score": score,
                    "order": index,
                    "text": text[:REPORT_FULLTEXT_SNIPPET_CHARS],
                    "pdf_page": page_idx + 1 if page_idx is not None else None,
                    "type": item.get("type"),
                }
            )

    enhanced = document_full.get("content_list_enhanced") if isinstance(document_full, dict) else {}
    tables = enhanced.get("tables") if isinstance(enhanced, dict) else []
    if isinstance(tables, list):
        for index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            text = " ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit"))
            score = _line_match_score(text, terms)
            if score <= 0:
                continue
            items.append(
                {
                    "score": score + 8,
                    "order": 100000 + index,
                    "text": text[:REPORT_FULLTEXT_SNIPPET_CHARS],
                    "pdf_page": table.get("pdf_page_number") or table.get("pdf_page"),
                    "table_index": table.get("table_index"),
                    "md_line": table.get("line") or table.get("md_line") or table.get("markdown_line"),
                    "type": "table",
                }
            )
    return items


def _should_consider_wiki_fulltext_fallback(message: str, context: Any | None = None) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or _is_general_assistant_request(text):
        return False
    if _resolve_company_dir(message, context) is None:
        return False
    if any(term.lower() in text.lower() for term in REPORT_FULLTEXT_FALLBACK_TERMS):
        return True
    company = _context_company(context)
    return bool(company and any(term in text for term in ("多少", "数据", "情况", "如何", "怎么样", "说明", "披露")))


def _wiki_fulltext_fallback_result(message: str, context: Any | None = None) -> dict[str, Any] | None:
    if not _should_consider_wiki_fulltext_fallback(message, context):
        return None
    company_dir = _resolve_company_dir(message, context)
    if not company_dir:
        return None
    report = _primary_report_for_company(company_dir, message)
    report_id = str(report.get("report_id") or "2025-annual")
    report_md = company_dir / "reports" / report_id / "report.md"
    document_full_path = company_dir / "reports" / report_id / "document_full.json"
    if not report_md.is_file() and not document_full_path.is_file():
        return None

    terms = _fallback_search_terms(message, company_dir)
    if not terms:
        return None
    specific_terms = _specific_fulltext_terms(terms)
    if not specific_terms:
        return None

    company = _read_json_file(company_dir / "company.json") or {}
    rows: list[dict[str, Any]] = []
    tables = _table_meta_by_line(company_dir, report_id)
    lines: list[str] = []
    if report_md.is_file():
        try:
            lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            lines = []
    if lines:
        scored_lines = [
            (_line_match_score(line, terms), index, line)
            for index, line in enumerate(lines, start=1)
        ]
        scored_lines = [(score, index, line) for score, index, line in scored_lines if score > 0]
        scored_lines.sort(key=lambda item: (-item[0], item[1]))
        seen_lines: set[int] = set()
        for score, line_number, _line in scored_lines[: REPORT_FULLTEXT_MAX_SNIPPETS * 2]:
            if any(abs(line_number - seen) <= 1 for seen in seen_lines):
                continue
            if not _line_matches_any_term(_line, specific_terms):
                continue
            seen_lines.add(line_number)
            table = _nearest_table_meta(tables, line_number)
            pdf_page = (
                (table.get("pdf_page_number") or table.get("pdf_page")) if table else None
            ) or _nearest_report_pdf_page(lines, line_number)
            table_index = table.get("table_index") if table else None
            rows.append(
                {
                    "source_type": "wiki_report_fulltext",
                    "file": f"reports/{report_id}/report.md",
                    "score": score,
                    "snippet": _snippet_window(lines, line_number, radius=2),
                    "task_id": report.get("task_id"),
                    "pdf_page": pdf_page,
                    "table_index": table_index,
                    "md_line": line_number,
                }
            )
            if len(rows) >= REPORT_FULLTEXT_MAX_SNIPPETS:
                break

    if len(rows) < REPORT_FULLTEXT_MAX_SNIPPETS and document_full_path.is_file():
        document_full = _read_json_file(document_full_path) or {}
        if isinstance(document_full, dict):
            existing_keys = {(row.get("pdf_page"), row.get("table_index"), row.get("md_line"), row.get("snippet")) for row in rows}
            items = _document_full_text_items(document_full, terms)
            items.sort(key=lambda item: (-int(item.get("score") or 0), int(item.get("order") or 0)))
            for item in items:
                if not _line_matches_any_term(str(item.get("text") or ""), specific_terms):
                    continue
                key = (item.get("pdf_page"), item.get("table_index"), item.get("md_line"), item.get("text"))
                if key in existing_keys:
                    continue
                rows.append(
                    {
                        "source_type": "wiki_document_full",
                        "file": f"reports/{report_id}/document_full.json",
                        "score": item.get("score"),
                        "snippet": str(item.get("text") or "")[:REPORT_FULLTEXT_SNIPPET_CHARS],
                        "task_id": report.get("task_id"),
                        "pdf_page": item.get("pdf_page"),
                        "table_index": item.get("table_index"),
                        "md_line": item.get("md_line"),
                        "content_type": item.get("type"),
                    }
                )
                existing_keys.add(key)
                if len(rows) >= REPORT_FULLTEXT_MAX_SNIPPETS:
                    break

    if not rows:
        return None

    return {
        "company_dir": company_dir,
        "company_id": company_dir.name,
        "company_name": company.get("company_short_name") or company.get("company_full_name") or company_dir.name,
        "stock_code": company.get("stock_code") or company_dir.name.split("-", 1)[0],
        "report_id": report_id,
        "task_id": report.get("task_id"),
        "report_md": report_md,
        "document_full": document_full_path,
        "terms": terms,
        "rows": rows,
    }


def _render_wiki_fulltext_fallback_context(result: dict[str, Any]) -> str:
    rows = result.get("rows") or []
    lines = [
        "以下是后端在结构化 Wiki metrics/evidence/semantic 未命中或命中不足时，从完整年报 Markdown 和完整解析 JSON 确定性检索出的全文兜底证据。",
        "输出要求：",
        "- 优先基于这些原文片段回答；不得再说“未找到/无法回答”，除非下方片段确实无关。",
        "- `reports/<report_id>/report.md` 是完整报告正文；`reports/<report_id>/document_full.json` 是完整解析容器。不要使用 `graph/report.md`，不要把 `report.json` 当 full json。",
        "- 必须在 `## 引用来源` 保留 `source_type/file/task_id/pdf_page/table_index/md_line`；字段为空时写 `未返回`。",
        f"- 公司: {result.get('company_name')} / 代码 {result.get('stock_code')} / company_id={result.get('company_id')}",
        f"- 报告: report_id={result.get('report_id')} / task_id={result.get('task_id') or '未返回'}",
        f"- 完整 Markdown: {result.get('report_md')}",
        f"- 完整 full JSON: {result.get('document_full')}",
        f"- 检索词: {', '.join(result.get('terms') or [])}",
        "",
        "## 全文兜底证据",
    ]
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                "",
                f"### F{index}. {row.get('source_type')} / score={row.get('score')}",
                f"- file={row.get('file')}",
                f"- task_id={row.get('task_id') or '未返回'}, pdf_page={row.get('pdf_page') or '未返回'}, table_index={row.get('table_index') if row.get('table_index') not in (None, '') else '未返回'}, md_line={row.get('md_line') or '未返回'}",
                "```text",
                str(row.get("snippet") or "").strip(),
                "```",
            ]
        )
    lines.extend(["", "## 全文兜底引用"])
    for index, row in enumerate(rows, start=1):
        task_id = row.get("task_id")
        pdf_page = row.get("pdf_page")
        table_index = row.get("table_index")
        links = []
        pdf_url = _evidence_url(task_id, pdf_page, table_index, "pdf")
        page_url = _evidence_url(task_id, pdf_page, table_index, "page")
        table_url = _evidence_url(task_id, pdf_page, table_index, "table")
        if pdf_url:
            links.append(f"[打开PDF页]({pdf_url})")
        if page_url:
            links.append(f"[查看页来源]({page_url})")
        if table_url:
            links.append(f"[查看表格]({table_url})")
        lines.append(
            f"[F{index}] source_type={row.get('source_type')}, file={row.get('file')}, "
            f"metric={','.join(result.get('terms') or []) or '全文检索'}, period={result.get('report_id')}, "
            f"task_id={task_id or '未返回'}, pdf_page={pdf_page or '未返回'}, "
            f"table_index={table_index if table_index not in (None, '') else '未返回'}, "
            f"md_line={row.get('md_line') or '未返回'}"
            + (("，" + "，".join(links)) if links else "")
        )
    return "\n".join(lines)


def build_wiki_fulltext_fallback_context(message: str, context: Any | None = None) -> str | None:
    result = _wiki_fulltext_fallback_result(message, context)
    if not result:
        return None
    return _render_wiki_fulltext_fallback_context(result)


def build_company_wiki_scope_context(message: str, context: Any | None = None) -> str | None:
    """Pin a single-company question to the resolved local Wiki workset."""
    company_dir = _resolve_company_dir(message, context)
    if not company_dir:
        return None
    company = _read_json_file(company_dir / "company.json") or {}
    report = _primary_report_for_company(company_dir, message)
    report_id = str(report.get("report_id") or "2025-annual")
    paths = _company_artifact_paths(company_dir, report_id)

    company_name = (
        company.get("company_short_name")
        or company.get("company_full_name")
        or (company_dir.name.split("-", 1)[1] if "-" in company_dir.name else company_dir.name)
    )
    stock_code = company.get("stock_code") or company_dir.name.split("-", 1)[0]
    lines = [
        "以下是后端已确定的单家公司 Wiki 工作集。回答本题时必须以此为公司边界；除非用户在本轮明确指定其他公司，不得沿用会话历史中的其他公司、备份 wiki 或 profile 目录。",
        f"- Wiki 根目录: {WIKI_ROOT}",
        f"- 公司: {company_name} / 代码 {stock_code} / company_id={company_dir.name}",
        f"- 公司目录: {company_dir}",
        f"- 主报告: report_id={report_id}, task_id={report.get('task_id') or '未返回'}",
        "- 数据优先级: 三大表 `three_statements.json` > 核心指标 `key_metrics.json` > legacy `evidence/evidence_index.json` / `evidence/pdf_refs.json` > semantic `evidence_semantic.json` / `retrieval_index.json` > `reports/<report_id>/report.json` 的 tables > 完整 `reports/<report_id>/report.md` > 完整 `reports/<report_id>/document_full.json` > PostgreSQL fallback。",
        "- 深度回溯协议: 任何一层证据文件存在但为空、字段为 `未返回`、或没有可打开 `/api/pdf_page` / `/api/source` 链接时，不得下结论说“无法溯源”；必须继续检查下一层，尤其是 `report.json.tables`、`document_full.content_list_enhanced.tables` 和 semantic evidence。",
        "- 溯源合格标准: 至少给出 `task_id` + `pdf_page` 或 `table_index`，并优先生成 `/api/pdf_page/{task_id}/{page}`、`/api/source/{task_id}/page/{page}`、`/api/source/{task_id}/table/{table_index}`。`pdf_page=未返回` 或 `table_index=未返回` 只能作为临时状态，不能作为最终证据充分结论。",
        "- 工作流约束: 先基于三大表确认金额、期间和表格来源，再用附注/semantic 解释构成或原因；不得用附注表替代三大表主表口径。",
        "- 兜底约束: 不得读取 `graph/report.md` 作为完整报告；不得把 `reports/<report_id>/report.json` 当作 full json。完整解析容器固定为 `document_full.json`。",
    ]
    if company.get("industry"):
        lines.append(f"- 行业: {_clean_context_value(company['industry'])}")
    for label, key in (
        ("三大表", "three_statements"),
        ("核心指标", "key_metrics"),
        ("校验结果", "validation"),
        ("证据索引", "evidence_index"),
        ("PDF页码映射", "pdf_refs"),
        ("语义证据", "evidence_semantic"),
        ("年报Markdown", "report_md"),
        ("完整full JSON", "document_full"),
        ("年报JSON", "report_json"),
        ("语义检索索引", "retrieval_index"),
        ("附注跳转", "document_links"),
        ("附注表索引", "note_links"),
    ):
        path = paths.get(key)
        if path:
            lines.append(f"- {label}: {path}")
    return "\n".join(lines)


def _iter_metric_records(obj: Any) -> list[dict[str, Any]]:
    return agent_runtime_statement_context.iter_metric_records(obj)


def _period_sort_key(value: Any) -> tuple[int, str]:
    return agent_runtime_statement_context.period_sort_key(value)


def _record_source(record: dict[str, Any]) -> dict[str, Any]:
    return agent_runtime_statement_context.record_source(record)


def _record_source_value(record: dict[str, Any], key: str) -> Any:
    return agent_runtime_statement_context.record_source_value(record, key)


def _normalize_wiki_metric_file_name(file_name: str) -> str:
    if not _current_default_source_type().startswith("wiki_"):
        return file_name
    if re.fullmatch(r"(?:metrics/reports/[^/]+|reports/[^/]+/metrics)/three_statements\.json", file_name):
        return "metrics/three_statements.json"
    return file_name


def _normalize_wiki_metric_file_refs(markdown: str) -> str:
    if not _current_default_source_type().startswith("wiki_"):
        return markdown
    return re.sub(
        r"file=(?:metrics/reports/[^,\s]+|reports/[^,\s]+/metrics)/three_statements\.json",
        "file=metrics/three_statements.json",
        markdown,
    )


def _statement_record_rank(record: dict[str, Any], statement_type: str) -> tuple[int, int, str]:
    return agent_runtime_statement_context.statement_record_rank(
        record,
        statement_type,
        core_keys=THREE_STATEMENT_CORE_KEYS,
        core_name_terms=THREE_STATEMENT_CORE_NAME_TERMS,
        normalize_financial_text=_normalize_financial_text,
    )


def _is_core_statement_record(record: dict[str, Any], statement_type: str) -> bool:
    return agent_runtime_statement_context.is_core_statement_record(
        record,
        statement_type,
        statement_record_rank_fn=_statement_record_rank,
    )


def _latest_records_by_statement(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return agent_runtime_statement_context.latest_records_by_statement(
        records,
        is_core_statement_record_fn=_is_core_statement_record,
        statement_record_rank_fn=_statement_record_rank,
    )


def _evidence_url(task_id: Any, pdf_page: Any, table_index: Any, kind: str) -> str | None:
    if not task_id:
        return None
    module = _load_note_detail_module()
    public_api_url = getattr(module, "public_api_url", None) if module else None
    if kind == "pdf" and pdf_page:
        path = f"/api/pdf_page/{task_id}/{pdf_page}?format=html"
    elif kind == "page" and pdf_page:
        path = f"/api/source/{task_id}/page/{pdf_page}?format=html"
    elif kind == "table" and table_index not in (None, ""):
        path = f"/api/source/{task_id}/table/{table_index}?format=html"
    else:
        return None
    if callable(public_api_url):
        try:
            return public_api_url(path)
        except Exception:
            pass
    origin = (os.environ.get("SIQ_PUBLIC_ORIGIN") or os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:9391")).rstrip("/")
    return f"{origin}{path}"


def _statement_record_to_row(record: dict[str, Any], report_id: str, metrics_file: Path, company_dir: Path) -> dict[str, Any]:
    source = _record_source(record)
    task_id = record.get("task_id") or source.get("task_id")
    pdf_page = record.get("pdf_page") or source.get("pdf_page") or source.get("pdf_page_number")
    table_index = record.get("table_index") if record.get("table_index") not in (None, "") else source.get("table_index")
    md_line = (
        record.get("md_line")
        or record.get("line")
        or source.get("md_line")
        or source.get("line")
    )
    file_name = str(metrics_file.relative_to(company_dir)) if metrics_file.is_relative_to(company_dir) else str(metrics_file)
    return {
        "statement_type": record.get("statement_type"),
        "statement_label": THREE_STATEMENT_LABELS.get(str(record.get("statement_type") or ""), str(record.get("statement_type") or "")),
        "metric_key": record.get("metric_key") or record.get("canonical_name"),
        "metric_name": record.get("metric_name") or record.get("name") or record.get("item_name") or record.get("metric_key"),
        "period": record.get("period") or source.get("period"),
        "raw_value": record.get("raw_value") or record.get("value") or record.get("normalized_value"),
        "unit": record.get("unit_hint") or record.get("raw_unit") or record.get("unit"),
        "normalized_value": record.get("normalized_value"),
        "report_id": report_id,
        "source_type": "wiki_metrics",
        "file": _normalize_wiki_metric_file_name(file_name),
        "task_id": task_id,
        "pdf_page": pdf_page,
        "table_index": table_index,
        "md_line": md_line,
        "open_pdf_page_url": _evidence_url(task_id, pdf_page, table_index, "pdf"),
        "open_source_page_url": _evidence_url(task_id, pdf_page, table_index, "page"),
        "open_source_table_url": _evidence_url(task_id, pdf_page, table_index, "table"),
    }


def _question_needs_three_statement_context(message: str, context: Any | None = None) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or _is_general_assistant_request(text):
        return False
    if _resolve_company_dir(message, context) is None:
        return False
    if _should_inject_note_detail_context(message) or _is_human_capital_query(message):
        return False
    if _is_statement_query(message):
        return True
    if any(term.lower() in text.lower() for term in CORE_KEY_METRIC_TERMS):
        return True
    return any(
        term in text
        for term in (
            "财务",
            "业绩",
            "表现",
            "分析",
            "评价",
            "评估",
            "对比",
            "趋势",
            "风险",
            "亮点",
            "怎么样",
            "如何",
            "核心数据",
            "主要数据",
            "基本面",
            "经营情况",
        )
    )


def _three_statement_core_result(message: str, context: Any | None = None) -> dict[str, Any] | None:
    if not _question_needs_three_statement_context(message, context):
        return None
    company_dir = _resolve_company_dir(message, context)
    if not company_dir:
        return None
    report = _primary_report_for_company(company_dir, message)
    report_id = str(report.get("report_id") or "2025-annual")
    metrics_file = _company_artifact_paths(company_dir, report_id).get("three_statements")
    if not metrics_file:
        return None
    payload = _read_json_file(metrics_file)
    if not isinstance(payload, dict):
        return None
    records = _latest_records_by_statement(_iter_metric_records(payload.get("data") or payload))
    if not records:
        return None
    company = _read_json_file(company_dir / "company.json") or {}
    return {
        "company_dir": company_dir,
        "company_id": company_dir.name,
        "company_name": company.get("company_short_name") or company.get("company_full_name") or company_dir.name,
        "stock_code": company.get("stock_code") or company_dir.name.split("-", 1)[0],
        "report_id": report_id,
        "task_id": report.get("task_id"),
        "metrics_file": metrics_file,
        "unit": payload.get("unit"),
        "rows": [
            _statement_record_to_row(record, report_id, metrics_file, company_dir)
            for record in records
        ],
    }


def _format_statement_value(row: dict[str, Any]) -> str:
    value = row.get("raw_value")
    unit = row.get("unit") or ""
    if value in (None, ""):
        value = row.get("normalized_value")
    return f"{value} {unit}".strip()


def _render_three_statement_context(result: dict[str, Any]) -> str:
    rows = result.get("rows") or []
    lines = [
        "以下是后端从本地 Wiki 三大表 `three_statements.json` 提取的核心数据底稿。模型可以润色、概括和解释数据本质，但不得改写任何 `raw_value`、期间、单位、公司、report_id、task_id、pdf_page、table_index、md_line 或来源路径。",
        "输出要求：",
        "- 回答先讲三大表透视出的经营本质，例如增长、盈利、现金流含金量、资产负债结构；再给关键数据表格。",
        "- 所有关键数字必须来自下方底稿；如果要换算成亿元/百分比，只能作为补充表述，并同时保留下方原始披露值。",
        "- `## 引用来源` 必须保留每张相关表的 `source_type/file/task_id/pdf_page/table_index/md_line` 和可打开链接。",
        f"- 公司: {result.get('company_name')} / 代码 {result.get('stock_code')} / report_id={result.get('report_id')} / 默认单位={result.get('unit') or '未返回'}",
        "",
        "## 三大表核心底稿",
    ]
    for statement_type in ("income_statement", "cash_flow_statement", "balance_sheet"):
        statement_rows = [row for row in rows if row.get("statement_type") == statement_type]
        if not statement_rows:
            continue
        lines.extend([
            "",
            f"### {THREE_STATEMENT_LABELS.get(statement_type, statement_type)}",
            "| 科目 | 期间 | 原始披露值 | 单位 | pdf_page | table_index | md_line |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: |",
        ])
        for row in statement_rows:
            lines.append(
                f"| {row.get('metric_name') or row.get('metric_key')} | {row.get('period') or '未返回'} | "
                f"{row.get('raw_value') if row.get('raw_value') not in (None, '') else row.get('normalized_value')} | "
                f"{row.get('unit') or '未返回'} | {row.get('pdf_page') or '未返回'} | "
                f"{row.get('table_index') if row.get('table_index') not in (None, '') else '未返回'} | "
                f"{row.get('md_line') or '未返回'} |"
            )
    lines.extend(["", "## 底稿引用"])
    seen_sources: set[tuple[Any, Any, Any, Any, str]] = set()
    source_index = 1
    for row in rows:
        key = (
            row.get("task_id"),
            row.get("pdf_page"),
            row.get("table_index"),
            row.get("md_line"),
            row.get("file"),
        )
        if key in seen_sources:
            continue
        seen_sources.add(key)
        links = []
        if row.get("open_pdf_page_url"):
            links.append(f"[打开PDF页]({row['open_pdf_page_url']})")
        if row.get("open_source_page_url"):
            links.append(f"[查看页来源]({row['open_source_page_url']})")
        if row.get("open_source_table_url"):
            links.append(f"[查看表格]({row['open_source_table_url']})")
        lines.append(
            f"[S{source_index}] source_type={_current_source_type('metrics')}, file={row.get('file')}, "
            f"metric={row.get('statement_label')}, period={row.get('report_id')}, "
            f"task_id={row.get('task_id') or '未返回'}, pdf_page={row.get('pdf_page') or '未返回'}, "
            f"table_index={row.get('table_index') if row.get('table_index') not in (None, '') else '未返回'}, "
            f"md_line={row.get('md_line') or '未返回'}"
            + (("，" + "，".join(links)) if links else "")
        )
        source_index += 1
    return "\n".join(lines)


def build_three_statement_core_context(message: str, context: Any | None = None) -> str | None:
    result = _three_statement_core_result(message, context)
    if not result:
        return None
    return _render_three_statement_context(result)


def _load_note_detail_module() -> Any | None:
    script_path = str(NOTE_DETAIL_SCRIPT_DIR)
    if script_path not in sys.path:
        sys.path.insert(0, script_path)
    try:
        module = _configure_wiki_module(importlib.import_module("note_detail_lookup"))
        _configure_wiki_module(getattr(module, "local_citations", None))
        return module
    except Exception:
        return None


def _load_note_detail_tools() -> tuple[Callable[..., dict[str, Any]] | None, Callable[..., str] | None]:
    module = _load_note_detail_module()
    if module is None:
        return None, None
    resolver = getattr(module, "resolve_note_detail_tables", None)
    renderer = getattr(module, "render_markdown", None)
    if not callable(resolver) or not callable(renderer):
        return None, None
    return resolver, renderer


def _load_statement_metric_tools() -> tuple[Callable[..., dict[str, Any]] | None, Callable[..., str] | None]:
    script_path = str(NOTE_DETAIL_SCRIPT_DIR)
    if script_path not in sys.path:
        sys.path.insert(0, script_path)
    try:
        module = _configure_wiki_module(importlib.import_module("statement_metric_lookup"))
        _configure_wiki_module(getattr(module, "local_citations", None))
        _configure_wiki_module(getattr(module, "note_detail_lookup", None))
    except Exception:
        return None, None
    resolver = getattr(module, "resolve_statement_metrics", None)
    renderer = getattr(module, "render_markdown", None)
    if not callable(resolver) or not callable(renderer):
        return None, None
    return resolver, renderer


def _is_human_capital_query(message: str) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or _is_general_assistant_request(text):
        return False
    return any(term in text for term in HUMAN_CAPITAL_QUERY_TERMS) or any(
        term in text
        for term in (
            "员工人数",
            "员工总数",
            "在职员工",
            "研发人员",
            "研发人员情况",
        )
    )


def _is_human_efficiency_query(message: str) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or _is_general_assistant_request(text):
        return False
    return any(term in text for term in HUMAN_EFFICIENCY_QUERY_TERMS)


def _human_capital_table_score(table: dict[str, Any]) -> int:
    text = " ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit"))
    score = 0
    for term in HUMAN_CAPITAL_TABLE_TERMS:
        if term in text:
            score += 20
    if "母公司在职员工的数量" in text and "专业构成" in text and "教育程度" in text:
        score += 100
    return score


def _nearest_pdf_page_for_md_line(report_md: Path, md_line: int) -> int | None:
    try:
        lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    for line in reversed(lines[:max(0, min(len(lines), md_line))]):
        match = re.search(r"\[PDF_PAGE:\s*(\d+)\]", line)
        if match:
            return int(match.group(1))
    return None


def _human_capital_table_meta(company_dir: Path, report_id: str) -> dict[str, Any] | None:
    report_json = _read_json_file(company_dir / "reports" / report_id / "report.json")
    tables = report_json.get("tables") if isinstance(report_json, dict) else None
    if isinstance(tables, list):
        scored = [
            (_human_capital_table_score(table), table)
            for table in tables
            if isinstance(table, dict)
        ]
        scored = [(score, table) for score, table in scored if score > 0]
        if scored:
            scored.sort(
                key=lambda item: (
                    -item[0],
                    int(item[1].get("table_index") or 10**9),
                    int(item[1].get("line") or 10**9),
                )
            )
            return scored[0][1]

    report_md = company_dir / "reports" / report_id / "report.md"
    try:
        lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    for index, line in enumerate(lines, start=1):
        if "母公司在职员工的数量" in line and "<table" in line:
            return {
                "line": index,
                "pdf_page_number": _nearest_pdf_page_for_md_line(report_md, index),
                "table_index": None,
                "heading": "员工情况",
            }
    return None


def _parse_count(value: Any) -> int | None:
    text = str(value or "").replace(",", "").replace("人", "").strip()
    if not text or not re.fullmatch(r"-?\d+", text):
        return None
    return int(text)


def _ratio_text(count_text: Any, total: int | None) -> str:
    count = _parse_count(count_text)
    if count is None or not total:
        return ""
    return f"{count / total * 100:.2f}%"


def _split_human_capital_rows(rows: list[list[str]]) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {
        "scale": [],
        "profession": [],
        "education": [],
    }
    section = "scale"
    for row in rows:
        if len(row) < 2:
            continue
        label = str(row[0] or "").strip()
        value = str(row[1] or "").strip()
        if not label:
            continue
        if label == "专业构成":
            section = "profession"
            continue
        if label == "教育程度":
            section = "education"
            continue
        if label in {"专业构成类别", "教育程度类别"}:
            continue
        sections[section].append((label, value))
    return sections


def _human_capital_result(message: str, context: Any | None = None) -> dict[str, Any] | None:
    if not _is_human_capital_query(message):
        return None
    module = _load_note_detail_module()
    if module is None:
        return None
    finder = getattr(module, "find_company_dir_from_text", None)
    primary = getattr(module, "primary_report", None)
    extract = getattr(module, "extract_table_html", None)
    parser = getattr(module, "parse_html_table", None)
    api_url = getattr(module, "public_api_url", None)
    if not all(callable(item) for item in (finder, primary, extract, parser, api_url)):
        return None

    company_hint = _context_company_hint(context)
    company_text_candidates = [message]
    if company_hint:
        company_text_candidates.append(company_hint)
        company_text_candidates.append(f"{message}\n{company_hint}")

    for company_text in company_text_candidates:
        company_dir = finder(company_text, WIKI_ROOT)
        if not company_dir:
            continue
        report = primary(company_dir)
        report_id = report.get("report_id") or "2025-annual"
        meta = _human_capital_table_meta(company_dir, report_id)
        if not meta:
            continue
        md_line = int(meta.get("line") or meta.get("md_line") or 0)
        if md_line <= 0:
            continue
        report_md = company_dir / "reports" / report_id / "report.md"
        html = extract(report_md, md_line)
        parsed = parser(html or "")
        rows = parsed.get("rows") if isinstance(parsed, dict) else None
        if not rows:
            continue
        sections = _split_human_capital_rows(rows)
        if not any(sections.values()):
            continue
        task_id = report.get("task_id")
        pdf_page = (
            meta.get("pdf_page_number")
            or meta.get("pdf_page")
            or _nearest_pdf_page_for_md_line(report_md, md_line)
        )
        table_index = meta.get("table_index")
        return {
            "company_id": company_dir.name,
            "report_id": report_id,
            "task_id": task_id,
            "pdf_page": pdf_page,
            "table_index": table_index,
            "md_line": md_line,
            "sections": sections,
            "open_pdf_page_url": api_url(f"/api/pdf_page/{task_id}/{pdf_page}?format=html") if task_id and pdf_page else None,
            "open_source_page_url": api_url(f"/api/source/{task_id}/page/{pdf_page}?format=html") if task_id and pdf_page else None,
            "open_source_table_url": api_url(f"/api/source/{task_id}/table/{table_index}?format=html") if task_id and table_index else None,
        }
    return None


def _human_capital_section_table(
    title: str,
    rows: list[tuple[str, str]],
    *,
    total: int | None = None,
    include_ratio: bool = False,
) -> list[str]:
    if not rows:
        return []
    lines = [f"### {title}"]
    if include_ratio:
        lines.extend(["| 类别 | 人数 | 占比 |", "| --- | ---: | ---: |"])
        for label, value in rows:
            lines.append(f"| {label} | {value} | {_ratio_text(value, total)} |")
    else:
        lines.extend(["| 项目 | 人数 |", "| --- | ---: |"])
        for label, value in rows:
            lines.append(f"| {label} | {value} |")
    return lines


def _first_value(rows: list[tuple[str, str]], label: str) -> str:
    for row_label, value in rows:
        if row_label == label:
            return value
    return ""


def render_human_capital_markdown(result: dict[str, Any]) -> str:
    sections = result.get("sections") or {}
    scale = sections.get("scale") or []
    profession = sections.get("profession") or []
    education = sections.get("education") or []
    total_text = _first_value(scale, "在职员工的数量合计") or _first_value(profession, "合计") or _first_value(education, "合计")
    total = _parse_count(total_text)
    company_name = str(result.get("company_id") or "").split("-", 1)[-1] or "该公司"

    lines = ["## 结论"]
    if total_text:
        parent = _first_value(scale, "母公司在职员工的数量")
        subsidiaries = _first_value(scale, "主要子公司在职员工的数量")
        detail = []
        if parent:
            detail.append(f"母公司 {parent} 人")
        if subsidiaries:
            detail.append(f"主要子公司 {subsidiaries} 人")
        suffix = f"；其中{('，'.join(detail))}" if detail else ""
        lines.append(f"- **员工规模**：{company_name}在职员工数量合计 **{total_text} 人**{suffix}。")
    if profession:
        ranked = sorted(
            [(label, value, _parse_count(value) or -1) for label, value in profession if label != "合计"],
            key=lambda item: item[2],
            reverse=True,
        )
        if ranked:
            top = "、".join(
                f"{label} {value} 人"
                + (f"（{_ratio_text(value, total)}）" if _ratio_text(value, total) else "")
                for label, value, _count in ranked[:2]
            )
            lines.append(f"- **专业构成**：人数最多的类别为 {top}。")
    if education:
        ranked = sorted(
            [(label, value, _parse_count(value) or -1) for label, value in education if label != "合计"],
            key=lambda item: item[2],
            reverse=True,
        )
        if ranked:
            top = "、".join(
                f"{label} {value} 人"
                + (f"（{_ratio_text(value, total)}）" if _ratio_text(value, total) else "")
                for label, value, _count in ranked[:3]
            )
            lines.append(f"- **教育程度**：主要分布为 {top}。")

    lines.extend(["", "## 依据/数据"])
    lines.append(f"- 来源表：报告期末母公司和主要子公司的员工情况；pdf_page={result.get('pdf_page')}, table_index={result.get('table_index')}, md_line={result.get('md_line')}")
    lines.append("")
    lines.extend(_human_capital_section_table("员工规模", scale))
    if profession:
        lines.append("")
        lines.extend(_human_capital_section_table("专业构成", profession, total=total, include_ratio=True))
    if education:
        lines.append("")
        lines.extend(_human_capital_section_table("教育程度", education, total=total, include_ratio=True))

    links = []
    if result.get("open_pdf_page_url"):
        links.append(f"[打开PDF页]({result['open_pdf_page_url']})")
    if result.get("open_source_page_url"):
        links.append(f"[查看页来源]({result['open_source_page_url']})")
    if result.get("open_source_table_url"):
        links.append(f"[查看表格]({result['open_source_table_url']})")
    lines.extend(["", "## 引用来源"])
    lines.append(
        f"[1] source_type=wiki_report_table, file=reports/{result.get('report_id')}/report.md, "
        f"metric=员工情况/人才结构, period={result.get('report_id')}, task_id={result.get('task_id')}, "
        f"pdf_page={result.get('pdf_page')}, table_index={result.get('table_index')}, md_line={result.get('md_line')}"
        + (("，" + "，".join(links)) if links else "")
    )
    return "\n".join(lines)


def _statement_metric_result(message: str, context: Any | None = None) -> tuple[dict[str, Any] | None, Callable[..., str] | None]:
    resolver, renderer = _load_statement_metric_tools()
    if not resolver or not renderer:
        return None, None
    company_hint = _context_company_hint(context)
    company_text_candidates = [message]
    if company_hint:
        company_text_candidates.append(company_hint)
        company_text_candidates.append(f"{message}\n{company_hint}")
    for company_text in company_text_candidates:
        try:
            result = resolver(company_text, message)
        except Exception:
            continue
        if result.get("tables"):
            return result, renderer
    return None, renderer


def build_statement_metric_context(message: str, context: Any | None = None) -> str | None:
    """Inject deterministic main-statement rows for cash-flow/balance/profit questions."""
    if not _is_statement_query(message):
        return None
    result, renderer = _statement_metric_result(message, context)
    if not result or not renderer:
        return None
    try:
        markdown = renderer(result, max_rows=40)
    except Exception:
        return None
    markdown = _normalize_wiki_metric_file_refs(markdown)
    return (
        "以下是后端从本地 Wiki 三大表结构化数据确定性解析出的主表数据，优先作为本题事实依据；"
        "主表指标不得改用 `semantic/document_links.json` 附注表溯源。\n"
        "涉及现金流/利润表/资产负债表核心数值时，必须保留 `source_type=wiki_metrics`、"
        "`file=metrics/three_statements.json`、`pdf_page/table_index/md_line`，不得编造或替换为其他页表。\n\n"
        f"{markdown}"
    )


def build_direct_statement_metric_reply(message: str, context: Any | None = None) -> str | None:
    """Return main-statement rows directly for deterministic statement data requests."""
    if not _should_direct_answer_statement_query(message):
        return None
    result, renderer = _statement_metric_result(message, context)
    if not result or not renderer:
        return None
    try:
        return _normalize_wiki_metric_file_refs(renderer(result, max_rows=40))
    except Exception:
        return None


def _note_detail_result(
    message: str,
    context: Any | None = None,
    *,
    limit: int = 8,
) -> tuple[dict[str, Any] | None, Callable[..., str] | None]:
    resolver, renderer = _load_note_detail_tools()
    if not resolver or not renderer:
        return None, renderer

    company_hint = _context_company_hint(context)
    company_text_candidates = [message]
    if company_hint:
        company_text_candidates.append(company_hint)
        company_text_candidates.append(f"{message}\n{company_hint}")

    for company_text in company_text_candidates:
        try:
            result = resolver(company_text, message, limit=limit)
        except Exception:
            continue
        if result.get("tables"):
            return result, renderer
    return None, renderer


def build_note_detail_context(message: str, context: Any | None = None) -> str | None:
    """Inject deterministic Wiki note-table rows for detail/composition questions."""
    if not _should_inject_note_detail_context(message):
        return None

    result, renderer = _note_detail_result(message, context, limit=4)
    if not result:
        return None

    try:
        markdown = renderer(result, max_rows=12)
    except Exception:
        return None

    return (
        "以下是后端从本地 Wiki 确定性解析出的附注表格行，优先作为本题事实依据；"
        "不得再回答“非结构化所以无法展示”。\n"
        "如果用户询问明细/构成/分布，请完整列出表格记录；不要用“等/部分”省略行。"
        "英文名称必须原样保留，空白单元格不得改写为 `0`，不得调换列含义。\n"
        "回答时请保留或引用其中的 `source_type/file/task_id/pdf_page/table_index/md_line` 字段，"
        "并保留可打开表格链接。\n\n"
        f"{markdown}"
    )


def build_direct_note_detail_reply(message: str, context: Any | None = None) -> str | None:
    """Return the Wiki table directly for deterministic note-detail requests."""
    if not _should_direct_answer_note_detail(message):
        return None

    result, renderer = _note_detail_result(message, context, limit=8)
    if result and renderer:
        return renderer(result, max_rows=80)
    return None


def build_human_capital_context(message: str, context: Any | None = None) -> str | None:
    """Inject deterministic employee/talent-structure table rows."""
    result = _human_capital_result(message, context)
    if not result:
        return None
    return (
        "以下是后端从本地年报员工情况表确定性解析出的人才/人员结构数据，优先作为本题事实依据；"
        "不得沿用上一轮其他公司的员工数据，也不得把 `metrics/three_statements.json` 伪装成员工来源。\n"
        "回答时必须保留 `source_type/file/task_id/pdf_page/table_index/md_line`，并保留可打开表格链接。\n\n"
        f"{render_human_capital_markdown(result)}"
    )


def build_direct_human_capital_reply(message: str, context: Any | None = None) -> str | None:
    """Return employee/talent structure directly for deterministic HR table requests."""
    result = _human_capital_result(message, context)
    if not result:
        return None
    return render_human_capital_markdown(result)


def _parse_number(value: Any) -> float | None:
    return agent_runtime_financial_format._parse_number(value)


def _row_numeric_values(row: list[str] | None) -> list[float]:
    return agent_runtime_financial_format._row_numeric_values(row)


def _find_report_table(tables: list[dict[str, Any]], *terms: str) -> dict[str, Any] | None:
    normalized_terms = [_normalize_financial_text(term) for term in terms if term]
    if not normalized_terms:
        return None
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for table in tables:
        text = _normalize_financial_text(" ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit")))
        score = sum(1 for term in normalized_terms if term and term in text)
        if score <= 0:
            continue
        candidates.append((score, int(table.get("line") or 10**9), table))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], int(item[2].get("table_index") or 10**9)))
    return candidates[0][2]


def _find_group_income_statement_table(tables: list[dict[str, Any]], report_md: Path) -> dict[str, Any] | None:
    candidates: list[tuple[float, int, dict[str, Any]]] = []
    for table in tables:
        text = _normalize_financial_text(" ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit")))
        if "statementofincome" not in text or "salesrevenue" not in text:
            continue
        row = _row_by_label(_parse_report_table_rows(report_md, table.get("line")), "Sales revenue")
        values = _row_numeric_values(row)
        if not values:
            continue
        candidates.append((values[0], int(table.get("line") or 10**9), table))
    if not candidates:
        return _find_report_table(tables, "Statement of income", "Sales revenue")
    candidates.sort(key=lambda item: (-item[0], item[1], int(item[2].get("table_index") or 10**9)))
    return candidates[0][2]


def _find_average_employee_table(tables: list[dict[str, Any]], report_md: Path) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for table in tables:
        text = _normalize_financial_text(" ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit")))
        if "averagenumberofemployees" not in text:
            continue
        rows = _parse_report_table_rows(report_md, table.get("line"))
        labels = {_normalize_financial_text(row[0]) for row in rows if row}
        score = 1
        if _normalize_financial_text("BASF Group") in labels:
            score += 10
        if _normalize_financial_text("Europe") in labels and _normalize_financial_text("North America") in labels:
            score += 10
        candidates.append((score, int(table.get("line") or 10**9), table))
    if not candidates:
        return _find_report_table(tables, "Average number of employees")
    candidates.sort(key=lambda item: (-item[0], item[1], int(item[2].get("table_index") or 10**9)))
    return candidates[0][2]


def _latest_statement_rows_for_company(company_dir: Path, report_id: str) -> list[dict[str, Any]]:
    metrics_file = _company_artifact_paths(company_dir, report_id).get("three_statements")
    if not metrics_file:
        return []
    payload = _read_json_file(metrics_file)
    if not isinstance(payload, dict):
        return []
    records = _latest_records_by_statement(_iter_metric_records(payload.get("data") or payload))
    return [
        _statement_record_to_row(record, report_id, metrics_file, company_dir)
        for record in records
    ]


def _find_statement_metric_row(rows: list[dict[str, Any]], aliases: tuple[str, ...]) -> dict[str, Any] | None:
    normalized_aliases = [_normalize_financial_text(alias) for alias in aliases if alias]
    for row in rows:
        key = _normalize_financial_text(row.get("metric_key"))
        name = _normalize_financial_text(row.get("metric_name"))
        if any(alias and (alias == key or alias in name) for alias in normalized_aliases):
            return row
    return None


def _human_capital_result_for_company(company_dir: Path) -> dict[str, Any] | None:
    return _human_capital_result(
        f"{_company_query_prefix(company_dir)} 员工情况",
        _context_for_company_dir(company_dir),
    )


def _employee_total_from_human_capital(result: dict[str, Any] | None) -> int | None:
    sections = (result or {}).get("sections") or {}
    for label, value in sections.get("scale") or []:
        if label == "在职员工的数量合计":
            return _parse_count(value)
    for rows in (sections.get("profession") or [], sections.get("education") or []):
        for label, value in rows:
            if label == "合计":
                count = _parse_count(value)
                if count:
                    return count
    return None


def _find_employee_compensation_table(
    tables: list[dict[str, Any]],
    report_md: Path,
) -> tuple[dict[str, Any], list[str], float | None] | None:
    candidates: list[tuple[int, int, dict[str, Any], list[str], float | None]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        text = _normalize_financial_text(" ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit")))
        score = 0
        if _normalize_financial_text("应付职工薪酬") in text:
            score += 30
        if _normalize_financial_text("短期薪酬") in text:
            score += 20
        if _normalize_financial_text("离职后福利") in text:
            score += 20
        if score <= 0:
            continue
        rows = _parse_report_table_rows(report_md, table.get("line"))
        total_row = _row_by_label(rows, "合计")
        values = _row_numeric_values(total_row)
        if total_row:
            score += 20
        if len(values) >= 4:
            score += 20
        increase = values[1] if len(values) > 1 else None
        candidates.append((score, int(table.get("line") or 10**9), table, total_row or [], increase))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], int(item[2].get("table_index") or 10**9)))
    _score, _line, table, row, increase = candidates[0]
    return table, row, increase


def _generic_human_efficiency_result(message: str, context: Any | None = None) -> dict[str, Any] | None:
    company_dir = _resolve_company_dir(message, context)
    if not company_dir:
        return None
    report = _primary_report_for_company(company_dir, message)
    report_id = str(report.get("report_id") or "2025-annual")
    report_md = company_dir / "reports" / report_id / "report.md"
    report_json = _read_json_file(company_dir / "reports" / report_id / "report.json") or {}
    tables = report_json.get("tables") if isinstance(report_json, dict) else []
    if not isinstance(tables, list):
        tables = []

    statement_rows = _latest_statement_rows_for_company(company_dir, report_id)
    revenue_row = _find_statement_metric_row(statement_rows, CORE_KEY_METRIC_ALIASES["营业收入"])
    parent_profit_row = _find_statement_metric_row(statement_rows, CORE_KEY_METRIC_ALIASES["归母净利润"])
    net_profit_row = _find_statement_metric_row(statement_rows, CORE_KEY_METRIC_ALIASES["净利润"])
    employee_result = _human_capital_result_for_company(company_dir)
    employee_total = _employee_total_from_human_capital(employee_result)
    compensation = _find_employee_compensation_table(tables, report_md)

    revenue = _parse_number((revenue_row or {}).get("raw_value") or (revenue_row or {}).get("normalized_value"))
    parent_profit = _parse_number((parent_profit_row or {}).get("raw_value") or (parent_profit_row or {}).get("normalized_value"))
    net_profit = _parse_number((net_profit_row or {}).get("raw_value") or (net_profit_row or {}).get("normalized_value"))
    compensation_table: dict[str, Any] | None = None
    compensation_row: list[str] = []
    compensation_increase: float | None = None
    if compensation:
        compensation_table, compensation_row, compensation_increase = compensation

    if not revenue or not employee_total:
        return None

    company = _read_json_file(company_dir / "company.json") or {}
    return {
        "mode": "generic_cny",
        "company_id": company_dir.name,
        "company_name": company.get("company_short_name") or company.get("company_full_name") or company_dir.name,
        "report_id": report_id,
        "task_id": report.get("task_id"),
        "rows": {
            "revenue": revenue_row,
            "parent_profit": parent_profit_row,
            "net_profit": net_profit_row,
        },
        "employee_result": employee_result,
        "compensation_table": compensation_table,
        "compensation_row": compensation_row,
        "values": {
            "revenue_2025": revenue,
            "parent_profit_2025": parent_profit,
            "net_profit_2025": net_profit,
            "employees_2025": employee_total,
            "compensation_increase_2025": compensation_increase,
        },
    }


def _parse_report_table_rows(report_md: Path, line: Any) -> list[list[str]]:
    module = _load_note_detail_module()
    extract = getattr(module, "extract_table_html", None) if module else None
    parser = getattr(module, "parse_html_table", None) if module else None
    if not callable(extract) or not callable(parser):
        return []
    md_line = _safe_int(line)
    if not md_line:
        return []
    try:
        parsed = parser(extract(report_md, md_line) or "")
    except Exception:
        return []
    rows = parsed.get("rows") if isinstance(parsed, dict) else None
    return [row for row in rows if isinstance(row, list)] if isinstance(rows, list) else []


def _row_by_label(rows: list[list[str]], label: str) -> list[str] | None:
    normalized_label = _normalize_financial_text(label)
    for row in rows:
        if row and normalized_label in _normalize_financial_text(row[0]):
            return row
    return None


def _table_source_links(task_id: Any, pdf_page: Any, table_index: Any) -> str:
    links: list[str] = []
    pdf_url = _evidence_url(task_id, pdf_page, table_index, "pdf")
    page_url = _evidence_url(task_id, pdf_page, table_index, "page")
    table_url = _evidence_url(task_id, pdf_page, table_index, "table")
    if pdf_url:
        links.append(f"[打开PDF页]({pdf_url})")
    if page_url:
        links.append(f"[查看页来源]({page_url})")
    if table_url:
        links.append(f"[查看表格]({table_url})")
    return "，".join(links)


def _table_trace(
    index: int,
    *,
    source_type: str,
    file: str,
    metric: str,
    report_id: str,
    task_id: Any,
    table: dict[str, Any],
) -> str:
    pdf_page = table.get("pdf_page_number") or table.get("pdf_page") or "未返回"
    table_index = table.get("table_index") if table.get("table_index") not in (None, "") else "未返回"
    links = _table_source_links(task_id, pdf_page, table_index)
    return agent_runtime_financial_format._table_trace(
        index,
        source_type=source_type,
        file=file,
        metric=metric,
        report_id=report_id,
        task_id=task_id,
        table=table,
        links=links,
    )


def _human_efficiency_result(message: str, context: Any | None = None) -> dict[str, Any] | None:
    if not _is_human_efficiency_query(message):
        return None
    company_dir = _resolve_company_dir(message, context)
    if not company_dir:
        return None
    report = _primary_report_for_company(company_dir, message)
    report_id = str(report.get("report_id") or "2025-annual")
    report_md = company_dir / "reports" / report_id / "report.md"
    report_json = _read_json_file(company_dir / "reports" / report_id / "report.json") or {}
    tables = report_json.get("tables") if isinstance(report_json, dict) else []
    if not isinstance(tables, list):
        return None

    income_table = _find_group_income_statement_table(tables, report_md)
    personnel_table = _find_report_table(tables, "Personnel expenses")
    employees_table = _find_report_table(tables, "Number of employees as of December")
    average_employees_table = _find_average_employee_table(tables, report_md)
    regional_sales_table = _find_report_table(tables, "Regions 2025", "Location of company", "Sales")
    if not all((income_table, personnel_table, employees_table)):
        return _generic_human_efficiency_result(message, context)

    income_row = _row_by_label(_parse_report_table_rows(report_md, income_table.get("line")), "Sales revenue")
    personnel_row = _row_by_label(_parse_report_table_rows(report_md, personnel_table.get("line")), "Personnel expenses")
    employee_rows = _parse_report_table_rows(report_md, employees_table.get("line"))
    avg_employee_rows = _parse_report_table_rows(report_md, average_employees_table.get("line") if average_employees_table else None)
    employees_group_row = _row_by_label(employee_rows, "BASF Group")
    avg_employees_group_row = _row_by_label(avg_employee_rows, "BASF Group")

    revenue_values = _row_numeric_values(income_row)
    personnel_values = _row_numeric_values(personnel_row)
    employee_values_group = _row_numeric_values(employees_group_row)
    avg_employee_values_group = _row_numeric_values(avg_employees_group_row)
    revenue_2025 = revenue_values[0] if len(revenue_values) > 0 else None
    revenue_2024 = revenue_values[1] if len(revenue_values) > 1 else None
    personnel_2025 = personnel_values[0] if len(personnel_values) > 0 else None
    personnel_2024 = personnel_values[1] if len(personnel_values) > 1 else None
    employees_2025 = employee_values_group[0] if len(employee_values_group) > 0 else None
    employees_2024 = employee_values_group[1] if len(employee_values_group) > 1 else None
    avg_employees_2025 = avg_employee_values_group[0] if len(avg_employee_values_group) > 0 else None
    avg_employees_2024 = avg_employee_values_group[1] if len(avg_employee_values_group) > 1 else None

    regional_rows: list[dict[str, Any]] = []
    if regional_sales_table:
        regional_sales_rows = _parse_report_table_rows(report_md, regional_sales_table.get("line"))
        location_company_sales_seen = False
        for row in regional_sales_rows:
            if row and _normalize_financial_text(row[0]) == _normalize_financial_text("Location of company"):
                location_company_sales_seen = True
                continue
            if not location_company_sales_seen or not row or _normalize_financial_text(row[0]) != "sales":
                continue
            region_specs = [
                ("Europe", "Europe", 2),
                ("North America", "North America", 4),
                ("Asia Pacific", "Asia Pacific", 5),
                ("South America, Africa and Middle East", "South America, Africa, Middle East", 6),
            ]
            employee_values = {
                _normalize_financial_text(employee_row[0]): _row_numeric_values(employee_row)[0]
                for employee_row in employee_rows
                if employee_row and _row_numeric_values(employee_row)
            }
            for region, employee_label, sales_index in region_specs:
                sales = _parse_number(row[sales_index]) if len(row) > sales_index else None
                employee_count = employee_values.get(_normalize_financial_text(employee_label))
                if sales is None or employee_count is None:
                    continue
                regional_rows.append(
                    {
                        "region": region,
                        "sales_million_eur": sales,
                        "employees": employee_count,
                        "revenue_per_employee": _calculator_per_capita(
                            sales,
                            amount_unit="百万欧元",
                            count=employee_count,
                            currency="EUR",
                        ),
                    }
                )
            break

    company = _read_json_file(company_dir / "company.json") or {}
    return {
        "company_id": company_dir.name,
        "company_name": company.get("company_short_name") or company.get("company_full_name") or company_dir.name,
        "report_id": report_id,
        "task_id": report.get("task_id"),
        "tables": {
            "income": income_table,
            "personnel": personnel_table,
            "employees": employees_table,
            "average_employees": average_employees_table,
            "regional_sales": regional_sales_table,
        },
        "values": {
            "revenue_2025": revenue_2025,
            "revenue_2024": revenue_2024,
            "personnel_2025": personnel_2025,
            "personnel_2024": personnel_2024,
            "employees_2025": employees_2025,
            "employees_2024": employees_2024,
            "avg_employees_2025": avg_employees_2025,
            "avg_employees_2024": avg_employees_2024,
        },
        "regional_rows": regional_rows,
    }


def _fmt_number(value: Any, digits: int = 1) -> str:
    return agent_runtime_financial_format._fmt_number(value, digits)


def _calculator_per_capita(
    amount: Any,
    *,
    amount_unit: str,
    count: Any,
    count_unit: str = "人",
    currency: str = "CNY",
) -> dict[str, Any] | None:
    calculator = _load_financial_calculator_module()
    if calculator is None or amount is None or count is None:
        return None
    try:
        payload = calculator.per_capita(
            SimpleNamespace(
                amount=str(amount),
                amount_unit=amount_unit,
                currency=currency,
                count=str(count),
                count_unit=count_unit,
                fx_to_cny="",
                fx_date="",
                fx_source="",
                reported_native_per="",
                reported_native_10k="",
                reported_cny_per="",
                reported_cny_10k="",
            )
        )
    except Exception:
        return None
    return payload if isinstance(payload, dict) and payload.get("status") == "ok" else payload


def _calculator_per_capita_display(payload: dict[str, Any] | None, *, preferred: str = "cny_10k") -> str:
    return agent_runtime_financial_format._calculator_per_capita_display(payload, preferred=preferred)


def _calculator_formula_text(payload: dict[str, Any] | None) -> str:
    return agent_runtime_financial_format._calculator_formula_text(payload)


def _statement_row_table(row: dict[str, Any] | None) -> dict[str, Any]:
    return agent_runtime_financial_format._statement_row_table(row)


def _render_generic_human_efficiency_evidence_markdown(result: dict[str, Any]) -> str:
    values = result.get("values") or {}
    rows = result.get("rows") or {}
    task_id = result.get("task_id")
    report_id = str(result.get("report_id") or "2025-annual")
    revenue = values.get("revenue_2025")
    parent_profit = values.get("parent_profit_2025")
    net_profit = values.get("net_profit_2025")
    employees = values.get("employees_2025")
    compensation = values.get("compensation_increase_2025")
    profit_base = parent_profit if parent_profit is not None else net_profit
    per_revenue = _calculator_per_capita(revenue, amount_unit="元", count=employees, currency="CNY")
    per_profit = _calculator_per_capita(profit_base, amount_unit="元", count=employees, currency="CNY")
    per_compensation = _calculator_per_capita(compensation, amount_unit="元", count=employees, currency="CNY")

    employee_result = result.get("employee_result") or {}
    employee_table = {
        "pdf_page_number": employee_result.get("pdf_page"),
        "table_index": employee_result.get("table_index"),
        "line": employee_result.get("md_line"),
    }
    compensation_table = result.get("compensation_table") or {}

    lines = [
        "## 财务指标溯源补充",
        "- 后端已按指标重新定位 PDF 页和表格；以下为本轮人效分析中财务指标/派生指标的可审计来源。",
        "",
        "| 指标 | 数值/公式 | 口径 | PDF页/表格 |",
        "| --- | --- | --- | --- |",
        (
            f"| 营业收入 | 2025: {_fmt_number(revenue, 2)} 元 | 合并利润表 / 营业收入 | "
            f"pdf_page={(rows.get('revenue') or {}).get('pdf_page') or '未返回'}, "
            f"table_index={(rows.get('revenue') or {}).get('table_index') or '未返回'} |"
        ),
        (
            f"| 年末员工数 | 2025: {_fmt_number(employees, 0)} 人 | 报告期末母公司和主要子公司的员工情况 / 在职员工的数量合计 | "
            f"pdf_page={employee_table.get('pdf_page_number') or '未返回'}, "
            f"table_index={employee_table.get('table_index') or '未返回'} |"
        ),
        (
            f"| 人均营收 | {_fmt_number(revenue, 2)} 元 / {_fmt_number(employees, 0)} 人 = "
            f"{_calculator_per_capita_display(per_revenue)} | "
            f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_revenue)} | 见营业收入 + 年末员工数来源 |"
        ),
    ]
    if profit_base is not None:
        profit_label = "归母净利润" if parent_profit is not None else "净利润"
        profit_row = rows.get("parent_profit") if parent_profit is not None else rows.get("net_profit")
        lines.extend(
            [
                (
                    f"| {profit_label} | 2025: {_fmt_number(profit_base, 2)} 元 | 合并利润表 / {profit_label} | "
                    f"pdf_page={(profit_row or {}).get('pdf_page') or '未返回'}, "
                    f"table_index={(profit_row or {}).get('table_index') or '未返回'} |"
                ),
                (
                    f"| 人均{profit_label} | {_fmt_number(profit_base, 2)} 元 / {_fmt_number(employees, 0)} 人 = "
                    f"{_calculator_per_capita_display(per_profit)} | "
                    f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_profit)} | 见{profit_label} + 年末员工数来源 |"
                ),
            ]
        )
    if compensation is not None:
        lines.extend(
            [
                (
                    f"| 人力成本 | 2025 本期增加: {_fmt_number(compensation, 2)} 元 | 应付职工薪酬列示 / 合计 / 本期增加 | "
                    f"pdf_page={compensation_table.get('pdf_page_number') or compensation_table.get('pdf_page') or '未返回'}, "
                    f"table_index={compensation_table.get('table_index') or '未返回'} |"
                ),
                (
                    f"| 人均人力成本 | {_fmt_number(compensation, 2)} 元 / {_fmt_number(employees, 0)} 人 = "
                    f"{_calculator_per_capita_display(per_compensation)} | "
                    f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_compensation)} | 见人力成本 + 年末员工数来源 |"
                ),
            ]
        )

    lines.extend(["", "## 指标级引用来源"])
    refs = [
        ("营业收入", "wiki_metrics", (rows.get("revenue") or {}).get("file") or "metrics/three_statements.json", _statement_row_table(rows.get("revenue"))),
        ("年末员工数", "wiki_report_table", f"reports/{report_id}/report.md", employee_table),
    ]
    if parent_profit is not None:
        refs.append(("归母净利润", "wiki_metrics", (rows.get("parent_profit") or {}).get("file") or "metrics/three_statements.json", _statement_row_table(rows.get("parent_profit"))))
    elif net_profit is not None:
        refs.append(("净利润", "wiki_metrics", (rows.get("net_profit") or {}).get("file") or "metrics/three_statements.json", _statement_row_table(rows.get("net_profit"))))
    if compensation_table:
        refs.append(("应付职工薪酬", "wiki_report_table", f"reports/{report_id}/report.md", compensation_table))
    for index, (metric, source_type, file, table) in enumerate(refs, start=1):
        lines.append(
            _table_trace(
                index,
                source_type=source_type,
                file=file,
                metric=metric,
                report_id=report_id,
                task_id=task_id,
                table=table or {},
            )
        )
    return "\n".join(lines)


def render_human_efficiency_evidence_markdown(result: dict[str, Any]) -> str:
    if result.get("mode") == "generic_cny":
        return _render_generic_human_efficiency_evidence_markdown(result)

    values = result.get("values") or {}
    tables = result.get("tables") or {}
    task_id = result.get("task_id")
    report_id = str(result.get("report_id") or "2025-annual")
    revenue_2025 = values.get("revenue_2025")
    personnel_2025 = values.get("personnel_2025")
    employees_2025 = values.get("employees_2025")
    per_revenue = _calculator_per_capita(
        revenue_2025,
        amount_unit="百万欧元",
        count=employees_2025,
        currency="EUR",
    )
    per_personnel = _calculator_per_capita(
        personnel_2025,
        amount_unit="百万欧元",
        count=employees_2025,
        currency="EUR",
    )

    lines = [
        "## 财务指标溯源补充",
        "- 后端已按指标重新定位 PDF 页和表格；以下为本轮人效分析中财务指标/派生指标的可审计来源。",
        "",
        "| 指标 | 数值/公式 | 口径 | PDF页/表格 |",
        "| --- | --- | --- | --- |",
    ]
    income_table = tables.get("income") or {}
    personnel_table = tables.get("personnel") or {}
    employees_table = tables.get("employees") or {}
    regional_sales_table = tables.get("regional_sales") or {}
    lines.append(
        f"| 营业收入 | 2025: €{_fmt_number(revenue_2025, 0)} million；2024: €{_fmt_number(values.get('revenue_2024'), 0)} million | Statement of income / Sales revenue | pdf_page={income_table.get('pdf_page_number') or income_table.get('pdf_page') or '未返回'}, table_index={income_table.get('table_index') or '未返回'} |"
    )
    lines.append(
        f"| 人力成本 | 2025: €{_fmt_number(personnel_2025, 0)} million；2024: €{_fmt_number(values.get('personnel_2024'), 0)} million | Personnel expenses | pdf_page={personnel_table.get('pdf_page_number') or personnel_table.get('pdf_page') or '未返回'}, table_index={personnel_table.get('table_index') or '未返回'} |"
    )
    lines.append(
        f"| 年末员工数 | 2025: {_fmt_number(employees_2025, 0)}；2024: {_fmt_number(values.get('employees_2024'), 0)} | Number of employees as of December 31 | pdf_page={employees_table.get('pdf_page_number') or employees_table.get('pdf_page') or '未返回'}, table_index={employees_table.get('table_index') or '未返回'} |"
    )
    lines.append(
        f"| 人均营收 | €{_fmt_number(revenue_2025, 0)} million / {_fmt_number(employees_2025, 0)} = "
        f"{_calculator_per_capita_display(per_revenue, preferred='native_per')} | "
        f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_revenue)} | 见营业收入 + 年末员工数来源 |"
    )
    lines.append(
        f"| 人均人力成本 | €{_fmt_number(personnel_2025, 0)} million / {_fmt_number(employees_2025, 0)} = "
        f"{_calculator_per_capita_display(per_personnel, preferred='native_per')} | "
        f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_personnel)} | 见人力成本 + 年末员工数来源 |"
    )
    if result.get("regional_rows"):
        for row in result["regional_rows"]:
            lines.append(
                f"| {row['region']} 人均营收 | €{_fmt_number(row['sales_million_eur'], 0)} million / {_fmt_number(row['employees'], 0)} = "
                f"{_calculator_per_capita_display(row.get('revenue_per_employee'), preferred='native_per')} | "
                f"派生计算（financial_calculator.py）：{_calculator_formula_text(row.get('revenue_per_employee'))} | "
                f"sales: pdf_page={regional_sales_table.get('pdf_page_number') or regional_sales_table.get('pdf_page') or '未返回'}, table_index={regional_sales_table.get('table_index') or '未返回'}；employees: pdf_page={employees_table.get('pdf_page_number') or '未返回'}, table_index={employees_table.get('table_index') or '未返回'} |"
            )

    lines.extend(["", "## 指标级引用来源"])
    refs = [
        ("营业收入", "wiki_report_table", "reports/%s/report.md" % report_id, income_table),
        ("人力成本", "wiki_report_table", "reports/%s/report.md" % report_id, personnel_table),
        ("年末员工数", "wiki_report_table", "reports/%s/report.md" % report_id, employees_table),
    ]
    if tables.get("average_employees"):
        refs.append(("平均员工数", "wiki_report_table", "reports/%s/report.md" % report_id, tables["average_employees"]))
    if regional_sales_table:
        refs.append(("区域销售/location of company", "wiki_report_table", "reports/%s/report.md" % report_id, regional_sales_table))
    for index, (metric, source_type, file, table) in enumerate(refs, start=1):
        lines.append(
            _table_trace(
                index,
                source_type=source_type,
                file=file,
                metric=metric,
                report_id=report_id,
                task_id=task_id,
                table=table or {},
            )
        )
    return "\n".join(lines)


def build_human_efficiency_evidence_context(message: str, context: Any | None = None) -> str | None:
    result = _human_efficiency_result(message, context)
    if not result:
        return None
    return (
        "以下是后端从本地年报表格确定性解析出的人效/人均财务指标底稿。"
        "回答涉及人均营收、人力成本、人均人力成本、区域人效时，必须逐项保留到唯一的 `## 引用来源`。"
        "派生指标必须说明分子、分母和对应 PDF/表格来源。\n\n"
        f"{render_human_efficiency_evidence_markdown(result)}"
    )


def append_human_efficiency_evidence_if_needed(
    message: str,
    context: Any | None,
    reply: str,
) -> str:
    if not _is_human_efficiency_query(message):
        return reply
    reply = _merge_primary_data_refs_into_citations(reply)
    evidence = build_human_efficiency_evidence_context(message, context)
    if not evidence:
        return reply
    return _merge_primary_data_refs_into_citations(reply, evidence)


def _has_primary_data_evidence_trace(reply: str) -> bool:
    return agent_runtime_citations._has_primary_data_evidence_trace(
        reply,
        markers=PRIMARY_DATA_EVIDENCE_MARKERS,
    )


def _source_locator_text(
    *,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
) -> str:
    return agent_runtime_citations._source_locator_text(
        task_id=task_id,
        pdf_page=pdf_page,
        table_index=table_index,
        md_line=md_line,
        table_source_links=_table_source_links,
    )


def _primary_data_source_ref(
    index: int,
    *,
    source_type: str,
    file: str,
    metric: str,
    period: Any,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
) -> str:
    return agent_runtime_citations._primary_data_source_ref(
        index,
        source_type=source_type,
        file=file,
        metric=metric,
        period=period,
        task_id=task_id,
        pdf_page=pdf_page,
        table_index=table_index,
        md_line=md_line,
        table_source_links=_table_source_links,
    )


def _append_unique_source_ref(
    refs: list[str],
    seen: set[tuple[Any, Any, Any, str, str]],
    *,
    source_type: str,
    file: str,
    metric: str,
    period: Any,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
) -> None:
    agent_runtime_citations._append_unique_source_ref(
        refs,
        seen,
        source_type=source_type,
        file=file,
        metric=metric,
        period=period,
        task_id=task_id,
        pdf_page=pdf_page,
        table_index=table_index,
        md_line=md_line,
        table_source_links=_table_source_links,
    )


def _markdown_heading(line: str) -> tuple[int, str] | None:
    return agent_runtime_citations._markdown_heading(line)


def _is_reference_line(line: str) -> bool:
    return agent_runtime_citations._is_reference_line(line)


def _extract_reference_lines(lines: list[str] | str) -> list[str]:
    return agent_runtime_citations._extract_reference_lines(lines)


def _source_field_value(line: str, field: str) -> str:
    return agent_runtime_citations._source_field_value(line, field)


def _source_reference_key(line: str) -> tuple[str, str, str, str] | tuple[str]:
    return agent_runtime_citations._source_reference_key(line)


def _reply_has_requested_metric_evidence(message: str, reply: str) -> bool:
    return agent_runtime_citations._reply_has_requested_metric_evidence(
        message,
        reply,
        postgres_requested_metric_terms=_postgres_requested_metric_terms,
        normalize_financial_text=_normalize_financial_text,
    )


def _strip_auto_evidence_sections(markdown: str) -> tuple[str, list[str]]:
    return agent_runtime_citations._strip_auto_evidence_sections(
        markdown,
        auto_evidence_section_titles=AUTO_EVIDENCE_SECTION_TITLES,
    )


def _merge_refs_into_reference_section(markdown: str, refs: list[str]) -> str:
    return agent_runtime_citations._merge_refs_into_reference_section(markdown, refs)


def _merge_primary_data_refs_into_citations(reply: str, supplement: str | None = None) -> str:
    return agent_runtime_citations._merge_primary_data_refs_into_citations(
        reply,
        supplement,
        auto_evidence_section_titles=AUTO_EVIDENCE_SECTION_TITLES,
    )


def _render_three_statement_primary_data_supplement(result: dict[str, Any]) -> str | None:
    return agent_runtime_citations._render_three_statement_primary_data_supplement(
        result,
        primary_data_supplement_max_rows=PRIMARY_DATA_SUPPLEMENT_MAX_ROWS,
        table_source_links=_table_source_links,
    )


def _first_record_label(record: dict[str, Any]) -> str:
    return agent_runtime_citations._first_record_label(record)


def _record_values_preview(record: dict[str, Any], *, max_values: int = 4) -> str:
    return agent_runtime_citations._record_values_preview(record, max_values=max_values)


def _render_statement_table_primary_data_supplement(result: dict[str, Any]) -> str | None:
    return agent_runtime_citations._render_statement_table_primary_data_supplement(
        result,
        primary_data_supplement_max_rows=PRIMARY_DATA_SUPPLEMENT_MAX_ROWS,
        table_source_links=_table_source_links,
    )


def _render_note_detail_primary_data_supplement(result: dict[str, Any]) -> str | None:
    return agent_runtime_citations._render_note_detail_primary_data_supplement(
        result,
        primary_data_supplement_max_rows=PRIMARY_DATA_SUPPLEMENT_MAX_ROWS,
        table_source_links=_table_source_links,
    )


def _render_human_capital_primary_data_supplement(result: dict[str, Any]) -> str | None:
    return agent_runtime_citations._render_human_capital_primary_data_supplement(
        result,
        primary_data_supplement_max_rows=PRIMARY_DATA_SUPPLEMENT_MAX_ROWS,
        table_source_links=_table_source_links,
    )


def _render_wiki_fulltext_primary_data_supplement(result: dict[str, Any]) -> str | None:
    return agent_runtime_citations._render_wiki_fulltext_primary_data_supplement(
        result,
        primary_data_supplement_max_rows=PRIMARY_DATA_SUPPLEMENT_MAX_ROWS,
        table_source_links=_table_source_links,
    )


def _render_postgres_primary_data_supplement(result: dict[str, Any]) -> str | None:
    return agent_runtime_citations._render_postgres_primary_data_supplement(
        result,
        primary_data_supplement_max_rows=PRIMARY_DATA_SUPPLEMENT_MAX_ROWS,
        evidence_url=_evidence_url,
        markdown_table_cell=_markdown_table_cell,
        table_source_links=_table_source_links,
        postgres_row_pdf_page=_postgres_row_pdf_page,
        postgres_row_table_index=_postgres_row_table_index,
        postgres_row_md_line=_postgres_row_md_line,
        postgres_row_metric_name=_postgres_row_metric_name,
        postgres_row_value=_postgres_row_value,
        postgres_row_unit=_postgres_row_unit,
    )


def build_primary_data_evidence_supplement(message: str, context: Any | None = None) -> str | None:
    human_efficiency = _human_efficiency_result(message, context)
    if human_efficiency:
        return render_human_efficiency_evidence_markdown(human_efficiency)

    human_capital = _human_capital_result(message, context)
    if human_capital:
        return _render_human_capital_primary_data_supplement(human_capital)

    statement_result = _three_statement_core_result(message, context)
    statement_supplement = _render_three_statement_primary_data_supplement(statement_result or {})
    if statement_supplement:
        return statement_supplement

    detailed_statement_result, _renderer = _statement_metric_result(message, context)
    statement_table_supplement = _render_statement_table_primary_data_supplement(detailed_statement_result or {})
    if statement_table_supplement:
        return statement_table_supplement

    note_result, _note_renderer = _note_detail_result(message, context, limit=8)
    note_supplement = _render_note_detail_primary_data_supplement(note_result or {})
    if note_supplement:
        return note_supplement

    fulltext = _wiki_fulltext_fallback_result(message, context)
    fulltext_supplement = _render_wiki_fulltext_primary_data_supplement(fulltext or {})
    if fulltext_supplement:
        return fulltext_supplement

    postgres = _postgres_fallback_result(message, context)
    postgres_supplement = _render_postgres_primary_data_supplement(postgres or {})
    if postgres_supplement:
        return postgres_supplement
    return None


def append_primary_data_evidence_if_needed(
    message: str,
    context: Any | None,
    reply: str,
) -> str:
    if _is_runtime_status_reply(reply):
        return reply
    reply = _merge_primary_data_refs_into_citations(reply)
    if _reply_has_requested_metric_evidence(message, reply):
        return reply
    supplement = build_primary_data_evidence_supplement(message, context)
    if not supplement:
        return reply
    return _merge_primary_data_refs_into_citations(reply, supplement)


def _load_financial_query_api() -> Any | None:
    script_path = str(FINANCIAL_QUERY_API_DIR)
    if script_path not in sys.path:
        sys.path.insert(0, script_path)
    try:
        return importlib.import_module("financial_query_api")
    except Exception:
        return None


def _financial_query_connection_factory(module: Any) -> Callable[[], Any] | None:
    get_connection = getattr(module, "get_connection", None)
    if callable(get_connection):
        return get_connection
    pg = getattr(module, "pg", None)
    get_connection = getattr(pg, "get_connection", None)
    if callable(get_connection):
        return get_connection
    return None


def _should_consider_postgres_fallback(message: str, context: Any | None = None) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or _is_general_assistant_request(text):
        return False
    if _is_human_capital_query(message):
        return False
    if _is_statement_query(message) or _should_inject_note_detail_context(message):
        return True
    if any(term.lower() in text.lower() for term in POSTGRES_FALLBACK_TERMS):
        return True
    company = _context_company(context)
    return bool(company and any(term in text for term in ("多少", "数据", "情况", "如何", "怎么样")))


def _postgres_query_text(message: str, context: Any | None = None) -> str:
    return agent_runtime_postgres_fallback.postgres_query_text(
        message,
        context,
        context_company_hint=_context_company_hint,
    )


def _postgres_prepare_parsed(parsed: dict[str, Any], message: str) -> dict[str, Any]:
    return agent_runtime_postgres_fallback.postgres_prepare_parsed(parsed, message)


def _postgres_requested_metric_terms(message: str) -> list[str]:
    return agent_runtime_postgres_fallback.postgres_requested_metric_terms(
        message,
        financial_note_metric_terms=FINANCIAL_NOTE_METRIC_TERMS,
        core_key_metric_terms=CORE_KEY_METRIC_TERMS,
        core_key_metric_aliases=CORE_KEY_METRIC_ALIASES,
    )


def _postgres_row_matches_requested_terms(row: dict[str, Any], requested_terms: list[str]) -> bool:
    return agent_runtime_postgres_fallback.postgres_row_matches_requested_terms(
        row,
        requested_terms,
        normalize_financial_text=_normalize_financial_text,
        postgres_row_payload=_postgres_row_payload,
    )


def _postgres_query_metric_rows(
    module: Any,
    cur: Any,
    parsed: dict[str, Any],
    company: dict[str, Any],
    query_text: str,
    limit: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    module.infer_metric_from_database(cur, parsed, company, query_text)
    if parsed.get("query_type") == "table":
        return module.query_statement_table(cur, parsed, company, limit)
    source_tables, rows = module.query_metric_from_split_tables(cur, parsed, company, limit)
    if not rows:
        wide_tables, wide_rows = module.query_metric_from_wide(cur, parsed, company, limit)
        source_tables = list(dict.fromkeys([*source_tables, *wide_tables]))
        rows.extend(wide_rows)
    return source_tables, module.dedupe_response_rows(rows, limit)


def _postgres_enrich_rows_with_table_pages(cur: Any, rows: list[dict[str, Any]]) -> None:
    pairs: list[tuple[str, int]] = []
    for row in rows:
        if _postgres_row_pdf_page(row):
            continue
        task_id = str(row.get("task_id") or "").strip()
        table_index = _postgres_row_table_index(row)
        if not task_id or table_index in (None, ""):
            continue
        try:
            pair = (task_id, int(table_index))
        except (TypeError, ValueError):
            continue
        if pair not in pairs:
            pairs.append(pair)
    if not pairs:
        return
    placeholders = ", ".join(["(%s, %s)"] * len(pairs))
    params: list[Any] = []
    for task_id, table_index in pairs:
        params.extend([task_id, table_index])
    try:
        cur.execute(
            f"""
            SELECT task_id, table_index, pdf_page_number, markdown_line
            FROM pdf2md.document_tables
            WHERE (task_id, table_index) IN ({placeholders})
            """,
            params,
        )
    except Exception:
        return
    table_pages = {
        (str(row.get("task_id")), int(row.get("table_index"))): dict(row)
        for row in cur.fetchall()
        if row.get("task_id") and row.get("table_index") is not None
    }
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        table_index = _postgres_row_table_index(row)
        try:
            key = (task_id, int(table_index))
        except (TypeError, ValueError):
            continue
        table = table_pages.get(key)
        if not table:
            continue
        if not _postgres_row_pdf_page(row) and table.get("pdf_page_number"):
            row["source_page_number"] = table.get("pdf_page_number")
        if not row.get("source_markdown_line") and table.get("markdown_line"):
            row["source_markdown_line"] = table.get("markdown_line")


def _postgres_fallback_result(
    message: str,
    context: Any | None = None,
    *,
    limit: int = POSTGRES_FALLBACK_ROW_LIMIT,
) -> dict[str, Any] | None:
    if not _should_consider_postgres_fallback(message, context):
        return None
    module = _load_financial_query_api()
    if module is None:
        return None
    get_connection = _financial_query_connection_factory(module)
    if get_connection is None:
        return None
    query_text = _postgres_query_text(message, context)
    try:
        parsed = module.merge_parse(query_text, False)
        parsed = _postgres_prepare_parsed(parsed, message)
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SET TRANSACTION READ ONLY")
                except Exception:
                    try:
                        cur.execute("SET default_transaction_read_only = on")
                    except Exception:
                        pass
                company = module.resolve_company(cur, parsed, query_text)
                if not company:
                    return None
                parsed.update({f"resolved_{key}": value for key, value in company.items()})
                requested_terms = _postgres_requested_metric_terms(message)
                source_tables: list[str] = []
                rows: list[dict[str, Any]] = []
                if requested_terms:
                    metric_parsed = dict(parsed)
                    source_tables, rows = _postgres_query_metric_rows(
                        module,
                        cur,
                        metric_parsed,
                        company,
                        query_text,
                        limit,
                    )
                    if rows:
                        parsed = metric_parsed

                if not rows and parsed.get("query_type") == "company_all":
                    source_tables, rows = module.query_company_all_metrics(cur, parsed, company, limit)
                elif not rows:
                    source_tables, rows = _postgres_query_metric_rows(
                        module,
                        cur,
                        parsed,
                        company,
                        query_text,
                        limit,
                    )
                if requested_terms and rows and not any(_postgres_row_matches_requested_terms(row, requested_terms) for row in rows):
                    return None
                _postgres_enrich_rows_with_table_pages(cur, rows)
    except Exception:
        return None
    if not rows:
        return None
    return {
        "question": message,
        "query_text": query_text,
        "parsed": module.normalize_json(parsed),
        "source_tables": source_tables,
        "rows": [module.normalize_json(row) for row in rows[:limit]],
    }


def _render_postgres_fallback_context(result: dict[str, Any]) -> str:
    return agent_runtime_citations._render_postgres_fallback_context(
        result,
        evidence_url=_evidence_url,
        markdown_table_cell=_markdown_table_cell,
        table_source_links=_table_source_links,
        postgres_row_pdf_page=_postgres_row_pdf_page,
        postgres_row_table_index=_postgres_row_table_index,
        postgres_row_md_line=_postgres_row_md_line,
        postgres_row_metric_name=_postgres_row_metric_name,
        postgres_row_value=_postgres_row_value,
        postgres_row_unit=_postgres_row_unit,
    )


def build_postgres_fallback_context(message: str, context: Any | None = None) -> str | None:
    result = _postgres_fallback_result(message, context)
    if not result:
        return None
    return _render_postgres_fallback_context(result)


def _needs_financial_evidence_contract(message: str, context: Any | None = None) -> bool:
    return (
        _is_human_efficiency_query(message)
        or _is_statement_query(message)
        or _should_inject_note_detail_context(message)
        or _question_needs_three_statement_context(message, context)
        or _should_consider_wiki_fulltext_fallback(message, context)
        or _should_consider_postgres_fallback(message, context)
        or _should_consider_pdf2md_parse_only_context(message, context)
    )


def _has_structured_evidence_trace(reply: str) -> bool:
    return agent_runtime_citations._has_structured_evidence_trace(reply)


def _is_runtime_status_reply(reply: str) -> bool:
    return agent_runtime_financial_guard._is_runtime_status_reply(reply, runtime_status_prefixes=RUNTIME_STATUS_PREFIXES)


def _reply_has_derived_financial_metric(reply: str) -> bool:
    return agent_runtime_financial_guard._reply_has_derived_financial_metric(reply)


def _reply_has_calculator_trace(reply: str) -> bool:
    return agent_runtime_financial_guard._reply_has_calculator_trace(reply)


def _reply_has_reconciliation_trace(reply: str) -> bool:
    return agent_runtime_financial_guard._reply_has_reconciliation_trace(reply)


def _reply_has_reconciliation_metric(reply: str) -> bool:
    return agent_runtime_financial_guard._reply_has_reconciliation_metric(reply)


def append_financial_tool_availability_correction_if_needed(reply: str) -> str:
    return agent_runtime_financial_guard.append_financial_tool_availability_correction_if_needed(
        reply,
        calculator_path=FINANCIAL_CALCULATOR_PATH,
        reconciliation_validator_path=FINANCIAL_RECONCILIATION_VALIDATOR_PATH,
    )


def append_calculation_trace_warning_if_needed(message: str, reply: str) -> str:
    return agent_runtime_financial_guard.append_calculation_trace_warning_if_needed(
        message,
        reply,
        runtime_status_prefixes=RUNTIME_STATUS_PREFIXES,
        calculator_path=FINANCIAL_CALCULATOR_PATH,
        reconciliation_validator_path=FINANCIAL_RECONCILIATION_VALIDATOR_PATH,
        calculator_path_text=FINANCIAL_CALCULATOR_PATH_TEXT,
        reconciliation_validator_path_text=FINANCIAL_RECONCILIATION_VALIDATOR_PATH_TEXT,
    )


def build_financial_evidence_fallback_reply(message: str, context: Any | None = None) -> str | None:
    """Return deterministic evidence when a model skips required citations."""
    primary_data_supplement = build_primary_data_evidence_supplement(message, context)
    if primary_data_supplement:
        return _merge_primary_data_refs_into_citations(
            "## 证据校验\n"
            "- 模型本轮输出缺少主要数据级溯源，后端已补充主要指标、PDF 页、表格/文本块和来源链接。\n"
            "- 需要解释或评价时，应基于 `## 引用来源` 继续组织语言。",
            primary_data_supplement,
        )

    human_efficiency_context = build_human_efficiency_evidence_context(message, context)
    if human_efficiency_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少指标级财务溯源，后端已补充人效/人均指标底稿。\n"
            "- 以下返回后端确定性解析出的指标、公式、PDF 页和表格入口；需要解释或评价时，应基于这些来源继续分析。\n\n"
            f"{human_efficiency_context}"
        )

    three_statement_context = build_three_statement_core_context(message, context)
    if three_statement_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的本地 Wiki 三大表证据引用，后端已阻断该事实答案。\n"
            "- 以下返回后端确定性解析出的三大表核心底稿；需要润色或解释时，应基于这些来源继续组织语言。\n\n"
            f"{three_statement_context}"
        )

    if _is_statement_query(message):
        result, renderer = _statement_metric_result(message, context)
        if result and renderer:
            try:
                body = renderer(result, max_rows=40)
            except Exception:
                body = None
            if body:
                return (
                    "## 证据校验\n"
                    "- 模型本轮输出缺少可解析的本地 Wiki 证据引用，后端已阻断该事实答案。\n"
                    "- 以下返回后端确定性解析出的主表证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
                    f"{body}"
                )

    if _should_inject_note_detail_context(message):
        result, renderer = _note_detail_result(message, context, limit=8)
        if result and renderer:
            try:
                body = renderer(result, max_rows=80)
            except Exception:
                body = None
            if body:
                return (
                    "## 证据校验\n"
                    "- 模型本轮输出缺少可解析的本地 Wiki 证据引用，后端已阻断该事实答案。\n"
                    "- 以下返回后端确定性解析出的附注证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
                    f"{body}"
                )
    wiki_fulltext_context = build_wiki_fulltext_fallback_context(message, context)
    if wiki_fulltext_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的结构化 Wiki 证据引用；后端已改用完整年报 Markdown 和完整 document_full.json 兜底检索。\n"
            "- 以下返回后端确定性检索出的原文证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
            f"{wiki_fulltext_context}"
        )
    postgres_context = build_postgres_fallback_context(message, context)
    if postgres_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的本地 Wiki 证据引用，且 Wiki 确定性解析未返回足够证据。\n"
            "- 以下返回后端只读查询 PostgreSQL `pdf2md` 得到的补充证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
            f"{postgres_context}"
        )
    parse_only_context = build_pdf2md_parse_only_context(message, context)
    if parse_only_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的本地 Wiki 证据引用；后端发现该报告尚未进入 Wiki，只返回真实 pdf2md 解析产物目录。\n"
            "- 原回答已被阻断；需要事实答案时，请基于下列 `result.md` / `document_full.json` / `financial_data.json` 重新定位证据。\n\n"
            f"{parse_only_context}"
        )
    return None


def build_invalid_task_id_evidence_reply(
    message: str,
    context: Any | None,
    invalid_task_ids: list[str],
) -> str:
    fallback = build_financial_evidence_fallback_reply(message, context)
    if fallback:
        return (
            "## 证据链无效\n"
            "- 模型本轮输出引用了本地不存在的 `task_id`，后端已阻断原回答并改用确定性证据返回。\n"
            f"- 无效 task_id: {', '.join(invalid_task_ids)}\n\n"
            f"{fallback}"
        )
    return (
        "## 证据链无效\n"
        "- 模型本轮输出引用了本地不存在的 `task_id`，后端已阻断原回答，避免伪造引用进入历史。\n"
        f"- 无效 task_id: {', '.join(invalid_task_ids)}\n"
        "- 当前后端未检索到可替换的本地 Wiki / pdf2md 确定性证据。请先完成对应 PDF 解析入库，或明确指定一个已存在的解析任务。"
    )


def enforce_financial_evidence_contract(
    message: str,
    context: Any | None,
    reply: str,
) -> str:
    """Do not let financial fact answers enter history without structured evidence."""
    if _is_runtime_status_reply(reply):
        return reply
    invalid_task_ids = _invalid_task_ids_in_reply(message, context, reply)
    if invalid_task_ids:
        return build_invalid_task_id_evidence_reply(message, context, invalid_task_ids)
    if not _needs_financial_evidence_contract(message, context):
        return reply
    reply = append_primary_data_evidence_if_needed(message, context, reply)
    reply = append_calculation_trace_warning_if_needed(message, reply)
    if _has_primary_data_evidence_trace(reply) or _has_structured_evidence_trace(reply):
        invalid_task_ids = _invalid_task_ids_in_reply(message, context, reply)
        if invalid_task_ids:
            return build_invalid_task_id_evidence_reply(message, context, invalid_task_ids)
        return reply
    fallback = build_financial_evidence_fallback_reply(message, context)
    return fallback or reply


def format_chat_context(context: Any | None) -> str | None:
    return agent_runtime_context.build_format_chat_context(
        wiki_root=WIKI_ROOT,
        context=context,
        context_header=CONTEXT_HEADER,
    )


def get_session_default_context(
    profile: HermesProfile,
    session_id: str,
    context: Any | None = None,
    *,
    allow_initialize: bool = False,
) -> str | None:
    profile = _runtime_profile(profile)
    key = _active_key(profile, session_id)
    if key in SESSION_DEFAULT_CONTEXTS:
        return SESSION_DEFAULT_CONTEXTS[key]

    if not allow_initialize:
        return None

    formatted_context = format_chat_context(context)
    if formatted_context:
        SESSION_DEFAULT_CONTEXTS[key] = formatted_context
    return formatted_context


def build_session_contextual_input(
    message: str,
    *,
    profile: HermesProfile,
    session_id: str,
    context: Any | None = None,
    allow_initialize: bool = False,
    local_memory_context: str | None = None,
) -> str:
    profile = _runtime_profile(profile)
    if _is_general_assistant_request(message):
        profile_label = PROFILE_LABELS.get(profile, profile)
        return agent_runtime_context.build_general_assistant_context_input(
            message,
            profile=profile,
            profile_label=profile_label,
            general_assistant_context=GENERAL_ASSISTANT_CONTEXT,
        )

    default_context = get_session_default_context(
        profile,
        session_id,
        context,
        allow_initialize=allow_initialize,
    )
    blocks: list[str] = []
    if default_context:
        blocks.append(default_context)
    if local_memory_context:
        blocks.append(local_memory_context)
    resolved_company_dirs = _resolve_company_dirs(message, context)
    company_scope_blocks, company_context_items = agent_runtime_context.build_company_context_items(
        message,
        context,
        resolved_company_dirs,
        context_for_company_dir=_context_for_company_dir,
        message_for_company=_message_for_company,
    )
    blocks.extend(company_scope_blocks)

    has_deterministic_evidence_context = False
    human_capital_context = None
    for scoped_message, scoped_context, _company_dir in company_context_items:
        company_scope_context = build_company_wiki_scope_context(scoped_message, scoped_context)
        if company_scope_context and company_scope_context not in blocks:
            blocks.append(company_scope_context)
        human_efficiency_context = build_human_efficiency_evidence_context(scoped_message, scoped_context)
        if human_efficiency_context and human_efficiency_context not in blocks:
            blocks.append(human_efficiency_context)
            has_deterministic_evidence_context = True
        current_human_capital_context = build_human_capital_context(scoped_message, scoped_context)
        if current_human_capital_context and current_human_capital_context not in blocks:
            blocks.append(current_human_capital_context)
            has_deterministic_evidence_context = True
            human_capital_context = current_human_capital_context
    scoped_message, scoped_context = agent_runtime_context.scoped_evidence_input(
        message,
        context,
        company_context_items,
    )
    if human_capital_context:
        has_deterministic_evidence_context = True
    else:
        three_statement_core_context = build_three_statement_core_context(scoped_message, scoped_context)
        if three_statement_core_context:
            blocks.append(three_statement_core_context)
            has_deterministic_evidence_context = True
        statement_context = build_statement_metric_context(scoped_message, scoped_context)
        if statement_context and statement_context not in blocks:
            blocks.append(statement_context)
            has_deterministic_evidence_context = True
        note_detail_context = build_note_detail_context(scoped_message, scoped_context)
        if note_detail_context:
            blocks.append(note_detail_context)
            has_deterministic_evidence_context = True
    if not has_deterministic_evidence_context:
        wiki_fulltext_context = build_wiki_fulltext_fallback_context(scoped_message, scoped_context)
        if wiki_fulltext_context:
            blocks.append(wiki_fulltext_context)
            has_deterministic_evidence_context = True
    if not has_deterministic_evidence_context:
        postgres_context = build_postgres_fallback_context(scoped_message, scoped_context)
        if postgres_context:
            blocks.append(postgres_context)
            has_deterministic_evidence_context = True
    if not has_deterministic_evidence_context:
        parse_only_context = build_pdf2md_parse_only_context(scoped_message, scoped_context)
        if parse_only_context:
            blocks.append(parse_only_context)
            has_deterministic_evidence_context = True
    return agent_runtime_context.build_session_contextual_input_text(
        message,
        blocks,
        chat_output_contract=CHAT_OUTPUT_CONTRACT,
        financial_calculation_runtime_contract=FINANCIAL_CALCULATION_RUNTIME_CONTRACT,
    )


def build_hermes_run_input(
    message: str,
    *,
    profile: HermesProfile,
    session_id: str,
    context: Any | None = None,
    allow_initialize: bool = False,
    attachments: Any | None = None,
    local_memory_context: str | None = None,
    image_analysis_context: str | None = None,
    use_hermes_image_fallback: bool = True,
) -> str | list[dict[str, Any]]:
    profile = _runtime_profile(profile)
    contextual_text = build_session_contextual_input(
        message,
        profile=profile,
        session_id=session_id,
        context=context,
        allow_initialize=allow_initialize,
        local_memory_context=local_memory_context,
    )
    all_attachments = _attachment_dicts(attachments)
    image_attachments = _image_attachment_dicts(all_attachments)
    document_context = _document_attachment_context(all_attachments)
    if not all_attachments:
        return contextual_text

    image_path_hints = agent_runtime_context.image_attachment_path_hints(image_attachments)
    text = agent_runtime_context.build_hermes_run_text(
        contextual_text,
        document_context=document_context,
        image_analysis_context=image_analysis_context,
        image_path_hints=image_path_hints,
    )
    if not image_attachments or not use_hermes_image_fallback:
        return text

    image_data_urls: list[str] = []
    for item in image_attachments:
        data_url = _image_attachment_data_url(item)
        if data_url:
            image_data_urls.append(data_url)

    return agent_runtime_context.build_hermes_multimodal_run_input(text, image_data_urls)


def hermes_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        READ_TIMEOUT_SECONDS,
        connect=10.0,
        read=READ_TIMEOUT_SECONDS,
    )


def stream_idle_timeout(profile: HermesProfile) -> int:
    if profile == "siq_assistant":
        return ASSISTANT_STREAM_IDLE_TIMEOUT_SECONDS
    return SPECIALIST_STREAM_IDLE_TIMEOUT_SECONDS


async def _prepare_chat_request_envelope(
    message: str,
    async_session: AsyncSession,
    *,
    session_id: str,
    context: Any | None = None,
    display_message: str | None = None,
    attachments: Any | None = None,
) -> ChatRequestEnvelope:
    all_attachments = _attachment_dicts(attachments)
    if not all_attachments and _should_reuse_recent_attachments(message):
        all_attachments = await load_recent_session_attachments(async_session, session_id)
    message_hash = _dedupe_hash_with_attachments(message, context, all_attachments)
    user_display_message = _display_message_with_attachments(
        (display_message or message).strip() or message,
        all_attachments,
    )
    return ChatRequestEnvelope(
        all_attachments=all_attachments,
        message_hash=message_hash,
        user_display_message=user_display_message,
    )


async def _load_chat_run_preflight_context(
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    attachments: list[dict[str, Any]],
    history_limit: int,
) -> ChatRunPreflightContext:
    history = await load_history(async_session, session_id, limit=history_limit)
    local_memory_context = await ensure_local_memory_context(async_session, profile, session_id)
    return ChatRunPreflightContext(
        history=history,
        local_memory_context=local_memory_context,
        attachments=attachments,
    )


async def _collect_chat_reply_impl(
    message: str,
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    context: Any | None = None,
    display_message: str | None = None,
    attachments: Any | None = None,
    history_limit: int = HISTORY_LIMIT,
) -> str:
    envelope = await _prepare_chat_request_envelope(
        message,
        async_session,
        session_id=session_id,
        context=context,
        display_message=display_message,
        attachments=attachments,
    )
    all_attachments = envelope.all_attachments
    message_hash = envelope.message_hash
    user_display_message = envelope.user_display_message
    catalog_reply = build_wiki_catalog_reply(message)
    if catalog_reply or _is_general_assistant_request(message):
        _forget_recent_completed_run(profile, session_id, message_hash)
    else:
        duplicate_reply = _recent_duplicate_reply(profile, session_id, message_hash)
        if duplicate_reply:
            return duplicate_reply

    if catalog_reply:
        await save_message(async_session, "user", user_display_message, session_id, attachments=all_attachments)
        await save_message(async_session, "assistant", catalog_reply, session_id)
        await refresh_session_memory(async_session, profile, session_id)
        _remember_completed_run(profile, session_id, message_hash, catalog_reply)
        return catalog_reply

    completed_guard_input: str | None = None
    if profile == "siq_analysis" and _should_use_analysis_completion_guard(message):
        completed_artifacts = _analysis_completed_artifacts(context)
        if completed_artifacts:
            completed_guard_input = _analysis_completion_guard_input(message, completed_artifacts)

    preflight_context = await _load_chat_run_preflight_context(
        async_session,
        session_id=session_id,
        profile=profile,
        attachments=all_attachments,
        history_limit=history_limit,
    )
    await wait_for_pdf_attachment_parses(preflight_context.attachments)
    all_attachments = _attachments_with_fresh_metadata(preflight_context.attachments)
    await save_message(async_session, "user", user_display_message, session_id, attachments=all_attachments)
    image_analysis_context, image_model_succeeded = await analyze_images_with_primary_model(
        completed_guard_input or message,
        all_attachments,
    )

    run_id = await create_run(
        build_hermes_run_input(
            completed_guard_input or message,
            profile=profile,
            session_id=session_id,
            context=context,
            allow_initialize=preflight_context.allow_initialize,
            attachments=all_attachments,
            local_memory_context=preflight_context.local_memory_context,
            image_analysis_context=image_analysis_context,
            use_hermes_image_fallback=not image_model_succeeded,
        ),
        preflight_context.history,
        profile=profile,
        session_id=hermes_runs_session_id(profile, session_id),
    )
    state = ActiveRunState(profile=profile, session_id=session_id, run_id=run_id)
    state.message_hash = message_hash
    state.original_message = message
    state.context = context
    ACTIVE_RUNS[_active_key(profile, session_id)] = state
    try:
        reply = await asyncio.wait_for(
            collect_run_result(run_id, profile=profile, timeout=hermes_timeout()),
            timeout=STREAM_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        await stop_run(run_id, profile=profile)
        reply = TIMEOUT_MESSAGE
    finally:
        ACTIVE_RUNS.pop(_active_key(profile, session_id), None)

    reply = normalize_evidence_trace_for_display(reply)
    reply = enforce_financial_evidence_contract(message, context, reply)
    reply = normalize_evidence_trace_for_display(reply)
    await save_message(async_session, "assistant", reply, session_id)
    await refresh_session_memory(async_session, profile, session_id)
    _remember_completed_run(profile, session_id, message_hash, reply)
    return reply


async def collect_chat_reply(
    message: str,
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    context: Any | None = None,
    display_message: str | None = None,
    attachments: Any | None = None,
    history_limit: int = HISTORY_LIMIT,
) -> str:
    with _profile_wiki_context(profile):
        return await _collect_chat_reply_impl(
            message,
            async_session,
            session_id=session_id,
            profile=profile,
            context=context,
            display_message=display_message,
            attachments=attachments,
            history_limit=history_limit,
        )


async def _collect_stream_run(
    state: ActiveRunState,
    done_payload_factory: Callable[[str], Awaitable[dict]] | None,
) -> None:
    full_reply = ""
    failed = False
    loop_detected = False
    idle_timed_out = False
    try:
        await _append_progress_event(
            state,
            _progress_payload(
                status="running",
                title="任务已启动",
                detail="正在连接智能体并准备执行",
                current=0,
                total=1,
            ),
        )
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            event_stream = stream_run(state.run_id, profile=state.profile, timeout=hermes_timeout()).__aiter__()
            while True:
                try:
                    ev = await asyncio.wait_for(
                        event_stream.__anext__(),
                        timeout=stream_idle_timeout(state.profile),
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    idle_timed_out = True
                    raise
                if ev.type == "delta":
                    full_reply += ev.text
                    state.tool_events_since_delta = 0
                    state.consecutive_same_tool_calls = 0
                    await _append_state_event(state, "delta", {"content": ev.text})
                    progress = _extract_progress_from_text(full_reply)
                    if progress:
                        await _append_progress_event(state, progress)
                    text_loop = _detect_stream_output_loop(state.profile, full_reply)
                    if text_loop:
                        loop_detected = True
                        failed = True
                        state.stop_requested = True
                        try:
                            await stop_run(state.run_id, profile=state.profile)
                        except Exception:
                            pass
                        loop_delta = (
                            f"\n\n{OUTPUT_LOOP_STOP_MESSAGE}\n\n"
                            f"循环样本：{text_loop['sample']}\n"
                            f"重复状态行：{text_loop['repeated_lines']}，"
                            f"不同状态行：{text_loop['unique_lines']}"
                        )
                        full_reply = f"{full_reply}{loop_delta}"
                        await _append_progress_event(
                            state,
                            _progress_payload(
                                status="error",
                                title="检测到重复输出",
                                detail=(
                                    f"智能体反复输出“{text_loop['sample']}”，"
                                    "已自动停止本次运行"
                                ),
                                source="runtime",
                            ),
                        )
                        await _append_state_event(state, "delta", {"content": loop_delta})
                        await _append_state_event(
                            state,
                            "error",
                            {
                                "message": OUTPUT_LOOP_STOP_MESSAGE,
                                "reason": text_loop["reason"],
                                "sample": text_loop["sample"],
                            },
                        )
                        break
                elif ev.type == "tool.started":
                    tool_label = ev.tool or "工具"
                    preview = ev.preview or ""
                    display_tool_label = _display_tool_label(tool_label, preview)
                    tool_signature = _hash_text(f"{tool_label}\n{preview}")
                    state.last_tool_started_signature = tool_signature
                    state.tool_events_since_delta += 1
                    if tool_signature == state.last_tool_signature:
                        state.consecutive_same_tool_calls += 1
                    else:
                        state.consecutive_same_tool_calls = 1
                        state.last_tool_signature = tool_signature
                        state.last_tool_label = tool_label
                        state.last_tool_preview = preview[:220] if preview else ""
                    await _append_progress_event(
                        state,
	                        _progress_payload(
	                            status="running",
	                            title=f"正在执行 {display_tool_label}",
	                            detail=preview[:180] if preview else "智能体正在调用工具",
	                            source="tool",
	                            tool=display_tool_label,
	                        ),
	                    )
                    await _append_state_event(
                        state,
                        "tool",
                        {"status": "started", "tool": ev.tool, "preview": ev.preview},
                    )
                    if (
                        state.consecutive_same_tool_calls >= REPEATED_TOOL_CALL_LIMIT
                        and state.tool_events_since_delta >= REPEATED_TOOL_CALL_LIMIT
                    ):
                        failed = True
                        state.stop_requested = True
                        try:
                            await stop_run(state.run_id, profile=state.profile)
                        except Exception:
                            pass
                        repeated_delta = (
                            f"\n\n{REPEATED_TOOL_CALL_STOP_MESSAGE}\n\n"
                            f"重复工具：{tool_label}\n"
                            f"重复次数：{state.consecutive_same_tool_calls}\n"
                            f"工具输入预览：{state.last_tool_preview or '未返回'}"
                        )
                        full_reply = f"{full_reply}{repeated_delta}" if full_reply else repeated_delta.strip()
                        await _append_progress_event(
                            state,
                            _progress_payload(
                                status="error",
                                title="检测到工具调用循环",
                                detail=f"{tool_label} 连续重复调用 {state.consecutive_same_tool_calls} 次，已自动停止",
                                source="runtime",
                                tool=tool_label,
                            ),
                        )
                        await _append_state_event(state, "delta", {"content": repeated_delta})
                        await _append_state_event(
                            state,
                            "error",
                            {
                                "message": REPEATED_TOOL_CALL_STOP_MESSAGE,
                                "reason": "repeated_tool_calls_without_delta",
                                "tool": tool_label,
                                "count": state.consecutive_same_tool_calls,
                            },
                        )
                        break
                elif ev.type == "tool.completed":
                    state.tool_events_since_delta += 1
                    tool_label = ev.tool or "工具"
                    display_tool_label = _display_tool_label(tool_label, state.last_tool_preview)
                    tool_signature = state.last_tool_started_signature or _hash_text(tool_label)
                    if ev.error:
                        if tool_signature == state.last_tool_error_signature:
                            state.consecutive_tool_errors += 1
                        else:
                            state.consecutive_tool_errors = 1
                            state.last_tool_error_signature = tool_signature
                        state.total_tool_errors += 1
                        state.last_tool_error_tool = tool_label
                    else:
                        state.consecutive_tool_errors = 0
                        state.last_tool_error_signature = None
                    await _append_progress_event(
                        state,
	                        _progress_payload(
	                            status="error" if ev.error else "running",
	                            title=f"{display_tool_label} 执行{'异常' if ev.error else '完成'}",
	                            detail=f"耗时 {ev.duration:.1f}s" if isinstance(ev.duration, (int, float)) else None,
	                            source="tool",
	                            tool=display_tool_label,
	                        ),
	                    )
                    await _append_state_event(
                        state,
                        "tool",
                        {
                            "status": "completed",
                            "tool": ev.tool,
                            "duration": ev.duration,
                            "error": ev.error,
                        },
                    )
                    if ev.error and state.consecutive_tool_errors >= CONSECUTIVE_TOOL_ERROR_LIMIT:
                        failed = True
                        state.stop_requested = True
                        try:
                            await stop_run(state.run_id, profile=state.profile)
                        except Exception:
                            pass
                        failure_delta = (
                            f"\n\n{TOOL_FAILURE_STOP_MESSAGE}\n\n"
                            f"连续失败工具：{tool_label}\n"
                            f"连续失败次数：{state.consecutive_tool_errors}"
                        )
                        full_reply = f"{full_reply}{failure_delta}" if full_reply else failure_delta.strip()
                        await _append_progress_event(
                            state,
                            _progress_payload(
                                status="error",
                                title="检测到工具错误循环",
                                detail=f"{tool_label} 连续失败 {state.consecutive_tool_errors} 次，已自动停止",
                                source="runtime",
                                tool=tool_label,
                            ),
                        )
                        await _append_state_event(state, "delta", {"content": failure_delta})
                        await _append_state_event(
                            state,
                            "error",
                            {
                                "message": TOOL_FAILURE_STOP_MESSAGE,
                                "reason": "consecutive_tool_errors",
                                "tool": tool_label,
                                "count": state.consecutive_tool_errors,
                            },
                        )
                        break
                elif ev.type == "reasoning":
                    await _append_reasoning_active_run(state, ev.text)
                elif ev.type == "done":
                    if loop_detected:
                        break
                    if ev.text and not full_reply:
                        full_reply = ev.text
                        await _append_state_event(state, "delta", {"content": ev.text})
                    elif ev.text and ev.text.startswith(full_reply):
                        suffix = ev.text[len(full_reply):]
                        if suffix:
                            full_reply = ev.text
                            await _append_state_event(state, "delta", {"content": suffix})
                    break
                elif ev.type in {"failed", "cancelled"}:
                    failed = True
                    status_message = (
                        RUN_FAILED_MESSAGE
                        if ev.type == "failed"
                        else (STOPPED_MESSAGE if state.user_stop_requested else RUN_CANCELLED_MESSAGE)
                    )
                    detail = _trim_tool_preview(ev.text, 600)
                    if ev.type == "cancelled" and state.user_stop_requested:
                        full_reply = status_message
                        await _append_state_event(state, "replace", {"content": status_message})
                    else:
                        failure_delta = f"\n\n{status_message}"
                        if detail:
                            failure_delta = f"{failure_delta}\n\n{detail}"
                        full_reply = f"{full_reply}{failure_delta}" if full_reply else failure_delta.strip()
                        await _append_state_event(state, "delta", {"content": failure_delta})
                    await _append_progress_event(
                        state,
                        _progress_payload(
                            status="error" if ev.type == "failed" else "stopped",
                            title="任务失败" if ev.type == "failed" else "任务已取消",
                            detail=detail or status_message,
                            source="runtime",
                        ),
                    )
                    await _append_state_event(
                        state,
                        "error",
                        {"message": status_message, "reason": f"run_{ev.type}", "detail": detail},
                    )
                    break
    except asyncio.TimeoutError:
        try:
            await stop_run(state.run_id, profile=state.profile)
        except Exception:
            pass
        timeout_message = IDLE_TIMEOUT_MESSAGE if idle_timed_out else TIMEOUT_MESSAGE
        timeout_delta = f"\n\n{timeout_message}" if full_reply else timeout_message
        full_reply = f"{full_reply}{timeout_delta}" if full_reply else timeout_delta
        await _append_progress_event(
            state,
            _progress_payload(
                status="error",
                title="任务超时",
                detail=timeout_message,
                source="runtime",
            ),
        )
        await _append_state_event(state, "delta", {"content": timeout_delta})
    except httpx.TimeoutException:
        try:
            await stop_run(state.run_id, profile=state.profile)
        except Exception:
            pass
        timeout_delta = f"\n\n{TIMEOUT_MESSAGE}" if full_reply else TIMEOUT_MESSAGE
        full_reply = f"{full_reply}{timeout_delta}" if full_reply else timeout_delta
        await _append_progress_event(
            state,
            _progress_payload(
                status="error",
                title="任务超时",
                detail=TIMEOUT_MESSAGE,
                source="runtime",
            ),
        )
        await _append_state_event(state, "delta", {"content": timeout_delta})
    except Exception as exc:
        failed = True
        error_text = f"\n\n[错误] {exc}"
        full_reply = f"{full_reply}{error_text}" if full_reply else error_text.strip()
        await _append_progress_event(
            state,
            _progress_payload(
                status="error",
                title="任务异常",
                detail=str(exc),
                source="runtime",
            ),
        )
        await _append_state_event(state, "delta", {"content": error_text})
        await _append_state_event(state, "error", {"message": str(exc)})
    finally:
        try:
            if state.user_stop_requested and full_reply != STOPPED_MESSAGE:
                full_reply = STOPPED_MESSAGE
                await _append_state_event(state, "replace", {"content": STOPPED_MESSAGE})
            if state.user_stop_requested and not full_reply:
                full_reply = STOPPED_MESSAGE
                await _append_progress_event(
                    state,
                    _progress_payload(
                        status="stopped",
                        title="任务已停止",
                        detail=STOPPED_MESSAGE,
                        source="runtime",
                    ),
                )
                await _append_state_event(state, "delta", {"content": STOPPED_MESSAGE})

            if full_reply:
                if failed or _is_loop_polluted_assistant_message(full_reply):
                    reply = _failed_run_reply_for_history(full_reply)
                else:
                    reply = normalize_evidence_trace_for_display(full_reply)
                    reply = enforce_financial_evidence_contract(
                        state.original_message or "",
                        state.context,
                        reply,
                    )
                    reply = normalize_evidence_trace_for_display(reply)

                if reply != full_reply and not failed:
                    full_reply = reply
                    await _append_state_event(state, "replace", {"content": reply})
                await save_message_in_background("assistant", reply, state.session_id, profile=state.profile)
                _remember_completed_run(state.profile, state.session_id, state.message_hash, reply)

            if not failed and not state.user_stop_requested:
                try:
                    done_payload = await done_payload_factory(full_reply) if done_payload_factory else {"new_achievements": []}
                except Exception as exc:
                    done_payload = {"new_achievements": [], "warning": str(exc)}
                if full_reply:
                    done_payload = {**done_payload, "content": full_reply}
                await _append_completed_active_run(state, done_payload)
            elif state.user_stop_requested:
                await _append_user_stopped_active_run(state, STOPPED_MESSAGE)
        finally:
            _clear_active_run(state)


async def _start_streaming_chat_run(
    *,
    profile: HermesProfile,
    session_id: str,
    run_id: str,
    message_hash: str | None,
    message: str,
    context: Any | None,
    done_payload_factory: Callable[[str], Awaitable[dict]] | None,
) -> ActiveRunState:
    state = ActiveRunState(profile=profile, session_id=session_id, run_id=run_id)
    state.message_hash = message_hash
    state.original_message = message
    state.context = context
    ACTIVE_RUNS[_active_key(profile, session_id)] = state
    await _append_state_event(state, "run", {"run_id": run_id, "session_id": session_id})
    state.task = asyncio.create_task(_collect_stream_run(state, done_payload_factory))
    return state


async def _stream_chat_reply_impl(
    message: str,
    request: Request,
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    context: Any | None = None,
    display_message: str | None = None,
    attachments: Any | None = None,
    history_limit: int = HISTORY_LIMIT,
    done_payload_factory: Callable[[str], Awaitable[dict]] | None = None,
) -> AsyncGenerator[dict, None]:
    envelope = await _prepare_chat_request_envelope(
        message,
        async_session,
        session_id=session_id,
        context=context,
        display_message=display_message,
        attachments=attachments,
    )
    all_attachments = envelope.all_attachments
    message_hash = envelope.message_hash
    user_display_message = envelope.user_display_message

    if has_active_run(profile, session_id):
        async for event in stream_active_run_events(
            request,
            profile=profile,
            session_id=session_id,
        ):
            yield event
        return

    catalog_reply = build_wiki_catalog_reply(message)
    if catalog_reply or _is_general_assistant_request(message):
        _forget_recent_completed_run(profile, session_id, message_hash)
    else:
        duplicate_reply = _recent_duplicate_reply(profile, session_id, message_hash)
        if duplicate_reply:
            yield {"event": "delta", "data": json.dumps({"content": duplicate_reply}, ensure_ascii=False)}
            yield {
                "event": "done",
                "data": json.dumps(
                    {"new_achievements": [], "deduped": True, "content": duplicate_reply},
                    ensure_ascii=False,
                ),
            }
            return

    if catalog_reply:
        await save_message(async_session, "user", user_display_message, session_id, attachments=all_attachments)
        await save_message(async_session, "assistant", catalog_reply, session_id)
        await refresh_session_memory(async_session, profile, session_id)
        _remember_completed_run(profile, session_id, message_hash, catalog_reply)
        yield {"event": "delta", "data": json.dumps({"content": catalog_reply}, ensure_ascii=False)}
        yield {
            "event": "done",
            "data": json.dumps(
                {"new_achievements": [], "catalog": True, "content": catalog_reply},
                ensure_ascii=False,
            ),
        }
        return

    completed_guard_input: str | None = None
    completed_guard_active = False
    if profile == "siq_analysis" and _should_use_analysis_completion_guard(message):
        completed_artifacts = _analysis_completed_artifacts(context)
        if completed_artifacts:
            completed_guard_active = True
            completed_guard_input = _analysis_completion_guard_input(message, completed_artifacts)

    preflight_context = await _load_chat_run_preflight_context(
        async_session,
        session_id=session_id,
        profile=profile,
        attachments=all_attachments,
        history_limit=history_limit,
    )
    all_attachments = preflight_context.attachments
    if _pdf_attachment_parse_dirs(all_attachments):
        yield {
            "event": "progress",
            "data": json.dumps(
                _progress_payload(
                    status="running",
                    title="正在等待 PDF 解析",
                    detail="聊天附件只走 MinerU 直连解析，不进入财报解析前端队列；正在等待独立解析产物写入本地后再启动智能体。",
                    source="runtime",
                ),
                ensure_ascii=False,
            ),
        }
        await wait_for_pdf_attachment_parses(all_attachments)
        all_attachments = _attachments_with_fresh_metadata(all_attachments)
    await save_message(async_session, "user", user_display_message, session_id, attachments=all_attachments)
    image_analysis_context, image_model_succeeded = await analyze_images_with_primary_model(
        completed_guard_input or message,
        all_attachments,
    )

    run_id = await create_run(
        build_hermes_run_input(
            completed_guard_input or message,
            profile=profile,
            session_id=session_id,
            context=context,
            allow_initialize=preflight_context.allow_initialize,
            attachments=all_attachments,
            local_memory_context=preflight_context.local_memory_context,
            image_analysis_context=image_analysis_context,
            use_hermes_image_fallback=not image_model_succeeded,
        ),
        preflight_context.history,
        profile=profile,
        session_id=hermes_runs_session_id(profile, session_id),
    )
    async def guarded_done_payload(reply: str) -> dict:
        if completed_guard_active:
            return {"new_achievements": [], "stage": "already_completed_llm_reply", "deduped": True}
        return await done_payload_factory(reply) if done_payload_factory else {"new_achievements": []}

    await _start_streaming_chat_run(
        profile=profile,
        session_id=session_id,
        run_id=run_id,
        message_hash=message_hash,
        message=message,
        context=context,
        done_payload_factory=guarded_done_payload,
    )

    async for event in stream_active_run_events(
        request,
        profile=profile,
        session_id=session_id,
    ):
        yield event


async def stream_chat_reply(
    message: str,
    request: Request,
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    context: Any | None = None,
    display_message: str | None = None,
    attachments: Any | None = None,
    history_limit: int = HISTORY_LIMIT,
    done_payload_factory: Callable[[str], Awaitable[dict]] | None = None,
) -> AsyncGenerator[dict, None]:
    with _profile_wiki_context(profile):
        async for event in _stream_chat_reply_impl(
            message,
            request,
            async_session,
            session_id=session_id,
            profile=profile,
            context=context,
            display_message=display_message,
            attachments=attachments,
            history_limit=history_limit,
            done_payload_factory=done_payload_factory,
        ):
            yield event


async def stop_active_run(profile: HermesProfile, session_id: str) -> dict:
    return await _streaming_stop_active_run(
        profile,
        session_id,
        stop_run_call=stop_run,
        stopped_message=STOPPED_MESSAGE,
        orphaned_run_message=ORPHANED_RUN_MESSAGE,
    )
