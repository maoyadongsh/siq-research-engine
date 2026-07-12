#!/usr/bin/env python3
"""
Import pdf2md document_full.json artifacts into PostgreSQL.

Usage:
  export DATABASE_URL='postgresql://postgres:password@127.0.0.1:15432/siq'
  python import_document_full_to_postgres.py /path/to/document_full.json
  python import_document_full_to_postgres.py /path/to/results_dir --recursive
  python import_document_full_to_postgres.py /path/to/document_full.json --ddl
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("SIQ_DATA_ROOT", REPO_ROOT / "data")).expanduser()
EXTERNAL_ASSETS_ROOT = REPO_ROOT / "_external_assets"


def _first_existing_path(*paths: str | Path, marker: str | Path | None = None, default: str | Path) -> Path:
    for path in paths:
        candidate = Path(path).expanduser()
        probe = candidate / marker if marker else candidate
        if probe.exists():
            return candidate
    return Path(default).expanduser()


DEFAULT_WIKI_ROOT = _first_existing_path(
    os.environ.get("SIQ_WIKI_ROOT", ""),
    os.environ.get("WIKI_ROOT", ""),
    DATA_ROOT / "wiki",
    EXTERNAL_ASSETS_ROOT / "wiki" / "wiki",
    marker="companies",
    default=DATA_ROOT / "wiki",
)
WIKISET_DIR = Path(
    os.environ.get("SIQ_WIKISET_ROOT")
    or os.environ.get("WIKISET_ROOT")
    or os.environ.get("SIQ_WIKISET_ROOT")
    or REPO_ROOT / "scripts" / "wiki" / "wikiset"
).expanduser()
if str(WIKISET_DIR) not in sys.path:
    sys.path.insert(0, str(WIKISET_DIR))

import company_identity as identity_rules

# Import stock name -> code mapping from companion module
_BASE_DIR = Path(__file__).resolve().parents[0]
_sys_path_inserted = False
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))
    _sys_path_inserted = True

try:
    from stock_name_to_code import code_to_name_detail, infer_exchange_from_code, name_to_code_detail
except ImportError:
    # Fallback if stock_name_to_code.py is not available. Never do network lookup here.
    def infer_exchange_from_code(code):
        if not code:
            return None
        if str(code).startswith("6"):
            return "SSE"
        if str(code).startswith(("0", "3")):
            return "SZSE"
        if str(code).startswith("8"):
            return "BSE"
        return None

    def name_to_code_detail(name):
        return {"stock_code": None, "exchange": None, "matched_name": None, "source": None}

    def code_to_name_detail(code):
        return {"stock_name": None, "stock_code": code, "exchange": infer_exchange_from_code(code), "source": None}
finally:
    if _sys_path_inserted:
        try:
            sys.path.remove(str(_BASE_DIR))
        except ValueError:
            pass

try:
    import psycopg
    from psycopg.types.json import Jsonb

    DRIVER = "psycopg3"
except ImportError:  # pragma: no cover - depends on deployment env
    psycopg = None
    Jsonb = None
    DRIVER = ""

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - depends on deployment env
    psycopg2 = None


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = Path(
    os.environ.get("SIQ_PDF_RESULTS_ROOT")
    or os.environ.get("PDF_RESULTS_ROOT")
    or os.environ.get("SIQ_PDF_RESULTS_ROOT")
    or DATA_ROOT / "pdf-parser" / "results"
).expanduser()
DEFAULT_WIKI_COMPANIES_DIR = Path(
    os.environ.get("SIQ_WIKI_COMPANIES_DIR")
    or os.environ.get("WIKI_COMPANIES_DIR")
    or os.environ.get("SIQ_WIKI_COMPANIES_DIR")
    or DEFAULT_WIKI_ROOT / "companies"
).expanduser()

# ---------------------------------------------------------------------------
# Canonical name fallback: item_name -> canonical_name
# Covers the ~70% of items missing canonical_name in source JSON.
# Merged from: existing DB data + common PRC GAAP item names.
# ---------------------------------------------------------------------------
_CANONICAL_FALLBACK: dict[str, str] = {
    # --- Income statement ---
    "营业收入": "operating_revenue",
    "营业总收入": "total_operating_revenue",
    "营业成本": "operating_cost",
    "税金及附加": "tax_surcharges",
    "销售费用": "selling_expenses",
    "管理费用": "administrative_expenses",
    "研发费用": "research_development_expenses",
    "财务费用": "financial_expenses",
    "利息费用": "interest_expense",
    "利息收入": "interest_income",
    "利息净收入": "net_interest_income",
    "手续费及佣金净收入": "net_fees_and_commissions_income",
    "资产减值损失": "asset_impairment_loss",
    "信用减值损失": "credit_impairment_loss",
    "资产处置收益": "asset_disposal_gain",
    "其他收益": "other_income",
    "营业利润": "operating_profit",
    "营业外收入": "non_operating_income",
    "营业外支出": "non_operating_expenses",
    "利润总额": "total_profit",
    "所得税费用": "income_tax_expense",
    "持续经营净利润": "net_profit_continuing_ops",
    "终止经营净利润": "net_profit_discontinued_ops",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "parent_net_profit",
    "归属于母公司所有者的净利润": "parent_net_profit",
    "少数股东损益": "minority_profit_loss",
    "基本每股收益": "basic_eps",
    "稀释每股收益": "diluted_eps",
    "其他综合收益": "other_comprehensive_income",
    "综合收益总额": "total_comprehensive_income",
    "归属于母公司股东的综合收益总额": "parent_total_comprehensive_income",
    "归属于少数股东的综合收益总额": "minority_total_comprehensive_income",

    # --- Balance sheet ---
    "货币资金": "monetary_funds",
    "交易性金融资产": "trading_financial_assets",
    "衍生金融资产": "derivative_financial_assets",
    "应收票据": "notes_receivable",
    "应收账款": "accounts_receivable",
    "应收款项融资": "receivables_financing",
    "预付款项": "prepayments",
    "其他应收款": "other_receivables",
    "应收利息": "interest_receivable",
    "应收股利": "dividend_receivable",
    "存货": "inventories",
    "合同资产": "contract_assets",
    "持有待售资产": "assets_held_for_sale",
    "一年内到期的非流动资产": "non_current_assets_due_within_one_year",
    "其他流动资产": "other_current_assets",
    "流动资产合计": "current_assets",
    "长期股权投资": "long_term_equity_investment",
    "其他权益工具投资": "equity_instrument_investments",
    "其他债权投资": "debt_instrument_investments",
    "固定资产": "fixed_assets",
    "在建工程": "construction_in_progress",
    "生产性生物资产": "productive_biological_assets",
    "油气资产": "oil_and_gas_assets",
    "使用权资产": "right_of_use_assets",
    "无形资产": "intangible_assets",
    "开发支出": "development_expenditure",
    "商誉": "goodwill",
    "长期待摊费用": "long_term_deferred_expenses",
    "递延所得税资产": "deferred_tax_assets",
    "其他非流动资产": "other_non_current_assets",
    "非流动资产合计": "non_current_assets",
    "资产总计": "total_assets",
    "短期借款": "short_term_borrowings",
    "应付票据": "notes_payable",
    "应付账款": "accounts_payable",
    "预收款项": "advances_from_customers",
    "合同负债": "contract_liabilities",
    "应付职工薪酬": "employee_benefits_payable",
    "应交税费": "taxes_payable",
    "其他应付款": "other_payables",
    "应付利息": "interest_payable",
    "应付股利": "dividends_payable",
    "持有待售负债": "liabilities_held_for_sale",
    "一年内到期的非流动负债": "non_current_liabilities_due_within_one_year",
    "其他流动负债": "other_current_liabilities",
    "流动负债合计": "current_liabilities",
    "长期借款": "long_term_borrowings",
    "应付债券": "bonds_payable",
    "租赁负债": "lease_liabilities",
    "长期应付款": "long_term_payables",
    "长期应付职工薪酬": "long_term_employee_benefits_payable",
    "预计负债": "provisions",
    "递延所得税负债": "deferred_tax_liabilities",
    "递延收益": "deferred_income",
    "其他非流动负债": "other_non_current_liabilities",
    "非流动负债合计": "non_current_liabilities",
    "负债合计": "total_liabilities",
    "实收资本（或股本）": "paid_in_capital",
    "股本": "share_capital",
    "其他综合收益": "other_comprehensive_income",
    "专项储备": "special_reserves",
    "盈余公积": "surplus_reserves",
    "未分配利润": "retained_earnings",
    "归属于母公司所有者权益合计": "equity_attributable_parent",
    "所有者权益（或股东权益）合计": "total_equity",
    "负债和所有者权益（或股东权益）总计": "total_liabilities_and_equity",

    # --- Cash flow statement ---
    "销售商品、提供劳务收到的现金": "cash_from_sales",
    "客户存款和同业存放款项净增加额": "net_increase_customer_deposits",
    "向中央银行借款净增加额": "net_increase_borrowings_from_central_bank",
    "向其他金融机构拆入资金净增加额": "net_increase_borrowings_from_financial_institutions",
    "收到原保险合同保费取得的现金": "cash_received_from_original_insurance_premiums",
    "收到再保业务现金净额": "net_cash_from_reinsurance",
    "保户储金及投资款净增加额": "net_increase_policyholders_savings_investment",
    "收取利息、手续费及佣金的现金": "cash_received_interest_fees_commissions",
    "拆入资金净增加额": "net_increase_interbank_borrowing",
    "回购业务资金净增加额": "net_increase_repurchase_funds",
    "收到税费返还": "tax_refunds_received",
    "收到其他与经营活动有关的现金": "other_cash_received_from_operating",
    "经营活动现金流入小计": "operating_cash_inflow_total",
    "购买商品、接受劳务支付的现金": "cash_paid_to_suppliers",
    "客户贷款及垫款净增加额": "net_increase_customer_loans_advances",
    "存放中央银行和同业款项净增加额": "net_increase_deposits_central_bank_interbank",
    "支付原保险合同赔付款项的现金": "cash_paid_original_insurance_claims",
    "支付再保业务现金净额": "net_cash_reinsurance_claim",
    "支付手续费及佣金的现金": "cash_paid_fees_commissions",
    "支付保单红利的现金": "cash_paid_policy_dividends",
    "支付给职工以及为职工支付的现金": "cash_paid_to_employees",
    "支付的各项税费": "taxes_paid",
    "支付其他与经营活动有关的现金": "other_cash_paid_operating",
    "经营活动现金流出小计": "operating_cash_outflow_total",
    "经营活动产生的现金流量净额": "operating_cash_flow_net",
    "收回投资收到的现金": "cash_received_from_investment_recovery",
    "取得投资收益收到的现金": "cash_received_from_investment_return",
    "处置固定资产、无形资产和其他长期资产收回的现金净额": "net_cash_from_disposal_long_term_assets",
    "处置子公司及其他营业单位收到的现金净额": "net_cash_from_disposal_subsidiary",
    "收到其他与投资活动有关的现金": "other_cash_received_investment",
    "投资活动现金流入小计": "investing_cash_inflow_total",
    "购建固定资产、无形资产和其他长期资产支付的现金": "cash_paid_acquire_long_term_assets",
    "投资支付的现金": "cash_paid_investment",
    "质押贷款净增加额": "net_increase_pledged_loans",
    "取得子公司及其他营业单位支付的现金净额": "net_cash_paid_acquire_subsidiary",
    "支付其他与投资活动有关的现金": "other_cash_paid_investment",
    "投资活动现金流出小计": "investing_cash_outflow_total",
    "投资活动产生的现金流量净额": "investing_cash_flow_net",
    "吸收投资收到的现金": "cash_received_from_investment",
    "其中：子公司吸收少数股东投资收到的现金": "cash_received_minority_investment",
    "取得借款收到的现金": "cash_received_from_borrowings",
    "收到其他与筹资活动有关的现金": "other_cash_received_financing",
    "筹资活动现金流入小计": "financing_cash_inflow_total",
    "偿还债务支付的现金": "cash_paid_debt_repayment",
    "分配股利、利润或偿付利息支付的现金": "cash_paid_dividends_interest",
    "其中：子公司支付给少数股东的股利、利润": "dividends_paid_minority",
    "支付其他与筹资活动有关的现金": "other_cash_paid_financing",
    "筹资活动现金流出小计": "financing_cash_outflow_total",
    "筹资活动产生的现金流量净额": "financing_cash_flow_net",
    "汇率变动对现金及现金等价物的影响": "fx_effect_cash",
    "现金及现金等价物净增加额": "cash_equivalents_net_increase",
    "年初现金及现金等价物余额": "cash_equivalents_beginning",
    "年末现金及现金等价物余额": "cash_equivalents_ending",

    # --- Key metrics ---
    "基本每股收益": "basic_eps",
    "稀释每股收益": "diluted_eps",
    "扣除非经常性损益后的净利润": "deducted_net_profit",
    "加权平均净资产收益率": "weighted_avg_roe",
    "营业收入增长率": "operating_revenue_growth_rate",
    "净利润增长率": "net_profit_growth_rate",
}
DDL_PATH = Path(os.environ.get("SIQ_DB_DDL_PATH", BASE_DIR / "ddl" / "001_create_pdf2md_schema.sql")).expanduser()
DML_PATH = Path(os.environ.get("SIQ_DB_DML_PATH", BASE_DIR / "dml" / "001_upsert_document_full.sql")).expanduser()
ENRICHED_DML_PATH = Path(
    os.environ.get("SIQ_DB_ENRICHED_DML_PATH", BASE_DIR / "dml" / "002_build_financial_items_enriched.sql")
).expanduser()


def json_value(value: Any) -> Any:
    if DRIVER == "psycopg3":
        return Jsonb(value if value is not None else {})
    if psycopg2 is not None:
        return psycopg2.extras.Json(value if value is not None else {})
    return value if value is not None else {}


def resolve_canonical_name(item: dict[str, Any]) -> str | None:
    """
    Resolve canonical_name for a financial item.
    Priority: source canonical_name > derived from item_name > None.
    """
    cn = item.get("canonical_name")
    if cn:
        return cn
    name = item.get("name", "")
    if name:
        return _CANONICAL_FALLBACK.get(name)
    return None


def connect(database_url: str):
    if isinstance(database_url, dict):
        if DRIVER == "psycopg3":
            return psycopg.connect(**database_url)
        if psycopg2 is not None:
            return psycopg2.connect(**database_url)
    if DRIVER == "psycopg3":
        return psycopg.connect(database_url)
    if psycopg2 is not None:
        return psycopg2.connect(database_url)
    raise RuntimeError("Install psycopg or psycopg2 first: pip install psycopg[binary]")


def load_connection_config(config_path: Path | None) -> dict[str, Any] | None:
    if config_path is None:
        return None
    spec = importlib.util.spec_from_file_location("pdf2md_pg_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load PostgreSQL config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = getattr(module, "PG_CONFIG", None)
    if not isinstance(config, dict):
        raise RuntimeError(f"{config_path} does not define PG_CONFIG")

    global psycopg, Jsonb, DRIVER
    if DRIVER != "psycopg3":
        try:
            import psycopg as psycopg_import
            from psycopg.types.json import Jsonb as jsonb_import

            psycopg = psycopg_import
            Jsonb = jsonb_import
            DRIVER = "psycopg3"
        except ImportError:
            pass
    return config


def parse_sql_blocks(path: Path) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current_name = None
    current_lines: list[str] = []
    marker = re.compile(r"^\s*--\s*name:\s*([a-zA-Z0-9_]+)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = marker.match(line)
        if match:
            if current_name:
                blocks[current_name] = current_lines
            current_name = match.group(1)
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)
    if current_name:
        blocks[current_name] = current_lines
    return {name: "\n".join(lines).strip() for name, lines in blocks.items()}


def load_json_artifact(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        text = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text)
        print(
            f"warning: repaired invalid UTF-8 bytes while reading {path} "
            f"at byte {exc.start}",
            file=sys.stderr,
        )
        return data


def iso(value: Any) -> Any:
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return value


def int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def json_decimal_text(value: Any) -> str | None:
    parsed = decimal_or_none(value)
    return str(parsed) if parsed is not None else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(compact_text(item, 100) for item in value[:8])
    elif isinstance(value, dict):
        for key in ("text", "content", "title", "caption", "preview"):
            if key in value:
                value = value.get(key)
                break
        else:
            value = json.dumps(value, ensure_ascii=False)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit]


def path_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def file_ref_size(raw: dict[str, Any] | None) -> int | None:
    return int_or_none(path_payload(raw).get("size_bytes"))


def stable_id(prefix: str, *parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def safe_company_token(value: Any, fallback: str = "unknown_company") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", "", text)
    text = text.strip("._- ：:")
    return text or fallback


_A_SHARE_CODE_RE = re.compile(r"^[03689]\d{5}$")
_REPORT_FINDER_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>CN|HK|US)_"
    r"(?P<ticker>[^_]+)_"
    r"(?P<report_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})$",
)
_LEGACY_CNINFO_FILENAME_RE = re.compile(
    r"^(?P<stock_code>[03689]\d{5})_20\d{2}_(?P<stock_name>[^_]+)_"
)


def is_a_share_stock_code(value: Any) -> bool:
    return bool(_A_SHARE_CODE_RE.match(str(value or "").strip()))


def is_non_a_share_company_json(company_json: dict[str, Any]) -> bool:
    if company_json.get("synthetic_stock_code") is True:
        return True
    if str(company_json.get("identity_kind") or "").strip() in {"generic_subject", "non_a_share"}:
        return True
    route = str(company_json.get("identity_route") or "")
    if "non_a_share" in route or "generic" in route:
        return True
    stock_code = str(company_json.get("stock_code") or "").strip()
    return bool(stock_code and not is_a_share_stock_code(stock_code))


def strip_report_name_noise(value: Any) -> str:
    text = re.sub(r"\.pdf$", "", Path(str(value or "")).name, flags=re.IGNORECASE).strip()
    text = re.sub(r"[\(\[（【]\s*(?:SH|SZ|BJ)?\s*[0368]\d{5}\s*(?:\.[A-Z]{2})?\s*[\)\]）】]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!\d)(?:SH|SZ|BJ)?[03689]\d{5}(?:\.[A-Z]{2})?(?!\d)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:SSE|SZSE|BSE|SH|SZ|BJ|CN)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*20\d{2}\s*年\s*年?\s*", "", text)
    text = re.split(
        r"20\d{2}\s*年?\s*(?:年度报告全文|年度报告|年报|半年度报告|季度报告|第一季度报告|第三季度报告|报告摘要)|"
        r"年度报告全文|年度报告|年报|半年度报告|季度报告|第一季度报告|第三季度报告|报告摘要",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(r"20\d{2}[-年].*$", "", text)
    text = re.sub(r"[_\-—–]+", " ", text)
    text = re.sub(r"\s+", "", text)
    return text.strip(" _-—–：:，,；;（）()[]【】")


def parse_download_filename_identity(filename: Any) -> dict[str, Any]:
    parsed = identity_rules.parse_download_filename_identity(filename)
    if not parsed:
        return {}
    return {
        **parsed,
        "stock_code": parsed.get("stock_code") or None,
        "stock_name": parsed.get("company_short_name") or parsed.get("stock_name"),
    }


def extract_stock_name_candidate(stem: str, stock_code: str | None) -> str:
    parsed = parse_download_filename_identity(stem)
    if parsed.get("stock_name"):
        return str(parsed["stock_name"])

    parts: list[str] = []
    if stock_code:
        code_match = re.search(re.escape(stock_code), stem)
        if code_match:
            parts.extend([stem[:code_match.start()], stem[code_match.end():]])
    for sep in ("：", ":"):
        if sep in stem:
            parts.extend(stem.split(sep))
    parts.append(stem)

    for part in parts:
        candidate = strip_report_name_noise(part)
        if candidate and candidate != stock_code:
            return candidate
    return ""


def normalize_path_key(path: Any) -> str:
    if not path:
        return ""
    try:
        return str(Path(str(path)).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(path)


def normalize_filename_key(filename: Any) -> str:
    if not filename:
        return ""
    return Path(str(filename)).name.strip().casefold()


def unique_list(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def infer_company_from_filename(filename: str) -> dict[str, Any]:
    base = Path(str(filename or "")).name
    stem = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE).strip()
    parsed = parse_download_filename_identity(stem)
    stock_code = parsed.get("stock_code") or None
    code_source = f"{parsed.get('source')}_stock_code" if stock_code and parsed.get("source") else None
    if not stock_code:
        code_match = re.search(r"(?<!\d)([03689]\d{5})(?!\d)", stem)
        if code_match:
            stock_code = code_match.group(1)
            code_source = "filename_embedded_code"

    stock_name = str(parsed.get("stock_name") or "").strip() or extract_stock_name_candidate(stem, stock_code)
    matched_name = None
    name_source = parsed.get("source") or "filename"

    if not stock_code:
        resolved = name_to_code_detail(stock_name)
        stock_code = resolved.get("stock_code")
        code_source = resolved.get("source") or "unresolved"
        matched_name = resolved.get("matched_name")
        if matched_name:
            stock_name = strip_report_name_noise(matched_name)
            name_source = "name_mapping"

    if stock_code:
        code_match_detail = code_to_name_detail(stock_code)
        mapped_stock_name = strip_report_name_noise(code_match_detail.get("stock_name"))
        if mapped_stock_name:
            stock_name = mapped_stock_name
            name_source = code_match_detail.get("source") or "code_mapping"

    stock_name = stock_name or (stock_code if stock_code else stem) or "unknown_company"
    identity = identity_rules.canonicalize_identity(
        stock_code=stock_code,
        company_short_name=stock_name,
        company_full_name=stock_name,
        exchange=infer_exchange_from_code(stock_code),
    )
    stock_code = identity.stock_code or None
    stock_name = identity.company_short_name
    company_id = identity.company_id if stock_code else stable_id("co", stock_name)
    exchange = infer_exchange_from_code(stock_code)

    return {
        "company_id": company_id,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "exchange": exchange,
        "aliases": unique_list([stock_name, matched_name, stem]),
        "listing_status": "listed" if stock_code else None,
        "raw": {
            "filename": filename,
            "inferred_from": "filename",
            "stock_code_source": code_source,
            "stock_name_source": name_source,
            "matched_name": matched_name,
            "network_lookup": False,
        },
    }


_WIKI_COMPANY_INDEX_CACHE: dict[str, dict[str, Any]] = {}


def build_wiki_company_index(companies_dir: Path) -> dict[str, Any]:
    cache_key = normalize_path_key(companies_dir)
    cached = _WIKI_COMPANY_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    index: dict[str, Any] = {
        "by_task_id": {},
        "by_document_full_path": {},
        "by_filename": {},
        "by_stock_code": {},
        "by_short_name": {},
    }
    if not companies_dir.exists():
        _WIKI_COMPANY_INDEX_CACHE[cache_key] = index
        return index

    for company_json_path in sorted(companies_dir.glob("*/company.json")):
        try:
            company_json = load_json_artifact(company_json_path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: skip unreadable company.json {company_json_path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(company_json, dict):
            continue

        company_dir = company_json_path.parent
        entry = {
            "company_json": company_json,
            "company_json_path": company_json_path,
            "company_dir": company_dir,
        }
        stock_code = str(company_json.get("stock_code") or "").strip()
        if stock_code:
            index["by_stock_code"].setdefault(stock_code, entry)
        for name in unique_list([
            company_json.get("company_short_name"),
            company_json.get("stock_name"),
            company_json.get("company_full_name"),
            *(company_json.get("aliases") or []),
        ]):
            index["by_short_name"].setdefault(name, entry)

        reports = company_json.get("reports") or []
        if not isinstance(reports, list):
            reports = []
        for report in reports:
            if not isinstance(report, dict):
                continue
            report_entry = {**entry, "report": report}
            task_id = str(report.get("task_id") or "").strip()
            if task_id:
                index["by_task_id"][task_id] = report_entry

            document_full = report.get("document_full")
            if document_full:
                index["by_document_full_path"][normalize_path_key(company_dir / str(document_full))] = report_entry

            source_filename = normalize_filename_key(report.get("source_filename"))
            if source_filename:
                index["by_filename"].setdefault(source_filename, report_entry)

    _WIKI_COMPANY_INDEX_CACHE[cache_key] = index
    return index


def lookup_wiki_company(
    data: dict[str, Any],
    doc: dict[str, Any],
    json_path: Path,
    companies_dir: Path = DEFAULT_WIKI_COMPANIES_DIR,
) -> dict[str, Any] | None:
    index = build_wiki_company_index(companies_dir)

    task_id = str(doc.get("task_id") or "").strip()
    if task_id and task_id in index["by_task_id"]:
        return {**index["by_task_id"][task_id], "matched_by": "reports.task_id"}

    candidate_paths = [
        json_path,
        doc.get("document_full_path"),
        ((data.get("artifacts") or {}).get("document_full.json") or {}).get("path"),
    ]
    for path in candidate_paths:
        path_key = normalize_path_key(path)
        if path_key and path_key in index["by_document_full_path"]:
            return {**index["by_document_full_path"][path_key], "matched_by": "reports.document_full"}

    filename_key = normalize_filename_key(doc.get("filename"))
    if filename_key and filename_key in index["by_filename"]:
        return {**index["by_filename"][filename_key], "matched_by": "reports.source_filename"}

    inferred = infer_company_from_filename(doc.get("filename") or "")
    stock_code = str(inferred.get("stock_code") or "").strip()
    if stock_code and stock_code in index["by_stock_code"]:
        return {**index["by_stock_code"][stock_code], "report": None, "matched_by": "stock_code"}

    stock_name = str(inferred.get("stock_name") or "").strip()
    if stock_name and stock_name in index["by_short_name"]:
        return {**index["by_short_name"][stock_name], "report": None, "matched_by": "company_name"}

    return None


def wiki_company_params(match: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    company_json = match["company_json"]
    report = match.get("report") if isinstance(match.get("report"), dict) else None
    is_non_a_share = is_non_a_share_company_json(company_json)
    source_stock_code = str(company_json.get("stock_code") or "").strip()
    stock_code = str((None if is_non_a_share else company_json.get("stock_code")) or fallback.get("stock_code") or "").strip() or None
    fallback_stock_code = str(fallback.get("stock_code") or "").strip()
    fallback_stock_name = str(fallback.get("stock_name") or "").strip()
    short_name = (
        fallback_stock_name if fallback_stock_code and fallback_stock_code == stock_code else None
    ) or (
        company_json.get("company_short_name")
        or company_json.get("stock_name")
        or fallback_stock_name
        or company_json.get("company_full_name")
        or company_json.get("company_id")
    )
    if is_non_a_share and source_stock_code and str(short_name or "").strip() == source_stock_code:
        short_name = (
            company_json.get("company_short_name")
            or company_json.get("stock_name")
            or company_json.get("company_full_name")
            or fallback_stock_name
            or source_stock_code
        )
    if stock_code:
        code_match_detail = code_to_name_detail(stock_code)
        mapped_stock_name = strip_report_name_noise(code_match_detail.get("stock_name"))
        if mapped_stock_name:
            short_name = mapped_stock_name
    full_name = company_json.get("company_full_name")
    if full_name and str(full_name).strip() == str(company_json.get("company_short_name") or "").strip():
        full_name = short_name
    aliases = unique_list([
        short_name,
        full_name,
        company_json.get("company_id"),
        *(company_json.get("aliases") or []),
        fallback_stock_name,
    ])
    raw = {
        "source": "wiki_company_json",
        "inferred_from": "wiki_company_json",
        "matched_by": match.get("matched_by"),
        "company_json_path": str(match["company_json_path"]),
        "wiki_company_dir": str(match["company_dir"]),
        "matched_report": report,
        "company_json": company_json,
        "fallback_filename_inference": fallback.get("raw") or {},
    }
    if is_non_a_share:
        identity = identity_rules.canonicalize_identity(
            stock_code="",
            company_short_name=short_name,
            company_full_name=full_name,
            exchange=company_json.get("exchange") or company_json.get("market") or "GENERIC",
        )
        canonical_company_id = company_json.get("company_id") or identity.company_id
        normalized_stock_code = None
        normalized_exchange = company_json.get("exchange") or company_json.get("market") or "GENERIC"
        listing_status = company_json.get("listing_status") or "non_a_share"
    else:
        identity = identity_rules.canonicalize_identity(
            stock_code=stock_code,
            company_short_name=short_name,
            company_full_name=full_name,
            exchange=company_json.get("exchange") or infer_exchange_from_code(stock_code),
        )
        canonical_company_id = identity.company_id if stock_code else (
            company_json.get("company_id") or stable_id("co", stock_code or short_name)
        )
        normalized_stock_code = identity.stock_code or None
        normalized_exchange = identity.exchange
        listing_status = company_json.get("listing_status") or ("listed" if stock_code else None)
    company = {
        "company_id": canonical_company_id,
        "stock_code": normalized_stock_code,
        "stock_name": identity.company_short_name or "unknown_company",
        "exchange": normalized_exchange,
        "industry": company_json.get("industry") or company_json.get("industry_tushare"),
        "listing_status": listing_status,
        "aliases": json_value(aliases),
        "raw": json_value(raw),
        "_is_non_a_share": is_non_a_share,
        "_source_stock_code": source_stock_code,
        "_wiki_company_json": company_json,
        "_wiki_report": report,
        "_wiki_company_dir": str(match["company_dir"]),
        "_wiki_company_json_path": str(match["company_json_path"]),
        "_matched_by": match.get("matched_by"),
    }
    return company


def infer_report_period(report_kind: str | None, filename: str) -> str:
    text = f"{report_kind or ''} {filename or ''}"
    if "一季" in text or "第一季度" in text or "q1" in text.lower():
        return "Q1"
    if "半年度" in text or "半年" in text or "interim" in text.lower():
        return "H1"
    if "三季" in text or "第三季度" in text or "q3" in text.lower():
        return "Q3"
    if "年度" in text or "annual" in text.lower() or report_kind == "annual_report":
        return "FY"
    return ""


def company_params(
    data: dict[str, Any],
    doc: dict[str, Any],
    json_path: Path,
    wiki_companies_dir: Path = DEFAULT_WIKI_COMPANIES_DIR,
) -> dict[str, Any]:
    inferred = infer_company_from_filename(doc.get("filename") or "")
    wiki_match = lookup_wiki_company(data, doc, json_path, wiki_companies_dir)
    if wiki_match:
        return wiki_company_params(wiki_match, inferred)

    return {
        "company_id": inferred["company_id"],
        "stock_code": inferred["stock_code"],
        "stock_name": inferred["stock_name"],
        "exchange": inferred["exchange"],
        "industry": (data.get("financial_data") or {}).get("industry_profile", {}).get("industry") if isinstance((data.get("financial_data") or {}).get("industry_profile"), dict) else None,
        "listing_status": inferred.get("listing_status"),
        "aliases": json_value(inferred["aliases"]),
        "raw": json_value(inferred["raw"]),
    }


def non_a_share_company_params(company: dict[str, Any]) -> dict[str, Any] | None:
    if not company.get("_is_non_a_share"):
        return None
    company_json = company.get("_wiki_company_json") if isinstance(company.get("_wiki_company_json"), dict) else {}
    synthetic_code = str(company_json.get("stock_code") or company.get("_source_stock_code") or "").strip() or None
    security_code = str(company_json.get("security_code") or "").strip() or None
    market = str(company_json.get("market") or "").strip() or None
    exchange = str(company_json.get("exchange") or company.get("exchange") or "").strip() or None
    identity_kind = str(company_json.get("identity_kind") or "non_a_share").strip()
    identity_route = str(company_json.get("identity_route") or "").strip() or None
    return {
        "non_a_share_company_id": company["company_id"],
        "company_id": company["company_id"],
        "display_name": company.get("stock_name") or company_json.get("company_short_name") or company["company_id"],
        "legal_name": company_json.get("company_full_name") or company.get("stock_name"),
        "market": market,
        "exchange": exchange,
        "security_code": security_code,
        "synthetic_code": synthetic_code,
        "identity_kind": identity_kind,
        "identity_route": identity_route,
        "aliases": company["aliases"],
        "raw": company["raw"],
    }


def filing_params(data: dict[str, Any], doc: dict[str, Any], company: dict[str, Any]) -> dict[str, Any]:
    source_files = data.get("source_files") or {}
    pdf_ref = path_payload(source_files.get("pdf"))
    artifacts = data.get("artifacts") or {}
    wiki_report = company.get("_wiki_report") if isinstance(company.get("_wiki_report"), dict) else {}
    wiki_company = company.get("_wiki_company_json") if isinstance(company.get("_wiki_company_json"), dict) else {}
    report_kind = wiki_report.get("report_kind") or doc.get("report_kind")
    title = wiki_report.get("source_filename") or doc.get("filename")
    report_period = infer_report_period(report_kind, title or "")
    filing_id = stable_id(
        "filing",
        company["company_id"],
        wiki_report.get("report_id") or doc.get("report_year"),
        report_period,
        title,
    )
    report_year = int_or_none(wiki_report.get("report_year")) or doc.get("report_year")
    return {
        "filing_id": filing_id,
        "company_id": company["company_id"],
        "task_id": doc["task_id"],
        "report_year": report_year,
        "report_period": report_period,
        "report_type": report_kind or "annual_report",
        "title": title,
        "announcement_date": None,
        "source_url": pdf_ref.get("url") or (artifacts.get("document_full.json") or {}).get("url"),
        "pdf_path": pdf_ref.get("path"),
        "pdf_sha256": None,
        "is_latest": bool(wiki_report and wiki_report.get("report_id") == wiki_company.get("primary_report_id")),
        "raw": json_value({
            "source": "wiki_company_json" if wiki_report else "document_full",
            "source_files": source_files,
            "filename": doc.get("filename"),
            "wiki_report": wiki_report,
            "wiki_company_json_path": company.get("_wiki_company_json_path"),
            "matched_by": company.get("_matched_by"),
        }),
    }


def non_a_share_filing_params(company: dict[str, Any], filing: dict[str, Any]) -> dict[str, Any] | None:
    if not company.get("_is_non_a_share"):
        return None
    return {
        "non_a_share_filing_id": stable_id("non_a_share_filing", company["company_id"], filing["filing_id"]),
        "non_a_share_company_id": company["company_id"],
        "filing_id": filing["filing_id"],
        "task_id": filing["task_id"],
        "report_year": filing.get("report_year"),
        "report_period": filing.get("report_period"),
        "report_type": filing.get("report_type"),
        "title": filing.get("title"),
        "source_url": filing.get("source_url"),
        "pdf_path": filing.get("pdf_path"),
        "is_latest": filing.get("is_latest"),
        "raw": filing.get("raw"),
    }


def parse_run_params(data: dict[str, Any], doc: dict[str, Any], filing: dict[str, Any]) -> dict[str, Any]:
    financial_data = data.get("financial_data") or {}
    quality = data.get("quality_report") or {}
    parse_run_id = stable_id("run", doc["task_id"], doc.get("schema_version"), data.get("generated_at"))
    warnings = quality.get("warnings") or []
    table_count = int_or_none(quality.get("table_count")) or 0
    warning_penalty = min(len(warnings) * 2, 30)
    quality_score = max(0, min(100, 100 - warning_penalty)) if table_count else None
    return {
        "parse_run_id": parse_run_id,
        "task_id": doc["task_id"],
        "filing_id": filing["filing_id"],
        "mineru_task_id": doc.get("mineru_task_id"),
        "parser_name": "mineru",
        "parser_version": (data.get("result_payload_summary") or {}).get("version"),
        "schema_version": doc.get("schema_version"),
        "rule_version": financial_data.get("rule_version"),
        "status": doc.get("status"),
        "started_at": doc.get("created_at"),
        "completed_at": doc.get("completed_at"),
        "quality_score": decimal_or_none(quality_score),
        "quality_summary": doc.get("quality_summary"),
        "raw": json_value({"task": data.get("task") or {}, "result_payload_summary": data.get("result_payload_summary") or {}}),
    }


def existing_parse_run_id(cur, task_id: str) -> str | None:
    cur.execute("SELECT parse_run_id FROM pdf2md.parse_runs WHERE task_id = %s", (task_id,))
    row = cur.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row.get("parse_run_id")
    return row[0]


def link_params(doc: dict[str, Any], company: dict[str, Any], filing: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": doc["task_id"],
        "company_id": company["company_id"],
        "stock_code": company.get("stock_code"),
        "stock_name": company.get("stock_name"),
        "exchange": company.get("exchange"),
        "filing_id": filing["filing_id"],
        "parse_run_id": run["parse_run_id"],
        "report_year": filing.get("report_year") or doc.get("report_year"),
        "report_period": filing.get("report_period"),
        "normalized_unit": None,
        "value_scale": None,
    }


def collect_document_params(data: dict[str, Any], json_path: Path) -> dict[str, Any]:
    task = data.get("task") or {}
    source_files = data.get("source_files") or {}
    markdown = data.get("markdown") or {}
    quality = data.get("quality_report") or {}
    financial_data = data.get("financial_data") or {}
    financial_checks = data.get("financial_checks") or {}
    resources = data.get("resources") or {}
    document_ref = (data.get("artifacts") or {}).get("document_full.json") or {}
    markdown_ref = source_files.get("markdown") or {}
    complete_ref = source_files.get("complete_markdown") or {}

    task_id = task.get("task_id") or json_path.parent.name
    return {
        "task_id": task_id,
        "mineru_task_id": task.get("mineru_task_id"),
        "filename": task.get("filename") or json_path.parent.name,
        "status": task.get("status"),
        "stage": task.get("stage"),
        "created_at": iso(task.get("created_at")),
        "completed_at": iso(task.get("completed_at")),
        "generated_at": iso(data.get("generated_at")),
        "pdf_page_count": int_or_none(task.get("pdf_page_count") or quality.get("pdf_page_count")),
        "schema_version": int_or_none(data.get("schema_version")),
        "report_kind": quality.get("report_kind") or financial_data.get("report_kind"),
        "report_year": int_or_none(quality.get("report_year") or financial_data.get("report_year")),
        "submit_config": json_value(task.get("submit_config") or {}),
        "source_files": json_value(source_files),
        "result_dir": str(json_path.parent),
        "document_full_path": document_ref.get("path") or str(json_path),
        "markdown_path": markdown_ref.get("path"),
        "complete_markdown_path": complete_ref.get("path"),
        "markdown_chars": int_or_none(markdown.get("chars")),
        "markdown_line_count": int_or_none(markdown.get("line_count")),
        "financial_overall_status": financial_checks.get("overall_status") or quality.get("financial_overall_status"),
        "quality_summary": json_value({
            "table_count": quality.get("table_count"),
            "fact_table_count": quality.get("fact_table_count"),
            "dimension_table_count": quality.get("dimension_table_count"),
            "single_row_table_count": quality.get("single_row_table_count"),
            "empty_cell_count": quality.get("empty_cell_count"),
            "image_ref_count": quality.get("image_ref_count"),
            "found_sections": quality.get("found_sections"),
            "missing_sections": quality.get("missing_sections"),
            "found_financial_tables": quality.get("found_financial_tables"),
            "warnings": quality.get("warnings"),
        }),
        "financial_summary": json_value(financial_checks.get("summary") or financial_data.get("summary") or {}),
        "resources_summary": json_value({
            "images": (resources.get("images") or {}).get("summary"),
            "pdf_pages": (resources.get("pdf_pages") or {}).get("summary"),
        }),
        "raw_task": json_value(task),
        "raw_json_hash": sha256_file(json_path),
        "raw_json_size_bytes": json_path.stat().st_size,
    }


def execute_many(cur, sql: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    if DRIVER == "psycopg3":
        cur.executemany(sql, rows)
    else:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)


def execute_block(cur, sql: str, params: dict[str, Any]) -> None:
    statements = [item.strip() for item in sql.split(";") if item.strip()]
    for statement in statements:
        cur.execute(statement, params)


def execute_optional_block(cur, sql: dict[str, str], name: str, params: dict[str, Any]) -> None:
    block = sql.get(name)
    if block:
        execute_block(cur, block, params)


def artifact_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    artifacts = data.get("artifacts") or {}
    source_files = data.get("source_files") or {}
    combined = {**artifacts}
    for name, payload in source_files.items():
        combined.setdefault(f"source:{name}", payload)
    for name, raw in combined.items():
        ref = path_payload(raw)
        rows.append({
            "task_id": task_id,
            "artifact_name": name,
            "kind": ref.get("kind"),
            "path": ref.get("path"),
            "url": ref.get("url"),
            "exists": ref.get("exists"),
            "size_bytes": file_ref_size(ref),
            "mtime": iso(ref.get("mtime")),
            "sha256": None,
            "raw": json_value(ref),
        })
    return rows


def page_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    pages = (data.get("markdown") or {}).get("pages") or []
    enhanced_pages = {(item.get("page_number") or item.get("page")): item for item in ((data.get("content_list_enhanced") or {}).get("pages") or []) if isinstance(item, dict)}
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_number = int_or_none(page.get("page_number") or page.get("page"))
        if page_number is None:
            continue
        enhanced = enhanced_pages.get(page_number) or {}
        merged = {**enhanced, **page}
        rows.append({
            "task_id": task_id,
            "page_number": page_number,
            "page_index": page_number - 1,
            "markdown_start": int_or_none(page.get("start") or page.get("start_offset")),
            "markdown_end": int_or_none(page.get("end") or page.get("end_offset")),
            "block_count": int_or_none(enhanced.get("block_count") or enhanced.get("blocks")),
            "preview": compact_text(page.get("content") or page.get("preview") or enhanced.get("preview"), 800),
            "raw": json_value(merged),
        })
    if not rows:
        for page in enhanced_pages.values():
            page_number = int_or_none(page.get("page_number") or page.get("page"))
            if page_number is None:
                continue
            rows.append({
                "task_id": task_id,
                "page_number": page_number,
                "page_index": page_number - 1,
                "markdown_start": None,
                "markdown_end": None,
                "block_count": int_or_none(page.get("block_count") or page.get("blocks")),
                "preview": compact_text(page.get("preview"), 800),
                "raw": json_value(page),
            })
    return rows


def content_block_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, block in enumerate(data.get("content_list") or [], start=1):
        if not isinstance(block, dict):
            continue
        page_idx = int_or_none(block.get("page_idx"))
        block_type = block.get("type")
        rows.append({
            "task_id": task_id,
            "block_index": index,
            "block_type": block_type,
            "page_idx": page_idx,
            "page_number": page_idx + 1 if page_idx is not None else None,
            "bbox": json_value(block.get("bbox")) if block.get("bbox") is not None else None,
            "text_preview": compact_text(block.get("text") or block.get("table_caption") or block.get("image_caption"), 1000),
            "image_path": block.get("img_path") or block.get("image_path"),
            "table_html_present": bool(block.get("table_body") or block.get("table_html")),
            "raw": json_value(block),
        })
    return rows


def table_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    quality_tables = {
        item.get("table_index"): item
        for item in (data.get("quality_report") or {}).get("table_index") or []
        if isinstance(item, dict)
    }
    suspicious = {
        item.get("table_index"): item
        for item in (data.get("quality_report") or {}).get("suspicious_tables") or []
        if isinstance(item, dict)
    }
    rows = []
    for table in (data.get("content_list_enhanced") or {}).get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_index = int_or_none(table.get("table_index"))
        if table_index is None:
            continue
        q = quality_tables.get(table_index) or {}
        s = suspicious.get(table_index) or {}
        structure = table.get("structure") or {}
        raw = {**q, **table}
        rows.append({
            "task_id": task_id,
            "table_index": table_index,
            "markdown_line": int_or_none(table.get("line") or q.get("line")),
            "pdf_page_number": int_or_none(table.get("pdf_page_number") or q.get("pdf_page_number")),
            "pdf_page_index": int_or_none(table.get("pdf_page_index")),
            "bbox": json_value(table.get("bbox") or q.get("bbox") or []),
            "source": table.get("source") or q.get("pdf_page_source"),
            "confidence": table.get("confidence") or q.get("confidence"),
            "source_image_path": table.get("source_image_path"),
            "rows_count": int_or_none(table.get("rows") or q.get("rows")),
            "cells_count": int_or_none(table.get("cells") or q.get("cells")),
            "heading": q.get("heading"),
            "unit": q.get("unit"),
            "preview": compact_text(table.get("preview") or q.get("preview"), 1000),
            "report_year": int_or_none(table.get("report_year") or q.get("report_year")),
            "is_multi_level_header_candidate": structure.get("multi_level_header_candidate"),
            "is_suspicious": bool(s),
            "suspect_reasons": json_value(s.get("suspect_reasons") or q.get("suspect_reasons") or []),
            "source_caption": json_value(table.get("source_caption") or []),
            "source_footnote": json_value(table.get("source_footnote") or []),
            "structure": json_value(structure),
            "raw": json_value(raw),
        })
    return rows


def simple_index_rows(task_id: str, items: Any, text_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    if isinstance(items, dict):
        candidates = items.get("items") or items.get("definitions") or items.get("candidates") or []
    else:
        candidates = items or []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            item = {"text": str(item)}
        text = next((item.get(key) for key in text_keys if item.get(key)), "")
        rows.append({
            "task_id": task_id,
            "index": index,
            "page_number": int_or_none(item.get("page_number") or item.get("page")),
            "markdown_line": int_or_none(item.get("line") or item.get("markdown_line")),
            "text": compact_text(text, 4000),
            "plain": item,
            "raw": json_value(item),
        })
    return rows


def financial_statement_rows(task_id: str, data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    statement_rows = []
    item_rows = []
    for stmt in (data.get("financial_data") or {}).get("statements") or []:
        if not isinstance(stmt, dict):
            continue
        statement_id = stmt.get("statement_id") or f"{stmt.get('statement_type') or 'statement'}:{len(statement_rows) + 1}"
        statement_rows.append({
            "task_id": task_id,
            "statement_id": statement_id,
            "statement_type": stmt.get("statement_type"),
            "statement_name": stmt.get("statement_name"),
            "scope": stmt.get("scope"),
            "scope_name": stmt.get("scope_name"),
            "title": stmt.get("title"),
            "unit": stmt.get("unit"),
            "scale": decimal_or_none(stmt.get("scale")),
            "currency": stmt.get("currency"),
            "table_indexes": json_value(stmt.get("table_indexes") or []),
            "line_numbers": json_value(stmt.get("line_numbers") or []),
            "columns": json_value(stmt.get("columns") or []),
            "raw": json_value(stmt),
        })
        for item_index, item in enumerate(stmt.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            values = item.get("values") or {}
            raw_values = item.get("raw_values") or {}
            sources = item.get("sources") or {}
            for period_key, value in values.items():
                item_rows.append({
                    "task_id": task_id,
                    "statement_id": statement_id,
                    "item_index": item_index,
                    "period_key": str(period_key),
                    "item_name": item.get("name"),
                    "canonical_name": item.get("canonical_name"),
                    "value": decimal_or_none(value),
                    "raw_value": str(raw_values.get(period_key)) if period_key in raw_values else None,
                    "source": json_value(sources.get(period_key) or {}),
                    "raw_item": json_value(item),
                })
    return statement_rows, item_rows


def source_page_number(source: Any) -> int | None:
    if not isinstance(source, dict):
        return None
    return int_or_none(source.get("pdf_page_number") or source.get("page_number") or source.get("page"))


def source_table_index(source: Any) -> int | None:
    if not isinstance(source, dict):
        return None
    return int_or_none(source.get("table_index"))


def source_bbox(source: Any) -> Any:
    if not isinstance(source, dict) or "bbox" not in source:
        return json_value([])
    return json_value(source.get("bbox") or [])


def infer_financial_statement_unit(data: dict[str, Any]) -> str | None:
    candidates = [
        data.get("content"),
        data.get("markdown"),
        data.get("complete_markdown"),
        data.get("result_markdown"),
    ]
    text = "\n".join(str(value) for value in candidates if value)
    if not text:
        text = json.dumps(data.get("financial_data") or {}, ensure_ascii=False)
    if re.search(r"除[^。；;\n]{0,40}特别说明[^。；;\n]{0,80}人民币\s*千元", text):
        return "人民币千元"
    if re.search(r"均以人民币\s*千元为单位", text):
        return "人民币千元"
    return None


def statement_item_unit(stmt: dict[str, Any], item: dict[str, Any], fallback_unit: str | None) -> str | None:
    unit = stmt.get("unit")
    if unit:
        return unit
    name = str(item.get("name") or item.get("canonical_name") or "")
    if "每股收益" in name:
        return "元/股"
    return fallback_unit


def statement_split_rows(task_id: str, data: dict[str, Any], links: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows_by_type = {
        "balance_sheet": [],
        "income_statement": [],
        "cash_flow_statement": [],
    }
    fallback_unit = infer_financial_statement_unit(data)
    for stmt in (data.get("financial_data") or {}).get("statements") or []:
        if not isinstance(stmt, dict):
            continue
        statement_type = stmt.get("statement_type")
        if statement_type not in rows_by_type:
            continue
        statement_id = stmt.get("statement_id") or f"{statement_type}:{len(rows_by_type[statement_type]) + 1}"
        for item_index, item in enumerate(stmt.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            values = item.get("values") or {}
            raw_values = item.get("raw_values") or {}
            sources = item.get("sources") or {}
            for period_key, value in values.items():
                source = sources.get(period_key) or {}
                rows_by_type[statement_type].append({
                    "task_id": task_id,
                    "statement_id": statement_id,
                    "item_index": item_index,
                    "period_key": str(period_key),
                    "company_id": links.get("company_id"),
                    "stock_code": links.get("stock_code"),
                    "stock_name": links.get("stock_name"),
                    "exchange": links.get("exchange"),
                    "filing_id": links.get("filing_id"),
                    "parse_run_id": links.get("parse_run_id"),
                    "report_year": links.get("report_year"),
                    "report_period": links.get("report_period"),
                    "statement_name": stmt.get("statement_name"),
                    "scope": stmt.get("scope"),
                    "scope_name": stmt.get("scope_name"),
                    "item_name": item.get("name"),
                    "canonical_name": resolve_canonical_name(item),
                    "value": decimal_or_none(value),
                    "raw_value": str(raw_values.get(period_key)) if period_key in raw_values else None,
                    "unit": statement_item_unit(stmt, item, fallback_unit),
                    "currency": stmt.get("currency"),
                    "source_page_number": source_page_number(source),
                    "source_table_index": source_table_index(source),
                    "source_bbox": source_bbox(source),
                    "source": json_value(source),
                    "raw_item": json_value(item),
                })
    return rows_by_type


def metric_key(prefix: str, name: Any, existing: dict[str, Any]) -> str:
    base = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(name or "unnamed")).strip("_")
    key = base or "unnamed"
    if key not in existing:
        return key
    scoped = f"{prefix}_{key}" if prefix else key
    if scoped not in existing:
        return scoped
    index = 2
    while f"{scoped}_{index}" in existing:
        index += 1
    return f"{scoped}_{index}"


def financial_all_metrics_wide_rows(task_id: str, data: dict[str, Any], links: dict[str, Any]) -> list[dict[str, Any]]:
    by_period: dict[str, dict[str, Any]] = {}
    fallback_unit = infer_financial_statement_unit(data)

    def period_bucket(period_key: str) -> dict[str, Any]:
        if period_key not in by_period:
            by_period[period_key] = {
                "balance_sheet": {},
                "income_statement": {},
                "cash_flow_statement": {},
                "key_metrics": {},
                "raw": {"statements": [], "key_metrics": []},
            }
        return by_period[period_key]

    for stmt in (data.get("financial_data") or {}).get("statements") or []:
        if not isinstance(stmt, dict):
            continue
        statement_type = stmt.get("statement_type")
        if statement_type not in ("balance_sheet", "income_statement", "cash_flow_statement"):
            continue
        for item in stmt.get("items") or []:
            if not isinstance(item, dict):
                continue
            values = item.get("values") or {}
            raw_values = item.get("raw_values") or {}
            sources = item.get("sources") or {}
            resolved_cn = resolve_canonical_name(item)
            name = resolved_cn or item.get("name")
            for period_key, value in values.items():
                bucket = period_bucket(str(period_key))
                family = bucket[statement_type]
                key = metric_key("", name, family)
                family[key] = {
                    "value": json_decimal_text(value),
                    "raw_value": str(raw_values.get(period_key)) if period_key in raw_values else None,
                    "item_name": item.get("name"),
                    "canonical_name": resolved_cn,
                    "statement_type": statement_type,
                    "statement_id": stmt.get("statement_id"),
                    "statement_name": stmt.get("statement_name"),
                    "scope": stmt.get("scope"),
                    "unit": statement_item_unit(stmt, item, fallback_unit),
                    "currency": stmt.get("currency"),
                    "source": sources.get(period_key) or {},
                }
                bucket["raw"]["statements"].append({"statement": stmt.get("statement_id"), "item": item.get("name"), "period_key": str(period_key)})

    for metric in (data.get("financial_data") or {}).get("key_metrics") or []:
        if not isinstance(metric, dict):
            continue
        values = metric.get("values") or {}
        raw_values = metric.get("raw_values") or {}
        sources = metric.get("sources") or {}
        resolved_cn = resolve_canonical_name(metric)
        name = resolved_cn or metric.get("name")
        for period_key, value in values.items():
            bucket = period_bucket(str(period_key))
            family = bucket["key_metrics"]
            key = metric_key("", name, family)
            family[key] = {
                "value": json_decimal_text(value),
                "raw_value": str(raw_values.get(period_key)) if period_key in raw_values else None,
                "metric_name": metric.get("name"),
                "canonical_name": resolved_cn,
                "unit": metric.get("unit"),
                "source": sources.get(period_key) or {},
            }
            bucket["raw"]["key_metrics"].append({"metric": metric.get("name"), "period_key": str(period_key)})

    rows = []
    for period_key, bucket in sorted(by_period.items()):
        all_metrics = {}
        for family_name in ("balance_sheet", "income_statement", "cash_flow_statement", "key_metrics"):
            for key, payload in bucket[family_name].items():
                all_metrics[metric_key(family_name, key, all_metrics)] = payload
        rows.append({
            "task_id": task_id,
            "period_key": period_key,
            "company_id": links.get("company_id"),
            "stock_code": links.get("stock_code"),
            "stock_name": links.get("stock_name"),
            "exchange": links.get("exchange"),
            "filing_id": links.get("filing_id"),
            "parse_run_id": links.get("parse_run_id"),
            "report_year": links.get("report_year"),
            "report_period": links.get("report_period"),
            "balance_sheet": json_value(bucket["balance_sheet"]),
            "income_statement": json_value(bucket["income_statement"]),
            "cash_flow_statement": json_value(bucket["cash_flow_statement"]),
            "key_metrics": json_value(bucket["key_metrics"]),
            "all_metrics": json_value(all_metrics),
            "raw": json_value(bucket["raw"]),
        })
    return rows


def financial_metric_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for metric_index, metric in enumerate((data.get("financial_data") or {}).get("key_metrics") or [], start=1):
        if not isinstance(metric, dict):
            continue
        values = metric.get("values") or {}
        raw_values = metric.get("raw_values") or {}
        sources = metric.get("sources") or {}
        for period_key, value in values.items():
            rows.append({
                "task_id": task_id,
                "metric_index": metric_index,
                "period_key": str(period_key),
                "metric_name": metric.get("name"),
                "canonical_name": resolve_canonical_name(metric),
                "value": decimal_or_none(value),
                "raw_value": str(raw_values.get(period_key)) if period_key in raw_values else None,
                "unit": metric.get("unit"),
                "source": json_value(sources.get(period_key) or {}),
                "raw_metric": json_value(metric),
            })
    return rows


def financial_check_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, check in enumerate((data.get("financial_checks") or {}).get("checks") or [], start=1):
        if not isinstance(check, dict):
            continue
        rows.append({
            "task_id": task_id,
            "check_index": index,
            "rule_id": check.get("rule_id"),
            "rule_name": check.get("rule_name"),
            "statement_type": check.get("statement_type"),
            "scope": check.get("scope"),
            "period": check.get("period"),
            "status": check.get("status"),
            "diff": decimal_or_none(check.get("diff")),
            "tolerance": decimal_or_none(check.get("tolerance")),
            "inputs": json_value(check.get("inputs") or []),
            "left_side": json_value(check.get("left") or {}),
            "right_side": json_value(check.get("right") or {}),
            "raw": json_value(check),
        })
    return rows


def raw_payload_ref_rows(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = data.get("artifacts") or {}
    rows = []
    for payload_name, artifact_name in (
        ("document_full", "document_full.json"),
        ("content_list", "content_list.json"),
        ("middle_json", "middle.json"),
        ("model_output", "model_output.json"),
        ("result_markdown", "result.md"),
        ("complete_markdown", "result_complete.md"),
    ):
        ref = artifacts.get(artifact_name) or {}
        rows.append({
            "task_id": task_id,
            "payload_name": payload_name,
            "path": ref.get("path"),
            "url": ref.get("url"),
            "size_bytes": int_or_none(ref.get("size_bytes")),
            "sha256": None,
            "summary": json_value({
                "exists": ref.get("exists"),
                "artifact_name": artifact_name,
                "embedded_type": type(data.get(payload_name)).__name__ if payload_name in data else None,
            }),
        })
    return rows


def document_chunk_rows(task_id: str, parse_run_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    pages = page_rows(task_id, data)
    for page in pages:
        content = page.get("preview") or ""
        if not content:
            continue
        rows.append({
            "chunk_id": stable_id("chunk", task_id, "page", page.get("page_number")),
            "task_id": task_id,
            "parse_run_id": parse_run_id,
            "chunk_index": len(rows) + 1,
            "chunk_type": "page",
            "page_number": page.get("page_number"),
            "title": f"PDF_PAGE:{page.get('page_number')}",
            "content": content,
            "token_count": len(content),
            "source_block_ids": json_value([]),
            "source_table_ids": json_value([]),
            "source": json_value({"source": "markdown.pages/content_list_enhanced.pages"}),
            "embedding": json_value({}),
            "raw": page["raw"],
        })

    table_start = len(rows)
    for table_offset, table in enumerate(table_rows(task_id, data), start=1):
        content = table.get("preview") or ""
        if not content:
            continue
        table_index = table.get("table_index")
        rows.append({
            "chunk_id": stable_id("chunk", task_id, "table", table_index),
            "task_id": task_id,
            "parse_run_id": parse_run_id,
            "chunk_index": table_start + table_offset,
            "chunk_type": "table",
            "page_number": table.get("pdf_page_number"),
            "title": table.get("heading") or f"table:{table_index}",
            "content": content,
            "token_count": len(content),
            "source_block_ids": json_value([]),
            "source_table_ids": json_value([table_index]),
            "source": json_value({"table_index": table_index, "source": table.get("source")}),
            "embedding": json_value({}),
            "raw": table["raw"],
        })
    return rows


def evidence_citation_rows(task_id: str, parse_run_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for table in table_rows(task_id, data):
        table_index = table.get("table_index")
        rows.append({
            "citation_id": stable_id("cite", task_id, "table", table_index),
            "task_id": task_id,
            "parse_run_id": parse_run_id,
            "source_type": "table",
            "source_id": str(table_index),
            "page_number": table.get("pdf_page_number"),
            "bbox": table.get("bbox"),
            "quote_text": table.get("preview"),
            "path": table.get("source_image_path"),
            "url": None,
            "raw": table["raw"],
        })

    for item in financial_check_rows(task_id, data):
        rows.append({
            "citation_id": stable_id("cite", task_id, "financial_check", item.get("check_index")),
            "task_id": task_id,
            "parse_run_id": parse_run_id,
            "source_type": "financial_check",
            "source_id": str(item.get("check_index")),
            "page_number": None,
            "bbox": json_value([]),
            "quote_text": item.get("rule_name"),
            "path": None,
            "url": None,
            "raw": item["raw"],
        })
    return rows


def merge_latest_financial_rows(cur) -> None:
    split_tables = (
        "pdf2md.financial_balance_sheet_items",
        "pdf2md.financial_income_statement_items",
        "pdf2md.financial_cash_flow_statement_items",
    )
    for table in split_tables:
        cur.execute(
            f"""
            DELETE FROM {table} item
            USING (
                SELECT ctid
                FROM (
                    SELECT
                        ctid,
                        row_number() OVER (
                            PARTITION BY
                                coalesce(nullif(stock_code, ''), stock_name, company_id::text, ''),
                                coalesce(report_year::text, ''),
                                coalesce(report_period, ''),
                                coalesce(period_key, ''),
                                coalesce(statement_id, ''),
                                coalesce(scope, ''),
                                coalesce(item_name, ''),
                                coalesce(canonical_name, ''),
                                coalesce(source_table_index::text, ''),
                                coalesce(source_page_number::text, '')
                            ORDER BY imported_at DESC, task_id DESC, item_index DESC
                        ) AS rn
                    FROM {table}
                ) ranked
                WHERE rn > 1
            ) duplicate
            WHERE item.ctid = duplicate.ctid
            """
        )

    cur.execute(
        """
        DELETE FROM pdf2md.financial_all_metrics_wide wide
        USING (
            SELECT ctid
            FROM (
                SELECT
                    ctid,
                    row_number() OVER (
                        PARTITION BY
                            coalesce(nullif(stock_code, ''), stock_name, company_id::text, ''),
                            coalesce(report_year::text, ''),
                            coalesce(report_period, ''),
                            coalesce(period_key, '')
                        ORDER BY imported_at DESC, task_id DESC
                    ) AS rn
                FROM pdf2md.financial_all_metrics_wide
            ) ranked
            WHERE rn > 1
        ) duplicate
        WHERE wide.ctid = duplicate.ctid
        """
    )


def refresh_financial_items_enriched(cur) -> None:
    cur.execute(ENRICHED_DML_PATH.read_text(encoding="utf-8"))


def import_one(
    conn,
    sql: dict[str, str],
    json_path: Path,
    apply_ddl: bool = False,
    wiki_companies_dir: Path = DEFAULT_WIKI_COMPANIES_DIR,
    refresh_enriched: bool = True,
    import_legacy_chunks: bool = True,
) -> str:
    if apply_ddl:
        with conn.cursor() as cur:
            cur.execute(DDL_PATH.read_text(encoding="utf-8"))
        conn.commit()

    data = load_json_artifact(json_path)
    doc = collect_document_params(data, json_path)
    task_id = doc["task_id"]
    enhanced = data.get("content_list_enhanced") or {}
    company = company_params(data, doc, json_path, wiki_companies_dir)
    filing = filing_params(data, doc, company)
    run = parse_run_params(data, doc, filing)
    links = link_params(doc, company, filing, run)
    non_a_share_company = non_a_share_company_params(company)
    non_a_share_filing = non_a_share_filing_params(company, filing)

    with conn.cursor() as cur:
        cur.execute(sql["upsert_document"], doc)
        execute_block(cur, sql["delete_document_children"], {"task_id": task_id})
        run["parse_run_id"] = existing_parse_run_id(cur, task_id) or run["parse_run_id"]
        links["parse_run_id"] = run["parse_run_id"]
        if company.get("stock_code"):
            execute_optional_block(cur, sql, "prepare_company_identity", company)
        cur.execute(sql["upsert_company"], company)
        if non_a_share_company:
            execute_optional_block(cur, sql, "upsert_non_a_share_company", non_a_share_company)
        if company.get("stock_code"):
            execute_optional_block(cur, sql, "normalize_company_identity", company)
        execute_optional_block(cur, sql, "prepare_filing_task_rebind", filing)
        cur.execute(sql["upsert_company_filing"], filing)
        if non_a_share_filing:
            execute_optional_block(cur, sql, "upsert_non_a_share_company_filing", non_a_share_filing)
        execute_optional_block(cur, sql, "rebind_filing_links", filing)
        cur.execute(sql["upsert_parse_run"], run)
        cur.execute(sql["update_document_links"], links)

        execute_many(cur, sql["insert_artifact"], artifact_rows(task_id, data))
        execute_many(cur, sql["insert_page"], page_rows(task_id, data))
        execute_many(cur, sql["insert_content_block"], content_block_rows(task_id, data))
        execute_many(cur, sql["insert_table"], table_rows(task_id, data))

        warnings = [
            {"task_id": task_id, "warning_index": idx, "warning": str(item)}
            for idx, item in enumerate((data.get("quality_report") or {}).get("warnings") or [], start=1)
        ]
        execute_many(cur, sql["insert_quality_warning"], warnings)

        footnotes = simple_index_rows(task_id, enhanced.get("footnotes"), ("text", "content", "definition"))
        execute_many(cur, sql["insert_footnote"], [
            {
                "task_id": item["task_id"],
                "footnote_index": item["index"],
                "page_number": item["page_number"],
                "markdown_line": item["markdown_line"],
                "text": item["text"],
                "raw": item["raw"],
            }
            for item in footnotes
        ])

        toc = simple_index_rows(task_id, enhanced.get("toc"), ("title", "text", "heading"))
        execute_many(cur, sql["insert_toc_entry"], [
            {
                "task_id": item["task_id"],
                "toc_index": item["index"],
                "title": item["text"],
                "level": int_or_none(item["plain"].get("level")),
                "page_number": item["page_number"],
                "markdown_line": item["markdown_line"],
                "raw": item["raw"],
            }
            for item in toc
        ])

        note_links = []
        for index, item in enumerate(enhanced.get("financial_note_links") or [], start=1):
            if not isinstance(item, dict):
                continue
            note_links.append({
                "task_id": task_id,
                "link_index": index,
                "item_name": item.get("item_name") or item.get("name"),
                "canonical_name": item.get("canonical_name"),
                "note_title": item.get("note_title") or item.get("title"),
                "note_ref": item.get("note_ref") or item.get("ref"),
                "table_index": int_or_none(item.get("table_index")),
                "page_number": int_or_none(item.get("page_number") or item.get("page")),
                "raw": json_value(item),
            })
        execute_many(cur, sql["insert_financial_note_link"], note_links)

        statement_rows, item_rows = financial_statement_rows(task_id, data)
        execute_many(cur, sql["insert_financial_statement"], statement_rows)
        execute_many(cur, sql["insert_financial_statement_item"], item_rows)
        split_rows = statement_split_rows(task_id, data, links)
        execute_many(cur, sql["insert_balance_sheet_item"], split_rows["balance_sheet"])
        execute_many(cur, sql["insert_income_statement_item"], split_rows["income_statement"])
        execute_many(cur, sql["insert_cash_flow_statement_item"], split_rows["cash_flow_statement"])
        execute_many(cur, sql["insert_financial_key_metric"], financial_metric_rows(task_id, data))
        execute_many(cur, sql["insert_all_metrics_wide"], financial_all_metrics_wide_rows(task_id, data, links))
        execute_many(cur, sql["insert_financial_check"], financial_check_rows(task_id, data))
        execute_many(cur, sql["insert_raw_payload_ref"], raw_payload_ref_rows(task_id, data))
        cur.execute(sql["update_financial_statement_item_links"], links)
        cur.execute(sql["update_financial_key_metric_links"], links)
        if import_legacy_chunks:
            execute_many(cur, sql["insert_document_chunk"], document_chunk_rows(task_id, run["parse_run_id"], data))
        execute_many(cur, sql["insert_evidence_citation"], evidence_citation_rows(task_id, run["parse_run_id"], data))
        merge_latest_financial_rows(cur)
        if refresh_enriched:
            refresh_financial_items_enriched(cur)

    conn.commit()
    return task_id


def find_document_full_files(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]
    pattern = "**/document_full.json" if recursive else "*/document_full.json"
    return sorted(path.glob(pattern))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import document_full.json into PostgreSQL.")
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=DEFAULT_RESULTS_DIR,
        help=f"document_full.json file or results directory (default: {DEFAULT_RESULTS_DIR})",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), help="PostgreSQL connection URL")
    parser.add_argument(
        "--config-py",
        type=Path,
        default=os.environ.get("SIQ_DB_CONFIG_PY") or os.environ.get("DB_CONFIG_PY") or os.environ.get("SIQ_DB_CONFIG_PY"),
        help="Python file that defines PG_CONFIG",
    )
    parser.add_argument("--recursive", action="store_true", help="Recursively find document_full.json under a directory")
    parser.add_argument(
        "--wiki-companies-dir",
        type=Path,
        default=DEFAULT_WIKI_COMPANIES_DIR,
        help=f"wiki companies directory used as company master data (default: {DEFAULT_WIKI_COMPANIES_DIR})",
    )
    parser.add_argument("--limit", type=int, default=0, help="Import at most this many files")
    parser.add_argument("--ddl", action="store_true", help="Apply DDL before import")
    parser.add_argument("--skip-enriched", action="store_true", help="Skip refreshing the financial_items_enriched layer")
    parser.add_argument(
        "--skip-legacy-chunks",
        action="store_true",
        help="Skip pdf2md.document_chunks page/table snippets. Structured facts, pages, tables and citations are still imported.",
    )
    args = parser.parse_args()

    pg_config = load_connection_config(args.config_py)
    connection_target = pg_config or args.database_url
    if not connection_target:
        raise SystemExit("Missing --database-url, DATABASE_URL, or --config-py")

    files = find_document_full_files(args.path, args.recursive)
    if args.limit and args.limit > 0:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"No document_full.json files found under {args.path}")

    sql = parse_sql_blocks(DML_PATH)
    conn = connect(connection_target)
    try:
        if args.ddl:
            with conn.cursor() as cur:
                cur.execute(DDL_PATH.read_text(encoding="utf-8"))
            conn.commit()
        for file_path in files:
            task_id = import_one(
                conn,
                sql,
                file_path,
                apply_ddl=False,
                wiki_companies_dir=args.wiki_companies_dir,
                refresh_enriched=not args.skip_enriched,
                import_legacy_chunks=not args.skip_legacy_chunks,
            )
            print(f"imported {task_id} <- {file_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
