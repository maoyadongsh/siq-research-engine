"""Read-only, multi-market Wiki catalog helpers for the agent runtime."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.path_config import WIKI_ROOT as CONFIG_WIKI_ROOT

WIKI_ROOT = CONFIG_WIKI_ROOT
MARKET_ORDER = ("CN", "HK", "US", "JP", "KR", "EU")
MARKET_LABELS = {
    "CN": "A股",
    "HK": "港股",
    "US": "美股",
    "JP": "日股",
    "KR": "韩股",
    "EU": "欧洲市场",
}
MARKET_SUBDIRECTORIES = {
    "CN": "",
    "HK": "hk",
    "US": "us",
    "JP": "jp",
    "KR": "kr",
    "EU": "eu",
}
MARKET_QUERY_TERMS = {
    "CN": ("a股", "沪深", "沪市", "深市", "北交所", "中国a股", "中国市场"),
    "HK": ("港股", "香港", "港交所", "hkex"),
    "US": ("美股", "美国", "美国证券", "sec公司"),
    "JP": ("日股", "日本", "东京证券", "东证"),
    "KR": ("韩股", "韩国", "韩国证券"),
    "EU": ("欧股", "欧洲", "欧洲公司"),
}
MARKET_CODE_PATTERN = re.compile(r"(?<![a-z0-9])(cn|hk|us|jp|kr|eu)(?![a-z0-9])", re.IGNORECASE)

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
    "哪些",
    "都有谁",
    "list",
)
WIKI_CATALOG_SUBJECT_TERMS = (
    "已入库",
    "已收录",
    "入库",
    "收录",
    "wiki",
    "Wiki",
    "公司",
    "财报",
    "工作集",
    "知识库",
    "市场",
    "market",
)


@dataclass(frozen=True)
class MarketCatalog:
    market: str
    wiki_root: Path
    catalog_path: Path
    payload: dict[str, Any]
    companies: tuple[dict[str, Any], ...]


def _read_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_wiki_catalog_query(
    message: str,
    *,
    is_general_assistant_request: Callable[[str], bool] | None = None,
    count_terms: tuple[str, ...] = WIKI_CATALOG_COUNT_TERMS,
    list_terms: tuple[str, ...] = WIKI_CATALOG_LIST_TERMS,
    subject_terms: tuple[str, ...] = WIKI_CATALOG_SUBJECT_TERMS,
) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text:
        return False
    if is_general_assistant_request and is_general_assistant_request(message):
        return False
    lower = text.lower()
    has_subject = any(term.lower() in lower for term in subject_terms)
    has_count = any(term.lower() in lower for term in count_terms)
    has_list = any(term.lower() in lower for term in list_terms)
    if has_subject and (has_count or has_list):
        return True
    return "company_catalog" in lower or "公司catalog" in lower


def wiki_catalog_path(*, wiki_root: Path | str | None = None) -> Path:
    root = Path(wiki_root) if wiki_root is not None else WIKI_ROOT
    return root / "_meta" / "company_catalog.json"


def market_wiki_roots(*, wiki_root: Path | str | None = None) -> dict[str, Path]:
    root = Path(wiki_root) if wiki_root is not None else WIKI_ROOT
    return {
        market: root / subdirectory if subdirectory else root
        for market, subdirectory in MARKET_SUBDIRECTORIES.items()
    }


def requested_catalog_markets(message: str) -> tuple[str, ...]:
    """Return explicit market filters, or all supported markets when unspecified."""
    compact = re.sub(r"\s+", "", message or "").lower()
    if any(term in compact for term in ("全市场", "所有市场", "各市场", "多市场", "全球市场")):
        return MARKET_ORDER

    requested: set[str] = set()
    for market, terms in MARKET_QUERY_TERMS.items():
        if any(term in compact for term in terms):
            requested.add(market)
    requested.update(match.group(1).upper() for match in MARKET_CODE_PATTERN.finditer(message or ""))
    return tuple(market for market in MARKET_ORDER if market in requested) or MARKET_ORDER


def load_wiki_catalog_companies(
    *,
    wiki_root: Path | str | None = None,
    read_json_file: Callable[[Path], Any | None] = _read_json_file,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Load one catalog. Kept as the backward-compatible single-root API."""
    catalog = read_json_file(wiki_catalog_path(wiki_root=wiki_root))
    companies = catalog.get("companies") if isinstance(catalog, dict) else None
    if not isinstance(companies, list):
        return catalog if isinstance(catalog, dict) else None, []
    normalized = [item for item in companies if isinstance(item, dict)]
    normalized.sort(key=_company_sort_key)
    return catalog, normalized


