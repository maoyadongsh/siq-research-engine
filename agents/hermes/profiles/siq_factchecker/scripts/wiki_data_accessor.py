#!/usr/bin/env python3
"""
SIQ_factchecker Wiki 数据访问封装模块
复用 SIQ_analysis 的数据访问层，保持数据优先级一致
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

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

    def __init__(self, wiki_dir: Optional[Path] = None):
        self.wiki_dir = wiki_dir or WIKI_DIR
        self._catalog: Optional[Dict] = None
        self._companies: Dict[str, CompanyInfo] = {}

    # ============================================================
    # 第一层：公司目录与定位
    # ============================================================

    def load_catalog(self) -> Dict:
        """加载公司目录"""
        if self._catalog is None:
            with open(COMPANY_CATALOG_PATH, "r", encoding="utf-8") as f:
                self._catalog = json.load(f)
        return self._catalog

    def list_companies(self) -> List[CompanyInfo]:
        """列出所有工作集公司"""
        catalog = self.load_catalog()
        companies = []
        for c in catalog.get("companies", []):
            info = CompanyInfo(
                company_id=c["company_id"],
                stock_code=c["stock_code"],
                exchange=c["exchange"],
                company_short_name=c["company_short_name"],
                company_full_name=c["company_full_name"],
                industry_sw1=c.get("industry_sw1", ""),
                industry_sw2=c.get("industry_sw2", ""),
                industry_sw3=c.get("industry_sw3", ""),
                industry_sw1_code=c.get("industry_sw1_code", ""),
                industry_sw2_code=c.get("industry_sw2_code", ""),
                industry_sw3_code=c.get("industry_sw3_code", ""),
                primary_report_id=c.get("primary_report_id", ""),
                status=c.get("status", ""),
                has_v641_metrics=c.get("has_v641_metrics", False),
                company_path=self.wiki_dir / c["company_path"],
            )
            self._companies[c["company_id"]] = info
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
        candidates = [
            company.company_path / "metrics" / "reports" / company.primary_report_id / filename,
            company.company_path / "metrics" / "latest" / filename,
            company.company_path / "metrics" / filename,
        ]
        for path in candidates:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

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
        return self._load_metric_json(company, "key_metrics.json")

    def load_validation(self, company_id: str) -> Optional[Dict]:
        """读取校验结果，优先 metrics/reports/<primary_report_id>/，再 latest/，最后旧路径。"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        return self._load_metric_json(company, "validation.json")

    def load_all_metrics(self, company_id: str) -> Optional[FinancialMetrics]:
        """加载全部财务指标"""
        ts = self.load_three_statements(company_id)
        km = self.load_key_metrics(company_id)
        val = self.load_validation(company_id)
        if not ts or not km:
            return None
        return FinancialMetrics(
            three_statements=ts,
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
        path = company.company_path / "evidence" / "evidence_index.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

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
        path = company.company_path / "reports" / report_id / "report.md"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def load_report_json(self, company_id: str, report_id: Optional[str] = None) -> Optional[Dict]:
        """读取报告结构化数据"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        if not report_id:
            report_id = company.primary_report_id
        path = company.company_path / "reports" / report_id / "report.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_document_full(self, company_id: str, report_id: Optional[str] = None) -> Optional[Dict]:
        """读取完整文档结构（含 financial_data、content_list_enhanced）"""
        company = self.get_company_by_id(company_id)
        if not company:
            return None
        if not report_id:
            report_id = company.primary_report_id
        path = company.company_path / "reports" / report_id / "document_full.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ============================================================
    # 第七层：分析与核查输出目录
    # ============================================================

    def get_analysis_dir(self, company_id: str) -> Path:
        """获取公司分析输出目录"""
        company = self.get_company_by_id(company_id)
        if not company:
            return WIKI_DIR / "analysis"
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
            return WIKI_DIR / "factcheck"
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
