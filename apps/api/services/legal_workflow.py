"""Deterministic legal opinion artifact workflow for the legal agent.

The workflow is intentionally narrow: it only handles explicit requests for a
formal legal artifact/HTML file. Ordinary legal Q&A remains on the chat path so
the assistant can answer naturally without creating wiki reports.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from services.command_runner import run_command
from services.path_config import PROJECT_ROOT, WIKI_ROOT
from services.specialist_artifact_contract import (
    SpecialistArtifactValidation,
    citation_has_locator,
    finalize_specialist_artifact,
    write_specialist_artifact_manifest,
)

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_LEGAL_WORKFLOW_TIMEOUT_SECONDS", "900"))
MIN_CITATIONS = 3
DEFAULT_TOP_K = 8

LEGAL_ACTION_RE = re.compile(r"(生成|出具|保存|导出|固化|落盘|创建|形成|产出|做一份|出一份)")
LEGAL_ARTIFACT_RE = re.compile(r"(HTML|html|网页|页面|文件|法律意见书|法律意见|意见书|合规审查报告|合规报告|法务报告)")
LEGAL_NO_HTML_RE = re.compile(
    r"((不要|不需要|无需|不用).{0,10}(HTML|html|网页|页面|文件|落盘|保存|固化)|"
    r"直接.{0,12}(对话|聊天).{0,12}(输出|回答)|不要生成\s*HTML|不要保存)"
)
LEGAL_META_QUESTION_RE = re.compile(r"(为什么|为何|原因|怎么没有|没有调用|没调用|没有固化|没固化|如何设计|怎么设计)")
OVERWRITE_RE = re.compile(r"(覆盖|替换现有|覆盖现有|写回默认|更新现有|改写现有)")
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
SAFE_FILENAME_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


@dataclass(frozen=True)
class LegalWorkflowRequest:
    company_query: str
    topic: str
    jurisdiction: str = "中国大陆"
    report_path: Path | None = None
    prompt: str = ""
    top_k: int = DEFAULT_TOP_K
    allow_overwrite: bool = False
    session_id: str = ""


@dataclass(frozen=True)
class LegalWorkflowResponse:
    handled: bool
    reply: str
    result: dict[str, Any]


def _context_dict(context: Any | None) -> dict[str, Any]:
    if context is None:
        return {}
    if isinstance(context, Mapping):
        return dict(context)
    if hasattr(context, "model_dump"):
        dumped = context.model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _context_company(context: Any | None) -> dict[str, Any]:
    raw = _context_dict(context).get("company")
    return raw if isinstance(raw, dict) else {}


def _context_report(context: Any | None) -> dict[str, Any]:
    raw = _context_dict(context).get("report")
    return raw if isinstance(raw, dict) else {}


def _clean(value: str | None) -> str:
    return str(value or "").strip().strip(" :：,，。；;")


def is_legal_generation_request(message: str, context: Any | None = None) -> bool:
    """Return True only for explicit legal artifact/HTML generation requests."""

    text = (message or "").strip()
    if not text or LEGAL_META_QUESTION_RE.search(text):
        return False
    if LEGAL_NO_HTML_RE.search(text):
        return False
    return bool(LEGAL_ACTION_RE.search(text) and LEGAL_ARTIFACT_RE.search(text))


def _extract_company_query(message: str, context: Any | None) -> str:
    company = _context_company(context)
    for key in ("dir", "code", "name"):
        value = _clean(company.get(key))
        if value:
            return value
    match = STOCK_CODE_RE.search(message or "")
    if match:
        return match.group(1)
    return _clean(message)


def _report_path_from_context(context: Any | None) -> Path | None:
    report = _context_report(context)
    value = str(report.get("url") or "").strip()
    filename = str(report.get("filename") or "").strip()
    company_dir = str(_context_company(context).get("dir") or "").strip()
    if value:
        match = re.search(r"(?:/api/wiki)?/companies/([^/]+)/([^/]+)/([^?#\s]+)", value)
        if match:
            company, section, name = match.groups()
            candidate = WIKI_ROOT / "companies" / company / section / name
            if candidate.suffix.lower() in {".html", ".json"}:
                md_candidate = candidate.with_suffix(".md")
                if md_candidate.exists():
                    return md_candidate
            return candidate
    if filename and company_dir:
        section = str(report.get("type") or "analysis").strip() or "analysis"
        return WIKI_ROOT / "companies" / company_dir / section / filename
    return None


def _strip_intent_words(text: str) -> str:
    text = re.sub(r"(请|麻烦|帮我|基于|按照|当前公司|当前报告|我提供的事实)", " ", text)
    text = LEGAL_ACTION_RE.sub(" ", text)
    text = LEGAL_ARTIFACT_RE.sub(" ", text)
    text = re.sub(r"(法务合规|公司法务|律师|工作底稿|口径|正式|初稿|草稿)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ：:，,。；;")


def _extract_topic(message: str, context: Any | None) -> str:
    text = _strip_intent_words(message or "")
    company = _context_company(context)
    for value in (company.get("dir"), company.get("code"), company.get("name")):
        clean = _clean(value)
        if clean:
            text = text.replace(clean, " ")
    text = re.sub(r"\s+", " ", text).strip(" ：:，,。；;")
    if text:
        return text[:80]
    report = _context_report(context)
    filename = _clean(report.get("filename"))
    if filename:
        return Path(filename).stem[:80]
    return "当前事项合规审查"


def _extract_jurisdiction(message: str) -> str:
    if re.search(r"(香港|港股|联交所|HKEX)", message, re.IGNORECASE):
        return "中国香港"
    if re.search(r"(美国|SEC|NASDAQ|NYSE)", message, re.IGNORECASE):
        return "美国"
    return "中国大陆"


def build_legal_workflow_request(message: str, context: Any | None = None) -> LegalWorkflowRequest | None:
    if not is_legal_generation_request(message, context):
        return None
    company_query = _extract_company_query(message, context)
    if not company_query:
        return None
    return LegalWorkflowRequest(
        company_query=company_query,
        topic=_extract_topic(message, context),
        jurisdiction=_extract_jurisdiction(message or ""),
        report_path=_report_path_from_context(context),
        prompt=(message or "").strip(),
        allow_overwrite=bool(OVERWRITE_RE.search(message or "")),
    )


def _load_catalog() -> list[dict[str, Any]]:
    path = WIKI_ROOT / "_meta" / "company_catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    companies = payload.get("companies")
    return companies if isinstance(companies, list) else []


def _normalize(value: str) -> str:
    return re.sub(r"[\s（）()\-_/]", "", str(value or "").lower())


def _company_payload(company: dict[str, Any]) -> dict[str, str]:
    return {
        "company_id": str(company.get("company_id") or ""),
        "stock_code": str(company.get("stock_code") or ""),
        "company_short_name": str(company.get("company_short_name") or ""),
        "company_full_name": str(company.get("company_full_name") or ""),
        "company_path": str(company.get("company_path") or ""),
    }


def _resolve_company(company_query: str) -> dict[str, str] | None:
    query = _normalize(company_query)
    if not query:
        return None

    best: tuple[int, dict[str, str]] | None = None
    for company in _load_catalog():
        if not isinstance(company, dict):
            continue
        values = [
            company.get("company_id"),
            company.get("stock_code"),
            company.get("company_short_name"),
            company.get("company_full_name"),
            company.get("company_path"),
            *(company.get("aliases") or []),
        ]
        normalized_values = [_normalize(str(value or "")) for value in values]
        if any(query == value for value in normalized_values if value):
            return _company_payload(company)
        containment_scores = [len(value) for value in normalized_values if value and value in query]
        if containment_scores:
            score = max(containment_scores)
            if best is None or score > best[0]:
                best = (score, _company_payload(company))

    if best is not None:
        return best[1]
    match = STOCK_CODE_RE.search(company_query)
    if match:
        code = match.group(1)
        return {
            "company_id": code,
            "stock_code": code,
            "company_short_name": code,
            "company_full_name": "",
            "company_path": f"companies/{code}",
        }
    return None


def _company_dir(company: dict[str, str]) -> Path:
    company_path = company.get("company_path")
    if company_path:
        return WIKI_ROOT / company_path
    company_id = company.get("company_id") or company.get("stock_code") or ""
    return WIKI_ROOT / "companies" / company_id


def _company_dir_name(company_dir: Path) -> str:
    return company_dir.name


def _legal_milvus_script() -> Path:
    return PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_legal" / "scripts" / "legal_milvus_cli.py"


def _validator_script() -> Path:
    return PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_legal" / "scripts" / "validate_legal_opinion.py"


def _load_stdout_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}
    for index in range(len(stdout)):
        if stdout[index] != "{":
            continue
        try:
            payload = json.loads(stdout[index:])
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def _retrieval_query(request: LegalWorkflowRequest, company: dict[str, str]) -> str:
    company_name = company.get("company_short_name") or company.get("company_full_name") or company.get("stock_code") or ""
    listed_hint = "上市公司 信息披露 公司治理 证券法 公司法"
    return " ".join(part for part in [company_name, request.topic, request.jurisdiction, listed_hint] if part).strip()


def _retrieve_legal_sources(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str] | None]:
    script = _legal_milvus_script()
    if not script.is_file():
        return {"ok": False, "stage": "script_missing", "results": []}, None
    cmd = [
        sys.executable,
        str(script),
        "hybrid_search",
        query,
        "--top-k",
        str(max(MIN_CITATIONS, min(top_k, 20))),
        "--no-rerank",
    ]
    completed = run_command(cmd, cwd=PROJECT_ROOT, timeout=timeout)
    payload = _load_stdout_json(completed)
    payload["ok"] = completed.returncode == 0 and isinstance(payload.get("results"), list)
    return payload, completed


def _compact(value: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _citation_source(result: Mapping[str, Any]) -> str:
    source = str(result.get("source") or "").strip()
    if source:
        return source
    source_path = str(result.get("source_path") or "").strip()
    if source_path:
        return Path(source_path).stem
    return "法规检索片段"


def _normalize_citations(results: list[Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, Mapping):
            continue
        source_path = str(result.get("source_path") or "").strip()
        chunk_index = str(result.get("chunk_index") or "").strip()
        key = f"{source_path}#{chunk_index}"
        if key in seen:
            continue
        seen.add(key)
        source = _citation_source(result)
        text = _compact(str(result.get("text") or ""), 220)
        if not source_path and not text:
            continue
        citations.append(
            {
                "rank": str(result.get("rank") or len(citations) + 1),
                "source_type": "legal_corpus",
                "source": source,
                "source_path": source_path or source,
                "chunk_index": chunk_index or "N/A",
                "quote": text or source,
                "relevance": "作为本事项法律适用和风险判断的检索依据",
            }
        )
    return citations


def _relative(path: str | Path | None) -> str:
    if not path:
        return ""
    raw = Path(str(path))
    try:
        return raw.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return str(path)


def _wiki_legal_url(html_path: str | Path | None) -> str:
    if not html_path:
        return ""
    path = Path(str(html_path))
    parts = path.parts
    try:
        companies_index = parts.index("companies")
        company_dir = parts[companies_index + 1]
    except (ValueError, IndexError):
        return ""
    return (
        f"/api/wiki/companies/{quote(company_dir, safe='')}/legal/"
        f"{quote(path.name, safe='')}"
    )


def _safe_filename_part(value: str, default: str = "legal_opinion") -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", value.strip()).strip("._-")
    return (cleaned or default)[:48]


def _default_output_path(company_dir: Path, topic: str, allow_overwrite: bool) -> Path:
    legal_dir = company_dir / "legal"
    slug = _safe_filename_part(topic, "current_matter")
    if allow_overwrite:
        return legal_dir / f"legal_opinion_{slug}.html"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return legal_dir / f"legal_opinion_{slug}_{timestamp}.html"


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _build_citation_table_rows(citations: list[dict[str, str]]) -> str:
    rows = []
    for index, citation in enumerate(citations, start=1):
        rows.append(
            "<tr>"
            f"<td>[{index}]</td>"
            f"<td>{_h(citation['source'])}</td>"
            f"<td>{_h(citation['source_path'])}</td>"
            f"<td>{_h(citation['chunk_index'])}</td>"
            f"<td>{_h(citation['relevance'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _build_citation_lines(citations: list[dict[str, str]]) -> str:
    lines = []
    for index, citation in enumerate(citations, start=1):
        lines.append(
            "<p>"
            f"[{index}] source={_h(citation['source'])}, "
            f"source_path={_h(citation['source_path'])}, "
            f"chunk_index={_h(citation['chunk_index'])}, "
            f"quote=&quot;{_h(citation['quote'])}&quot;, "
            f"relevance={_h(citation['relevance'])}"
            "</p>"
        )
    return "\n".join(lines)


def _build_legal_opinion_html(
    *,
    company: dict[str, str],
    company_dir: Path,
    request: LegalWorkflowRequest,
    citations: list[dict[str, str]],
    retrieval_query: str,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stock_code = company.get("stock_code") or company.get("company_id") or ""
    company_name = company.get("company_short_name") or company.get("company_full_name") or stock_code or "当前公司"
    full_name = company.get("company_full_name") or company_name
    subject = f"{stock_code}-{company_name}" if stock_code and stock_code != company_name else company_name
    topic = request.topic or "当前事项合规审查"
    report_note = _relative(request.report_path) if request.report_path else "未绑定特定报告，以用户当前问题和法规检索结果为基础"
    primary_sources = "、".join(_h(citation["source"]) for citation in citations[:3])
    citation_refs = "".join(f"[{index}]" for index in range(1, min(len(citations), 5) + 1))
    table_rows = _build_citation_table_rows(citations)
    citation_lines = _build_citation_lines(citations)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h(subject)} - {_h(topic)}法律意见</title>
<style>
  body {{ margin: 0; background: #f6f8fb; color: #243041; font-family: Arial, "Microsoft YaHei", sans-serif; line-height: 1.72; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 56px; }}
  header {{ background: #ffffff; border: 1px solid #d9e2ec; border-top: 5px solid #1f6f8b; padding: 26px 30px; margin-bottom: 18px; }}
  h1 {{ margin: 0 0 10px; font-size: 25px; line-height: 1.35; color: #16324f; }}
  h2 {{ margin: 0 0 14px; font-size: 19px; color: #16324f; }}
  h3 {{ margin: 18px 0 8px; font-size: 16px; color: #245b78; }}
  section {{ background: #ffffff; border: 1px solid #d9e2ec; padding: 24px 30px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #d9e2ec; padding: 9px 10px; vertical-align: top; }}
  th {{ background: #eef4f7; color: #16324f; text-align: left; }}
  ul, ol {{ padding-left: 22px; }}
  .meta {{ display: grid; gap: 6px; color: #526173; font-size: 14px; }}
  .notice {{ background: #fff7ed; border-left: 4px solid #b45309; padding: 12px 14px; margin-top: 14px; color: #704214; }}
  .summary {{ background: #eef8f6; border-left: 4px solid #0f766e; padding: 12px 14px; }}
  .source-line p {{ margin: 0 0 10px; word-break: break-word; }}
  footer {{ color: #697586; font-size: 12px; text-align: center; margin-top: 22px; }}
</style>
</head>
<body>
<main>
<header>
  <h1>{_h(subject)} - {_h(topic)}法律意见</h1>
  <div class="meta">
    <span>出具时间：{_h(now)}</span>
    <span>事项主体：{_h(full_name)}（股票代码：{_h(stock_code or "未提供")}）</span>
    <span>意见类型：合规审查 / 风险初筛 / 法务工作底稿</span>
    <span>管辖口径：{_h(request.jurisdiction)}</span>
    <span>公司目录：{_h(_company_dir_name(company_dir))}</span>
  </div>
  <div class="notice">本意见基于本机 Milvus 法律库 ic_legal_scanner 检索结果与用户提供事实形成，不构成最终法律意见，不替代执业律师判断。</div>
</header>

<section>
  <h2>一、事项摘要</h2>
  <p class="summary">基于现有事实和本次法规检索结果，初步倾向认为，本事项应先按上市公司合规事项进行审慎识别，再分别核对信息披露、公司治理程序、交易安排及后续监管沟通要求。当前结论以用户提供事实真实、完整且未发生重大变化为前提；如交易结构、关联关系、审批记录或公告时点存在差异，结论需进一步核实。</p>
  <p>从公司法务角度，管理层需要优先关注三件事：第一，是否存在应披露而未披露或披露不充分的事项；第二，内部决策程序、授权链条和留痕材料是否完整；第三，是否需要同步董办、证券部、财务及外部律师形成复核闭环。主要检索依据包括：{primary_sources}。</p>
</section>

<section>
  <h2>二、事实背景</h2>
  <table>
    <tr><th>主体</th><td>{_h(full_name)}（{_h(stock_code or "股票代码未提供")}）</td></tr>
    <tr><th>事项</th><td>{_h(topic)}</td></tr>
    <tr><th>用户请求</th><td>{_h(request.prompt)}</td></tr>
    <tr><th>关联报告</th><td>{_h(report_note)}</td></tr>
    <tr><th>已检索材料</th><td>本机法律库 ic_legal_scanner，检索式：{_h(retrieval_query)}</td></tr>
    <tr><th>尚待核实事项</th><td>交易文件、董事会或股东大会决议、关联方识别清单、公告草稿、财务影响测算、监管问询或处罚记录。</td></tr>
  </table>
  <p>本意见以下判断以现有事实为基础。若后续补充材料显示交易金额、交易对方、控制关系、审批权限或信息披露时点与目前描述不一致，应相应调整风险等级和建议动作。</p>
</section>

<section>
  <h2>三、适用法规与检索路径</h2>
  <table>
    <thead><tr><th>序号</th><th>法规/规则名称</th><th>source_path</th><th>chunk_index</th><th>本事项关联</th></tr></thead>
    <tbody>
{table_rows}
    </tbody>
  </table>
</section>

<section>
  <h2>四、法律分析</h2>
  <h3>4.1 信息披露与监管沟通</h3>
  <p>就上市公司二级市场场景而言，信息披露的核心不是简单判断事项是否“重大”，而是结合交易金额、交易性质、交易对方、是否涉及关联关系以及对投资者决策的影响进行综合判断。基于现有事实，建议先按审慎口径准备披露判断底稿，并由董办或证券部核对交易所规则、监管问询口径及历史公告一致性。相关依据参见{citation_refs}。</p>
  <h3>4.2 公司治理程序与内部控制</h3>
  <p>如事项涉及董事会、股东大会、关联董事回避、独立董事或审计委员会前置审查，公司应重点核查授权链条和会议材料是否完整。法务判断上，程序瑕疵往往会放大监管和交易执行风险；即使实体安排具备商业合理性，也建议补齐决议、审批、用印和信息披露留痕。</p>
  <h3>4.3 交易结构、责任边界与后续跟踪</h3>
  <p>若事项涉及关联交易、对外担保、资金占用、重大资产重组、股份减持或回购，应进一步核实交易结构是否触发专项规则。现阶段不宜作出“完全合规”或“必然违规”的结论；更稳妥的处理方式，是将法规适用、事实缺口和需管理层决策的事项分别列明，并在补充材料后出具更新意见。</p>
</section>

<section>
  <h2>五、风险提示</h2>
  <ul>
    <li><strong>监管风险：</strong>如披露义务判断偏保守不足、公告时点滞后或关键事实遗漏，可能引发问询、监管关注或后续整改要求。</li>
    <li><strong>治理风险：</strong>如内部审批权限、关联方回避或会议记录不完整，可能影响交易程序效力和管理层勤勉履职评价。</li>
    <li><strong>交易风险：</strong>如合同条件、付款安排、估值基础或业绩承诺与披露口径不一致，可能带来交易执行和投资者关系风险。</li>
    <li><strong>检索局限：</strong>本意见依赖本机法律库与当前检索结果，仍需补充最新交易所规则、监管案例、公司章程及具体交易文件。</li>
  </ul>
</section>

<section>
  <h2>六、结论与建议</h2>
  <ol>
    <li><strong>初步结论：</strong>基于目前材料，本事项宜按需披露、需留痕、需复核的审慎路径推进。最终结论需以交易文件、决策程序和监管规则核验结果为准。</li>
    <li><strong>立即措施：</strong>建议由法务牵头建立事项清单，董办核对披露口径，财务补充金额和影响测算，业务部门确认交易背景与商业合理性。</li>
    <li><strong>待核实事项：</strong>补充交易对方及关联关系、审批权限、董事/股东回避安排、公告草稿、历史同类事项披露口径。</li>
    <li><strong>外部复核：</strong>如事项金额较大、市场敏感或存在监管问询可能，建议提交外部律师和中介机构复核。</li>
    <li><strong>后续跟踪：</strong>建议纳入持续跟踪清单，关注公告披露、监管问询、交易进展、诉讼仲裁及整改完成情况。</li>
  </ol>
</section>

<section>
  <h2>七、引用来源</h2>
  <div class="source-line">
{citation_lines}
  </div>
</section>

<section>
  <h2>八、免责声明</h2>
  <ul>
    <li>本意见基于本机 Milvus 法律库 ic_legal_scanner 在出具日之前的检索结果，可能未覆盖最新法规修订、地方规则或交易所窗口指导。</li>
    <li>本意见为风险初筛与合规辅助，不替代执业律师与监管机构的正式认定。</li>
    <li>本意见不得作为诉讼、仲裁、行政程序或信息披露文件的最终依据。</li>
    <li>公司在采取实际行动前，应结合完整事实材料咨询具有相应执业资格的律师。</li>
  </ul>
</section>

<footer>SIQ 法务合规智能体 · {_h(now)}</footer>
</main>
</body>
</html>
"""