def _normalized_market(value: Any) -> str:
    market = str(value or "").strip().upper().replace("-", "_")
    if market == "US_SEC":
        market = "US"
    return market if market in MARKET_ORDER else ""


def _company_id_market(value: Any) -> str:
    text = str(value or "").strip()
    if ":" not in text:
        return ""
    return _normalized_market(text.split(":", 1)[0])


def _canonical_company_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if ":" not in text:
        return text.casefold()
    prefix, suffix = text.split(":", 1)
    market = _normalized_market(prefix)
    normalized_prefix = market or prefix.strip().upper()
    if market == "US" and suffix.upper().startswith("CIK"):
        suffix = suffix[3:]
    return f"{normalized_prefix}:{suffix}".casefold()


def _company_sort_key(company: dict[str, Any]) -> tuple[str, str]:
    code = str(company.get("stock_code") or company.get("ticker") or company.get("company_id") or "")
    name = str(
        company.get("company_short_name")
        or company.get("company_name")
        or company.get("company_full_name")
        or ""
    )
    return code.upper(), name.casefold()


def _company_belongs_to_market(
    company: dict[str, Any],
    market: str,
    *,
    include_unclassified: bool = False,
) -> bool:
    item_market = _normalized_market(company.get("market"))
    if market != "CN":
        return not item_market or item_market == market
    if item_market:
        return item_market == "CN"
    # The migrated root catalog can contain unclassified generic subjects. They
    # are not silently counted as A-share companies.
    return include_unclassified or str(company.get("identity_kind") or "").strip() != "generic_subject"


def load_market_catalogs(
    *,
    wiki_root: Path | str | None = None,
    markets: Iterable[str] | None = None,
    include_unclassified: bool = False,
    read_json_file: Callable[[Path], Any | None] = _read_json_file,
) -> list[MarketCatalog]:
    roots = market_wiki_roots(wiki_root=wiki_root)
    selected_market_set = set(markets or MARKET_ORDER)
    selected = tuple(market for market in MARKET_ORDER if market in selected_market_set)
    catalogs: list[MarketCatalog] = []
    for market in selected:
        market_root = roots[market]
        catalog_path = wiki_catalog_path(wiki_root=market_root)
        payload = read_json_file(catalog_path)
        raw_companies = payload.get("companies") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or not isinstance(raw_companies, list):
            continue
        companies = [
            company
            for company in raw_companies
            if isinstance(company, dict)
            and _company_belongs_to_market(
                company,
                market,
                include_unclassified=include_unclassified,
            )
        ]
        companies.sort(key=_company_sort_key)
        catalogs.append(
            MarketCatalog(
                market=market,
                wiki_root=market_root,
                catalog_path=catalog_path,
                payload=payload,
                companies=tuple(companies),
            )
        )
    return catalogs


def catalog_company_code(company: dict[str, Any]) -> str:
    return str(company.get("stock_code") or company.get("ticker") or company.get("security_code") or "").strip()


def catalog_company_name(company: dict[str, Any]) -> str:
    return str(
        company.get("company_short_name")
        or company.get("company_name")
        or company.get("company_full_name")
        or ""
    ).strip()


def format_catalog_company_line(index: int, company: dict[str, Any]) -> str:
    code = catalog_company_code(company)
    name = catalog_company_name(company)
    company_id = str(company.get("company_id") or "").strip()
    status = str(company.get("status") or "").strip()
    report_count = company.get("report_count")
    parts = [f"{index}. {code} {name}".strip()]
    if company_id and (not code or company_id not in {code, f"{code}-{name}"}):
        parts.append(f"company_id={company_id}")
    if status:
        parts.append(f"status={status}")
    if report_count not in (None, ""):
        parts.append(f"reports={report_count}")
    if company.get("has_three_statement_metrics") is False:
        parts.append("三大表指标=无")
    return "，".join(parts)


def _catalog_summary(catalog: MarketCatalog) -> str:
    ready = sum(1 for company in catalog.companies if company.get("status") == "ready")
    needs_review = sum(1 for company in catalog.companies if company.get("status") == "needs_review")
    reports = sum(int(company.get("report_count") or 0) for company in catalog.companies)
    label = MARKET_LABELS[catalog.market]
    return (
        f"{label}（{catalog.market}）{len(catalog.companies)} 家；"
        f"ready：{ready} 家；needs_review：{needs_review} 家；报告合计：{reports} 份。"
    )


