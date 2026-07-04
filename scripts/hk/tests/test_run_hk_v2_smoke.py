from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "run_hk_v2_smoke.py"
SPEC = importlib.util.spec_from_file_location("run_hk_v2_smoke", SCRIPT_PATH)
assert SPEC is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
assert SPEC.loader is not None
SPEC.loader.exec_module(smoke)


def _write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _write_complete_v2_package(root: Path, sample: Path) -> Path:
    package_dir = root / sample
    _write_json(
        package_dir / "manifest.json",
        '{"company_name":"Sample Co","ticker":"00700","filing_id":"HK:00700:12100024","quality_status":"pass"}',
    )
    _write_json(package_dir / "qa" / "quality_report.json", '{"overall_status":"pass","section_count":1}')
    _write_json(package_dir / "tables" / "table_index.json", '{"tables":[{"table_index":1}]}')
    _write_json(package_dir / "metrics" / "normalized_metrics.json", '{"metrics":[{"metric_id":"m1"}]}')
    _write_json(package_dir / "qa" / "source_map.json", '{"entries":[{"metric_id":"m1"}]}')
    for rel in smoke.REQUIRED_V2_FILES.values():
        path = package_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text("{}\n", encoding="utf-8")
        else:
            path.write_text("# complete report\n", encoding="utf-8")
    return package_dir


def _passing_validator(_: Path) -> smoke.ValidatorResult:
    return smoke.ValidatorResult(ok=True)


def _detail_with_all_paths(_: Path, *, display_path: str | None = None) -> dict:
    return {
        "package_path": display_path,
        "paths": {**smoke.REQUIRED_BASE_FILES, **smoke.REQUIRED_V2_FILES},
    }


def test_sample_fails_when_detail_lacks_v2_paths_even_if_files_exist(tmp_path: Path) -> None:
    sample = smoke.SAMPLE_PACKAGES[0]
    _write_complete_v2_package(tmp_path, sample)

    def detail_without_v2_paths(_: Path, *, display_path: str | None = None) -> dict:
        return {
            "package_path": display_path,
            "paths": dict(smoke.REQUIRED_BASE_FILES),
        }

    result = smoke._sample_result(tmp_path, sample, _passing_validator, detail_reader=detail_without_v2_paths)

    assert result.missing_v2_files == []
    assert result.status == "fail"
    assert result.missing_detail_paths == list(smoke.DETAIL_REQUIRED_V2_KEYS)
    assert any("package detail 缺少 V2 paths" in failure for failure in result.failures)


def test_sample_fails_clearly_when_detail_cannot_be_read(tmp_path: Path) -> None:
    sample = smoke.SAMPLE_PACKAGES[0]
    _write_complete_v2_package(tmp_path, sample)

    def broken_detail_reader(_: Path, *, display_path: str | None = None) -> dict:
        raise RuntimeError("boom")

    result = smoke._sample_result(tmp_path, sample, _passing_validator, detail_reader=broken_detail_reader)

    assert result.status == "fail"
    assert result.missing_detail_paths == list(smoke.DETAIL_REQUIRED_V2_KEYS)
    assert any("无法读取 package detail: boom" in failure for failure in result.failures)


def test_sample_fails_when_required_v2_file_is_missing(tmp_path: Path) -> None:
    sample = smoke.SAMPLE_PACKAGES[0]
    package_dir = _write_complete_v2_package(tmp_path, sample)
    (package_dir / smoke.REQUIRED_V2_FILES["toc"]).unlink()

    result = smoke._sample_result(tmp_path, sample, _passing_validator, detail_reader=_detail_with_all_paths)

    assert result.status == "fail"
    assert result.missing_v2_files == [smoke.REQUIRED_V2_FILES["toc"]]
    assert any("缺失必需 V2 文件" in failure for failure in result.failures)


def test_sample_fails_when_validator_fails(tmp_path: Path) -> None:
    sample = smoke.SAMPLE_PACKAGES[0]
    _write_complete_v2_package(tmp_path, sample)

    def failing_validator(_: Path) -> smoke.ValidatorResult:
        return smoke.ValidatorResult(ok=False, errors=["bad package"])

    result = smoke._sample_result(tmp_path, sample, failing_validator, detail_reader=_detail_with_all_paths)

    assert result.status == "fail"
    assert any("validator 失败: bad package" in failure for failure in result.failures)


def test_sample_fails_when_metrics_and_evidence_are_empty(tmp_path: Path) -> None:
    sample = smoke.SAMPLE_PACKAGES[0]
    package_dir = _write_complete_v2_package(tmp_path, sample)
    _write_json(package_dir / "metrics" / "normalized_metrics.json", '{"metrics":[]}')
    _write_json(package_dir / "qa" / "source_map.json", '{"entries":[]}')

    result = smoke._sample_result(tmp_path, sample, _passing_validator, detail_reader=_detail_with_all_paths)

    assert result.status == "fail"
    assert any("metrics/normalized_metrics.json 中 metrics 为空" in failure for failure in result.failures)
    assert any("qa/source_map.json 中 entries 为空" in failure for failure in result.failures)