def _validate_legal_artifact(path: Path, validation_path: Path, *, timeout: int | float = 120) -> dict[str, Any]:
    script = _validator_script()
    if not script.is_file():
        return {"ok": False, "failures": ["validator_missing"], "warnings": []}
    completed = run_command(
        [sys.executable, str(script), str(path), "--write-json", str(validation_path)],
        cwd=PROJECT_ROOT,
        timeout=timeout,
    )
    payload = _load_stdout_json(completed)
    if not payload and validation_path.exists():
        try:
            payload = json.loads(validation_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    if not payload:
        payload = {
            "ok": False,
            "failures": [f"validator_returncode:{completed.returncode}"],
            "warnings": [],
            "stdout": (completed.stdout or "").strip()[-1000:],
            "stderr": (completed.stderr or "").strip()[-1000:],
        }
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_manifest(
    *,
    html_path: Path,
    retrieval_path: Path,
    validation_path: Path,
    company: dict[str, str],
    company_dir: Path,
    request: LegalWorkflowRequest,
    citations: list[dict[str, str]],
    validation: dict[str, Any],
) -> tuple[Path, Path]:
    manifest = {
        "artifact_type": "legal_opinion_html",
        "company_code": company.get("stock_code") or company.get("company_id") or "",
        "company_name": company.get("company_short_name") or company.get("company_full_name") or "",
        "company_full_name": company.get("company_full_name") or "",
        "company_dir": _company_dir_name(company_dir),
        "subject": request.topic,
        "topic": request.topic,
        "jurisdiction": request.jurisdiction,
        "source_report": str(request.report_path) if request.report_path else "",
        "prompt": request.prompt,
        "html_path": str(html_path),
        "html_url": _wiki_legal_url(html_path),
        "retrieval_path": str(retrieval_path),
        "validation_path": str(validation_path),
        "validation": validation,
        "citation_count": len(citations),
        "citations": [
            {
                "source": item.get("source"),
                "source_path": item.get("source_path"),
                "chunk_index": item.get("chunk_index"),
                "relevance": item.get("relevance"),
            }
            for item in citations
        ],
        "created_at": datetime.now().isoformat(),
    }
    manifest_path = html_path.with_suffix(".manifest.json")
    latest_manifest_path = html_path.parent / "legal_manifest.json"
    _write_json(manifest_path, manifest)
    _write_json(latest_manifest_path, {**manifest, "manifest_path": str(manifest_path)})
    return manifest_path, latest_manifest_path


def format_legal_workflow_reply(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    title = "已生成正式法务合规 HTML 意见书" if ok else "法务合规 HTML 意见书生成未完成"
    html_path = str(result.get("html_path") or "")
    html_url = _wiki_legal_url(html_path)
    validation = result.get("validation_result")
    if not isinstance(validation, dict):
        validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    validation_status = "通过" if validation.get("ok") else f"需复核，failures={len(validation.get('failures') or [])}"

    lines = [
        f"**{title}**",
        "",
        f"- 公司请求: `{result.get('company_query') or ''}`",
        f"- 公司: `{result.get('stock_code') or ''}-{result.get('company_name') or ''}`",
        f"- 事项: `{result.get('topic') or ''}`",
        f"- 工作流状态: `{result.get('stage') or 'unknown'}`",
        f"- 法规引用: `{result.get('citation_count') or 0}` 条",
        f"- 质量校验: `{validation_status}`",
    ]
    if html_url:
        lines.append(f"- 打开意见书: [HTML 法律意见书]({html_url})")
    if html_path:
        lines.append(f"- HTML: `{_relative(html_path)}`")
    for key, label in (
        ("manifest_path", "Manifest"),
        ("retrieval_path", "法规检索记录"),
        ("validation_path", "校验记录"),
    ):
        if result.get(key):
            lines.append(f"- {label}: `{_relative(result.get(key))}`")
    next_action = str(result.get("next_action") or "").strip()
    if next_action and not ok:
        lines.extend(["", f"下一步: {next_action}"])
    return "\n".join(lines)


def _failure_response(stage: str, request: LegalWorkflowRequest, **extra: Any) -> LegalWorkflowResponse:
    result = {
        "ok": False,
        "stage": stage,
        "company_query": request.company_query,
        "topic": request.topic,
        **extra,
    }
    return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)


def run_legal_workflow(
    request: LegalWorkflowRequest,
    *,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> LegalWorkflowResponse:
    company = _resolve_company(request.company_query)
    if company is None:
        return _failure_response(
            "company_resolve_failed",
            request,
            next_action="请在当前页面选择公司，或在消息中提供唯一股票代码/company_id。",
        )

    company_dir = _company_dir(company)
    stock_code = company.get("stock_code") or company.get("company_id") or request.company_query
    company_name = company.get("company_short_name") or company.get("company_full_name") or stock_code
    query = _retrieval_query(request, company)

    try:
        retrieval_payload, completed = _retrieve_legal_sources(query, top_k=request.top_k, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return _failure_response(
            "timeout",
            request,
            stock_code=stock_code,
            company_name=company_name,
            next_action=f"法规检索超时: {exc}",
        )

    if not retrieval_payload.get("ok"):
        return _failure_response(
            str(retrieval_payload.get("stage") or "legal_retrieval_failed"),
            request,
            stock_code=stock_code,
            company_name=company_name,
            returncode=getattr(completed, "returncode", None),
            stdout=(getattr(completed, "stdout", "") or "").strip()[-2000:],
            stderr=(getattr(completed, "stderr", "") or "").strip()[-2000:],
            next_action="请确认 Milvus 法律库、Docker/Attu 容器和 legal_milvus_cli.py 可用；普通问答仍可继续走对话模式。",
        )

    citations = _normalize_citations(retrieval_payload.get("results") or [])
    if len(citations) < MIN_CITATIONS:
        return _failure_response(
            "insufficient_legal_citations",
            request,
            stock_code=stock_code,
            company_name=company_name,
            citation_count=len(citations),
            next_action="检索到的可引用法规不足 3 条。请补充事项关键词，或先让法务助手检索法规依据后再生成 HTML 意见书。",
        )

    html_path = _default_output_path(company_dir, request.topic, request.allow_overwrite)
    draft_dir = html_path.parent / "_drafts"
    draft_path = draft_dir / html_path.name
    validation_path = draft_path.with_suffix(".validation.json")
    draft_dir.mkdir(parents=True, exist_ok=True)

    html_text = _build_legal_opinion_html(
        company=company,
        company_dir=company_dir,
        request=request,
        citations=citations,
        retrieval_query=query,
    )
    draft_path.write_text(html_text, encoding="utf-8")
    validation = _validate_legal_artifact(draft_path, validation_path)

    if not validation.get("ok"):
        result = {
            "ok": False,
            "stage": "validation_failed",
            "company_query": request.company_query,
            "stock_code": stock_code,
            "company_name": company_name,
            "topic": request.topic,
            "citation_count": len(citations),
            "draft_path": str(draft_path),
            "validation_path": str(validation_path),
            "validation": validation,
            "next_action": "草稿未通过法务意见质量门禁，已留在 _drafts 目录，未发布到公司 legal/ 列表。",
        }
        return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)

    contract_checks = {
        "quality_validator_passed": validation.get("ok") is True,
        "html_present": draft_path.exists(),
        "minimum_citations_met": len(citations) >= MIN_CITATIONS,
        "citations_traceable": bool(citations) and all(citation_has_locator(item) for item in citations),
        "conditional_language_present": "不替代执业律师" in html_text and "最终结论需" in html_text,
    }
    contract_failures = [name for name, passed in contract_checks.items() if not passed]
    contract_validation = SpecialistArtifactValidation(
        ok=not contract_failures,
        checks=contract_checks,
        failures=contract_failures,
        warnings=list(validation.get("warnings") or []),
    )
    if not contract_validation.ok:
        draft_retrieval_path = draft_path.with_suffix(".retrieval.json")
        _write_json(draft_retrieval_path, retrieval_payload)
        artifact = finalize_specialist_artifact(
            artifact_type="legal",
            company_id=company.get("company_id") or stock_code,
            source_report_path=str(request.report_path or draft_retrieval_path),
            output_path=str(draft_path),
            html_url="",
            citations=citations,
            validation_result=contract_validation,
            profile="siq_legal",
            message=request.prompt or request.topic,
            session_id=request.session_id,
            metadata={"topic": request.topic, "jurisdiction": request.jurisdiction},
            specialist_facts={"legal_facts": citations},
        )
        artifact_manifest_path = draft_path.with_suffix(".artifact.json")
        write_specialist_artifact_manifest(artifact, artifact_manifest_path)
        result = {
            "ok": False,
            "stage": "contract_validation_failed",
            "company_query": request.company_query,
            "stock_code": stock_code,
            "company_name": company_name,
            "topic": request.topic,
            "citation_count": len(citations),
            "draft_path": str(draft_path),
            "retrieval_path": str(draft_retrieval_path),
            "validation_path": str(validation_path),
            "validation": validation,
            "artifact": artifact.model_dump(),
            "artifact_manifest_path": str(artifact_manifest_path),
            "audit_trace_id": artifact.audit_trace_id,
            "validation_result": contract_validation.model_dump(),
            "next_action": "草稿未通过统一 specialist artifact contract，已留在 _drafts 目录且未发布。",
        }
        return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(draft_path.read_text(encoding="utf-8"), encoding="utf-8")
    retrieval_path = html_path.with_suffix(".retrieval.json")
    published_validation_path = html_path.with_suffix(".validation.json")
    _write_json(retrieval_path, retrieval_payload)
    _write_json(published_validation_path, validation)
    manifest_path, latest_manifest_path = _write_manifest(
        html_path=html_path,
        retrieval_path=retrieval_path,
        validation_path=published_validation_path,
        company=company,
        company_dir=company_dir,
        request=request,
        citations=citations,
        validation=validation,
    )

    artifact = finalize_specialist_artifact(
        artifact_type="legal",
        company_id=company.get("company_id") or stock_code,
        source_report_path=str(request.report_path or retrieval_path),
        output_path=str(html_path),
        html_url=_wiki_legal_url(html_path),
        citations=citations,
        validation_result=contract_validation,
        profile="siq_legal",
        message=request.prompt or request.topic,
        session_id=request.session_id,
        metadata={"topic": request.topic, "jurisdiction": request.jurisdiction},
        specialist_facts={"legal_facts": citations},
    )
    artifact_manifest_path = html_path.with_suffix(".artifact.json")
    write_specialist_artifact_manifest(artifact, artifact_manifest_path)

    result = {
        "ok": True,
        "stage": "completed",
        "company_query": request.company_query,
        "stock_code": stock_code,
        "company_name": company_name,
        "company_path": str(company_dir),
        "topic": request.topic,
        "jurisdiction": request.jurisdiction,
        "citation_count": len(citations),
        "html_path": str(html_path),
        "html_url": _wiki_legal_url(html_path),
        "manifest_path": str(manifest_path),
        "latest_manifest_path": str(latest_manifest_path),
        "retrieval_path": str(retrieval_path),
        "validation_path": str(published_validation_path),
        "validation": validation,
        "artifact": artifact.model_dump(),
        "artifact_manifest_path": str(artifact_manifest_path),
        "audit_trace_id": artifact.audit_trace_id,
        "validation_result": contract_validation.model_dump(),
        "finished_at": datetime.now().isoformat(),
    }
    return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)