def build_wiki_catalog_reply(
    message: str,
    *,
    wiki_root: Path | str | None = None,
    is_general_assistant_request: Callable[[str], bool] | None = None,
    read_json_file: Callable[[Path], Any | None] = _read_json_file,
    count_terms: tuple[str, ...] = WIKI_CATALOG_COUNT_TERMS,
    list_terms: tuple[str, ...] = WIKI_CATALOG_LIST_TERMS,
    subject_terms: tuple[str, ...] = WIKI_CATALOG_SUBJECT_TERMS,
) -> str | None:
    if not is_wiki_catalog_query(
        message,
        is_general_assistant_request=is_general_assistant_request,
        count_terms=count_terms,
        list_terms=list_terms,
        subject_terms=subject_terms,
    ):
        return None

    root = Path(wiki_root) if wiki_root is not None else WIKI_ROOT
    requested_markets = requested_catalog_markets(message)
    catalogs = load_market_catalogs(
        wiki_root=root,
        markets=requested_markets,
        read_json_file=read_json_file,
    )
    available_markets = {catalog.market for catalog in catalogs}
    missing_markets = [market for market in requested_markets if market not in available_markets]
    if not catalogs:
        paths = [wiki_catalog_path(wiki_root=market_wiki_roots(wiki_root=root)[market]) for market in requested_markets]
        return (
            "## 结论\n"
            "- 当前无法读取已入库公司清单。\n\n"
            "## 依据/数据\n"
            f"- Wiki 根目录：{root}\n"
            f"- catalog：{'；'.join(str(path) for path in paths)}\n"
            "- 问题：文件不存在、格式异常，或 `companies` 为空。\n\n"
            "## 引用来源\n"
            f"[1] source_type=wiki_metadata, file={paths[0]}, count=0"
        )

    actual_count = sum(len(catalog.companies) for catalog in catalogs)
    compact_message = re.sub(r"\s+", "", message or "").lower()
    needs_list = any(term.lower() in compact_message for term in list_terms)
    scope_label = "全市场" if len(requested_markets) > 1 else MARKET_LABELS[requested_markets[0]]
    lines = [
        "## 结论",
        f"- 当前 Wiki {scope_label}已入库公司一共 **{actual_count} 家**。",
        "- 统计口径：实时聚合本项目各市场生产 catalog，不使用备份目录、历史 README、会话记忆或模型记忆。",
        "",
        "## 依据/数据",
    ]
    lines.extend(f"- {_catalog_summary(catalog)}" for catalog in catalogs)
    if missing_markets:
        lines.append(f"- 未读取到 catalog 的市场：{', '.join(missing_markets)}；以上为可用 catalog 的部分结果。")

    for catalog in catalogs:
        declared_count = catalog.payload.get("company_count")
        raw_count = len(catalog.payload.get("companies") or [])
        if declared_count not in (None, raw_count):
            lines.append(
                f"- {catalog.market} catalog 声明 `company_count={declared_count}`，"
                f"实际 `companies` 数组为 {raw_count}，本次以数组实际内容为准。"
            )

    if needs_list:
        lines.extend(["", "## 公司清单"])
        index = 1
        for catalog in catalogs:
            lines.append(f"### {MARKET_LABELS[catalog.market]}（{catalog.market}）")
            for company in catalog.companies:
                lines.append(format_catalog_company_line(index, company))
                index += 1

    lines.extend(["", "## 引用来源"])
    for index, catalog in enumerate(catalogs, 1):
        generated_at = catalog.payload.get("generated_at") or catalog.payload.get("last_updated") or "未返回"
        lines.append(
            f"[{index}] source_type=wiki_metadata, market={catalog.market}, file={catalog.catalog_path}, "
            f"count={len(catalog.companies)}, generated_at={generated_at}"
        )
    return "\n".join(lines)


def _company_aliases(company: dict[str, Any], alias_overrides: Sequence[str] = ()) -> list[str]:
    aliases = [
        company.get("company_id"),
        company.get("company_wiki_id"),
        catalog_company_code(company),
        company.get("cik"),
        company.get("company_short_name"),
        company.get("company_name"),
        company.get("company_full_name"),
        *((company.get("aliases") or []) if isinstance(company.get("aliases"), list) else []),
        *alias_overrides,
    ]
    return [str(alias).strip() for alias in aliases if str(alias or "").strip()]


def _alias_match_score(text: str, normalized_text: str, alias: str, normalize_text: Callable[[Any], str]) -> int:
    normalized_alias = normalize_text(alias)
    if not normalized_alias:
        return 0
    alias_text = alias.strip().casefold()
    # Boundary matching is for short ASCII tickers/codes. Python considers
    # CJK names alphanumeric too; treating 英伟达 as a ticker makes
    # "英伟达2026" fail because the following year digit violates the ASCII
    # look-ahead boundary.
    if re.fullmatch(r"[a-z0-9]+", alias_text) and len(alias_text) <= 6:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(alias_text)}(?![a-z0-9])", re.IGNORECASE)
        if not pattern.search(text):
            return 0
        return 1000 + len(normalized_alias)
    if normalized_alias not in normalized_text:
        return _latin_name_prefix_score(text, alias)
    return 100 + len(normalized_alias)


