#!/usr/bin/env python3
"""Rebuild the annual-report wiki as a company-centric evidence base."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from company_identity import (  # noqa: E402
    canonicalize_identity,
    looks_like_report_instance_name,
    parse_download_filename_identity as parse_canonical_download_filename_identity,
    report_source_metadata,
)

VALID_CODE_PREFIXES = (
    "000", "001", "002", "003", "300", "301",
    "600", "601", "603", "605", "688", "689",
)

MANUAL_STOCK_CODE_MAPPING = {
    "02f3c3c4-8560-4c7e-bffb-45420e666e1c": ("600028", "中国石化", "中国石油化工股份有限公司", "SSE"),
    "05091917-a1f0-4439-836f-84fbec119345": ("601211", "国泰海通", "国泰海通证券股份有限公司", "SSE"),
    "13f09c94-d054-4f1a-9054-698b222c89b0": ("300014", "亿纬锂能", "惠州亿纬锂能股份有限公司", "SZSE"),
    "2061b349-b457-4506-ba6f-e34ecb12760e": ("300759", "康龙化成", "康龙化成（北京）新药技术股份有限公司", "SZSE"),
    "34753f42-870d-4a02-bd7b-f402c6bc6f69": ("601601", "中国太保", "中国太平洋保险（集团）股份有限公司", "SSE"),
    "47f769d4-c525-4138-92b7-b25836700479": ("601998", "中信银行", "中信银行股份有限公司", "SSE"),
    "4bf5a3d1-1765-4f3c-9f15-b4e87e8302e1": ("300760", "迈瑞医疗", "深圳迈瑞生物医疗电子股份有限公司", "SZSE"),
    "51328f2d-30d5-4006-93f2-7a2c3ce119a2": ("689009", "九号公司", "九号有限公司", "SSE"),
    "7fabd2f9-881b-4251-b42b-d9e8777796ad": ("600000", "浦发银行", "上海浦东发展银行股份有限公司", "SSE"),
    "86b380f5-c1ff-4bd5-b29c-9bb5fb647e2a": ("000878", "云南铜业", "云南铜业股份有限公司", "SZSE"),
    "9ffc25f1-e7e2-4bd3-922a-d4f19e44bf9b": ("601319", "中国人保", "中国人民保险集团股份有限公司", "SSE"),
    "ae99837d-87dd-494e-9b66-d5f2a11ea096": ("000810", "创维数字", "创维数字股份有限公司", "SZSE"),
    "c158a512-403a-4cc8-a120-8d8f83ff62b8": ("300446", "航天智造", "航天智造科技股份有限公司", "SZSE"),
    "d1803836-bf60-429a-bbd0-b60471d9983e": ("601066", "中信建投", "中信建投证券股份有限公司", "SSE"),
    "d776cb84-18d0-4859-9a4a-0a2d71edc37c": ("600332", "白云山", "广州白云山医药集团股份有限公司", "SSE"),
    "ece66a27-587a-4c2d-a1e9-b8d4fb1519a4": ("601601", "中国太保", "中国太平洋保险（集团）股份有限公司", "SSE"),
    "f7ee451f-8651-4e65-8ea0-078730542d95": ("601998", "中信银行", "中信银行股份有限公司", "SSE"),
}

MANUAL_COMPANY_BY_CODE = {
    "002233": ("塔牌集团", "广东塔牌集团股份有限公司"),
    "002422": ("川宁生物", "伊犁川宁生物技术股份有限公司"),
    "600456": ("宝钛股份", "宝鸡钛业股份有限公司"),
    "600635": ("大众公用", "上海大众公用事业（集团）股份有限公司"),
    "600685": ("中船防务", "中船海洋与防务装备股份有限公司"),
    "601318": ("中国平安", "中国平安保险（集团）股份有限公司"),
    "601398": ("工商银行", "中国工商银行股份有限公司"),
    "601901": ("方正证券", "方正证券股份有限公司"),
    "603501": ("豪威集团", "豪威集成电路（集团）股份有限公司"),
}

CURRENT_SCHEMAS = {
    "document_full": 1,
    "content_list_enhanced": 8,
    "quality_report": 10,
    "financial_data": 13,
    "financial_checks": 12,
}
CURRENT_RULE_VERSION = "financial_rules_v14"

REPORT_KIND_SLUG = {
    "annual_report": "annual",
    "annual_report_summary": "annual-summary",
    "interim_report": "interim",
    "interim_report_summary": "interim-summary",
}

STOCK_NAME_TO_CODE_DATA = Path("/home/maoyd/DB/PROGRAM/stock_name_to_code_data.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as infile:
            return json.load(infile)
    except Exception:
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as outfile:
        json.dump(payload, outfile, ensure_ascii=False, indent=2)
        outfile.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as outfile:
        outfile.write(text)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", "", text)
    text = text.strip("._- ")
    return text or fallback


def load_stock_name_mapping() -> dict[str, str]:
    data = read_json(STOCK_NAME_TO_CODE_DATA, {})
    mapping = data.get("mapping") if isinstance(data, dict) else {}
    return mapping if isinstance(mapping, dict) else {}


def stock_name_from_code(code: str) -> str:
    if not code:
        return ""
    mapping = load_stock_name_mapping()
    candidates = [name for name, mapped_code in mapping.items() if str(mapped_code).strip() == code]
    if code == "000333":
        candidates.append("美的集团")
    if not candidates:
        return ""
    return sorted(
        set(candidates),
        key=lambda name: (
            int(any(suffix in name for suffix in ("股份有限公司", "有限责任公司", "有限公司"))),
            len(name),
            name,
        ),
    )[0]


def stock_code_from_name(name: str) -> str:
    key = re.sub(r"\s+", "", str(name or "")).strip().lower()
    if not key:
        return ""
    mapping = load_stock_name_mapping()
    if key == "美的集团" or key == "美的集团股份有限公司":
        return "000333"
    for mapped_name, code in mapping.items():
        mapped_key = re.sub(r"\s+", "", mapped_name).lower()
        if key == mapped_key or key in mapped_key or mapped_key in key:
            return str(code).strip()
    return ""


def exchange_from_code(code: str) -> str:
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "SSE"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZSE"
    return "UNKNOWN"


def clean_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    return re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE).strip()


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


def parse_download_filename_identity(filename: str) -> dict:
    parsed = parse_canonical_download_filename_identity(filename)
    if not parsed:
        return {}
    result = dict(parsed)
    if "company_short_name" in result:
        result["stock_name"] = result["company_short_name"]
    return result


def strip_report_suffix(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[\(\[（【]\s*(?:SH|SZ|BJ)?\s*\d{6}\s*(?:\.[A-Z]{2})?\s*[\)\]）】]", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<!\d)(?:SH|SZ|BJ)?\d{6}(?:\.[A-Z]{2})?(?!\d)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:SSE|SZSE|BSE|SH|SZ|BJ|CN)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*20\d{2}\s*年\s*年?\s*", "", value)
    value = re.sub(r"20\d{2}\s*年?\s*(?:年度报告|年报|年度报告全文|半年度报告|季度报告|报告摘要).*", "", value)
    value = re.sub(r"20\d{2}(?:年度报告|年报|年度报告全文|半年度报告|季度报告|报告摘要).*", "", value)
    value = re.sub(r"[：:].*$", "", value)
    value = re.sub(r"[_\-—–]+", " ", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" -_：:，,；;（）()[]【】")


def stock_from_filename(filename: str) -> str:
    parsed = parse_download_filename_identity(filename)
    if parsed.get("stock_code"):
        return parsed["stock_code"]
    stem = clean_filename(filename)
    for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", stem):
        if code.startswith(VALID_CODE_PREFIXES):
            return code
    return ""


def short_from_filename(filename: str) -> str:
    base = clean_filename(filename)
    if base.lower() in {"result", "result.md"}:
        return ""
    parsed = parse_download_filename_identity(base)
    if parsed.get("stock_name"):
        return parsed["stock_name"]
    if "：" in base:
        return strip_report_suffix(base.split("：", 1)[0])
    if ":" in base:
        return strip_report_suffix(base.split(":", 1)[0])
    return strip_report_suffix(base)


def full_from_filename(filename: str) -> str:
    base = clean_filename(filename)
    candidate = ""
    if "：" in base:
        candidate = base.split("：", 1)[1]
    elif ":" in base:
        candidate = base.split(":", 1)[1]
    candidate = strip_report_suffix(candidate or base)
    if is_valid_company_full_name(candidate):
        return candidate
    return ""


def is_valid_company_full_name(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    if not text or len(text) > 46:
        return False
    if re.search(r"[。；;]", text):
        return False
    if any(bad in text for bad in ("情况说明", "风险", "详见", "报告期内", "财务报表", "非经常性损益", "主要有", "已经", "按照")):
        return False
    return bool(re.search(r"(股份有限公司|有限责任公司|有限公司|银行股份有限公司|证券股份有限公司|保险.*股份有限公司|集团.*股份有限公司)$", text))


def headings_from_markdown(markdown: str) -> list[str]:
    headings = []
    for line in str(markdown or "").splitlines()[:160]:
        line = re.sub(r"^#+\s*", "", line).strip()
        if not line or line.startswith("[PDF_PAGE"):
            continue
        if len(line) > 80:
            continue
        headings.append(line)
    return headings


def full_from_markdown(markdown: str) -> str:
    for line in headings_from_markdown(markdown):
        if "年度报告" in line or "目录" == line:
            continue
        candidate = strip_report_suffix(line)
        if is_valid_company_full_name(candidate):
            return candidate
    return ""


def stock_from_markdown(markdown: str) -> str:
    head = str(markdown or "")[:20000]
    for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", head):
        if code.startswith(VALID_CODE_PREFIXES):
            return code
    return ""


def report_year_from_text(*parts) -> int | None:
    text = "\n".join(str(part or "") for part in parts)
    for match in re.finditer(r"(20\d{2})", text):
        year = int(match.group(1))
        if 2000 <= year <= 2100:
            return year
    return None


def load_tasks(db_path: Path) -> dict[str, dict]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("select * from tasks").fetchall()
    conn.close()
    return {row["task_id"]: dict(row) for row in rows}


def parse_time(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def collect_image_refs(markdown: str) -> set[str]:
    refs = set()
    for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown or ""):
        ref = ref.strip()
        if ref.startswith("images/") and ".." not in ref:
            refs.add(ref)
    return refs


def high_value_image_paths(enhanced: dict) -> set[str]:
    paths = set()
    useful_kinds = {"chart", "table_image", "formula", "flowchart", "diagram", "map", "product", "business_visual"}
    useful_actions = {"data_usable", "structure_usable", "needs_ocr", "needs_vlm"}
    for item in (enhanced or {}).get("image_semantic_blocks") or []:
        path = item.get("image_path") or ""
        if not path.startswith("images/"):
            continue
        if item.get("show_in_complete"):
            paths.add(path)
            continue
        if item.get("semantic_kind") in useful_kinds:
            paths.add(path)
            continue
        if item.get("actionability") in useful_actions:
            paths.add(path)
    return paths


def table_lookup(report: dict) -> dict[int, dict]:
    lookup = {}
    for table in (report or {}).get("table_index") or []:
        try:
            index = int(table.get("table_index") or 0)
        except Exception:
            continue
        if index:
            lookup[index] = table
    return lookup


def _clean_metric_label(text) -> str:
    text = str(text or "").replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[（(][^()（）]*[）)]", "", text)
    text = re.sub(r"^[一二三四五六七八九十]+[、.．]", "", text)
    text = re.sub(r"^\d+(?:[.．、]|\s+)", "", text)
    text = re.sub(r"^(?:其中|加|减)[:：]", "", text)
    text = re.sub(r"[:：]$", "", text)
    return text.replace("（", "(").replace("）", ")").strip()


def _compact_metric_label(text) -> str:
    text = _clean_metric_label(text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("或", "")
    text = text.replace("产生/的", "产生的").replace("/产生", "产生")
    return re.sub(r"[，,、:：；;（）()\[\]【】“”\"'·/\\]", "", text)


THREE_STATEMENT_CANONICAL_ALIASES = [
    ("total_liabilities_and_equity", ("负债和所有者权益总计", "负债和股东权益总计", "负债和所有者权益合计", "负债及所有者权益总计", "负债及股东权益总计")),
    ("equity_attributable_parent", ("归属于母公司所有者权益合计", "归属于母公司股东权益合计", "归属于上市公司股东的净资产", "归属于母公司股东的权益", "归属于母公司股东权益", "归属于母公司所有者的权益", "归属于母公司所有者权益", "归属于上市公司股东的权益", "归属于本公司股东权益合计", "归属于本公司股东权益", "归属于本公司股东的权益")),
    ("total_assets", ("资产总计", "资产合计", "资产总额", "总资产")),
    ("current_assets", ("流动资产合计",)),
    ("non_current_assets", ("非流动资产合计",)),
    ("total_liabilities", ("负债合计", "负债总额", "总负债")),
    ("current_liabilities", ("流动负债合计",)),
    ("non_current_liabilities", ("非流动负债合计",)),
    ("minority_interests", ("少数股东权益",)),
    ("total_equity", ("所有者权益合计", "股东权益合计", "所有者权益总额", "股东权益总额")),
    ("monetary_capital", ("货币资金",)),
    ("trading_financial_assets", ("交易性金融资产",)),
    ("derivative_financial_assets", ("衍生金融资产",)),
    ("buyback_resale_assets", ("买入返售金融资产", "买入返售")),
    ("notes_receivable", ("应收票据",)),
    ("accounts_receivable", ("应收账款",)),
    ("receivable_financing", ("应收款项融资",)),
    ("prepayments", ("预付款项", "预付账款")),
    ("other_receivables", ("其他应收款",)),
    ("inventory", ("存货",)),
    ("contract_assets", ("合同资产",)),
    ("current_portion_noncurrent_assets", ("一年内到期的非流动资产",)),
    ("other_current_assets", ("其他流动资产",)),
    ("long_term_receivables", ("长期应收款",)),
    ("long_term_equity_investments", ("长期股权投资",)),
    ("other_equity_investments", ("其他权益工具投资",)),
    ("other_debt_investments", ("其他债权投资",)),
    ("other_noncurrent_financial_assets", ("其他非流动金融资产",)),
    ("investment_property", ("投资性房地产",)),
    ("fixed_assets", ("固定资产",)),
    ("construction_in_progress", ("在建工程",)),
    ("right_of_use_assets", ("使用权资产",)),
    ("intangible_assets", ("无形资产",)),
    ("development_expenditure", ("开发支出",)),
    ("goodwill", ("商誉",)),
    ("long_term_prepaid_expenses", ("长期待摊费用",)),
    ("deferred_tax_assets", ("递延所得税资产",)),
    ("other_noncurrent_assets", ("其他非流动资产",)),
    ("short_term_borrowings", ("短期借款",)),
    ("notes_payable", ("应付票据",)),
    ("accounts_payable", ("应付账款",)),
    ("advance_receipts", ("预收款项", "预收账款")),
    ("contract_liabilities", ("合同负债",)),
    ("employee_benefits_payable", ("应付职工薪酬",)),
    ("taxes_payable", ("应交税费",)),
    ("other_payables", ("其他应付款",)),
    ("provisions", ("预计负债",)),
    ("current_portion_noncurrent_liabilities", ("一年内到期的非流动负债",)),
    ("other_current_liabilities", ("其他流动负债",)),
    ("long_term_borrowings", ("长期借款",)),
    ("bonds_payable", ("应付债券",)),
    ("lease_liabilities", ("租赁负债",)),
    ("long_term_payables", ("长期应付款",)),
    ("deferred_tax_liabilities", ("递延所得税负债",)),
    ("other_noncurrent_liabilities", ("其他非流动负债",)),
    ("share_capital", ("实收资本", "股本")),
    ("other_equity_instruments", ("其他权益工具",)),
    ("capital_reserve", ("资本公积",)),
    ("treasury_shares", ("减：库存股", "库存股")),
    ("other_comprehensive_income_bs", ("其他综合收益",)),
    ("surplus_reserve", ("盈余公积",)),
    ("retained_earnings", ("未分配利润",)),
    ("total_operating_revenue", ("营业总收入",)),
    ("operating_revenue", ("营业收入",)),
    ("operating_cost", ("营业成本", "营业总成本")),
    ("taxes_and_surcharges", ("税金及附加",)),
    ("sales_expenses", ("销售费用",)),
    ("administrative_expenses", ("管理费用",)),
    ("research_expenses", ("研发费用", "研发支出")),
    ("financial_expenses", ("财务费用",)),
    ("interest_expense", ("利息费用",)),
    ("interest_income", ("利息收入",)),
    ("other_income", ("其他收益",)),
    ("investment_income", ("投资收益",)),
    ("associate_joint_venture_investment_income", ("对联营企业和合营企业的投资收益",)),
    ("fair_value_change", ("公允价值变动收益",)),
    ("credit_impairment", ("信用减值损失",)),
    ("asset_impairment", ("资产减值损失",)),
    ("asset_disposal_income", ("资产处置收益",)),
    ("operating_profit", ("营业利润",)),
    ("non_operating_income", ("营业外收入",)),
    ("non_operating_expenses", ("营业外支出",)),
    ("total_profit", ("利润总额",)),
    ("income_tax_expense", ("所得税费用",)),
    ("parent_net_profit", ("归属于母公司股东的净利润", "归属于母公司所有者的净利润", "归属于上市公司股东的净利润", "归属于本公司股东的净利润")),
    ("minority_profit_loss", ("少数股东损益", "少数股东收益", "少数股东的净利润")),
    ("net_profit", ("净利润",)),
    ("other_comprehensive_income", ("其他综合收益的税后净额", "其他综合收益税后净额", "其他综合收益合计", "其他综合收益")),
    ("parent_other_comprehensive_income", ("归属于母公司所有者的其他综合收益的税后净额", "归属于母公司股东的其他综合收益的税后净额")),
    ("minority_other_comprehensive_income", ("归属于少数股东的其他综合收益的税后净额",)),
    ("total_comprehensive_income", ("综合收益总额",)),
    ("parent_total_comprehensive_income", ("归属于母公司所有者的综合收益总额", "归属于母公司股东的综合收益总额")),
    ("minority_total_comprehensive_income", ("归属于少数股东的综合收益总额",)),
    ("operating_cash_inflow_total", ("经营活动现金流入小计",)),
    ("operating_cash_outflow_total", ("经营活动现金流出小计",)),
    ("operating_cash_flow_net", ("经营活动产生的现金流量净额", "经营活动使用的现金流量净额", "经营活动现金流量净额")),
    ("investing_cash_inflow_total", ("投资活动现金流入小计",)),
    ("investing_cash_outflow_total", ("投资活动现金流出小计",)),
    ("investing_cash_flow_net", ("投资活动产生的现金流量净额", "投资活动使用的现金流量净额", "投资活动现金流量净额")),
    ("financing_cash_inflow_total", ("筹资活动现金流入小计",)),
    ("financing_cash_outflow_total", ("筹资活动现金流出小计",)),
    ("financing_cash_flow_net", ("筹资活动产生的现金流量净额", "筹资活动使用的现金流量净额", "筹资活动现金流量净额")),
    ("fx_effect_cash", ("汇率变动对现金及现金等价物的影响", "汇率变动对现金的影响")),
    ("cash_equivalents_net_increase", ("现金及现金等价物净增加额", "现金及现金等价物净减少额", "现金及现金等价物净变动额", "现金及现金等价物增加额", "现金及现金等价物减少额")),
    ("cash_equivalents_beginning", ("期初现金及现金等价物余额", "现金的期初余额", "年初现金及现金等价物余额")),
    ("cash_equivalents_ending", ("期末现金及现金等价物余额", "现金的期末余额", "年末现金及现金等价物余额")),
    ("cash_from_sales", ("销售商品、提供劳务收到的现金",)),
    ("cash_from_tax_refund", ("收到的税费返还",)),
    ("other_cash_from_operating", ("收到其他与经营活动有关的现金",)),
    ("cash_for_purchases", ("购买商品、接受劳务支付的现金",)),
    ("cash_for_employees", ("支付给职工以及为职工支付的现金",)),
    ("cash_for_taxes", ("支付的各项税费",)),
    ("other_cash_for_operating", ("支付其他与经营活动有关的现金",)),
    ("cash_from_disposal_investments", ("收回投资收到的现金",)),
    ("cash_from_investment_income", ("取得投资收益收到的现金",)),
    ("cash_from_disposal_assets", ("处置固定资产、无形资产和其他长期资产收回的现金净额",)),
    ("other_cash_from_investing", ("收到其他与投资活动有关的现金",)),
    ("cash_for_purchases_investments", ("购建固定资产、无形资产和其他长期资产支付的现金",)),
    ("cash_for_investments", ("投资支付的现金",)),
    ("other_cash_for_investing", ("支付其他与投资活动有关的现金",)),
    ("cash_from_investors", ("吸收投资收到的现金",)),
    ("cash_from_borrowings", ("取得借款收到的现金",)),
    ("cash_for_debt_repayment", ("偿还债务支付的现金", "偿还债务所支付的现金")),
    ("cash_for_dividends", ("分配股利、利润或偿付利息支付的现金",)),
    ("other_cash_for_financing", ("支付其他与筹资活动有关的现金",)),
]

THREE_STATEMENT_ALIAS_MAP = {
    _compact_metric_label(alias): canonical
    for canonical, aliases in THREE_STATEMENT_CANONICAL_ALIASES
    for alias in aliases
}

NON_AMOUNT_THREE_STATEMENT_KEYS = {
    "basic_eps",
    "diluted_eps",
    "deducted_basic_eps",
    "weighted_avg_roe",
    "deducted_weighted_avg_roe",
    "parent_nav_per_share",
    "ending_share_capital",
}

CALIBRATION_METRIC_KEYS = {
    "operating_revenue",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "total_assets",
    "equity_attributable_parent",
    "total_liabilities",
    "net_profit",
}


def _looks_like_metric_ratio_label(compact: str) -> bool:
    return any(word in compact for word in ("占营业收入", "比例", "比率", "变动比例", "增长率", "资产负债率"))


def _allow_metric_substring_alias(alias: str, canonical: str, compact: str) -> bool:
    if canonical in {"operating_revenue", "net_profit"}:
        return compact.startswith(alias) or compact.endswith(alias)
    if canonical == "total_equity" and "归属于" in compact:
        return False
    if canonical == "other_comprehensive_income_bs" and "税后净额" in compact:
        return False
    return len(alias) >= 6


def canonical_three_statement_metric(label: str, existing: str | None = None, statement_type: str | None = None) -> str | None:
    compact = _compact_metric_label(label)
    if not compact:
        return None
    if existing:
        if statement_type == "balance_sheet" and existing == "other_comprehensive_income":
            return "other_comprehensive_income_bs"
        return existing
    if "扣除股份支付影响" in compact:
        return None
    if "扣除非经常性损益" in compact and "每股收益" in compact:
        return "deducted_basic_eps"
    if "扣除非经常性损益" in compact and "净资产收益率" in compact:
        return "deducted_weighted_avg_roe"
    if "扣除非经常性损益" in compact and "净利润" in compact:
        return "deducted_parent_net_profit"
    if "稀释每股收益" in compact:
        return "diluted_eps"
    if "基本每股收益" in compact:
        return "basic_eps"
    if "加权平均净资产收益率" in compact:
        return "weighted_avg_roe"
    if "每股净资产" in compact:
        return "parent_nav_per_share"
    if "总股本" in compact:
        return "ending_share_capital"
    if "归属于" in compact and "权益" in compact and any(term in compact for term in ("母公司", "上市公司", "本公司", "普通股股东")):
        return "equity_attributable_parent"
    if "归属于少数股东权益" in compact:
        return None
    if "经营活动" in compact and "现金流量净额" in compact:
        return "operating_cash_flow_net"
    if "投资活动" in compact and "现金流量净额" in compact:
        return "investing_cash_flow_net"
    if "筹资活动" in compact and "现金流量净额" in compact:
        return "financing_cash_flow_net"
    if "其他综合收益" in compact and "综合收益总额" not in compact:
        if statement_type == "balance_sheet":
            return "other_comprehensive_income_bs"
        if "少数股东" in compact:
            return "minority_other_comprehensive_income"
        if "归属于" in compact:
            return "parent_other_comprehensive_income"
        detail_terms = ("重分类进损益", "转损益", "不可转损益", "不能转损益", "公允价值变动", "信用损失", "减值准备", "折算差额", "权益法下", "现金流量套期", "小计")
        if any(term in compact for term in detail_terms):
            return None
        return "other_comprehensive_income"
    if compact in THREE_STATEMENT_ALIAS_MAP:
        return THREE_STATEMENT_ALIAS_MAP[compact]
    if _looks_like_metric_ratio_label(compact):
        return None
    for alias, canonical in sorted(THREE_STATEMENT_ALIAS_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in compact and _allow_metric_substring_alias(alias, canonical, compact):
            return canonical
    return None


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _period_sort_key(period: str) -> tuple[int, int, int, str]:
    text = str(period or "")
    date_match = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if date_match:
        return (int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)), text)
    year_match = re.search(r"(20\d{2})", text)
    if year_match:
        return (int(year_match.group(1)), 12, 31, text)
    return (0, 0, 0, text)


def _period_year(period: str) -> str:
    match = re.search(r"(20\d{2})", str(period or ""))
    return match.group(1) if match else str(period or "")


def latest_metric_period(values: dict) -> str | None:
    valid_periods = [str(period) for period, value in (values or {}).items() if _to_float(value) is not None]
    if not valid_periods:
        return None
    return max(valid_periods, key=_period_sort_key)


def latest_statement_period(statement: dict) -> str | None:
    column_periods = []
    for column in statement.get("columns") or []:
        period = column.get("period") or column.get("key") or column.get("label")
        if period:
            column_periods.append(str(period))
    if column_periods:
        return max(column_periods, key=_period_sort_key)

    periods = []
    for metric in statement.get("items") or []:
        periods.extend(str(period) for period in (metric.get("values") or {}).keys())
    if not periods:
        return None
    return max(periods, key=_period_sort_key)


def key_metrics_lookup(financial_data: dict) -> dict[tuple[str, str], float]:
    lookup = {}
    for metric in (financial_data or {}).get("key_metrics") or []:
        canonical = canonical_three_statement_metric(metric.get("name"), metric.get("canonical_name"))
        if not canonical:
            continue
        for period, value in (metric.get("values") or {}).items():
            number = _to_float(value)
            if number is None:
                continue
            period_text = str(period)
            lookup[(canonical, period_text)] = number
            year = _period_year(period_text)
            if year:
                lookup.setdefault((canonical, year), number)
    return lookup


def nearest_statement_multiplier(ratio: float) -> float | None:
    if ratio <= 0:
        return None
    for factor in (1.0, 1000.0, 10000.0, 1000000.0, 100000000.0):
        if factor * 0.98 <= ratio <= factor * 1.02:
            return factor
    return None


def infer_statement_value_multiplier(statement: dict, financial_data: dict) -> float:
    lookup = key_metrics_lookup(financial_data)
    candidates = []
    for metric in statement.get("items") or []:
        canonical = canonical_three_statement_metric(metric.get("name"), metric.get("canonical_name"), statement.get("statement_type"))
        if canonical not in CALIBRATION_METRIC_KEYS:
            continue
        for period, value in (metric.get("values") or {}).items():
            number = _to_float(value)
            if not number:
                continue
            target = lookup.get((canonical, str(period))) or lookup.get((canonical, _period_year(str(period))))
            if not target:
                continue
            multiplier = nearest_statement_multiplier(abs(float(target) / number))
            if multiplier:
                candidates.append(multiplier)
    if not candidates:
        return 1.0
    return Counter(candidates).most_common(1)[0][0]


def build_financial_data_three_statements(row: dict) -> dict:
    fd = row.get("financial_data") or {}
    quality_tables = table_lookup(row.get("quality") or {})
    metrics = []
    for statement in fd.get("statements") or []:
        statement_type = statement.get("statement_type")
        if statement_type not in {"balance_sheet", "income_statement", "cash_flow_statement"}:
            continue
        scope = statement.get("scope") or ""
        if scope and scope != "consolidated":
            continue
        value_multiplier = infer_statement_value_multiplier(statement, fd)
        statement_scale = _to_float(statement.get("scale")) or 1.0
        base_scale = statement_scale * value_multiplier
        statement_period = latest_statement_period(statement)
        if not statement_period:
            continue
        for metric in statement.get("items") or []:
            canonical = canonical_three_statement_metric(metric.get("name"), metric.get("canonical_name"), statement_type)
            if not canonical or canonical in NON_AMOUNT_THREE_STATEMENT_KEYS:
                continue
            values = metric.get("values") or {}
            period = statement_period
            if period not in values:
                continue
            value = _to_float(values.get(period))
            if value is None:
                continue
            normalized_value = round(value * value_multiplier / 100000000.0, 6)
            src = (metric.get("sources") or {}).get(period) or {}
            table_index = src.get("table_index")
            table = {}
            try:
                table = quality_tables.get(int(table_index or 0), {})
            except Exception:
                table = {}
            page = src.get("pdf_page") or src.get("pdf_page_number") or table.get("pdf_page_number")
            metrics.append(
                {
                    "metric_key": canonical,
                    "metric_name": metric.get("name"),
                    "raw_value": (metric.get("raw_values") or {}).get(period),
                    "normalized_value": normalized_value,
                    "base_scale": base_scale,
                    "unit_hint": statement.get("unit") or "",
                    "source": {
                        "md_line": src.get("md_line") or src.get("line"),
                        "pdf_page": page,
                        "table_index": table_index,
                        "task_id": row.get("task_id"),
                        "period": period,
                        "source_kind": "financial_data_statement",
                    },
                    "statement_type": statement_type,
                    "scope": scope or "consolidated",
                    "period": period,
                }
            )
    if not metrics:
        return {}
    return {
        "company": (row.get("identity") or {}).get("company_short_name"),
        "stock_code": (row.get("identity") or {}).get("stock_code"),
        "metrics": metrics,
        "extraction_method": "financial_data_statement_ingest_v1",
    }


def build_three_statement_payload(row: dict, v641_company: dict | None = None) -> dict:
    payload = build_financial_data_three_statements(row)
    if payload.get("metrics"):
        return payload
    return v641_company or {}


def three_statement_payload_source(payload: dict) -> str:
    source_kinds = {
        ((metric.get("source") or {}).get("source_kind") or "")
        for metric in (payload or {}).get("metrics") or []
    }
    if "financial_data_statement" in source_kinds:
        return "financial_data.json"
    if payload:
        return "extracted_three_statements_v6.41_test.json"
    return "none"


def build_three_statement_evidence(row: dict, payload: dict) -> list[dict]:
    code = row["identity"]["stock_code"]
    task_id = row["task_id"]
    evidence = []
    for metric in (payload or {}).get("metrics") or []:
        source = metric.get("source") or {}
        page = source.get("pdf_page") or source.get("pdf_page_number") or None
        table_index = source.get("table_index") or None
        item = {
            "company_id": row["identity"]["company_id"],
            "report_id": row["report_id"],
            "stock_code": code,
            "metric_key": metric.get("metric_key"),
            "metric_name": metric.get("metric_name"),
            "statement_type": metric.get("statement_type"),
            "scope": metric.get("scope"),
            "period": metric.get("period") or source.get("period"),
            "raw_value": metric.get("raw_value"),
            "normalized_value": metric.get("normalized_value"),
            "normalized_unit": "亿元",
            "base_scale": metric.get("base_scale"),
            "unit_hint": metric.get("unit_hint"),
            "task_id": source.get("task_id") or task_id,
            "md_line": source.get("md_line") or source.get("line"),
            "pdf_page_number": page,
            "table_index": table_index,
            "source_kind": source.get("source_kind") or "wiki_v6.41_three_statement_metric",
            "source_pdf_path_legacy": source.get("pdf_path"),
        }
        item.update(evidence_urls(task_id, page, table_index))
        evidence.append(item)
    return evidence


def summarize_statements(financial_data: dict) -> list[dict]:
    summaries = []
    for stmt in (financial_data or {}).get("statements") or []:
        summaries.append(
            {
                "statement_id": stmt.get("statement_id"),
                "statement_type": stmt.get("statement_type"),
                "statement_name": stmt.get("statement_name"),
                "scope": stmt.get("scope"),
                "scope_name": stmt.get("scope_name"),
                "title": stmt.get("title"),
                "unit": stmt.get("unit"),
                "scale": stmt.get("scale"),
                "currency": stmt.get("currency"),
                "columns": stmt.get("columns") or [],
                "item_count": len(stmt.get("items") or []),
                "table_indexes": stmt.get("table_indexes") or [],
                "line_numbers": stmt.get("line_numbers") or [],
            }
        )
    return summaries


def build_identity(task_id: str, filename: str, markdown: str, financial_data: dict, enhanced: dict) -> tuple[dict, list[str]]:
    evidence = []
    manual = MANUAL_STOCK_CODE_MAPPING.get(task_id)
    if manual:
        code, short, full, exchange = manual
        evidence.append("manual_mapping")
    else:
        code = stock_from_filename(filename) or stock_from_markdown(markdown)
        if code:
            evidence.append("filename_or_markdown_stock_code")
        short = short_from_filename(filename)
        if not code and short:
            code = stock_code_from_name(short)
            if code:
                evidence.append("filename_short_name_stock_mapping")
        mapped_short = stock_name_from_code(code)
        if mapped_short:
            short = mapped_short
            evidence.append("stock_code_short_name_mapping")
        parsed_download = parse_download_filename_identity(filename)
        if parsed_download and (not code or parsed_download.get("stock_code") == code):
            code = code or parsed_download.get("stock_code", "")
            short = parsed_download.get("stock_name") or short
            if parsed_download.get("source"):
                evidence.append(parsed_download["source"])
        full = full_from_filename(filename) or full_from_markdown(markdown)
        exchange = exchange_from_code(code)
        if short:
            evidence.append("filename_short_name")
        if full:
            evidence.append("filename_or_markdown_full_name")

    if not full:
        full = full_from_markdown(markdown)
        if full:
            evidence.append("markdown_full_name")
    if code in MANUAL_COMPANY_BY_CODE:
        short, full = MANUAL_COMPANY_BY_CODE[code]
        evidence.append("manual_company_by_code")
    mapped_short = stock_name_from_code(code)
    if mapped_short:
        short = mapped_short
        evidence.append("stock_code_short_name_mapping")
    if not short and full:
        short = re.sub(r"(股份有限公司|有限公司)$", "", full)
    if not short:
        short = short_from_filename((financial_data or {}).get("filename") or (enhanced or {}).get("filename") or filename)
    if looks_like_report_instance_name(short):
        parsed_download = parse_download_filename_identity(filename)
        if parsed_download.get("stock_name"):
            short = parsed_download["stock_name"]
            evidence.append("report_filename_short_name_canonicalized")
    if not full:
        full = short
    if looks_like_report_instance_name(full) or not is_valid_company_full_name(full):
        full = short

    code = code or ""
    identity = canonicalize_identity(
        stock_code=code,
        company_short_name=short,
        company_full_name=full,
        exchange=exchange if code else "UNKNOWN",
    )
    aliases = sorted({
        x
        for x in [
            identity.company_short_name,
            identity.company_full_name,
            short_from_filename(filename),
            full_from_filename(filename),
        ]
        if x
    })
    return (
        {
            "company_id": identity.company_id,
            "stock_code": identity.stock_code,
            "exchange": identity.exchange,
            "company_short_name": identity.company_short_name,
            "company_full_name": identity.company_full_name,
            "aliases": aliases,
        },
        evidence,
    )


def inspect_result_dir(result_dir: Path, tasks: dict[str, dict]) -> dict:
    task_id = result_dir.name
    task = tasks.get(task_id, {})
    doc = read_json(result_dir / "document_full.json", {})
    fd = read_json(result_dir / "financial_data.json", {})
    fc = read_json(result_dir / "financial_checks.json", {})
    enhanced = read_json(result_dir / "content_list_enhanced.json", {})
    quality = read_json(result_dir / "quality_report.json", {})
    md_path = result_dir / "result_complete.md"
    markdown = md_path.read_text("utf-8", errors="ignore") if md_path.exists() else ""
    filename = (
        task.get("filename")
        or ((doc.get("task") or {}).get("filename") if isinstance(doc, dict) else "")
        or fd.get("filename")
        or enhanced.get("filename")
        or ""
    )
    identity, identity_evidence = build_identity(task_id, filename, markdown, fd, enhanced)
    year = fd.get("report_year") or enhanced.get("report_year") or quality.get("report_year")
    if not year:
        year = report_year_from_text(filename, markdown[:5000])
    kind = fd.get("report_kind") or quality.get("report_kind") or "annual_report"
    report_id = f"{int(year)}-{REPORT_KIND_SLUG.get(kind, safe_name(kind))}" if year else "unknown-report"
    refs = collect_image_refs(markdown)
    broken_refs = [ref for ref in refs if not (result_dir / ref).exists()]
    warnings = []
    if not identity["stock_code"]:
        warnings.append("missing_stock_code")
    if not year:
        warnings.append("missing_report_year")
    for key, version in CURRENT_SCHEMAS.items():
        source = {
            "document_full": doc,
            "content_list_enhanced": enhanced,
            "quality_report": quality,
            "financial_data": fd,
            "financial_checks": fc,
        }.get(key, {})
        if isinstance(source, dict) and source.get("schema_version") != version:
            warnings.append(f"{key}_schema_{source.get('schema_version')}")
    if fd.get("rule_version") != CURRENT_RULE_VERSION:
        warnings.append("financial_data_rule_mismatch")
    if fc.get("rule_version") != CURRENT_RULE_VERSION:
        warnings.append("financial_checks_rule_mismatch")
    if broken_refs:
        warnings.append("broken_markdown_images")

    score = 0
    score += 1000 if identity["stock_code"] else 0
    score += 200 if fc.get("overall_status") == "pass" else 0
    score += 100 if not broken_refs else 0
    score += 50 if task else 0
    score += int(quality.get("table_count") or 0)
    score -= len(quality.get("warnings") or []) * 20
    score += int(parse_time(task.get("completed_at") or (doc.get("task") or {}).get("completed_at")))

    return {
        "task_id": task_id,
        "result_dir": str(result_dir),
        "task": task,
        "filename": filename,
        "identity": identity,
        "identity_evidence": identity_evidence,
        "report_year": int(year) if year else None,
        "report_kind": kind,
        "report_id": report_id,
        "markdown": markdown,
        "markdown_path": md_path,
        "document_full": doc,
        "financial_data": fd,
        "financial_checks": fc,
        "enhanced": enhanced,
        "quality": quality,
        "image_refs": refs,
        "high_value_images": high_value_image_paths(enhanced),
        "broken_image_refs": broken_refs,
        "warnings": warnings,
        "score": score,
    }


def active_candidates(candidates: list[dict]) -> tuple[list[dict], dict]:
    grouped = defaultdict(list)
    skipped = []
    for item in candidates:
        ident = item["identity"]
        if not ident["stock_code"] or not item["report_year"]:
            item["selected"] = False
            item["selection_reason"] = "missing_identity"
            skipped.append(item)
            continue
        key = (ident["stock_code"], item["report_year"], item["report_kind"])
        grouped[key].append(item)

    active = []
    duplicates = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda row: row["score"], reverse=True)
        winner = rows[0]
        winner["selected"] = True
        winner["selection_reason"] = "primary_report"
        active.append(winner)
        if len(rows) > 1:
            dup_key = f"{key[0]}-{key[1]}-{key[2]}"
            duplicates[dup_key] = []
            for index, row in enumerate(rows):
                row["selected"] = index == 0
                row["selection_reason"] = "primary_report" if index == 0 else "duplicate_not_selected"
                duplicates[dup_key].append(
                    {
                        "task_id": row["task_id"],
                        "filename": row["filename"],
                        "score": row["score"],
                        "selected": row["selected"],
                        "result_dir": row["result_dir"],
                    }
                )
    return sorted(active, key=lambda row: (row["identity"]["stock_code"], row["report_year"] or 0)), {
        "duplicates": duplicates,
        "skipped": [
            {
                "task_id": row["task_id"],
                "filename": row["filename"],
                "warnings": row["warnings"],
                "result_dir": row["result_dir"],
            }
            for row in skipped
        ],
    }


def evidence_urls(task_id: str, page: int | None, table_index: int | None) -> dict:
    payload = {
        "open_pdf_page_url": "",
        "open_source_page_url": "",
        "open_source_table_url": "",
    }
    if page:
        payload["open_pdf_page_url"] = f"/api/pdf_page/{task_id}/{page}"
        payload["open_source_page_url"] = f"/api/source/{task_id}/page/{page}"
    if table_index:
        payload["open_source_table_url"] = f"/api/source/{task_id}/table/{table_index}"
    return payload


def build_v641_evidence(row: dict, v641: dict) -> list[dict]:
    code = row["identity"]["stock_code"]
    task_id = row["task_id"]
    report = v641.get(code) or {}
    evidence = []
    for metric in report.get("metrics") or []:
        source = metric.get("source") or {}
        page = source.get("pdf_page") or None
        table_index = source.get("table_index") or None
        item = {
            "company_id": row["identity"]["company_id"],
            "report_id": row["report_id"],
            "stock_code": code,
            "metric_key": metric.get("metric_key"),
            "statement_type": metric.get("statement_type"),
            "raw_value": metric.get("raw_value"),
            "normalized_value": metric.get("normalized_value"),
            "normalized_unit": "亿元",
            "base_scale": metric.get("base_scale"),
            "unit_hint": metric.get("unit_hint"),
            "task_id": task_id,
            "md_line": source.get("md_line"),
            "pdf_page_number": page,
            "table_index": table_index,
            "source_kind": "wiki_v6.41_three_statement_metric",
            "source_pdf_path_legacy": source.get("pdf_path"),
        }
        item.update(evidence_urls(task_id, page, table_index))
        evidence.append(item)
    return evidence


def build_fallback_evidence(row: dict) -> list[dict]:
    fd = row["financial_data"]
    quality_tables = table_lookup(row["quality"])
    evidence = []
    for stmt in fd.get("statements") or []:
        for metric in stmt.get("items") or []:
            canonical = metric.get("canonical_name")
            if not canonical:
                continue
            for period, value in (metric.get("values") or {}).items():
                src = (metric.get("sources") or {}).get(period) or {}
                table_index = src.get("table_index")
                table = quality_tables.get(int(table_index or 0), {})
                page = table.get("pdf_page_number")
                item = {
                    "company_id": row["identity"]["company_id"],
                    "report_id": row["report_id"],
                    "stock_code": row["identity"]["stock_code"],
                    "metric_key": canonical,
                    "metric_name": metric.get("name"),
                    "statement_type": stmt.get("statement_type"),
                    "scope": stmt.get("scope"),
                    "period": period,
                    "value": value,
                    "raw_value": (metric.get("raw_values") or {}).get(period),
                    "raw_unit": stmt.get("unit"),
                    "task_id": row["task_id"],
                    "md_line": src.get("line"),
                    "pdf_page_number": page,
                    "table_index": table_index,
                    "source_kind": "upstream_financial_data",
                }
                item.update(evidence_urls(row["task_id"], page, table_index))
                evidence.append(item)
    return evidence


def copy_report_assets(row: dict, report_dir: Path) -> list[dict]:
    source_dir = Path(row["result_dir"])
    image_paths = sorted(row["image_refs"] | row["high_value_images"])
    enhanced_by_path = {}
    for item in (row["enhanced"] or {}).get("image_semantic_blocks") or []:
        if item.get("image_path"):
            enhanced_by_path[item["image_path"]] = item
    manifest = []
    for rel in image_paths:
        source = source_dir / rel
        if not source.exists():
            continue
        dest = report_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        info = enhanced_by_path.get(rel) or {}
        manifest.append(
            {
                "image_id": f"{row['identity']['stock_code']}-{row['report_year']}-img-{len(manifest) + 1:04d}",
                "company_id": row["identity"]["company_id"],
                "report_id": row["report_id"],
                "stock_code": row["identity"]["stock_code"],
                "company_short_name": row["identity"]["company_short_name"],
                "report_year": row["report_year"],
                "wiki_path": str(Path("reports") / row["report_id"] / rel),
                "source_path": str(source),
                "sha256": sha256_file(source),
                "size_bytes": source.stat().st_size,
                "pdf_page_number": info.get("pdf_page_number"),
                "markdown_line": info.get("markdown_line"),
                "semantic_kind": info.get("semantic_kind") or info.get("type"),
                "detail_type": info.get("detail_type") or info.get("sub_type"),
                "confidence": info.get("confidence"),
                "actionability": info.get("actionability"),
                "recognized_preview": info.get("display_preview") or info.get("recognized_preview"),
                "chart_data": info.get("chart_data") or {},
                "source_task_id": row["task_id"],
                "copied_reason": "markdown_ref" if rel in row["image_refs"] else "semantic_high_value",
            }
        )
    return manifest


def build_report_json(row: dict, image_manifest: list[dict], evidence: list[dict]) -> dict:
    fd = row["financial_data"]
    fc = row["financial_checks"]
    quality = row["quality"]
    enhanced = row["enhanced"]
    source_dir = Path(row["result_dir"])
    schemas = {
        "document_full": (row["document_full"] or {}).get("schema_version"),
        "content_list_enhanced": enhanced.get("schema_version"),
        "quality_report": quality.get("schema_version"),
        "financial_data": fd.get("schema_version"),
        "financial_checks": fc.get("schema_version"),
    }
    source_metadata = report_source_metadata(row["filename"])
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "identity": row["identity"],
        "report": {
            "report_id": row["report_id"],
            "report_year": row["report_year"],
            "report_kind": row["report_kind"],
            "industry_profile": fd.get("industry_profile"),
            "source_filename": row["filename"],
            "source_filename_metadata": source_metadata,
        },
        "source": {
            "task_id": row["task_id"],
            "result_dir": row["result_dir"],
            "upload_pdf_path": (row["task"] or {}).get("upload_path") or str(Path("/home/maoyd/pdf2md_web/uploads") / f"{row['task_id']}.pdf"),
            "document_full_path": str(source_dir / "document_full.json"),
            "schema_versions": schemas,
            "financial_rule_version": fd.get("rule_version"),
            "financial_checks_rule_version": fc.get("rule_version"),
            "pdf_page_count": (row["task"] or {}).get("pdf_page_count") or (row["document_full"].get("task") or {}).get("pdf_page_count"),
            "pdf2md_result_dir": row["result_dir"],
            "pdf2md_pdf_pages_dir": str(source_dir / "pdf_pages"),
            "pdf_page_url_template": "/api/pdf_page/{task_id}/{page_number}",
            "source_page_url_template": "/api/source/{task_id}/page/{page_number}",
            "source_table_url_template": "/api/source/{task_id}/table/{table_index}",
            "sha256": {
                "result_complete_md": sha256_file(row["markdown_path"]) if row["markdown_path"].exists() else "",
                "document_full_json": sha256_file(source_dir / "document_full.json") if (source_dir / "document_full.json").exists() else "",
            },
            "source_filename_metadata": source_metadata,
        },
        "quality_summary": {
            "markdown_chars": quality.get("markdown_chars"),
            "table_count": quality.get("table_count"),
            "image_ref_count": quality.get("image_ref_count"),
            "found_financial_tables": quality.get("found_financial_tables") or [],
            "financial_overall_status": quality.get("financial_overall_status") or fc.get("overall_status"),
            "financial_summary": quality.get("financial_summary") or fc.get("summary"),
            "warnings": quality.get("warnings") or [],
        },
        "tables": quality.get("table_index") or [],
        "financial_data_summary": {
            "statements": summarize_statements(fd),
            "key_metrics": fd.get("key_metrics") or [],
            "classification_evidence": fd.get("classification_evidence") or [],
            "llm_table_judgments": fd.get("llm_table_judgments") or [],
            "warnings": fd.get("warnings") or [],
        },
        "financial_checks_summary": {
            "overall_status": fc.get("overall_status"),
            "summary": fc.get("summary"),
            "checks": fc.get("checks") or [],
            "warnings": fc.get("warnings") or [],
        },
        "note_links": (enhanced.get("financial_note_links") or {}),
        "images": image_manifest,
        "evidence": {
            "count": len(evidence),
            "sample": evidence[:20],
        },
        "status": "ready" if not row["warnings"] else "needs_review",
        "warnings": row["warnings"],
    }


def build_company_md(company: dict, reports: list[dict], three_statement_payload: dict | None) -> str:
    primary = reports[0]
    lines = [
        f"# {company['company_short_name']}（{company['stock_code']}）",
        "",
        f"- 公司全称：{company['company_full_name']}",
        f"- 证券代码：{company['stock_code']}",
        f"- 交易所：{company['exchange']}",
        f"- 主报告：{primary['report_id']}",
        f"- 当前状态：{primary.get('status', 'ready')}",
        "",
        "## 可用报告",
        "",
    ]
    for report in reports:
        lines.append(f"- {report['report_year']} {report['report_kind']}：[{report['report_id']}](reports/{report['report_id']}/report.md)")
    lines.extend(["", "## 指标入口", "", "- [三大表指标](metrics/three_statements.json)", "- [关键指标](metrics/key_metrics.json)", "- [校验结果](metrics/validation.json)", "", "## 证据链入口", "", "- [证据索引](evidence/evidence_index.json)", "- [PDF 引用](evidence/pdf_refs.json)", "- [图片证据](evidence/image_manifest.json)", "", "## 分析入口", "", "- [分析目录](analysis/README.md)", ""])
    if three_statement_payload:
        totals = {}
        for metric in three_statement_payload.get("metrics") or []:
            key = metric.get("metric_key")
            if key in {"total_assets", "total_liabilities", "total_equity", "operating_revenue", "net_profit", "parent_net_profit"}:
                totals.setdefault(key, metric.get("normalized_value"))
        if totals:
            lines.extend(["## 快速财务摘要", ""])
            for key, label in [
                ("total_assets", "资产总计"),
                ("total_liabilities", "负债合计"),
                ("total_equity", "所有者权益合计"),
                ("operating_revenue", "营业收入"),
                ("net_profit", "净利润"),
                ("parent_net_profit", "归母净利润"),
            ]:
                if key in totals:
                    lines.append(f"- {label}：{totals[key]} 亿元")
            lines.append("")
    return "\n".join(lines)


def build_analysis_readme(company: dict) -> str:
    return "\n".join(
        [
            f"# {company['company_short_name']} 分析工作区",
            "",
            "本目录用于沉淀围绕单个上市公司的多维分析结论。",
            "",
            "建议维度：",
            "",
            "- financial.md：财务质量与三大表分析",
            "- operations.md：经营业务与增长驱动",
            "- governance.md：治理结构与股东情况",
            "- risk.md：风险因素与审计关注",
            "- valuation.md：估值与市场定价",
            "- strategy.md：战略、资本开支与长期竞争力",
            "",
            "所有重要判断必须引用 `../evidence/evidence_index.json` 中的证据对象，或引用年报 PDF 页码和表格索引。",
            "",
        ]
    )


def rebuild(args):
    source_root = Path(args.results_dir)
    pdf2md_root = Path(args.pdf2md_root)
    output = Path(args.output)
    old_wiki = Path(args.old_wiki)
    v641_candidates = [
        old_wiki / "extracted_three_statements_v6.41_test.json",
        old_wiki / "derived" / "three_statements_latest.json",
    ]
    v641 = {}
    for v641_path in v641_candidates:
        v641 = read_json(v641_path, {})
        if v641:
            break
    tasks = load_tasks(pdf2md_root / "tasks.db")

    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    (output / "companies").mkdir(parents=True)
    (output / "derived").mkdir()
    (output / "_meta").mkdir()

    candidates = [inspect_result_dir(path, tasks) for path in sorted(source_root.iterdir()) if path.is_dir()]
    active, selection = active_candidates(candidates)

    company_rows = defaultdict(list)
    for row in active:
        company_rows[row["identity"]["stock_code"]].append(row)

    company_catalog = []
    report_catalog = []
    extraction_issues = []
    all_evidence = {}
    three_statement_latest = {}

    for code, rows in sorted(company_rows.items()):
        rows.sort(key=lambda row: (row["report_year"] or 0, row["report_id"]), reverse=True)
        identity = rows[0]["identity"]
        v641_company = v641.get(code)
        three_statement_payload = build_three_statement_payload(rows[0], v641_company)
        if three_statement_payload:
            three_statement_latest[code] = three_statement_payload
        company_dir = output / "companies" / identity["company_id"]
        (company_dir / "metrics").mkdir(parents=True)
        (company_dir / "reports").mkdir()
        (company_dir / "evidence").mkdir()
        (company_dir / "analysis").mkdir()

        company_reports = []
        company_image_manifest = []
        company_evidence = []
        pdf_refs = []

        for row in rows:
            report_dir = company_dir / "reports" / row["report_id"]
            report_dir.mkdir(parents=True)
            shutil.copy2(row["markdown_path"], report_dir / "report.md")
            shutil.copy2(Path(row["result_dir"]) / "document_full.json", report_dir / "document_full.json")
            images = copy_report_assets(row, report_dir)
            report_payload = three_statement_payload if row is rows[0] else build_financial_data_three_statements(row)
            evidence = build_three_statement_evidence(row, report_payload) if report_payload else build_fallback_evidence(row)
            report_json = build_report_json(row, images, evidence)
            write_json(report_dir / "report.json", report_json)
            company_image_manifest.extend(images)
            company_evidence.extend(evidence)
            for item in evidence:
                key = (item.get("pdf_page_number"), item.get("table_index"))
                if not key[0] and not key[1]:
                    continue
                ref = {
                    "company_id": identity["company_id"],
                    "report_id": row["report_id"],
                    "task_id": row["task_id"],
                    "pdf_page_number": item.get("pdf_page_number"),
                    "table_index": item.get("table_index"),
                    "md_line": item.get("md_line"),
                }
                ref.update(evidence_urls(row["task_id"], item.get("pdf_page_number"), item.get("table_index")))
                pdf_refs.append(ref)
            report_entry = {
                "report_id": row["report_id"],
                "report_year": row["report_year"],
                "report_kind": row["report_kind"],
                "status": report_json["status"],
                "task_id": row["task_id"],
                "source_filename": row["filename"],
                "source_filename_metadata": report_source_metadata(row["filename"]),
                "report_md": f"reports/{row['report_id']}/report.md",
                "report_json": f"reports/{row['report_id']}/report.json",
                "document_full": f"reports/{row['report_id']}/document_full.json",
            }
            company_reports.append(report_entry)
            report_catalog.append({**identity, **report_entry, "company_path": f"companies/{identity['company_id']}"})
            if report_json["status"] != "ready":
                extraction_issues.append(
                    {
                        "company_id": identity["company_id"],
                        "stock_code": code,
                        "report_id": row["report_id"],
                        "task_id": row["task_id"],
                        "warnings": row["warnings"],
                    }
                )

        three_statement_source = three_statement_payload_source(three_statement_payload)
        write_json(company_dir / "metrics" / "three_statements.json", {"schema_version": 1, "source": three_statement_source, "unit": "亿元", "data": three_statement_payload or {}, "generated_at": now_iso()})
        write_json(company_dir / "metrics" / "key_metrics.json", {"schema_version": 1, "source": "financial_data.json", "data": rows[0]["financial_data"].get("key_metrics") or [], "generated_at": now_iso()})
        write_json(company_dir / "metrics" / "validation.json", {"schema_version": 1, "financial_checks": rows[0]["financial_checks"], "wiki_v641_available": bool(v641_company), "three_statement_source": three_statement_source, "three_statement_metric_count": len((three_statement_payload or {}).get("metrics") or []), "generated_at": now_iso()})
        write_json(company_dir / "evidence" / "evidence_index.json", {"schema_version": 1, "company_id": identity["company_id"], "evidence_count": len(company_evidence), "evidence": company_evidence, "generated_at": now_iso()})
        write_json(company_dir / "evidence" / "pdf_refs.json", {"schema_version": 1, "company_id": identity["company_id"], "refs": pdf_refs, "generated_at": now_iso()})
        write_json(company_dir / "evidence" / "image_manifest.json", {"schema_version": 1, "company_id": identity["company_id"], "images": company_image_manifest, "generated_at": now_iso()})

        company_json = {
            "schema_version": 1,
            **identity,
            "primary_report_id": company_reports[0]["report_id"],
            "reports": company_reports,
            "metrics": {
                "three_statements": "metrics/three_statements.json",
                "key_metrics": "metrics/key_metrics.json",
                "validation": "metrics/validation.json",
            },
            "evidence": {
                "evidence_index": "evidence/evidence_index.json",
                "pdf_refs": "evidence/pdf_refs.json",
                "image_manifest": "evidence/image_manifest.json",
            },
            "generated_at": now_iso(),
        }
        write_json(company_dir / "company.json", company_json)
        write_text(company_dir / "company.md", build_company_md(identity, company_reports, three_statement_payload))
        write_text(company_dir / "analysis" / "README.md", build_analysis_readme(identity))

        company_catalog.append(
            {
                **identity,
                "company_path": f"companies/{identity['company_id']}",
                "primary_report_id": company_reports[0]["report_id"],
                "report_count": len(company_reports),
                "status": "ready" if all(r["status"] == "ready" for r in company_reports) else "needs_review",
                "has_v641_metrics": bool(v641_company),
                "has_three_statement_metrics": bool((three_statement_payload or {}).get("metrics")),
                "three_statement_source": three_statement_source,
            }
        )
        all_evidence[code] = company_evidence

    if three_statement_latest:
        write_json(output / "derived" / "three_statements_latest.json", three_statement_latest)
    build_sqlite(output / "derived" / "financial_metrics.db", company_catalog, report_catalog, three_statement_latest)

    write_json(output / "_meta" / "company_catalog.json", {"schema_version": 1, "generated_at": now_iso(), "company_count": len(company_catalog), "companies": company_catalog})
    write_json(output / "_meta" / "report_catalog.json", {"schema_version": 1, "generated_at": now_iso(), "report_count": len(report_catalog), "reports": report_catalog})
    write_json(
        output / "_meta" / "import_manifest.json",
        {
            "schema_version": 1,
            "generated_at": now_iso(),
            "source_results_dir": str(source_root),
            "task_db": str(pdf2md_root / "tasks.db"),
            "candidate_count": len(candidates),
            "active_report_count": len(active),
            "company_count": len(company_catalog),
            "selection": selection,
            "file_policy": {
                "copied": ["result_complete.md", "document_full.json", "referenced/high_value images"],
                "derived": ["company.json", "report.json", "metrics", "evidence", "catalogs"],
                "referenced_only": ["uploads pdf", "pdf_pages"],
            },
        },
    )
    write_json(output / "_meta" / "extraction_issues.json", {"schema_version": 1, "generated_at": now_iso(), "issues": extraction_issues, "selection": selection})
    guide = agent_guide()
    write_text(output / "_meta" / "AGENT_GUIDE.md", guide)
    write_text(output / "AGENTS.md", guide)
    write_text(output / "README.md", root_readme(len(company_catalog), len(report_catalog)))
    return {
        "output": str(output),
        "company_count": len(company_catalog),
        "report_count": len(report_catalog),
        "candidate_count": len(candidates),
        "duplicates": len(selection["duplicates"]),
        "skipped": len(selection["skipped"]),
    }


def build_derived_layers(wiki_root: Path) -> dict:
    script_dir = Path(__file__).resolve().parent
    steps = [
        ("semantic", script_dir / "extract_company_semantics.py"),
        ("obsidian", script_dir / "generate_obsidian_graph.py"),
    ]
    results = []
    for name, script in steps:
        completed = subprocess.run(
            [sys.executable, str(script), "--wiki-root", str(wiki_root)],
            check=False,
            text=True,
            capture_output=True,
        )
        results.append(
            {
                "layer": name,
                "script": str(script),
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{name} layer generation failed: {completed.stderr or completed.stdout}")
    return {
        "enabled": True,
        "results": results,
    }


def build_sqlite(path: Path, companies: list[dict], reports: list[dict], three_statement_payloads: dict) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("create table companies (stock_code text primary key, company_id text, company_short_name text, company_full_name text, exchange text, company_path text, status text)")
    cur.execute("create table reports (stock_code text, report_id text, report_year integer, report_kind text, task_id text, report_md text, report_json text, primary key(stock_code, report_id))")
    cur.execute("create table three_statement_metrics (id integer primary key autoincrement, stock_code text, company_id text, report_id text, company_name text, statement_type text, metric_key text, raw_value text, normalized_value real, unit text, md_line integer, pdf_page_number integer, table_index integer, task_id text, open_pdf_page_url text, open_source_table_url text, extraction_method text)")
    cur.execute("create table validation_anomalies (id integer primary key autoincrement, stock_code text, company_id text, report_id text, severity text, message text)")
    report_by_code = {}
    for report in reports:
        report_by_code.setdefault(report["stock_code"], report)
    for c in companies:
        cur.execute("insert into companies values (?,?,?,?,?,?,?)", (c["stock_code"], c["company_id"], c["company_short_name"], c["company_full_name"], c["exchange"], c["company_path"], c["status"]))
    for r in reports:
        cur.execute("insert or replace into reports values (?,?,?,?,?,?,?)", (r["stock_code"], r["report_id"], r["report_year"], r["report_kind"], r["task_id"], r["report_md"], r["report_json"]))
    for code, payload in (three_statement_payloads or {}).items():
        report = report_by_code.get(code)
        if not report:
            continue
        for metric in payload.get("metrics") or []:
            source = metric.get("source") or {}
            page = source.get("pdf_page") or source.get("pdf_page_number") or None
            table = source.get("table_index") or None
            urls = evidence_urls(report["task_id"], page, table)
            source_kind = source.get("source_kind") or ""
            extraction_method = "financial_data_statement_ingest_v1" if source_kind == "financial_data_statement" else "v6.41_rebuild"
            cur.execute(
                "insert into three_statement_metrics (stock_code, company_id, report_id, company_name, statement_type, metric_key, raw_value, normalized_value, unit, md_line, pdf_page_number, table_index, task_id, open_pdf_page_url, open_source_table_url, extraction_method) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    code,
                    report["company_id"],
                    report["report_id"],
                    payload.get("company"),
                    metric.get("statement_type"),
                    metric.get("metric_key"),
                    metric.get("raw_value"),
                    metric.get("normalized_value"),
                    "亿元",
                    source.get("md_line") or source.get("line"),
                    page,
                    table,
                    source.get("task_id") or report["task_id"],
                    urls["open_pdf_page_url"],
                    urls["open_source_table_url"],
                    extraction_method,
                ),
            )
    cur.execute("create index idx_tsm_stock on three_statement_metrics(stock_code)")
    cur.execute("create index idx_tsm_metric on three_statement_metrics(metric_key)")
    cur.execute("create index idx_reports_stock on reports(stock_code)")
    conn.commit()
    conn.close()


def agent_guide() -> str:
    return """# Agent Guide

