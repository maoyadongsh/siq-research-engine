from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.openshell import test_siq_analysis_wide_pilot_contract as pilot  # noqa: E402


def _project(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "project"
    company = "600104-test"
    company_root = root / "data/wiki/companies" / company
    (company_root / "analysis/.work").mkdir(parents=True)
    (company_root / "company.json").write_text(
        json.dumps({"stock_code": "600104"}) + "\n",
        encoding="utf-8",
    )
    return root, company


def _pilot_paths(root: Path, company: str, pilot_id: str = "pilot-0123456789ab") -> pilot.PilotPaths:
    output_root = root / "data/wiki/companies" / company / "analysis/.work" / pilot_id
    output_root.mkdir(mode=0o700)
    return pilot.resolve_pilot_paths(root, market="cn", company=company, pilot_id=pilot_id)


def test_paths_are_fixed_to_one_company_analysis_work_root(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    paths = _pilot_paths(root, company)

    assert paths.source == root / "data/wiki/companies" / company / "company.json"
    assert paths.output == root / "data/wiki/companies" / company / "analysis/.work/pilot-0123456789ab/result.json"


@pytest.mark.parametrize(
    ("company", "pilot_id"),
    [
        ("../escape", "pilot-0123456789ab"),
        ("600104-test", "pilot-not-hex"),
        ("600104/test", "pilot-0123456789ab"),
    ],
)
def test_paths_reject_untrusted_identity(tmp_path: Path, company: str, pilot_id: str) -> None:
    root, _ = _project(tmp_path)
    if company == "600104-test" and pilot.PILOT_ID_RE.fullmatch(pilot_id):
        (root / "data/wiki/companies" / company / "analysis/.work" / pilot_id).mkdir(mode=0o700)
    with pytest.raises(pilot.PilotContractError, match="pilot_identity_invalid"):
        pilot.resolve_pilot_paths(root, market="cn", company=company, pilot_id=pilot_id)


def test_source_contract_and_output_validation_are_exact(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    paths = _pilot_paths(root, company)
    _content, digest, stock_code = pilot.source_contract(paths.source)
    paths.output.write_text(
        json.dumps(
            {
                "pilot_id": "pilot-0123456789ab",
                "schema_version": pilot.PILOT_SCHEMA,
                "source_sha256": digest,
                "stock_code": stock_code,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="ascii",
    )

    pilot.validate_output(
        paths.output,
        pilot_id="pilot-0123456789ab",
        stock_code="600104",
        source_sha256=digest,
    )
    pilot.remove_exact_output(
        paths,
        pilot_id="pilot-0123456789ab",
        stock_code="600104",
        source_sha256=digest,
    )

    assert not paths.output_root.exists()


def test_cleanup_refuses_unexpected_files(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    paths = _pilot_paths(root, company)
    paths.output.write_text("{}\n", encoding="ascii")
    (paths.output_root / "unexpected.txt").write_text("unexpected\n", encoding="ascii")

    with pytest.raises(pilot.PilotContractError, match="pilot_output_cleanup_unsafe"):
        pilot.remove_exact_output(
            paths,
            pilot_id="pilot-0123456789ab",
            stock_code="600104",
            source_sha256="0" * 64,
        )


def test_prompt_is_one_deterministic_terminal_write(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    paths = _pilot_paths(root, company)
    prompt = pilot.build_prompt(paths, pilot_id="pilot-0123456789ab")

    assert "必须且只需调用一次 terminal" in prompt
    assert pilot.TOOL_MARKER in prompt
    assert pilot.FINAL_MARKER in prompt
    assert paths.source.as_posix() in prompt
    assert paths.output.as_posix() in prompt
    assert "不要访问网络" in prompt


def test_paths_require_one_precreated_private_empty_output_root(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    with pytest.raises(pilot.PilotContractError, match="pilot_output_root_missing"):
        pilot.resolve_pilot_paths(root, market="cn", company=company, pilot_id="pilot-0123456789ab")

    output_root = root / "data/wiki/companies" / company / "analysis/.work/pilot-0123456789ab"
    output_root.mkdir(mode=0o755)
    with pytest.raises(pilot.PilotContractError, match="pilot_output_conflict"):
        pilot.resolve_pilot_paths(root, market="cn", company=company, pilot_id="pilot-0123456789ab")


def test_cleanup_removes_an_empty_precreated_output_root(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    paths = _pilot_paths(root, company)

    pilot.remove_exact_output(
        paths,
        pilot_id="pilot-0123456789ab",
        stock_code="600104",
        source_sha256="0" * 64,
    )

    assert not paths.output_root.exists()


def test_contract_receipt_is_private_exact_and_single_write(tmp_path: Path) -> None:
    root, _company = _project(tmp_path)
    pilot_id = "pilot-0123456789ab"
    run_dir = root / pilot.WIDE_PILOT_RUNS_RELATIVE / pilot_id
    run_dir.mkdir(parents=True, mode=0o700)
    result = {
        "schema_version": pilot.RESULT_SCHEMA,
        "mode": "NOT_PRODUCTION_WIDE_PILOT",
        "readiness_effect": "none",
    }

    receipt = pilot.write_contract_receipt(root, pilot_id=pilot_id, result=result)

    assert receipt == run_dir / pilot.CONTRACT_RECEIPT_NAME
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert json.loads(receipt.read_text(encoding="ascii")) == result
    with pytest.raises(pilot.PilotContractError, match="pilot_contract_receipt_exists"):
        pilot.write_contract_receipt(root, pilot_id=pilot_id, result=result)
