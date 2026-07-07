import importlib.util
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "observe_large_files.py"
    spec = importlib.util.spec_from_file_location("observe_large_files_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_lines(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {index}" for index in range(count)) + "\n", encoding="utf-8")


def test_observe_large_files_prunes_runtime_data_and_artifact_dirs_by_default(tmp_path):
    module = _load_module()
    _write_lines(tmp_path / "apps" / "api" / "large.py", 12)
    _write_lines(tmp_path / "artifacts" / "generated.py", 999)
    _write_lines(tmp_path / "data" / "generated.py", 999)
    _write_lines(tmp_path / "runtimes" / "tool.py", 999)
    _write_lines(tmp_path / "var" / "runtime.py", 999)
    _write_lines(tmp_path / "node_modules" / "pkg" / "index.js", 999)

    records = module.observe_large_files(tmp_path, limit=10, warning_lines=10, report_lines=20)

    assert [record.path for record in records] == ["apps/api/large.py"]
    assert records[0].level == "warning"


def test_observe_large_files_skips_lockfiles_and_architecture_docs(tmp_path):
    module = _load_module()
    _write_lines(tmp_path / "apps" / "web" / "package-lock.json", 999)
    _write_lines(tmp_path / "docs" / "architecture" / "long-plan.md", 999)
    _write_lines(tmp_path / "db" / "imports" / "reference-data.json", 999)
    _write_lines(tmp_path / "agents" / "hermes" / "profiles" / "siq_analysis" / "rules" / "long-rule.md", 20)

    records = module.observe_large_files(tmp_path, limit=10, warning_lines=10, report_lines=20)

    assert [record.path for record in records] == ["agents/hermes/profiles/siq_analysis/rules/long-rule.md"]
    assert records[0].level == "report"


def test_observe_large_files_sorts_and_marks_report_level(tmp_path):
    module = _load_module()
    _write_lines(tmp_path / "small.py", 2)
    _write_lines(tmp_path / "warning.ts", 5)
    _write_lines(tmp_path / "report.tsx", 8)

    records = module.observe_large_files(tmp_path, limit=2, warning_lines=5, report_lines=8)

    assert [(record.path, record.line_count, record.level) for record in records] == [
        ("report.tsx", 8, "report"),
        ("warning.ts", 5, "warning"),
    ]


def test_level_for_keeps_thresholds_observe_only():
    module = _load_module()

    assert module.level_for(2499, warning_lines=2500, report_lines=4000) == "ok"
    assert module.level_for(2500, warning_lines=2500, report_lines=4000) == "warning"
    assert module.level_for(4000, warning_lines=2500, report_lines=4000) == "report"


def test_main_returns_zero_for_report_level_files(tmp_path, monkeypatch, capsys):
    module = _load_module()
    _write_lines(tmp_path / "huge.py", 5)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observe_large_files.py",
            "--root",
            str(tmp_path),
            "--warning-lines",
            "3",
            "--report-lines",
            "5",
        ],
    )

    assert module.main() == 0
    assert "report" in capsys.readouterr().out
