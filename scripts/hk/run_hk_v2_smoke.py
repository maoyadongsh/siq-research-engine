#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SRC = REPO_ROOT / "packages" / "market-contracts" / "src"
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
for candidate in (CONTRACT_SRC, RULES_SRC):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


SAMPLE_PACKAGES = (
    Path("00700/2025/annual_12100024"),
    Path("01299/2025/annual_12106543"),
    Path("00981/2025/annual_12097338"),
    Path("03988/2025/annual_12132549"),
    Path("09988/2025/annual_11727038"),
)

REQUIRED_BASE_FILES = {
    "manifest": "manifest.json",
    "quality_report": "qa/quality_report.json",
    "table_index": "tables/table_index.json",
    "normalized_metrics": "metrics/normalized_metrics.json",
    "source_map": "qa/source_map.json",
}

REQUIRED_V2_FILES = {
    "report_complete": "sections/report_complete.md",
    "document_full": "parser/document_full.json",
    "content_list_enhanced": "parser/content_list_enhanced.json",
    "table_relations": "parser/table_relations.json",
    "footnotes": "qa/footnotes.json",
    "toc": "qa/toc.json",
    "financial_note_links": "qa/financial_note_links.json",
    "table_quality_signals": "qa/table_quality_signals.json",
}

DETAIL_REQUIRED_V2_KEYS = tuple(REQUIRED_V2_FILES)
FAIL_QUALITY_STATUSES = {"fail", "failed", "error", "critical"}
STATUS_LABELS = {"pass": "通过", "warning": "警告", "fail": "失败"}
DetailReader = Callable[..., dict[str, Any]]


@dataclass
class JsonReadResult:
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ValidatorResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unavailable: str | None = None


@dataclass
class DetailPathResult:
    missing_paths: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SampleResult:
    sample: str
    package_path: str
    company: str = ""
    ticker: str = ""
    filing_id: str = ""
    quality: str = "unknown"
    counts: dict[str, int] = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)
    missing_v2_files: list[str] = field(default_factory=list)
    missing_detail_paths: list[str] = field(default_factory=list)
    detail_error: str | None = None
    validator: ValidatorResult = field(default_factory=lambda: ValidatorResult(ok=False))
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failures:
            return "fail"
        if self.warnings or self.validator.warnings:
            return "warning"
        return "pass"


def _load_validator() -> Callable[[Path], ValidatorResult]:
    try:
        from siq_market_contracts.evidence_package import validate_evidence_package
    except ModuleNotFoundError:
        try:
            from market_report_rules_service.evidence_package import validate_evidence_package
        except ModuleNotFoundError as exc:
            message = f"无法导入 evidence package validator: {exc}"

            def unavailable(_: Path) -> ValidatorResult:
                return ValidatorResult(ok=False, unavailable=message, errors=[message])

            return unavailable

    def run(package_dir: Path) -> ValidatorResult:
        try:
            validation = validate_evidence_package(package_dir)
        except Exception as exc:  # noqa: BLE001 - smoke report should capture the exact validator failure.
            return ValidatorResult(ok=False, errors=[f"validator 执行异常: {exc}"])
        return ValidatorResult(
            ok=bool(getattr(validation, "ok", False)),
            errors=list(getattr(validation, "errors", []) or []),
            warnings=list(getattr(validation, "warnings", []) or []),
        )

    return run


def _load_package_detail_reader() -> DetailReader:
    try:
        from siq_market_contracts.evidence_package import read_market_package_detail
    except ModuleNotFoundError:
        try:
            from market_report_rules_service.evidence_package import read_market_package_detail
        except ModuleNotFoundError as exc:
            message = f"无法导入 package detail reader: {exc}"

            def unavailable(_: Path, *, display_path: str | None = None) -> dict[str, Any]:
                raise RuntimeError(message)

            return unavailable

    return read_market_package_detail


def _resolve_path(value: Path) -> Path:
    return value if value.is_absolute() else (Path.cwd() / value).resolve()