_CORPORATE_SUFFIX_TOKENS = {
    "ag",
    "co",
    "company",
    "corp",
    "corporation",
    "group",
    "holding",
    "holdings",
    "inc",
    "limited",
    "ltd",
    "nv",
    "plc",
    "sa",
    "se",
}


def _latin_name_prefix_score(text: str, alias: str) -> int:
    """Match a meaningful two-token company-name prefix, never arbitrary substrings."""
    alias_tokens = re.findall(r"[a-z0-9]+", alias.casefold())
    significant = [token for token in alias_tokens if token not in _CORPORATE_SUFFIX_TOKENS]
    if len(significant) < 2:
        return 0
    phrase = r"(?<![a-z0-9])" + r"[\s._,&/\-]+".join(map(re.escape, significant[:2])) + r"(?![a-z0-9])"
    return 500 + sum(map(len, significant[:2])) if re.search(phrase, text, re.IGNORECASE) else 0


def catalog_company_dir(
    catalog: MarketCatalog,
    company: dict[str, Any],
    *,
    company_roots: Sequence[Path] = (),
) -> Path | None:
    raw_path = company.get("company_path") or company.get("company_wiki_path") or company.get("path")
    candidates: list[Path] = []
    if raw_path:
        path = Path(str(raw_path)).expanduser()
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(catalog.wiki_root / path)
            candidates.extend(Path(root) / path for root in company_roots)
            parts = path.parts
            if "companies" in parts:
                company_index = parts.index("companies")
                candidates.append(catalog.wiki_root.joinpath(*parts[company_index:]))
                candidates.extend(Path(root).joinpath(*parts[company_index:]) for root in company_roots)
    wiki_id = company.get("company_wiki_id") or company.get("company_id")
    if wiki_id:
        candidates.append(catalog.wiki_root / "companies" / str(wiki_id))
        candidates.extend(Path(root) / "companies" / str(wiki_id) for root in company_roots)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def resolve_catalog_company_dirs(
    text: str,
    *,
    wiki_root: Path | str | None = None,
    normalize_text: Callable[[Any], str],
    alias_overrides: dict[str, Sequence[str]] | None = None,
    market_hint: str | None = None,
    company_id_hint: str | None = None,
    company_roots: Sequence[Path] = (),
    limit: int = 4,
    read_json_file: Callable[[Path], Any | None] = _read_json_file,
) -> list[Path]:
    """Resolve companies across market catalogs with market-aware alias ranking."""
    requested = requested_catalog_markets(text)
    explicit_market = tuple(requested) != MARKET_ORDER
    normalized_hint = _normalized_market(market_hint)
    canonical_company_id = _canonical_company_id(company_id_hint)
    company_id_market = _company_id_market(company_id_hint)
    if canonical_company_id and normalized_hint and company_id_market and company_id_market != normalized_hint:
        return []
    if canonical_company_id and not normalized_hint:
        normalized_hint = company_id_market
    markets = (normalized_hint,) if normalized_hint else requested
    catalogs = load_market_catalogs(
        wiki_root=wiki_root,
        markets=markets,
        include_unclassified=True,
        read_json_file=read_json_file,
    )
    if canonical_company_id:
        exact_matches = [
            (catalog, company)
            for catalog in catalogs
            for company in catalog.companies
            if _canonical_company_id(company.get("company_id")) == canonical_company_id
        ]
        if len(exact_matches) != 1:
            return []
        catalog, company = exact_matches[0]
        company_dir = catalog_company_dir(catalog, company, company_roots=company_roots)
        return [company_dir] if company_dir else []

    normalized_text = normalize_text(text)
    if not normalized_text:
        return []

    matches: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()
    overrides = alias_overrides or {}
    for catalog in catalogs:
        for company in catalog.companies:
            company_id = str(company.get("company_id") or "")
            score = max(
                (
                    _alias_match_score(
                        text.casefold(),
                        normalized_text,
                        alias,
                        normalize_text,
                    )
                    for alias in _company_aliases(company, overrides.get(company_id, ()))
                ),
                default=0,
            )
            if not score:
                continue
            if explicit_market or normalized_hint:
                score += 10_000
            company_dir = catalog_company_dir(catalog, company, company_roots=company_roots)
            if not company_dir or company_dir in seen:
                continue
            seen.add(company_dir)
            matches.append((score, company_id, company_dir))
    matches.sort(key=lambda item: (-item[0], item[1], str(item[2])))
    return [company_dir for _score, _company_id, company_dir in matches[:limit]]
