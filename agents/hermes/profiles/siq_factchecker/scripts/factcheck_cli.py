#!/usr/bin/env python3
"""
SIQ_factchecker CLI v2.

No scores, no ratings. The checker emits an issue-driven verdict and a
machine-readable evidence trail for SIQ_analysis reports.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

from generate_factcheck_html import generate_html

scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))
shared_scripts_dir = Path("/home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts")
if str(shared_scripts_dir) not in sys.path:
    sys.path.insert(0, str(shared_scripts_dir))
# Legacy fallback: tracking scripts still ship a re-export shim.
tracking_scripts_dir = Path("/home/maoyd/siq-research-engine/data/wiki/tracking/scripts")
if str(tracking_scripts_dir) not in sys.path:
    sys.path.append(str(tracking_scripts_dir))

try:
    from wiki_data_accessor import WikiDataAccessor
except ImportError:
    print("错误: 无法加载 wiki_data_accessor")
    sys.exit(1)

try:
    from local_citations import collect_company_evidence_refs
except Exception:
    collect_company_evidence_refs = None

CN_TZ = timezone(timedelta(hours=8))
PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:8276").rstrip("/")
PROJECT_ROOT = Path(os.environ.get("SIQ_PROJECT_ROOT", "/home/maoyd/siq-research-engine")).expanduser()
COMPANY_INDEX_PUBLISHER = PROJECT_ROOT / "scripts/openshell/publish_company_index.py"
PUBLISHER_TIMEOUT_SECONDS = 30
MARKET_COMPANY_PREFIXES = {
    ("companies",): "cn",
    ("eu", "companies"): "eu",
    ("hk", "companies"): "hk",
    ("jp", "companies"): "jp",
    ("kr", "companies"): "kr",
    ("us", "companies"): "us",
}
CHECK_NAMES = {
    "data_consistency": "数据原文一致性",
    "calculation_consistency": "计算公式一致性",
    "traceability": "证据链完整性",
    "logic_support": "结论支撑充分性",
    "a_share_risk_completeness": "A股风险完整性",
    "template_compliance": "模板与规则合规性",
}


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def publish_company_index_after_host_run(company_dir: Path) -> dict[str, Any]:
    """Use the fixed host publisher; sandbox runs deliberately defer this write."""

    if os.environ.get("SIQ_OPENSHELL_SANDBOX") == "1":
        return {"ok": False, "deferred": True, "error_code": "sandbox_host_publish_required"}
    try:
        relative = company_dir.relative_to(PROJECT_ROOT / "data/wiki")
        market = MARKET_COMPANY_PREFIXES[tuple(relative.parts[:-1])]
        company_id = relative.parts[-1]
    except (KeyError, ValueError, IndexError):
        return {"ok": False, "deferred": True, "error_code": "company_index_identity_invalid"}
    if not COMPANY_INDEX_PUBLISHER.is_file() or COMPANY_INDEX_PUBLISHER.is_symlink():
        return {"ok": False, "deferred": True, "error_code": "publisher_script_missing"}
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(COMPANY_INDEX_PUBLISHER),
                "--project-root",
                str(PROJECT_ROOT),
                "--market",
                market,
                "--company-id",
                company_id,
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PUBLISHER_TIMEOUT_SECONDS,
            check=False,
            close_fds=True,
            start_new_session=True,
            env={
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            },
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "deferred": True, "error_code": "company_index_publish_timeout"}
    except OSError:
        return {"ok": False, "deferred": True, "error_code": "company_index_publish_failed"}
    if completed.returncode != 0:
        return {"ok": False, "deferred": True, "error_code": "company_index_publish_failed"}
    try:
        payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    expected_projection = hashlib.sha256(f"{market}:{company_id}".encode()).hexdigest()[:24]
    if not (
        isinstance(payload, dict)
        and payload.get("schema_version") == "siq.openshell.company_index_publish.v1"
        and payload.get("ok") is True
        and payload.get("market") == market
        and payload.get("company_projection") == expected_projection
        and payload.get("index_schema_version") == 1
    ):
        return {"ok": False, "deferred": True, "error_code": "company_index_publish_invalid"}
    return {"ok": True, "json": payload}


def _database_from_url(url: str, *, database: str = "siq") -> Dict[str, Any]:
    parsed = urlsplit(url.replace("postgresql+psycopg://", "postgresql://"))
    if parsed.scheme not in {"postgresql", "postgres"}:
        return {}
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": int(parsed.port or 15432),
        "dbname": database,
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
    }


def _project_pdf2md_pg_config() -> Dict[str, Any]:
    database = (
        os.environ.get("SIQ_PDF2MD_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or "siq"
    )
    explicit_url = os.environ.get("SIQ_PDF2MD_DATABASE_URL") or os.environ.get("SIQ_CN_DATABASE_URL")
    if explicit_url:
        parsed = _database_from_url(explicit_url, database=database)
        if parsed:
            return parsed

    app_url = os.environ.get("SIQ_APP_DATABASE_URL")
    app_config = _database_from_url(app_url, database=database) if app_url else {}
    return {
        "host": os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or app_config.get("host") or os.environ.get("DB_HOST") or "127.0.0.1",
        "port": int(os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or app_config.get("port") or os.environ.get("DB_PORT") or 15432),
        "dbname": database,
        "user": os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or app_config.get("user") or os.environ.get("DB_USER") or "postgres",
        "password": (
            os.environ.get("SIQ_PGPASSWORD")
            or os.environ.get("PGPASSWORD")
            or os.environ.get("POSTGRES_PASSWORD")
            or app_config.get("password")
            or os.environ.get("DB_PASSWORD")
            or ""
        ),
    }


CORE_METRICS = {
    "operating_revenue": "营业收入",
    "operating_cost": "营业成本",
    "net_profit": "净利润",
    "parent_net_profit": "归母净利润",
    "deducted_parent_net_profit": "扣非归母净利润",
    "total_assets": "资产总计",
    "total_liabilities": "负债合计",
    "total_equity": "所有者权益合计",
    "monetary_capital": "货币资金",
    "inventory": "存货",
    "accounts_receivable": "应收账款",
    "short_term_borrowings": "短期借款",
    "operating_cash_flow_net": "经营活动现金流净额",
    "investing_cash_flow_net": "投资活动现金流净额",
    "financing_cash_flow_net": "筹资活动现金流净额",
    "asset_impairment_loss": "资产减值损失",
    "credit_impairment_loss": "信用减值损失",
    "cash_for_purchases_investments": "购建固定资产、无形资产和其他长期资产支付的现金",
    "current_assets": "流动资产合计",
    "current_liabilities": "流动负债合计",
    "current_portion_noncurrent_liabilities": "一年内到期的非流动负债",
    "interest_expense": "利息费用",
    "weighted_avg_roe": "加权平均ROE",
    "equity_attributable_parent": "归属于母公司所有者权益",
}


@dataclass
class FactCheckIssue:
    severity: str
    dimension: str
    location: str
    message: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CheckResult:
    status: str = "pass"
    issues: List[FactCheckIssue] = field(default_factory=list)


@dataclass
class FactCheckReport:
    verdict: str
    company_id: str
    report_file: str
    summary: Dict[str, Any]
    checks: Dict[str, CheckResult]
    evidence_summary: List[Dict[str, Any]]
    metric_evidence_map: Dict[str, Dict[str, Any]]
    calculation_audit: List[Dict[str, Any]]
    recommendations: List[str]
    verified_at: str


@dataclass
class ReportPair:
    md_path: Path
    json_path: Path
    selection_reason: str


class PostgresEvidenceAccessor:
    """Optional read-only PostgreSQL evidence helper."""

    def __init__(self) -> None:
        self.status = "unavailable"
        self.error = ""
        self._psycopg = None
        try:
            import psycopg  # type: ignore
            self._psycopg = psycopg
        except Exception as exc:  # pragma: no cover - depends on runtime
            self.error = f"psycopg unavailable: {exc}"

    def _connect(self):
        if not self._psycopg:
            raise RuntimeError(self.error or "psycopg unavailable")
        return self._psycopg.connect(**_project_pdf2md_pg_config(), connect_timeout=3)

    def fetch_company_evidence(self, stock_code: str, report_year: int, limit: int = 24, stock_name: str = '') -> List[Dict[str, Any]]:
        if not self._psycopg:
            return []
        sql = """
        with items as (
          select 'balance_sheet' as statement_type, task_id, stock_code, stock_name,
                 report_year, item_name, canonical_name, value, raw_value, unit,
                 source_page_number, source_table_index
          from pdf2md.financial_balance_sheet_items
          where report_year = %s and (stock_code = %s or stock_name = %s)
          union all
          select 'income_statement', task_id, stock_code, stock_name,
                 report_year, item_name, canonical_name, value, raw_value, unit,
                 source_page_number, source_table_index
          from pdf2md.financial_income_statement_items
          where report_year = %s and (stock_code = %s or stock_name = %s)
          union all
          select 'cash_flow_statement', task_id, stock_code, stock_name,
                 report_year, item_name, canonical_name, value, raw_value, unit,
                 source_page_number, source_table_index
          from pdf2md.financial_cash_flow_statement_items
          where report_year = %s and (stock_code = %s or stock_name = %s)
        )
        select i.statement_type, i.item_name, i.canonical_name, i.value::text,
               i.raw_value, i.unit, i.task_id, i.source_table_index,
               coalesce(dt.pdf_page_number, i.source_page_number) as pdf_page_number,
               dt.markdown_line
        from items i
        left join pdf2md.document_tables dt
          on dt.task_id = i.task_id and dt.table_index = i.source_table_index
        where i.canonical_name = any(%s)
           or i.item_name like any(%s)
        order by i.statement_type, i.item_name
        limit %s
        """
        canonical_names = list(CORE_METRICS.keys())
        item_patterns = [f"%{name}%" for name in CORE_METRICS.values()]
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (report_year, stock_code, stock_name, report_year, stock_code, stock_name, report_year, stock_code, stock_name, canonical_names, item_patterns, limit))
                    rows = cur.fetchall()
            self.status = "available"
            return [
                {
                    "source_type": "postgresql",
                    "statement_type": row[0],
                    "item_name": row[1],
                    "canonical_name": row[2],
                    "metric_or_claim": row[2] or row[1],
                    "value": row[3],
                    "raw_value": row[4],
                    "unit": row[5],
                    "task_id": row[6],
                    "table_index": row[7],
                    "pdf_page_number": row[8],
                    "markdown_line": row[9],
                    "open_pdf_page_url": public_api_url(f"/api/pdf_page/{row[6]}/{row[8]}") if row[6] and row[8] else "",
                    "open_source_page_url": public_api_url(f"/api/source/{row[6]}/page/{row[8]}") if row[6] and row[8] else "",
                    "open_source_table_url": public_api_url(f"/api/source/{row[6]}/table/{row[7]}") if row[6] and row[7] else "",
                }
                for row in rows
            ]
        except Exception as exc:
            self.status = "unavailable"
            self.error = str(exc)
            return []


def issue_to_dict(issue: FactCheckIssue) -> Dict[str, Any]:
    result = {
        "severity": issue.severity,
        "dimension": issue.dimension,
        "location": issue.location,
        "message": issue.message,
    }
    if issue.expected is not None:
        result["expected"] = issue.expected
    if issue.actual is not None:
        result["actual"] = issue.actual
    result["evidence_refs"] = issue.evidence_refs
    return result


def report_to_dict(report: FactCheckReport) -> Dict[str, Any]:
    return {
        "verdict": report.verdict,
        "company_id": report.company_id,
        "report_file": report.report_file,
        "summary": report.summary,
        "checks": {
            name: {
                "status": result.status,
                "issues": [issue_to_dict(issue) for issue in result.issues],
            }
            for name, result in report.checks.items()
        },
        "evidence_summary": report.evidence_summary,
        "metric_evidence_map": report.metric_evidence_map,
        "calculation_audit": report.calculation_audit,
        "recommendations": report.recommendations,
        "verified_at": report.verified_at,
    }


class FactCheckEngine:
    def __init__(self, accessor: WikiDataAccessor):
        self.accessor = accessor
        self.pg = PostgresEvidenceAccessor()

    def verify(self, company_id: str, report_year: int, report_path: Optional[Path] = None) -> FactCheckReport:
        company = self.accessor.get_company_by_id(company_id) or self.accessor.get_company_by_stock_code(company_id)
        if not company:
            return self._blocked(company_id, "", "公司不存在或无法定位")

        report_pair = self._select_analysis_report(company, report_year, report_path=report_path)
        if not report_pair:
            expected = self.accessor.get_analysis_dir(company.company_id) / f"{company.stock_code}-{company.company_short_name}-{report_year}-analysis.md"
            return self._blocked(company.company_id, str(expected), f"分析报告不存在或无法匹配: {expected}")

        report_md_path = report_pair.md_path
        report_json_path = report_pair.json_path
        report_md = report_md_path.read_text(encoding="utf-8")
        report_json = self._load_json(report_json_path)
        data = self.accessor.load_company_full(company.company_id)
        metrics = self._extract_metrics(data.get("metrics"))
        evidence_index = data.get("evidence", {}).get("evidence_index")
        pg_evidence = self.pg.fetch_company_evidence(company.stock_code, report_year, stock_name=company.company_short_name)
        local_evidence = self._fetch_local_evidence(company, report_year)
        metric_evidence_map = self._build_metric_evidence_map(metrics, evidence_index, pg_evidence, local_evidence, report_year)
        evidence_summary = self._build_evidence_summary(pg_evidence, local_evidence, metric_evidence_map)
        calculation_audit = self._build_calculation_audit(metrics, metric_evidence_map, report_md)

        checks = {
            "data_consistency": self.check_data_consistency(report_md, metrics, pg_evidence, local_evidence),
            "calculation_consistency": self.check_calculation_consistency(report_md, metrics, evidence_summary, calculation_audit),
            "traceability": self.check_traceability(report_md, report_json, evidence_index, pg_evidence, local_evidence),
            "logic_support": self.check_logic_support(report_md, evidence_summary),
            "a_share_risk_completeness": self.check_a_share_risk_completeness(report_md),
            "template_compliance": self.check_template_compliance(report_md, report_json),
        }
        counts = self._count_issues(checks)
        verdict = self._decide_verdict(counts, checks)
        recommendations = self._build_recommendations(checks)
        summary = {
            **counts,
            "database_status": self.pg.status,
            "database_connection": self.pg.status,
            "company_evidence_status": self._company_evidence_status(pg_evidence, local_evidence),
            "database_error": self.pg.error if self.pg.status == "unavailable" and self.pg.error else "",
            "evidence_rows": len(pg_evidence),
            "local_evidence_rows": len(local_evidence),
            "report_selection": report_pair.selection_reason,
            "report_json_file": report_json_path.name if report_json_path.exists() else "",
            "calculation_audit_items": len(calculation_audit),
            "metric_evidence_items": len(metric_evidence_map),
        }
        return FactCheckReport(
            verdict=verdict,
            company_id=company.company_id,
            report_file=report_md_path.name,
            summary=summary,
            checks=checks,
            evidence_summary=evidence_summary,
            metric_evidence_map=metric_evidence_map,
            calculation_audit=calculation_audit,
            recommendations=recommendations,
            verified_at=datetime.now(CN_TZ).isoformat(),
        )

    def _normalize_requested_report_path(self, company: Any, report_path: Optional[Path]) -> Optional[ReportPair]:
        if not report_path:
            return None
        analysis_dir = self.accessor.get_analysis_dir(company.company_id).resolve()
        path = report_path.expanduser()
        if not path.is_absolute():
            candidates = [(analysis_dir / path).resolve(), (Path.cwd() / path).resolve()]
        else:
            candidates = [path.resolve()]
        for candidate in candidates:
            if candidate.suffix.lower() in {".html", ".json"}:
                candidate = candidate.with_suffix(".md")
            try:
                candidate.relative_to(analysis_dir)
            except ValueError:
                continue
            if candidate.exists() and candidate.suffix.lower() == ".md":
                return ReportPair(candidate, candidate.with_suffix(".json"), f"explicit:{candidate.name}")
        return None

    def _select_analysis_report(self, company: Any, report_year: int, report_path: Optional[Path] = None) -> Optional[ReportPair]:
        explicit = self._normalize_requested_report_path(company, report_path)
        if explicit:
            return explicit

        analysis_dir = self.accessor.get_analysis_dir(company.company_id)
        canonical = analysis_dir / f"{company.stock_code}-{company.company_short_name}-{report_year}-analysis.md"

        candidates = []
        for md_path in analysis_dir.glob("*.md"):
            name = md_path.name
            if name == "README.md" or md_path.parent.name == ".work":
                continue
            if str(report_year) not in name:
                continue
            if company.stock_code not in name and company.company_short_name not in name:
                continue
            lowered = name.lower()
            penalty = 0
            if "research-pack" in lowered:
                penalty -= 30
            if "siq-depth" in lowered:
                penalty -= 15
            if "recovered" in lowered:
                penalty += 20
            if "templatecheck" in lowered:
                penalty += 30
            if "full" in lowered:
                penalty += 10
            if "deep_analysis" in lowered:
                penalty += 50
            if "test" in lowered:
                penalty += 15
            canonical_like = 0 if name.endswith(f"{report_year}-analysis.md") else 5
            candidates.append((penalty + canonical_like, md_path.stat().st_mtime, md_path))
        if canonical.exists() and not any(item[2] == canonical for item in candidates):
            candidates.append((0, canonical.stat().st_mtime, canonical))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], -item[1]))
        selected = candidates[0][2]
        json_path = selected.with_suffix(".json")
        if not json_path.exists():
            sibling = analysis_dir / f"{selected.stem}.json"
            json_path = sibling
        return ReportPair(selected, json_path, f"auto_selected:{selected.name}")

    def _fetch_local_evidence(self, company: Any, report_year: int) -> List[Dict[str, Any]]:
        if not collect_company_evidence_refs:
            return []
        try:
            refs = collect_company_evidence_refs(company.company_path, period_text=str(report_year), limit=24)
        except Exception:
            return []
        output: List[Dict[str, Any]] = []
        for ref in refs:
            output.append({
                "statement_type": ref.get("statement_type") or ref.get("source_type"),
                "item_name": ref.get("metric") or ref.get("canonical_name") or ref.get("evidence_id"),
                "canonical_name": ref.get("canonical_name"),
                "metric_or_claim": ref.get("metric") or ref.get("canonical_name") or ref.get("evidence_id"),
                "value": ref.get("value"),
                "raw_value": ref.get("raw_value"),
                "unit": ref.get("unit"),
                "task_id": ref.get("task_id"),
                "table_index": ref.get("table_index"),
                "pdf_page_number": ref.get("pdf_page"),
                "markdown_line": ref.get("md_line"),
                "open_pdf_page_url": ref.get("open_pdf_page_url"),
                "open_source_page_url": ref.get("open_source_page_url"),
                "open_source_table_url": ref.get("open_source_table_url"),
                "source_type": ref.get("source_type") or "wiki_evidence",
                "file": ref.get("file"),
            })
        return output

    def _build_metric_evidence_map(self, metrics: Dict[str, Dict[str, Any]], evidence_index: Optional[Dict[str, Any]], pg_evidence: List[Dict[str, Any]], local_evidence: List[Dict[str, Any]], report_year: int) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}

        def normalize_ev(ev: Dict[str, Any], source_type: str) -> Dict[str, Any]:
            task_id = ev.get("task_id")
            pdf_page = ev.get("pdf_page_number") or ev.get("pdf_page")
            table_index = ev.get("table_index") or ev.get("source_table_index")
            return {
                "source_type": ev.get("source_type") or source_type,
                "file": ev.get("file") or ("evidence/evidence_index.json" if source_type.startswith("wiki") else ""),
                "metric_or_claim": ev.get("metric_key") or ev.get("canonical_name") or ev.get("metric_or_claim") or ev.get("item_name") or ev.get("metric_name"),
                "metric_name": ev.get("metric_name") or ev.get("item_name"),
                "statement_type": ev.get("statement_type"),
                "scope": ev.get("scope"),
                "period": ev.get("period"),
                "value": ev.get("value"),
                "raw_value": ev.get("raw_value"),
                "unit": ev.get("raw_unit") or ev.get("unit"),
                "task_id": task_id,
                "pdf_page_number": pdf_page,
                "table_index": table_index,
                "md_line": ev.get("md_line") or ev.get("markdown_line"),
                "open_pdf_page_url": ev.get("open_pdf_page_url") or (public_api_url(f"/api/pdf_page/{task_id}/{pdf_page}") if task_id and pdf_page else ""),
                "open_source_page_url": ev.get("open_source_page_url") or (public_api_url(f"/api/source/{task_id}/page/{pdf_page}") if task_id and pdf_page else ""),
                "open_source_table_url": ev.get("open_source_table_url") or (public_api_url(f"/api/source/{task_id}/table/{table_index}") if task_id and table_index else ""),
            }

        evidence_items = []
        if isinstance(evidence_index, dict):
            evidence_items = evidence_index.get("evidence", []) or []

        for metric_key, item in metrics.items():
            if metric_key.startswith("__"):
                continue
            period = item.get("period") or (f"{report_year}-12-31" if item.get("statement_type") == "balance_sheet" else str(report_year))
            source = item.get("source") or {}
            result[metric_key] = normalize_ev({
                "metric_key": metric_key,
                "metric_name": item.get("metric_name"),
                "canonical_name": metric_key,
                "statement_type": item.get("statement_type"),
                "scope": item.get("scope"),
                "period": period,
                "value": item.get("normalized_value"),
                "raw_value": item.get("raw_value"),
                "unit": item.get("unit_hint"),
                "task_id": source.get("task_id"),
                "pdf_page": source.get("pdf_page"),
                "table_index": source.get("table_index"),
                "md_line": source.get("md_line"),
            }, "wiki_metrics")

        for ev in evidence_items:
            metric_key = ev.get("metric_key")
            if not metric_key:
                continue
            scope = ev.get("scope")
            period = str(ev.get("period", ""))
            is_target_period = period == str(report_year) or period == f"{report_year}-12-31"
            if scope == "consolidated" and is_target_period:
                result[metric_key] = normalize_ev(ev, "wiki_evidence")

        for ev in pg_evidence + local_evidence:
            metric_key = ev.get("canonical_name") or ev.get("metric_or_claim")
            if metric_key and metric_key not in result:
                result[str(metric_key)] = normalize_ev(ev, ev.get("source_type") or "wiki_evidence")
        return result

    def _build_evidence_summary(self, pg_evidence: List[Dict[str, Any]], local_evidence: List[Dict[str, Any]], metric_evidence_map: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        source = pg_evidence or local_evidence or list((metric_evidence_map or {}).values())
        if not source:
            return [{
                "status": "insufficient_evidence",
                "source_type": "none",
                "metric_or_claim": "global",
                "message": "未取得 PostgreSQL 或本地 wiki 可审计证据摘要",
            }]
        selected: List[Dict[str, Any]] = []
        seen = set()
        for ev in source:
            key = ev.get("canonical_name") or ev.get("metric_or_claim") or ev.get("item_name") or ev.get("metric")
            if key in seen:
                continue
            seen.add(key)
            selected.append(ev)
            if len(selected) >= 20:
                break
        return selected

    def _build_calculation_audit(self, metrics: Dict[str, Dict[str, Any]], metric_evidence_map: Dict[str, Dict[str, Any]], report_md: str) -> List[Dict[str, Any]]:
        audit: List[Dict[str, Any]] = []

        def mv(key: str) -> Optional[float]:
            value = self._metric_value(metrics, key)
            return abs(value) if key in {"operating_cost"} and value is not None else value

        def add(name: str, formula: str, value: Optional[float], unit: str, inputs: List[str], report_keyword: str, tolerance: float) -> None:
            if value is None:
                return
            reported = self._extract_nearby_number(report_md, report_keyword, unit)
            delta = abs(reported - value) if reported is not None else None
            status = "unchecked"
            if reported is not None:
                status = "pass" if delta is not None and delta <= tolerance else "warning"
            audit.append({
                "name": name,
                "formula": formula,
                "recomputed_value": round(value, 4),
                "unit": unit,
                "reported_value": reported,
                "delta": round(delta, 4) if delta is not None else None,
                "tolerance": tolerance,
                "status": status,
                "inputs": inputs,
                "evidence_refs": [metric_evidence_map[k] for k in inputs if k in metric_evidence_map],
            })

        revenue = mv("operating_revenue")
        cost = mv("operating_cost")
        total_assets = mv("total_assets")
        total_liabilities = mv("total_liabilities")
        current_assets = mv("current_assets")
        current_liabilities = mv("current_liabilities")
        inventory = mv("inventory")
        monetary_capital = mv("monetary_capital")
        short_debt = mv("short_term_borrowings") or 0.0
        current_noncurrent_liability = mv("current_portion_noncurrent_liabilities") or mv("non_current_liabilities_due_within_one_year") or 0.0
        ocf = mv("operating_cash_flow_net")
        capex = mv("cash_for_purchases_investments")
        parent_net_profit = mv("parent_net_profit") or mv("net_profit_parent")
        deducted_parent_net_profit = mv("deducted_parent_net_profit")
        equity_parent = mv("equity_attributable_parent")
        total_equity = mv("total_equity")
        accounts_receivable = mv("accounts_receivable")
        interest_expense = mv("interest_expense")

        if revenue and cost is not None:
            add("毛利率", "(营业收入-营业成本)/营业收入", (revenue - cost) / revenue * 100, "%", ["operating_revenue", "operating_cost"], "毛利率", 0.5)
        if total_assets:
            add("资产负债率", "负债合计/资产总计", (total_liabilities or 0) / total_assets * 100, "%", ["total_liabilities", "total_assets"], "资产负债率", 0.5)
        if current_liabilities:
            add("流动比率", "流动资产/流动负债", (current_assets or 0) / current_liabilities, "倍", ["current_assets", "current_liabilities"], "流动比率", 0.05)
            if current_assets is not None and inventory is not None:
                add("速动比率", "(流动资产-存货)/流动负债", (current_assets - inventory) / current_liabilities, "倍", ["current_assets", "inventory", "current_liabilities"], "速动比率", 0.05)
        short_interest_debt = short_debt + current_noncurrent_liability
        if short_interest_debt:
            add("现金短债覆盖", "货币资金/(短期借款+一年内到期非流动负债)", (monetary_capital or 0) / short_interest_debt, "倍", ["monetary_capital", "short_term_borrowings"], "现金短债覆盖", 0.05)

        # FCF: 经营现金流净额 - 购建固定资产、无形资产和其他长期资产支付的现金 (capex)
        # 旧实现错把"投资活动现金流净额"当作 capex，会包含理财/并购/处置净额
        if ocf is not None and capex is not None:
            add(
                "自由现金流",
                "经营现金流净额-购建固定资产、无形资产和其他长期资产支付的现金",
                ocf - abs(capex),
                "亿元",
                ["operating_cash_flow_net", "cash_for_purchases_investments"],
                "自由现金流",
                0.5,
            )

        # 杜邦 ROE = 净利率 × 总资产周转率 × 权益乘数
        if revenue and total_assets and equity_parent and parent_net_profit is not None:
            roe = parent_net_profit / equity_parent * 100
            add(
                "ROE(杜邦)",
                "归母净利润/归母权益",
                roe,
                "%",
                ["parent_net_profit", "equity_attributable_parent"],
                "ROE",
                0.5,
            )

        # 利息保障倍数 (EBIT/利息费用) — 当 interest_expense 可用且为正
        if interest_expense and interest_expense > 0 and parent_net_profit is not None:
            ebit_proxy = parent_net_profit + interest_expense  # 简化代理；如有所得税、营业利润可改进
            add(
                "利息保障倍数(代理)",
                "(归母净利润+利息费用)/利息费用",
                ebit_proxy / interest_expense,
                "倍",
                ["parent_net_profit", "interest_expense"],
                "利息保障",
                0.1,
            )

        # 经营现金流/总债务
        if ocf is not None and total_liabilities and total_liabilities > 0:
            add(
                "经营现金流覆盖率",
                "经营现金流净额/负债合计",
                ocf / total_liabilities * 100,
                "%",
                ["operating_cash_flow_net", "total_liabilities"],
                "经营现金流覆盖",
                0.5,
            )

        # 应收周转天数 DSO = 365 × 平均应收 / 营业收入 (用期末值代理)
        if accounts_receivable is not None and revenue and revenue > 0:
            add(
                "应收周转天数(代理)",
                "365×应收账款/营业收入",
                365 * accounts_receivable / revenue,
                "天",
                ["accounts_receivable", "operating_revenue"],
                "应收周转天数",
                3.0,
            )

        # 存货周转天数 DIO = 365 × 平均存货 / 营业成本
        if inventory is not None and cost is not None and cost > 0:
            add(
                "存货周转天数(代理)",
                "365×存货/营业成本",
                365 * inventory / cost,
                "天",
                ["inventory", "operating_cost"],
                "存货周转天数",
                3.0,
            )

        # 扣非率 = 扣非归母 / 归母
        if parent_net_profit not in (None, 0) and deducted_parent_net_profit is not None:
            add(
                "扣非利润占比",
                "扣非归母净利润/归母净利润",
                deducted_parent_net_profit / parent_net_profit * 100,
                "%",
                ["deducted_parent_net_profit", "parent_net_profit"],
                "扣非",
                1.0,
            )

        return audit

    def _company_evidence_status(self, pg_evidence: List[Dict[str, Any]], local_evidence: List[Dict[str, Any]]) -> str:
        if pg_evidence:
            return "postgresql_available"
        if local_evidence:
            return "local_wiki_available"
        if self.pg.status == "available":
            return "database_connected_no_company_rows"
        return "unavailable"

    def _issue_evidence_for_metric(self, metric_or_claim: str, evidence_summary: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        if not evidence_summary:
            return []
        metric_or_claim_lower = str(metric_or_claim).lower()
        for ev in evidence_summary:
            haystack = " ".join(str(ev.get(k, "")) for k in ("metric_or_claim", "canonical_name", "item_name", "metric")).lower()
            if metric_or_claim_lower in haystack or haystack in metric_or_claim_lower:
                return [ev]
        return []

    def check_data_consistency(self, report_md: str, metrics: Dict[str, Dict[str, Any]], pg_evidence: List[Dict[str, Any]], local_evidence: Optional[List[Dict[str, Any]]] = None) -> CheckResult:
        issues: List[FactCheckIssue] = []
        if not metrics:
            issues.append(FactCheckIssue("critical", "data_consistency", "全局", "本地 metrics 缺失，无法核对核心财务数据", evidence_refs=[]))
            return self._result(issues)

        missing_mentions = []
        for key, label in CORE_METRICS.items():
            item = metrics.get(key)
            if not item:
                continue
            value = item.get("normalized_value")
            if isinstance(value, (int, float)) and abs(value) >= 1:
                if not self._value_appears(report_md, float(value)) and label not in report_md:
                    missing_mentions.append(label)
        if missing_mentions:
            issues.append(FactCheckIssue(
                "suggestion",
                "data_consistency",
                "全文",
                "部分核心指标未在报告中清晰呈现或难以用数值匹配: " + "、".join(missing_mentions[:8]),
                evidence_refs=[],
            ))

        if not pg_evidence and not local_evidence:
            issues.append(FactCheckIssue(
                "warning",
                "data_consistency",
                "证据摘要",
                "未取得 PostgreSQL 或本地 wiki 证据摘要，本次只能依赖本地指标值核查",
                evidence_refs=[],
            ))
        return self._result(issues)

    def check_calculation_consistency(self, report_md: str, metrics: Dict[str, Dict[str, Any]], evidence_summary: Optional[List[Dict[str, Any]]] = None, calculation_audit: Optional[List[Dict[str, Any]]] = None) -> CheckResult:
        issues: List[FactCheckIssue] = []
        # 毛利率必须使用“营业成本/主营成本”口径。若当前指标来自“营业总成本”，
        # 自动重算会把期间费用也纳入成本，容易产生误报，因此只做口径提示。
        if "毛利率" in report_md and "operating_cost" in metrics:
            issues.append(FactCheckIssue(
                "warning",
                "calculation_consistency",
                "毛利率",
                "检测到报告使用毛利率，请人工确认 metrics.operating_cost 是否为营业成本而非营业总成本；口径不明时不自动判定为计算错误",
                expected="营业成本口径清晰",
                actual="本地指标仅提供 operating_cost，需核实来源表项目名称",
                evidence_refs=self._issue_evidence_for_metric("operating_cost", evidence_summary),
            ))

        key_metrics = self._extract_key_metrics(metrics)
        for name, values in key_metrics.items():
            years = sorted([y for y in values if str(y).isdigit()])
            if str(max(years, default="")) not in ("2025",):
                continue
            if "2025" in values and "2024" in values:
                try:
                    latest = float(values["2025"])
                    prev = float(values["2024"])
                    if prev:
                        yoy = (latest - prev) / abs(prev) * 100
                        # 阈值从 ±500% 收紧到 ±100%；±50%~±100% 出 suggestion，>±100% 出 warning
                        if abs(yoy) > 100 and name in report_md:
                            issues.append(FactCheckIssue(
                                "warning",
                                "calculation_consistency",
                                f"同比: {name}",
                                "同比变动幅度极大（>100%），报告必须说明基数效应、口径差异或经营原因",
                                expected="说明基数和口径",
                                actual=f"{yoy:.2f}%",
                                evidence_refs=self._issue_evidence_for_metric(name, evidence_summary),
                            ))
                        elif abs(yoy) > 50 and name in report_md:
                            issues.append(FactCheckIssue(
                                "suggestion",
                                "calculation_consistency",
                                f"同比: {name}",
                                "同比变动幅度较大（>50%），建议在报告中给出明确解释",
                                expected="说明波动原因",
                                actual=f"{yoy:.2f}%",
                                evidence_refs=self._issue_evidence_for_metric(name, evidence_summary),
                            ))
                except Exception:
                    continue
        for item in calculation_audit or []:
            if item.get("status") == "warning":
                issues.append(FactCheckIssue(
                    "warning",
                    "calculation_consistency",
                    item.get("name", "公式重算"),
                    "报告披露值与自动重算值差异超过容忍阈值",
                    expected=f"{item.get('recomputed_value')} {item.get('unit')}",
                    actual=f"{item.get('reported_value')} {item.get('unit')}，差异 {item.get('delta')}",
                    evidence_refs=item.get("evidence_refs", []),
                ))
        return self._result(issues)

    def check_traceability(self, report_md: str, report_json: Dict[str, Any], evidence_index: Optional[Dict], pg_evidence: List[Dict[str, Any]], local_evidence: Optional[List[Dict[str, Any]]] = None) -> CheckResult:
        issues: List[FactCheckIssue] = []
        data_points = re.findall(r"[-+−]?\d+(?:\.\d+)?\s*(?:亿元|万元|元|%|个百分点|pct|倍)", report_md)
        markers = re.findall(r"\^\[.*?\]|\[\^\d+\]|\[\d+\]|\^\{\[\d+\]\}", report_md)
        if len(data_points) > 20 and not markers:
            issues.append(FactCheckIssue(
                "warning",
                "traceability",
                "全文",
                f"报告包含 {len(data_points)} 个数值型数据点，但 Markdown 中没有 ^[] 或 [^N] 证据标记",
                expected="关键数据点具备证据标记",
                actual="0 个证据标记",
                evidence_refs=[],
            ))
        elif data_points and len(markers) / max(1, len(data_points)) < 0.3:
            issues.append(FactCheckIssue(
                "warning",
                "traceability",
                "全文",
                f"证据标记覆盖率偏低: {len(markers)}/{len(data_points)}",
                evidence_refs=[],
            ))

        evidence_count = 0
        if isinstance(evidence_index, dict):
            evidence_count = int(evidence_index.get("evidence_count") or len(evidence_index.get("evidence", [])))
        if evidence_count == 0:
            issues.append(FactCheckIssue("warning", "traceability", "evidence_index.json", "本地 evidence_index 不可用或为空", evidence_refs=[]))
        if not pg_evidence and not local_evidence:
            issues.append(FactCheckIssue("suggestion", "traceability", "PostgreSQL/wiki", "建议恢复 PostgreSQL 证据库连接或补齐本地 wiki 证据索引以增强 PDF 页码和表格编号校验", evidence_refs=[]))
        return self._result(issues)

    def check_logic_support(self, report_md: str, evidence_summary: Optional[List[Dict[str, Any]]] = None) -> CheckResult:
        issues: List[FactCheckIssue] = []
        contradictions = [
            (r"现金流(?:充裕|强劲|健康|良好|稳健)", r"经营现金流[^。；\n]*[-−]\d", "现金流正面判断附近出现负经营现金流"),
            (r"偿债(?:能力)?(?:强|良好|无忧|稳健|充足)", r"短期偿债压力|现金覆盖严重不足|流动性紧张|短债.*?上升", "偿债正面判断与压力表述冲突"),
            (r"盈利(?:能力)?(?:强|优秀|改善|稳健|韧性)", r"亏损|净利润[^。；\n]*[-−]\d|扣非.*?亏损", "盈利正面判断与亏损表述冲突"),
            (r"毛利率(?:稳定|提升|改善|健康)", r"毛利率.*?(?:下降|崩塌|转负|大幅收窄|跳水)|-\d+\.?\d*\s*pct", "毛利率正面判断与下行表述冲突"),
            (r"资产质量(?:良好|健康|稳健)", r"商誉减值|资产减值损失[^。；\n]*\d|存货.*?积压|应收.*?恶化", "资产质量正面判断与减值/积压表述冲突"),
            (r"治理(?:良好|稳健|规范)|无重大违规", r"问询函|监管处罚|立案|资金占用|违规担保|股权质押.*?(?:高比例|预警)", "治理正面判断与监管/治理事件冲突"),
            (r"行业(?:景气|向好|复苏)", r"价格(?:下行|下跌|战)|产能过剩|需求(?:疲软|萎缩)", "行业正面判断与下行表述冲突"),
            (r"估值(?:合理|安全|具备(?:优势|吸引力))", r"业绩(?:不达|低于|承压)|估值(?:数据|缺口)", "估值结论与业绩/数据缺口冲突"),
            (r"债务(?:可控|安全)", r"短债覆盖.*?不足|资产负债率.*?(?:>|超|高于).*?70|有息负债.*?上升", "债务安全判断与压力指标冲突"),
            (r"扣非(?:改善|转正|稳健)", r"扣非.*?(?:亏损|为负|大幅下滑)", "扣非正面判断与负值冲突"),
        ]
        for positive, negative, message in contradictions:
            for match in re.finditer(positive, report_md):
                nearby = report_md[max(0, match.start() - 220): match.end() + 220]
                if re.search(negative, nearby):
                    issues.append(FactCheckIssue(
                        "critical",
                        "logic_support",
                        f"位置 {match.start()}",
                        message,
                        evidence_refs=self._issue_evidence_for_metric("经营现金流", evidence_summary),
                    ))
                    break
        # 定性词必须有附近量化支撑
        vague_claims = [
            "估值合理", "安全边际", "困境反转", "拐点", "高确定性",
            "基本面改善", "经营改善", "韧性增强", "护城河", "壁垒提升",
            "份额提升", "竞争优势", "技术领先", "强劲增长", "持续向好",
        ]
        for claim in vague_claims:
            idx = report_md.find(claim)
            while idx >= 0:
                nearby = report_md[max(0, idx - 200): idx + 240]
                # 至少需要 ≥2 个数字证据（百分比/亿元/倍/天/pct）
                hits = re.findall(r"[-+−]?\d+(?:\.\d+)?\s*(?:倍|%|亿元|万元|pct|个百分点|天)", nearby)
                if len(hits) < 2:
                    issues.append(FactCheckIssue(
                        "warning",
                        "logic_support",
                        f"含'{claim}'的段落",
                        "定性判断附近缺少充分量化支撑（至少 2 个数字证据）",
                        evidence_refs=[],
                    ))
                    break  # 同一个 claim 只出一次
                idx = report_md.find(claim, idx + len(claim))
        return self._result(issues)

    def check_a_share_risk_completeness(self, report_md: str) -> CheckResult:
        issues: List[FactCheckIssue] = []
        risk_groups = {
            "ST/退市风险": ["ST", "退市", "净资产", "持续亏损"],
            "审计与内控": ["审计意见", "内控", "强调事项"],
            "监管问询处罚": ["问询", "监管", "处罚", "立案"],
            "股东质押减持解禁": ["质押", "减持", "解禁", "冻结"],
            "关联与担保": ["关联交易", "资金占用", "担保"],
            "减值风险": ["商誉", "资产减值", "信用减值"],
        }
        missing = [name for name, words in risk_groups.items() if not any(word in report_md for word in words)]
        if missing:
            issues.append(FactCheckIssue(
                "warning",
                "a_share_risk_completeness",
                "风险章节",
                "报告未明确覆盖部分 A 股二级市场风险核查项: " + "、".join(missing),
                evidence_refs=[],
            ))
        return self._result(issues)

    def check_template_compliance(self, report_md: str, report_json: Dict[str, Any]) -> CheckResult:
        issues: List[FactCheckIssue] = []
        payload = json.dumps(report_json, ensure_ascii=False)
        forbidden_patterns = {
            "综合得分": r"综合得分",
            "overall_score": r"overall_score",
            "overall_rating": r"overall_rating",
            "评级为": r"(?<!信用)评级为",
            "总分": r"(?<!信用)总分",
        }
        found = [label for label, pattern in forbidden_patterns.items() if re.search(pattern, report_md) or re.search(pattern, payload)]
        if found:
            issues.append(FactCheckIssue(
                "critical",
                "template_compliance",
                "评分层",
                "报告仍包含已取消的评分/评级字段或表达: " + "、".join(found),
                evidence_refs=[],
            ))
        required_terms = ["核心判断", "证据", "现金流", "偿债", "风险", "跟踪", "情景"]
        missing = [term for term in required_terms if term not in report_md]
        if missing:
            issues.append(FactCheckIssue(
                "suggestion",
                "template_compliance",
                "报告结构",
                "建议补齐或显式标注以下模块: " + "、".join(missing),
                evidence_refs=[],
            ))
        return self._result(issues)

    def _blocked(self, company_id: str, report_file: str, message: str) -> FactCheckReport:
        issue = FactCheckIssue("critical", "data_consistency", "系统", message, evidence_refs=[])
        checks = {name: CheckResult("pass", []) for name in CHECK_NAMES}
        checks["data_consistency"] = CheckResult("fail", [issue])
        return FactCheckReport(
            verdict="block",
            company_id=company_id,
            report_file=report_file,
            summary={"critical": 1, "warning": 0, "suggestion": 0, "database_status": "not_checked", "database_connection": "not_checked", "company_evidence_status": "not_checked", "evidence_rows": 0},
            checks=checks,
            evidence_summary=[{"status": "insufficient_evidence", "source_type": "none", "metric_or_claim": "global", "message": message}],
            metric_evidence_map={},
            calculation_audit=[],
            recommendations=[message],
            verified_at=datetime.now(CN_TZ).isoformat(),
        )

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _extract_metrics(self, metrics_obj: Any) -> Dict[str, Dict[str, Any]]:
        if not metrics_obj:
            return {}
        result: Dict[str, Dict[str, Any]] = {}
        ts = getattr(metrics_obj, "three_statements", {}) or {}
        for item in ts.get("data", {}).get("metrics", []):
            key = item.get("metric_key")
            if key:
                result[key] = item
        km = getattr(metrics_obj, "key_metrics", {}) or {}
        result["__key_metrics__"] = {"data": km.get("data", [])}
        return result

    def _extract_key_metrics(self, metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
        result: Dict[str, Dict[str, float]] = {}
        for item in metrics.get("__key_metrics__", {}).get("data", []):
            name = item.get("name") or item.get("canonical_name")
            values = item.get("values", {})
            if name and isinstance(values, dict):
                result[name] = values
        return result

    def _metric_value(self, metrics: Dict[str, Dict[str, Any]], key: str) -> Optional[float]:
        item = metrics.get(key)
        if not item:
            return None
        value = item.get("normalized_value")
        try:
            return float(value)
        except Exception:
            return None

    def _value_appears(self, text: str, value_yi: float) -> bool:
        candidates = {f"{value_yi:.2f}", f"{value_yi:.1f}", f"{value_yi:.0f}"}
        return any(candidate in text for candidate in candidates)

    def _extract_nearby_percent(self, text: str, keyword: str) -> Optional[float]:
        idx = text.find(keyword)
        if idx < 0:
            return None
        nearby = text[idx: idx + 120]
        match = re.search(r"([-+−]?\d+(?:\.\d+)?)\s*%", nearby)
        if not match:
            return None
        return float(match.group(1).replace("−", "-"))

    def _extract_nearby_number(self, text: str, keyword: str, unit: str) -> Optional[float]:
        idx = text.find(keyword)
        if idx < 0:
            return None
        nearby = text[max(0, idx - 80): idx + 140]
        escaped_unit = re.escape(unit)
        patterns = [
            rf"{re.escape(keyword)}[^-+−\d]{{0,40}}([-+−]?\d+(?:\.\d+)?)\s*{escaped_unit}",
            rf"([-+−]?\d+(?:\.\d+)?)\s*{escaped_unit}[^。；\n]{{0,40}}{re.escape(keyword)}",
        ]
        for pattern in patterns:
            match = re.search(pattern, nearby)
            if match:
                try:
                    return float(match.group(1).replace("−", "-"))
                except Exception:
                    return None
        return None

    def _result(self, issues: List[FactCheckIssue]) -> CheckResult:
        if any(i.severity == "critical" for i in issues):
            status = "fail"
        elif any(i.severity == "warning" for i in issues):
            status = "warning"
        else:
            status = "pass"
        return CheckResult(status, issues)

    def _count_issues(self, checks: Dict[str, CheckResult]) -> Dict[str, int]:
        counts = {"critical": 0, "warning": 0, "suggestion": 0}
        for result in checks.values():
            for issue in result.issues:
                counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts

    def _decide_verdict(self, counts: Dict[str, int], checks: Dict[str, CheckResult]) -> str:
        if counts.get("critical", 0) >= 2:
            return "block"
        if counts.get("critical", 0) >= 1:
            return "request_changes"
        if counts.get("warning", 0) >= 3:
            return "request_changes"
        return "approve"

    def _build_recommendations(self, checks: Dict[str, CheckResult]) -> List[str]:
        recommendations = []
        for check_name, result in checks.items():
            for issue in result.issues:
                if issue.severity in {"critical", "warning"}:
                    recommendations.append(f"[{CHECK_NAMES.get(check_name, check_name)}] {issue.message}")
        return recommendations[:12]


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def cmd_list(args):
    accessor = WikiDataAccessor()
    companies = accessor.list_companies()
    print(f"\n{'='*80}")
    print(f"SIQ_factchecker 工作集 - {len(companies)} 家公司")
    print(f"{'='*80}")
    print(f"{'股票代码':<10} {'公司简称':<12} {'申万一级':<10} {'申万二级':<12} {'v6.41':<6}")
    print(f"{'-'*80}")
    for c in companies:
        v641 = "✓" if c.has_v641_metrics else "✗"
        print(f"{c.stock_code:<10} {c.company_short_name:<12} {c.industry_sw1:<10} {c.industry_sw2:<12} {v641:<6}")
    print(f"{'='*80}\n")


def cmd_info(args):
    accessor = WikiDataAccessor()
    company = accessor.get_company_by_id(args.company_id) or accessor.get_company_by_stock_code(args.company_id)
    if not company:
        print(f"错误: 未找到公司 '{args.company_id}'")
        sys.exit(1)
    print(f"\n{'='*80}")
    print(f"公司详细信息: {company.company_short_name}")
    print(f"{'='*80}")
    print(f"  公司ID:        {company.company_id}")
    print(f"  股票代码:      {company.stock_code} ({company.exchange})")
    print(f"  申万一级:      {company.industry_sw1}")
    print(f"  申万二级:      {company.industry_sw2}")
    print(f"{'='*80}\n")


def cmd_check(args):
    accessor = WikiDataAccessor()
    engine = FactCheckEngine(accessor)
    company = accessor.get_company_by_id(args.company_id) or accessor.get_company_by_stock_code(args.company_id)
    if not company:
        print(f"错误: 未找到公司 '{args.company_id}'")
        sys.exit(1)

    report_pair = engine._select_analysis_report(company, args.year, report_path=args.report_path)
    report_md = report_pair.md_path if report_pair else accessor.get_analysis_dir(company.company_id) / f"{company.stock_code}-{company.company_short_name}-{args.year}-analysis.md"
    report_json = report_pair.json_path if report_pair else report_md.with_suffix(".json")
    data = accessor.load_company_full(company.company_id)
    evidence = engine.pg.fetch_company_evidence(company.stock_code, args.year, limit=3, stock_name=company.company_short_name)
    db_hit_status = "有目标公司证据" if evidence else ("连接可用但未命中目标公司证据" if engine.pg.status == "available" else "不可用")

    print(f"\n{'='*80}")
    print(f"事实核实前置检查: {company.company_short_name} ({company.stock_code})")
    print(f"{'='*80}")
    print(f"  分析报告 (md):  {'✓ 存在' if report_md.exists() else '✗ 缺失'}  {report_md}")
    print(f"  分析报告 (json): {'✓ 存在' if report_json.exists() else '✗ 缺失'}  {report_json}")
    if report_pair:
        print(f"  报告选择策略:    {report_pair.selection_reason}")
    print(f"  原始指标数据:    {'✓ 可用' if data['metrics'] else '✗ 缺失'}")
    print(f"  证据链索引:      {'✓ 可用' if data['evidence']['evidence_index'] else '✗ 缺失'}")
    print(f"  PostgreSQL连接: {'✓ 可用' if engine.pg.status == 'available' else '✗ 不可用'}")
    print(f"  PostgreSQL命中: {db_hit_status}  rows={len(evidence)}")
    ready = report_md.exists() and data["metrics"] is not None
    print(f"\n{'='*80}")
    print("  状态: ✓ 可以进行事实核实" if ready else "  状态: ✗ 前置条件不满足，无法核实")
    print(f"{'='*80}\n")
    return ready


def default_factcheck_output_path(accessor: WikiDataAccessor, company: Any, report_year: int, report_pair: Optional[ReportPair]) -> Path:
    factcheck_dir = accessor.ensure_factcheck_dir(company.company_id)
    canonical_analysis_name = f"{company.stock_code}-{company.company_short_name}-{report_year}-analysis.md"
    if report_pair and report_pair.md_path.name != canonical_analysis_name:
        return factcheck_dir / f"{report_pair.md_path.stem}-factcheck.json"
    return factcheck_dir / f"{company.stock_code}-{company.company_short_name}-{report_year}-factcheck.json"


def cmd_verify(args):
    accessor = WikiDataAccessor()
    engine = FactCheckEngine(accessor)
    company = accessor.get_company_by_id(args.company_id) or accessor.get_company_by_stock_code(args.company_id)
    if not company:
        print(f"错误: 未找到公司 '{args.company_id}'")
        sys.exit(1)

    print(f"\n{'='*80}")
    print("SIQ_factchecker v2 事实核实启动")
    print(f"{'='*80}")
    print(f"  目标公司: {company.company_short_name} ({company.stock_code})")
    print(f"  报告年份: {args.year}")
    print(f"{'='*80}\n")

    report_pair = engine._select_analysis_report(company, args.year, report_path=args.report_path)
    if report_pair:
        print(f"  分析报告: {report_pair.md_path}")
        print(f"  选择策略: {report_pair.selection_reason}")
    report = engine.verify(company.company_id, args.year, report_path=args.report_path)
    print("[核查结果]")
    for name, result in report.checks.items():
        issue_count = len(result.issues)
        issue_str = f"({issue_count} 个问题)" if issue_count else ""
        print(f"  {CHECK_NAMES.get(name, name):<16} {result.status.upper():<8} {issue_str}")
    print("\n[审校结论]")
    print(f"  verdict: {report.verdict.upper()}")
    print(f"  critical: {report.summary['critical']}  warning: {report.summary['warning']}  suggestion: {report.summary['suggestion']}")
    print(f"  PostgreSQL连接: {report.summary.get('database_connection')}  evidence_rows={report.summary.get('evidence_rows')}")
    print(f"  证据命中状态: {report.summary.get('company_evidence_status')}")

    if report.recommendations:
        print("\n[优先修改建议]")
        for rec in report.recommendations:
            print(f"  - {rec}")

    output_path = args.output or default_factcheck_output_path(accessor, company, args.year, report_pair)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_to_dict(report), f, ensure_ascii=False, indent=2)
    html_path = output_path.with_suffix(".html")
    html_path.write_text(generate_html(str(output_path)), encoding="utf-8")
    print(f"\n  ✓ 核实报告已保存: {output_path}")
    print(f"  ✓ HTML报告已生成: {html_path}")

    # The sandbox cannot mutate the finalized company index. Host Hermes uses
    # the fixed Publisher directly; OpenShell lifecycle publishes after stop.
    company_dir = accessor.get_analysis_dir(company.company_id).parent
    index_update = publish_company_index_after_host_run(company_dir)
    if not index_update.get("ok"):
        print("  ⚠ 公司索引已延期由宿主 Publisher 更新（不影响核查结果）")

    print(f"{'='*80}\n")
    return report


def cmd_status(args):
    accessor = WikiDataAccessor()
    companies = accessor.list_companies()
    print(f"\n{'='*100}")
    print("SIQ_factchecker 核实状态总览")
    print(f"{'='*100}")
    print(f"{'股票代码':<10} {'公司简称':<12} {'分析报告':<10} {'核实报告':<10} {'critical':<8} {'warning':<8} {'意见':<16}")
    print(f"{'-'*100}")
    for company in companies:
        analysis_dir = accessor.get_analysis_dir(company.company_id)
        md_files = [
            p for p in analysis_dir.glob("*.md")
            if p.name != "README.md"
            and "deep_analysis" not in p.name.lower()
            and ("analysis" in p.name.lower() or company.stock_code in p.name or company.company_short_name in p.name)
        ]
        factcheck_dir = accessor.get_factcheck_dir(company.company_id)
        fc_files = list(factcheck_dir.glob(f"{company.stock_code}-*-factcheck.json"))
        md_status = f"{len(md_files)} 份" if md_files else "✗"
        fc_status = f"{len(fc_files)} 份" if fc_files else "✗"
        critical = warning = "-"
        verdict = "-"
        if fc_files:
            latest_fc = max(fc_files, key=lambda p: p.stat().st_mtime)
            try:
                fc_data = json.loads(latest_fc.read_text(encoding="utf-8"))
                summary = fc_data.get("summary", {})
                critical = summary.get("critical", "-")
                warning = summary.get("warning", "-")
                verdict = fc_data.get("verdict", "-")
            except Exception:
                pass
        print(f"{company.stock_code:<10} {company.company_short_name:<12} {md_status:<10} {fc_status:<10} {str(critical):<8} {str(warning):<8} {verdict:<16}")
    print(f"{'='*100}\n")


def main():
    _load_env_file()
    parser = argparse.ArgumentParser(
        description="SIQ_factchecker - A股财务分析报告事实核实（无评分版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s list
  %(prog)s info 000333
  %(prog)s check 600399 --year 2025
  %(prog)s verify 600399 --year 2025
  %(prog)s status
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    subparsers.add_parser("list", help="列出所有工作集公司")
    info_parser = subparsers.add_parser("info", help="查看公司详细信息")
    info_parser.add_argument("company_id", help="公司ID或股票代码")
    check_parser = subparsers.add_parser("check", help="检查前置条件")
    check_parser.add_argument("company_id", help="公司ID或股票代码")
    check_parser.add_argument("--year", type=int, default=2025, help="报告年份")
    check_parser.add_argument("--report-path", type=Path, help="指定要核查的 analysis Markdown/HTML/JSON 文件")
    verify_parser = subparsers.add_parser("verify", help="执行事实核实")
    verify_parser.add_argument("company_id", help="公司ID或股票代码")
    verify_parser.add_argument("--year", type=int, default=2025, help="报告年份")
    verify_parser.add_argument("--report-path", type=Path, help="指定要核查的 analysis Markdown/HTML/JSON 文件")
    verify_parser.add_argument("--output", type=Path, help="指定 factcheck JSON 输出路径")
    subparsers.add_parser("status", help="查看核实状态")
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    {"list": cmd_list, "info": cmd_info, "check": cmd_check, "verify": cmd_verify, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
