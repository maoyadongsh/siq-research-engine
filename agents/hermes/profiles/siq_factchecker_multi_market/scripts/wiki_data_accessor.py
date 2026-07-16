#!/usr/bin/env python3
"""
SIQ_factchecker Wiki 数据访问封装模块
复用 SIQ_analysis 的数据访问层，保持数据优先级一致
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# 从环境变量读取基础路径
WIKI_DIR = Path(os.environ.get("WIKI_DIR", "/home/maoyd/siq-research-engine/data/wiki"))
COMPANY_CATALOG_PATH = WIKI_DIR / "_meta" / "company_catalog.json"


@dataclass
class CompanyInfo:
    """公司基本信息"""
    company_id: str
    stock_code: str
    exchange: str
    company_short_name: str
    company_full_name: str
    industry_sw1: str
    industry_sw2: str
    industry_sw3: str
    industry_sw1_code: str
    industry_sw2_code: str
    industry_sw3_code: str
    primary_report_id: str
    status: str
    has_v641_metrics: bool
    company_path: Path
    market: str = "CN"
    reporting_currency: str = ""
    accounting_standard: str = ""


@dataclass
class FinancialMetrics:
    """财务指标数据"""
    three_statements: Dict[str, Any]
    key_metrics: Dict[str, Any]
    validation: Dict[str, Any]


@dataclass
class EvidenceChain:
    """证据链数据"""
    evidence_index: Dict[str, Any]
    pdf_refs: Dict[str, Any]


@dataclass
class SemanticData:
    """语义层数据"""
    retrieval_index: Dict[str, Any]
    subject_profile: Optional[Dict[str, Any]]
    facts: Optional[Dict[str, Any]]
    claims: Optional[Dict[str, Any]]
    document_links: Optional[Dict[str, Any]]
    note_links: Optional[Dict[str, Any]]


class WikiDataAccessor:
    """
    Wiki 数据访问器
    严格遵循 SOUL.md 数据读取优先级
    """

    def __init__(
        self,
        wiki_dir: Optional[Path] = None,
        *,
        company_record: Optional[Dict[str, Any]] = None,
        company_dir: Optional[Path] = None,
        report_id: str = "",
    ):
        self.wiki_dir = (wiki_dir or WIKI_DIR).expanduser().resolve()
        self.catalog_path = self.wiki_dir / "_meta" / "company_catalog.json"
        self._catalog: Optional[Dict] = None
        self._companies: Dict[str, CompanyInfo] = {}
        self._injected_company_record = dict(company_record or {})
        self._injected_company_dir = company_dir.expanduser().resolve() if company_dir else None
        self.report_id = str(report_id or "").strip()

    # ============================================================
    # 第一层：公司目录与定位
    # ============================================================

    def load_catalog(self) -> Dict:
        """加载公司目录"""
        if self._catalog is None:
            if self._injected_company_record:
                self._catalog = {"companies": [self._injected_company_record]}
            else:
                with open(self.catalog_path, "r", encoding="utf-8") as f:
                    self._catalog = json.load(f)
        return self._catalog

    def _company_path(self, record: Dict[str, Any]) -> Path:
        if self._injected_company_dir is not None:
            return self._injected_company_dir
        raw = record.get("company_path") or record.get("company_wiki_path")
        if raw:
            path = Path(str(raw)).expanduser()
            if path.is_absolute():
                return path.resolve()
            direct = (self.wiki_dir / path).resolve()
            if direct.exists():
                return direct
            parts = path.parts
            if "companies" in parts:
                return self.wiki_dir.joinpath(*parts[parts.index("companies"):]).resolve()
        wiki_id = record.get("company_wiki_id") or record.get("company_id")
        return (self.wiki_dir / "companies" / str(wiki_id or "unknown")).resolve()

    def list_companies(self) -> List[CompanyInfo]:
        """列出所有工作集公司"""
        catalog = self.load_catalog()
        companies = []
        for c in catalog.get("companies", []):
            if not isinstance(c, dict):
                continue
            company_id = str(c.get("company_id") or "").strip()
            code = str(c.get("stock_code") or c.get("ticker") or c.get("security_code") or company_id).strip()
            short_name = str(c.get("company_short_name") or c.get("company_name") or c.get("company_full_name") or code).strip()
            info = CompanyInfo(
                company_id=company_id,
                stock_code=code,
                exchange=str(c.get("exchange") or ""),
                company_short_name=short_name,
                company_full_name=str(c.get("company_full_name") or c.get("company_name") or short_name),
                industry_sw1=c.get("industry_sw1", ""),
                industry_sw2=c.get("industry_sw2", ""),
                industry_sw3=c.get("industry_sw3", ""),
                industry_sw1_code=c.get("industry_sw1_code", ""),
                industry_sw2_code=c.get("industry_sw2_code", ""),
                industry_sw3_code=c.get("industry_sw3_code", ""),
                primary_report_id=self.report_id or c.get("primary_report_id", ""),
                status=c.get("status", ""),
                has_v641_metrics=c.get("has_v641_metrics", False),
                company_path=self._company_path(c),
                market=str(c.get("market") or "CN").upper(),
                reporting_currency=str(c.get("currency") or c.get("reporting_currency") or ""),
                accounting_standard=str(c.get("accounting_standard") or ""),
            )
            self._companies[company_id] = info
            companies.append(info)
        return companies

    def get_company_by_id(self, company_id: str) -> Optional[CompanyInfo]:
        """通过 company_id 获取公司信息"""
        if company_id in self._companies:
            return self._companies[company_id]
        companies = self.list_companies()
        for c in companies:
            if c.company_id == company_id:
                return c
        return None

    def get_company_by_stock_code(self, stock_code: str) -> Optional[CompanyInfo]:
        """通过股票代码获取公司信息"""
        companies = self.list_companies()
        for c in companies:
            if c.stock_code == stock_code:
                return c
        return None

    def get_companies_by_industry(self, sw1_code: Optional[str] = None,
                                   sw2_code: Optional[str] = None) -> List[CompanyInfo]:
        """按行业筛选公司"""
        companies = self.list_companies()
        result = []
        for c in companies:
            if sw1_code and c.industry_sw1_code == sw1_code:
                result.append(c)
            elif sw2_code and c.industry_sw2_code == sw2_code:
                result.append(c)
        return result

    # ============================================================
    # 第二层：机器入口 (company.json)
    # ============================================================

    def load_company_json(self, company_id: str) -> Optional[Dict]:
        """读取 company.json"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        path = company.company_path / "company.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_metric_json(self, company: CompanyInfo, filename: str) -> Optional[Dict]:
        """按当前 Wiki 契约读取指标：report 专属 -> latest -> 旧兼容路径。"""
        report_id = self.report_id or company.primary_report_id
        candidates = [
            company.company_path / "metrics" / "reports" / report_id / filename,
            company.company_path / "metrics" / "latest" / filename,
            company.company_path / "metrics" / filename,
            company.company_path / "reports" / report_id / "metrics" / filename,
        ]
        for path in candidates:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    @staticmethod
    def _normalized_to_key_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
        rows = payload.get("metrics")
        if not isinstance(rows, list):
            return payload
        grouped: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("segment_key") or row.get("scope") or "consolidated") not in {"", "consolidated"}:
                continue
            canonical = str(row.get("canonical_name") or row.get("metric_key") or "").strip()
            period = str(row.get("period_key") or row.get("period") or row.get("period_end") or "").strip()
            unit = str(row.get("unit") or row.get("currency") or "").strip()
            value = row.get("value", row.get("normalized_value", row.get("raw_value")))
            if not canonical or not period or value in (None, ""):
                continue
            item = grouped.setdefault(
                (canonical, unit),
                {
                    "canonical_name": canonical,
                    "name": row.get("metric_name") or row.get("label") or row.get("local_name") or canonical,
                    "unit": unit,
                    "values": {},
                    "evidence_refs_by_period": {},
                },
            )
            try:
                item["values"][period] = float(value)
            except (TypeError, ValueError):
                continue
            source = row.get("source") if isinstance(row.get("source"), dict) else {}
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
            raw_fact = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
            ref = {
                "source_type": source.get("source_type") or ("xbrl_fact" if row.get("raw_fact_id") else "normalized_metric"),
                "task_id": source.get("task_id"),
                "pdf_page": source.get("pdf_page") or source.get("pdf_page_number"),
                "table_index": source.get("table_index"),
                "md_line": source.get("md_line"),
                "source_url": source.get("source_url") or raw.get("source_url"),
                "html_anchor": source.get("html_anchor") or raw_fact.get("anchor"),
                "xbrl_fact_id": row.get("raw_fact_id") or raw_fact.get("fact_id") or raw_fact.get("id"),
                "xbrl_concept": row.get("concept") or raw.get("concept"),
                "xbrl_context": raw.get("context_id") or raw_fact.get("context_ref") or raw_fact.get("contextRef"),
                "xbrl_unit": row.get("currency") or row.get("unit"),
                "quote": source.get("quote_text") or source.get("quote"),
            }
            ref = {key: value for key, value in ref.items() if value not in (None, "")}
            if ref:
                item["evidence_refs_by_period"].setdefault(period, []).append(ref)
        return {"schema_version": "key_metrics_projection_v1", "data": list(grouped.values())}

    # ============================================================
    # 第三层：财务指标 (metrics/*.json) — 最高优先级
    # ============================================================

    def load_three_statements(self, company_id: str) -> Optional[Dict]:
        """读取三表数据，优先 metrics/reports/<primary_report_id>/，再 latest/，最后旧路径。"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        return self._load_metric_json(company, "three_statements.json")

    def load_key_metrics(self, company_id: str) -> Optional[Dict]:
        """读取关键指标，优先 metrics/reports/<primary_report_id>/，再 latest/，最后旧路径。"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        payload = self._load_metric_json(company, "key_metrics.json")
        if payload:
            return payload
        normalized = self._load_metric_json(company, "normalized_metrics.json")
        return self._normalized_to_key_metrics(normalized) if normalized else None

    def load_validation(self, company_id: str) -> Optional[Dict]:
        """读取校验结果，优先 metrics/reports/<primary_report_id>/，再 latest/，最后旧路径。"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        return self._load_metric_json(company, "validation.json") or self._load_metric_json(company, "financial_checks.json")

    def load_all_metrics(self, company_id: str) -> Optional[FinancialMetrics]:
        """加载全部财务指标"""
        ts = self.load_three_statements(company_id)
        km = self.load_key_metrics(company_id)
        val = self.load_validation(company_id)
        if not km:
            return None
        return FinancialMetrics(
            three_statements=ts or {"schema_version": "three_statements_unavailable", "statements": {}},
            key_metrics=km,
            validation=val or {}
        )

    # ============================================================
    # 第四层：证据链 (evidence/*.json)
    # ============================================================

    def load_evidence_index(self, company_id: str) -> Optional[Dict]:
        """读取证据索引"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        report_id = self.report_id or company.primary_report_id
        candidates = [
            company.company_path / "evidence" / "evidence_index.json",
            company.company_path / "evidence" / "source_map_latest.json",
            company.company_path / "reports" / report_id / "qa" / "source_map.json",
        ]
        for path in candidates:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def load_pdf_refs(self, company_id: str) -> Optional[Dict]:
        """读取 PDF 引用"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        path = company.company_path / "evidence" / "pdf_refs.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ============================================================
    # 第五层：语义层 (semantic/*.json)
    # ============================================================

    def load_retrieval_index(self, company_id: str) -> Optional[Dict]:
        """读取语义检索索引"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        path = company.company_path / "semantic" / "retrieval_index.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_semantic_data(self, company_id: str) -> Optional[SemanticData]:
        """加载全部语义层数据"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None

        semantic_dir = company.company_path / "semantic"
        if not semantic_dir.exists():
            return None

        def _load(filename: str) -> Optional[Dict]:
            path = semantic_dir / filename
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        return SemanticData(
            retrieval_index=_load("retrieval_index.json") or {},
            subject_profile=_load("subject_profile.json"),
            facts=_load("facts.json"),
            claims=_load("claims.json"),
            document_links=_load("document_links.json"),
            note_links=_load("note_links.json"),
        )

    # ============================================================
    # 第六层：报告原文 (reports/<report_id>/report.md)
    # ============================================================

    def load_report_md(self, company_id: str, report_id: Optional[str] = None) -> Optional[str]:
        """读取年报 Markdown 原文"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        if not report_id:
            report_id = company.primary_report_id
        report_dir = company.company_path / "reports" / report_id
        for path in (
            report_dir / "report.md",
            report_dir / "sections" / "report_complete.md",
            report_dir / "parser" / "report_complete.md",
        ):
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return None

    def load_report_json(self, company_id: str, report_id: Optional[str] = None) -> Optional[Dict]:
        """读取报告结构化数据"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        if not report_id:
            report_id = company.primary_report_id
        report_dir = company.company_path / "reports" / report_id
        for path in (report_dir / "report.json", report_dir / "sections.json"):
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def load_document_full(self, company_id: str, report_id: Optional[str] = None) -> Optional[Dict]:
        """读取完整文档结构（含 financial_data、content_list_enhanced）"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        if not report_id:
            report_id = company.primary_report_id
        report_dir = company.company_path / "reports" / report_id
        for path in (report_dir / "document_full.json", report_dir / "parser" / "document_full.json"):
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    # ============================================================
    # 第七层：分析与核查输出目录
    # ============================================================

    def get_analysis_dir(self, company_id: str) -> Path:
        """获取公司分析输出目录"""
        company = self.get_company_by_id(company_id)
        if not company:
            return self.wiki_dir / "analysis"
        return company.company_path / "analysis"

    def ensure_analysis_dir(self, company_id: str) -> Path:
        """确保分析输出目录存在"""
        analysis_dir = self.get_analysis_dir(company_id)
        analysis_dir.mkdir(parents=True, exist_ok=True)
        return analysis_dir

    def get_factcheck_dir(self, company_id: str) -> Path:
        """获取公司事实核查输出目录"""
        company = self.get_company_by_id(company_id)
        if not company:
            return self.wiki_dir / "factcheck"
        return company.company_path / "factcheck"

    def ensure_factcheck_dir(self, company_id: str) -> Path:
        """确保事实核查输出目录存在"""
        factcheck_dir = self.get_factcheck_dir(company_id)
        factcheck_dir.mkdir(parents=True, exist_ok=True)
        return factcheck_dir

    # ============================================================
    # 便捷方法：一键加载公司全部数据
    # ============================================================

    def load_company_full(self, company_id: str) -> Dict[str, Any]:
        """
        一键加载公司全部可用数据
        返回字典，包含所有层级数据
        """
        result = {
            "company_id": company_id,
            "company_info": None,
            "company_json": None,
            "metrics": None,
            "evidence": None,
            "semantic": None,
            "report_md": None,
            "report_json": None,
            "document_full": None,
            "data_availability": {},
        }

        # 公司信息
        info = self.get_company_by_id(company_id)
        result["company_info"] = info
        result["data_availability"]["company_info"] = info is not None

        # 机器入口
        result["company_json"] = self.load_company_json(company_id)
        result["data_availability"]["company_json"] = result["company_json"] is not None

        # 财务指标（最高优先级）
        result["metrics"] = self.load_all_metrics(company_id)
        result["data_availability"]["three_statements"] = result["metrics"] is not None
        result["data_availability"]["key_metrics"] = result["metrics"] is not None
        result["data_availability"]["validation"] = result["metrics"] is not None and result["metrics"].validation is not None

        # 证据链
        evidence_index = self.load_evidence_index(company_id)
        pdf_refs = self.load_pdf_refs(company_id)
        result["evidence"] = {
            "evidence_index": evidence_index,
            "pdf_refs": pdf_refs,
        }
        result["data_availability"]["evidence_index"] = evidence_index is not None
        result["data_availability"]["pdf_refs"] = pdf_refs is not None

        # 语义层
        result["semantic"] = self.load_semantic_data(company_id)
        result["data_availability"]["semantic"] = result["semantic"] is not None

        # 报告原文
        result["report_md"] = self.load_report_md(company_id)
        result["report_json"] = self.load_report_json(company_id)
        result["document_full"] = self.load_document_full(company_id)
        result["data_availability"]["report_md"] = result["report_md"] is not None
        result["data_availability"]["report_json"] = result["report_json"] is not None
        result["data_availability"]["document_full"] = result["document_full"] is not None

        return result