def _read_json(path: Path) -> JsonReadResult:
    if not path.is_file():
        return JsonReadResult(error=f"文件不存在: {_display(path)}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return JsonReadResult(error=f"JSON 解析失败: {_display(path)} ({exc})")
    if not isinstance(payload, dict):
        return JsonReadResult(error=f"JSON 顶层不是对象: {_display(path)}")
    return JsonReadResult(payload=payload)


def _list_count(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else 0


def _quality_warnings(quality: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for key in ("critical_warnings", "parser_warnings", "rule_warnings", "warnings"):
        value = quality.get(key)
        if isinstance(value, list):
            warnings.extend(str(item) for item in value[:10] if item)
    return warnings


def _read_detail_v2_paths(package_dir: Path, display_path: str, detail_reader: DetailReader) -> DetailPathResult:
    try:
        detail = detail_reader(package_dir, display_path=display_path)
    except Exception as exc:  # noqa: BLE001 - smoke report should explain package-detail read failures.
        return DetailPathResult(
            missing_paths=list(DETAIL_REQUIRED_V2_KEYS),
            error=f"无法读取 package detail: {exc}",
        )

    if not isinstance(detail, dict):
        return DetailPathResult(
            missing_paths=list(DETAIL_REQUIRED_V2_KEYS),
            error="package detail 顶层不是对象",
        )
    paths = detail.get("paths")
    if not isinstance(paths, dict):
        return DetailPathResult(
            missing_paths=list(DETAIL_REQUIRED_V2_KEYS),
            error="package detail 缺少 paths 对象",
        )
    return DetailPathResult(missing_paths=[key for key in DETAIL_REQUIRED_V2_KEYS if key not in paths])


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _sample_result(
    root: Path,
    sample: Path,
    validator: Callable[[Path], ValidatorResult],
    detail_reader: DetailReader | None = None,
) -> SampleResult:
    package_dir = root / sample
    result = SampleResult(sample=str(sample), package_path=_display(package_dir))
    if not package_dir.is_dir():
        result.failures.append("样本 package 目录不存在")
        return result

    result.missing_files = [rel for rel in REQUIRED_BASE_FILES.values() if not (package_dir / rel).is_file()]
    result.missing_v2_files = [rel for rel in REQUIRED_V2_FILES.values() if not (package_dir / rel).is_file()]
    detail_reader = detail_reader or _load_package_detail_reader()
    detail_paths = _read_detail_v2_paths(package_dir, result.package_path, detail_reader)
    result.missing_detail_paths = detail_paths.missing_paths
    result.detail_error = detail_paths.error

    manifest_read = _read_json(package_dir / "manifest.json")
    quality_read = _read_json(package_dir / "qa" / "quality_report.json")
    table_read = _read_json(package_dir / "tables" / "table_index.json")
    metrics_read = _read_json(package_dir / "metrics" / "normalized_metrics.json")
    source_map_read = _read_json(package_dir / "qa" / "source_map.json")
    json_errors = [
        item.error
        for item in (manifest_read, quality_read, table_read, metrics_read, source_map_read)
        if item.error
    ]
    result.failures.extend(json_errors)

    manifest = manifest_read.payload
    quality = quality_read.payload
    result.company = str(manifest.get("company_name") or "")
    result.ticker = str(manifest.get("ticker") or sample.parts[0])
    result.filing_id = str(manifest.get("filing_id") or "")
    result.quality = str(quality.get("overall_status") or manifest.get("quality_status") or "unknown").lower()
    result.counts = {
        "sections": int(quality.get("section_count") or 0) if isinstance(quality.get("section_count"), int) else 0,
        "tables": _list_count(table_read.payload, "tables"),
        "metrics": _list_count(metrics_read.payload, "metrics"),
        "evidence": _list_count(source_map_read.payload, "entries"),
    }
    result.warnings.extend(_quality_warnings(quality))

    validation = validator(package_dir)
    result.validator = validation

    if result.missing_files:
        result.failures.append("缺失必需基础文件: " + ", ".join(result.missing_files))
    if result.missing_v2_files:
        result.failures.append("缺失必需 V2 文件: " + ", ".join(result.missing_v2_files))
    if validation.unavailable:
        result.failures.append(validation.unavailable)
    elif not validation.ok:
        result.failures.append("validator 失败: " + "; ".join(validation.errors[:8]))
    if result.detail_error:
        result.failures.append(result.detail_error)
    if result.counts["metrics"] == 0:
        result.failures.append("metrics/normalized_metrics.json 中 metrics 为空")
    if result.counts["evidence"] == 0:
        result.failures.append("qa/source_map.json 中 entries 为空")
    if result.missing_detail_paths:
        result.failures.append("package detail 缺少 V2 paths: " + ", ".join(result.missing_detail_paths))
    if result.quality in FAIL_QUALITY_STATUSES:
        result.failures.append(f"quality_report overall_status 为 {result.quality}")
    return result


def _overall_status(samples: list[SampleResult]) -> str:
    if any(sample.status == "fail" for sample in samples):
        return "fail"
    if any(sample.status == "warning" for sample in samples):
        return "warning"
    return "pass"


def _next_steps(samples: list[SampleResult]) -> list[str]:
    steps: list[str] = []
    if any(sample.missing_v2_files or sample.missing_detail_paths for sample in samples):
        steps.append("重建 5 个 HK 样本为 V2 package，补齐 parser、report_complete 与 V2 QA artifacts。")
    if any(sample.counts.get("metrics", 0) == 0 for sample in samples):
        steps.append("补充 HK 指标 alias 和财务表格规则，确保 normalized_metrics 至少产出一条指标。")
    if any(sample.counts.get("evidence", 0) == 0 for sample in samples):
        steps.append("补齐 source_map 生成逻辑，确保每个指标有可追溯 evidence。")
    if any(sample.quality in FAIL_QUALITY_STATUSES for sample in samples):
        steps.append("复查 quality_report 为 fail 的样本，优先补银行/保险等行业表格规则。")
    if any(sample.validator.errors for sample in samples):
        steps.append("修正 validator 报错后再执行导入；本 smoke 未连接数据库，仅覆盖 importer 前置契约。")
    if not steps:
        steps.append("当前 5 个样本满足 V2 smoke；可继续执行真实导入或扩大 HK 样本集。")
    return steps


def _report_payload(root: Path, samples: list[SampleResult]) -> dict[str, Any]:
    status = _overall_status(samples)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": _display(root),
        "status": status,
        "summary": {
            "sample_count": len(samples),
            "pass_count": sum(1 for sample in samples if sample.status == "pass"),
            "warning_count": sum(1 for sample in samples if sample.status == "warning"),
            "fail_count": sum(1 for sample in samples if sample.status == "fail"),
            "required_base_files": REQUIRED_BASE_FILES,
            "required_v2_files": REQUIRED_V2_FILES,
            "next_steps": _next_steps(samples),
        },
        "samples": [
            {
                "sample": sample.sample,
                "package_path": sample.package_path,
                "status": sample.status,
                "company": sample.company,
                "ticker": sample.ticker,
                "filing_id": sample.filing_id,
                "quality": sample.quality,
                "counts": sample.counts,
                "missing_files": sample.missing_files,
                "missing_v2_files": sample.missing_v2_files,
                "missing_detail_paths": sample.missing_detail_paths,
                "detail_error": sample.detail_error,
                "import_dry_run": {
                    "status": "pass" if sample.validator.ok else "fail",
                    "scope": "validator 前置契约检查；未连接数据库，未写入数据。",
                },
                "validator": {
                    "ok": sample.validator.ok,
                    "errors": sample.validator.errors,
                    "warnings": sample.validator.warnings,
                    "unavailable": sample.validator.unavailable,
                },
                "warnings": sample.warnings,
                "failures": sample.failures,
            }
            for sample in samples
        ],
    }


def _md_cell(value: Any) -> str:
    text = str(value if value not in (None, "") else "-")
    return text.replace("|", "\\|").replace("\n", "<br>")


def _status_label(status: Any) -> str:
    return STATUS_LABELS.get(str(status), str(status))


def _render_markdown(payload: dict[str, Any]) -> str:
    status_label = _status_label(payload["status"])
    lines = [
        "# HK V2 5 样本 Smoke 报告",
        "",
        f"- 生成时间: `{payload['generated_at']}`",
        f"- 样本根目录: `{payload['root']}`",
        f"- 聚合结论: **{status_label}**",
        f"- 样本数: {payload['summary']['sample_count']}；通过: {payload['summary']['pass_count']}；警告: {payload['summary']['warning_count']}；失败: {payload['summary']['fail_count']}",
        "",
        "## 样本摘要",
        "",
        "| 样本 | 公司 | ticker | filing_id | quality | sections | tables | metrics | evidence | 状态 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for sample in payload["samples"]:
        counts = sample["counts"]
        lines.append(
            "| "
            + " | ".join(
                _md_cell(item)
                for item in (
                    sample["sample"],
                    sample["company"],
                    sample["ticker"],
                    sample["filing_id"],
                    sample["quality"],
                    counts.get("sections", 0),
                    counts.get("tables", 0),
                    counts.get("metrics", 0),
                    counts.get("evidence", 0),
                    _status_label(sample["status"]),
                )
            )
            + " |"
        )

    lines.extend(["", "## 失败与缺口", ""])
    for sample in payload["samples"]:
        lines.append(f"### {sample['sample']}")
        lines.append(f"- 状态: {_status_label(sample['status'])}")
        lines.append(
            "- 导入 dry run: "
            + ("validator 通过（未连接数据库，未写入数据）" if sample["import_dry_run"]["status"] == "pass" else "validator 失败（未连接数据库，未写入数据）")
        )
        if sample["missing_files"]:
            lines.append("- 缺失基础文件: " + ", ".join(f"`{item}`" for item in sample["missing_files"]))
        if sample["missing_v2_files"]:
            lines.append("- 缺失 V2 文件: " + ", ".join(f"`{item}`" for item in sample["missing_v2_files"]))
        if sample["missing_detail_paths"]:
            lines.append("- package detail 缺少 V2 paths: " + ", ".join(f"`{item}`" for item in sample["missing_detail_paths"]))
        if sample["detail_error"]:
            lines.append(f"- package detail 读取错误: {sample['detail_error']}")
        if sample["validator"]["errors"]:
            lines.append("- validator 错误: " + "; ".join(sample["validator"]["errors"][:8]))
        if sample["warnings"] or sample["validator"]["warnings"]:
            warnings = list(sample["warnings"][:8]) + list(sample["validator"]["warnings"][:8])
            lines.append("- 主要 warnings: " + "; ".join(warnings[:10]))
        if sample["failures"]:
            lines.append("- 硬失败原因: " + "; ".join(sample["failures"][:10]))
        lines.append("")

    lines.extend(["## 下一步", ""])
    for step in payload["summary"]["next_steps"]:
        lines.append(f"- {step}")
    lines.append("")
    return "\n".join(lines)


def _write_outputs(payload: dict[str, Any], output: Path, json_output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_markdown(payload), encoding="utf-8")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 HK V2 5 样本 smoke 检查并输出中文报告。")
    parser.add_argument("--root", type=Path, default=Path("data/wiki/hk_reports"))
    parser.add_argument("--output", type=Path, default=Path("docs/superpowers/reports/hk_v2_smoke_report.md"))
    parser.add_argument("--json-output", type=Path, default=Path("docs/superpowers/reports/hk_v2_smoke_report.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = _resolve_path(args.root)
    output = _resolve_path(args.output)
    json_output = _resolve_path(args.json_output)
    validator = _load_validator()
    detail_reader = _load_package_detail_reader()
    samples = [_sample_result(root, sample, validator, detail_reader) for sample in SAMPLE_PACKAGES]
    payload = _report_payload(root, samples)
    _write_outputs(payload, output, json_output)
    print(f"HK V2 smoke {payload['status']}: {output}")
    print(f"JSON: {json_output}")
    return 0 if payload["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