本 wiki 以单个上市公司为主入口。

主体身份规则：

- `stock_code` 是上市公司业务唯一锚点。
- `company_short_name` / `stock_name` 是独立简称字段。
- `company_id` 是兼容 wiki 路径和既有关联表的技术 ID，当前采用 `股票代码-公司简称` slug，不应把它当成不可拆的业务主键。
- 下载后的 PDF 文件名是报告实例来源契约，推荐格式为 `<公司简称>_<市场>_<股票代码>_<报告截止日>_<报告类型>_<公告日期>_<来源>_<hash>.pdf`。
- wiki 公司目录只能采用 `companies/<股票代码>-<公司简称>/`。市场、报告截止日、公告日期、来源和 hash 只进入 `reports/<report_id>/report.json`、catalog 和 evidence metadata，不能进入 `company_id` 或公司目录名。
- 存档基准样本为 `companies/000333-美的集团/`：公司主体字段干净，报告实例与来源文件名保存在 `reports/2025-annual/report.json`。
- 完整规则见 `_meta/wiki_naming_contract.md`。

	默认检索逻辑：

	1. `_meta/company_catalog.json` 定位公司。
	2. `companies/<company_id>/company.json` 读取公司机器入口，并先解析本次回答的 `report_id`。
	3. 报告期选择优先级：用户明确给出 `report_id`、年报/annual/12-31、季报/quarter/09-30 等报告类型或截止日时，必须匹配 `company.json.reports` 或 `_meta/report_catalog.json` 中对应报告；用户仅说年份时优先选择该年度年报，其次选择同年度 `primary_report_id`；用户未指定报告期时才使用 `company.json.primary_report_id` 或 `metrics/latest/`，并在回答中说明采用的报告口径。
	4. `companies/<company_id>/company.md` 只用于公司总览和人工可读摘要，不作为财务数字的最终来源。
	5. 先按问题类型路由，再读取文件；不得用一个线性顺序回答所有问题。
	6. 主表、核心指标和所有财务数字，先读 `companies/<company_id>/metrics/reports/<report_id>/three_statements.json`、`key_metrics.json`、`validation.json`；未指定报告期时读取 `metrics/latest/`，旧路径 `metrics/three_statements.json`、`metrics/key_metrics.json`、`metrics/validation.json` 仅作为兼容入口。
	7. `companies/<company_id>/evidence/evidence_index.json` 用于财务指标证据链、PDF 页码和表格入口。
	8. `companies/<company_id>/semantic/retrieval_index.json` 用于语义路由，facts、relations、claims、segments 用于管理层讨论、风险因素、业务结构和主表项目附注展开，不得替代主表数值来源。
	9. `companies/<company_id>/reports/<report_id>/report.md` 用于读取年报原文上下文和 Markdown 页码锚点。
	10. `companies/<company_id>/reports/<report_id>/document_full.json` 仅在深度审计、重放、页码/表格证据补全失败时读取。

	问题类型路由：

	- 主表数值、同比、利润、现金流、资产负债、ROE、偿债和经营质量：只以 `metrics/reports/<report_id>/` 或 `metrics/latest/` 为第一事实源，必须结合 `validation.json` 和 `evidence/evidence_index.json` 回到正文主表 PDF 页与 `table_index`。
	- 附注明细、构成、分布、组成、减值准备、账龄、前五名、资产组、可收回金额、变动等：先走 `semantic/document_links.json`、`semantic/note_links.json` 或 `note_detail_lookup.py`，再读取 `report.md` 对应表格行；不得因为标准 `metrics` 无字段就回答“无法展示”。
	- 业务结构、产品、区域、客户供应商、治理、管理层讨论和风险因素：先用 `semantic/retrieval_index.json` 找 topic/segment/evidence，再读 `semantic/segments.json`、`facts.json`、`claims.json` 和 `report.md` 原文确认。
	- 战略、经营变化、风险归纳和重大事件：可以读取 `semantic/llm/<report_id>/business_profile.json`、`risks.json`、`events.json`、`claims.json`，但 LLM 层只作为语义候选；只使用 `needs_review=false` 且带 `evidence_ids` 或 `source_segment_ids` 的条目，并回链到规则层证据或 `report.md` 后再回答。LLM 层不得抽取或改写财务金额。
	- 已生成的 `analysis/`、`factcheck/`、`tracking/`、`legal/` 产物默认不是公司事实源；只有用户明确询问这些产物、报告结论或历史输出时才读取。

	证据可信度优先级：

	1. `metrics/reports/<report_id>/` + `validation.json` + 结构化 `pdf_page_number/table_index`。
	2. `evidence/evidence_index.json` 和 `pdf_refs.json`。
	3. `semantic/document_links.json`、`semantic/note_links.json` 命中的附注表格行。
	4. `semantic/facts.json`、`relations.json`、`claims.json`、`segments.json` 且带 evidence/segment 回链的记录。
	5. `semantic/llm/<report_id>/` 中 `needs_review=false` 且可回链的记录。
	6. `report.md` 关键词命中的原文段落和 `[PDF_PAGE: n]` 锚点。
	7. `document_full.json` 或 PostgreSQL/pdf2md 只做深度审计、补页码、补表格或冲突交叉验证。

	同一事实多源冲突时，默认采用可信度更高的来源，并在回答中说明冲突来源和采用原因；不得混用两个来源的同一指标。

	报告中引用财报数据时，必须携带股票代码、报告年、PDF 页码和表格索引。PDF 页面展示由 `/home/maoyd/pdf2md_web` 提供，不在 wiki 内复制 `pdf_pages/`。

	若 `evidence/evidence_index.json` 查不到用户询问的指标或附注事项，智能体必须在回答或引用说明中显式写明“该指标在证据索引中无独立条目”，再说明后续页码来自 `report.md` 标记、`metrics/*.json`、`semantic/note_links.json`、`semantic/document_links.json` 或 `document_full.json` 的 fallback 解析；不得假装 evidence_index 已命中。

单个主体信息关系抽取规则见 `_meta/single_company_subject_extraction_rules.md`。语义层尚未生成时，智能体应按该规则从现有 `report.json`、`document_full.json`、`report.md`、`metrics` 和 `evidence` 中临时抽取，并在回答中保留证据链。

审计财务报表项目、会计科目明细或附注解释时，应优先读取 `companies/<company_id>/semantic/document_links.json`，再读取 `semantic/note_links.json`。`document_links.json` 保存通用“报表项目 -> 附注 -> 同节表格”的跳转图；该机制适用于应收、存货、商誉、固定资产、借款、收入成本、减值、关联方等所有可结构化项目，不是商誉专用。

Obsidian 可视化入口为 `companies/<company_id>/obsidian/index.md` 和 `companies/<company_id>/graph/`。该层由 `semantic/` 派生，只用于 Markdown 双链图谱展示；事实、指标和证据审计仍以 `semantic/`、`metrics/`、`evidence/` 和 `report.md` 为准。
"""


def root_readme(company_count: int, report_count: int) -> str:
    return f"""# 上市公司年报分析 Wiki

本 wiki 由 `/home/maoyd/pdf2md_web/results` 重构生成。

- 公司数量：{company_count}
- 主报告数量：{report_count}
- 组织方式：以单个上市公司为一级分析对象
- 主体身份：`stock_code` 是业务唯一锚点，简称独立存储；`company_id` 仅作为 wiki 路径/技术 ID
- 命名契约：下载 PDF 文件名可解析公司简称和股票代码；wiki 公司目录统一为 `companies/<股票代码>-<公司简称>/`，报告实例统一为 `reports/<年度>-<报告类型slug>/`
- 证据链：Markdown 行号、PDF 页码、表格索引、pdf2md task_id
- 单体语义层：运行 `wikiset/extract_company_semantics.py` 后生成 `semantic/`，覆盖主题片段、事实、关系、附注对应关系、可审计 claims 与智能体检索入口

入口：

- `_meta/company_catalog.json`
- `_meta/semantic_extraction_manifest.json`
- `_meta/obsidian_graph_manifest.json`
- `_meta/AGENT_GUIDE.md`
- `companies/`

	单家公司推荐检索入口：

	实际问答必须以 `_meta/AGENT_GUIDE.md` 的报告期解析、问题类型路由和证据可信度优先级为准。常用入口包括：

	1. `companies/<company_id>/company.json`
	2. `companies/<company_id>/metrics/reports/<report_id>/three_statements.json`
	3. `companies/<company_id>/metrics/reports/<report_id>/key_metrics.json`
	4. `companies/<company_id>/metrics/reports/<report_id>/validation.json`
	5. `companies/<company_id>/evidence/evidence_index.json`
	6. `companies/<company_id>/semantic/retrieval_index.json`
	7. `companies/<company_id>/semantic/document_links.json`
	8. `companies/<company_id>/semantic/note_links.json`
	9. `companies/<company_id>/semantic/segments.json`
	10. `companies/<company_id>/reports/<report_id>/report.md`
	11. `companies/<company_id>/obsidian/index.md`，仅用于 Obsidian 可视化

固定重构流水线：

1. `python3 scripts/wiki/wikiset/rebuild_wiki_v2.py --output <new_wiki_dir>`
2. 默认自动生成 `semantic/`、`graph/` 和 `obsidian/`

如果通过自定义部署目录运行，可以设置 `SIQ_WIKISET_ROOT` 指向包含 wikiset 脚本的代码目录。`graph/` 和 `obsidian/` 是可视化派生层，不替代 JSON 事实和证据链。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf2md-root", default="/home/maoyd/pdf2md_web")
    parser.add_argument("--results-dir", default="/home/maoyd/pdf2md_web/results")
    parser.add_argument("--old-wiki", default="/home/maoyd/wiki")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--skip-derived-layers",
        action="store_true",
        help="Only rebuild the base wiki; skip semantic and Obsidian graph generation.",
    )
    args = parser.parse_args()
    summary = rebuild(args)
    if not args.skip_derived_layers:
        summary["derived_layers"] = build_derived_layers(Path(summary["output"]))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
