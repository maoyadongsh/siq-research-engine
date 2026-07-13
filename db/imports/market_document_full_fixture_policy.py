"""Fail-closed identity policy for committed document_full evaluation fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

IDENTITY_SCOPE = "synthetic_fixture"
FIXTURE_PATH_MARKER = "eval_datasets/market_document_full_postgres/examples/"
EVAL_FIXTURE_MARKETS = frozenset({"CN", "HK", "JP", "KR", "EU", "US"})


class FixtureIdentityError(ValueError):
    """Raised before an eval fixture can use a production company namespace."""


class FixtureDatabaseWriteProhibitedError(RuntimeError):
    """Raised before a committed eval fixture can reach a fixed market database."""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _identity(document_full: dict[str, Any]) -> dict[str, str]:
    filing = _mapping(document_full.get("filing"))
    financial = _mapping(document_full.get("financial_data"))
    source = filing or financial
    return {
        "market": str(source.get("market") or financial.get("market") or "").strip().upper(),
        "company_id": str(source.get("company_id") or financial.get("company_id") or "").strip(),
        "filing_id": str(
            source.get("filing_id")
            or source.get("report_id")
            or financial.get("filing_id")
            or financial.get("report_id")
            or ""
        ).strip(),
        "ticker": str(source.get("ticker") or financial.get("ticker") or "").strip().upper(),
    }


def is_eval_fixture(document_full: dict[str, Any], *, document_path: Path | None = None) -> bool:
    task_id = str(_mapping(document_full.get("task")).get("task_id") or "").strip().lower()
    path_text = str(document_path or "").replace("\\", "/")
    identity = _identity(document_full)
    synthetic_namespace = any(
        ":FIXTURE:" in value.upper()
        for value in (identity["company_id"], identity["filing_id"])
    )
    return (
        document_full.get("identity_scope") == IDENTITY_SCOPE
        or task_id.startswith("fixture-")
        or FIXTURE_PATH_MARKER in path_text
        or synthetic_namespace
    )


def fixture_identity_errors(
    document_full: dict[str, Any],
    *,
    case: dict[str, Any] | None = None,
    document_path: Path | None = None,
    require_fixture: bool = False,
) -> list[str]:
    fixture = is_eval_fixture(document_full, document_path=document_path)
    if not fixture and not require_fixture:
        return []

    errors: list[str] = []
    identity = _identity(document_full)
    case_data = case or {}
    market = str(case_data.get("market") or identity["market"]).strip().upper()
    expected_company_prefix = f"{market}:FIXTURE:" if market else ""

    if document_full.get("identity_scope") != IDENTITY_SCOPE:
        errors.append(f"identity_scope must be {IDENTITY_SCOPE!r}")
    task_id = str(_mapping(document_full.get("task")).get("task_id") or "").strip()
    if not task_id.startswith("fixture-"):
        errors.append("task.task_id must start with 'fixture-'")
    if market not in EVAL_FIXTURE_MARKETS:
        errors.append(f"unsupported eval fixture market: {market or '<missing>'}")
    if identity["market"] != market:
        errors.append(
            f"document market must equal case market: expected {market!r}, got {identity['market']!r}"
        )
    if not expected_company_prefix or not identity["company_id"].startswith(expected_company_prefix):
        errors.append(
            "document company_id must use the synthetic fixture namespace "
            f"{expected_company_prefix or '<market>:FIXTURE:'!r}, got {identity['company_id']!r}"
        )
    if not identity["filing_id"].startswith(f"{identity['company_id']}:"):
        errors.append(
            "document filing_id must be nested below the synthetic company_id, "
            f"got {identity['filing_id']!r}"
        )
    if not identity["ticker"].startswith("FIXTURE_"):
        errors.append(
            f"document ticker must start with 'FIXTURE_', got {identity['ticker']!r}"
        )

    if case is not None:
        if case_data.get("identity_scope") != IDENTITY_SCOPE:
            errors.append(f"case identity_scope must be {IDENTITY_SCOPE!r}")
        case_company_id = str(case_data.get("company_id") or "").strip()
        if case_company_id != identity["company_id"]:
            errors.append(
                f"case company_id must equal document company_id: {case_company_id!r} != {identity['company_id']!r}"
            )
        expected_filing_id = str(
            _mapping(case_data.get("expected_identity")).get("filing_id") or ""
        ).strip()
        if expected_filing_id != identity["filing_id"]:
            errors.append(
                "case expected_identity.filing_id must equal document filing_id: "
                f"{expected_filing_id!r} != {identity['filing_id']!r}"
            )
    return errors


def assert_safe_fixture_identity(
    document_full: dict[str, Any],
    *,
    case: dict[str, Any] | None = None,
    document_path: Path | None = None,
    require_fixture: bool = False,
) -> bool:
    fixture = is_eval_fixture(document_full, document_path=document_path)
    errors = fixture_identity_errors(
        document_full,
        case=case,
        document_path=document_path,
        require_fixture=require_fixture,
    )
    if errors:
        raise FixtureIdentityError("unsafe eval fixture identity: " + "; ".join(errors))
    return fixture or require_fixture


def assert_safe_fixture_rows(
    document_full: dict[str, Any],
    *,
    company: dict[str, Any],
    filing: dict[str, Any],
    document_path: Path | None = None,
) -> bool:
    if not is_eval_fixture(document_full, document_path=document_path):
        return False
    expected = _identity(document_full)
    observed_company_id = str(company.get("company_id") or "").strip()
    observed_filing_company_id = str(filing.get("company_id") or "").strip()
    observed_filing_id = str(filing.get("filing_id") or "").strip()
    errors = []
    if observed_company_id != expected["company_id"]:
        errors.append(
            "import rows company_id drifted from the fixture contract: "
            f"{expected['company_id']!r} -> {observed_company_id!r}"
        )
    if observed_filing_company_id != expected["company_id"]:
        errors.append(
            "import rows filing.company_id drifted from the fixture contract: "
            f"expected {expected['company_id']!r}, got {observed_filing_company_id!r}"
        )
    if observed_filing_id != expected["filing_id"]:
        errors.append(
            "import rows filing_id drifted from the fixture contract: "
            f"expected {expected['filing_id']!r}, got {observed_filing_id!r}"
        )
    if errors:
        raise FixtureIdentityError("unsafe eval fixture import rows: " + "; ".join(errors))
    return True


def prohibit_fixture_database_write(
    document_full: dict[str, Any], *, document_path: Path | None = None
) -> bool:
    if not is_eval_fixture(document_full, document_path=document_path):
        return False
    raise FixtureDatabaseWriteProhibitedError(
        "committed document_full eval fixtures are contract-only and must not be imported "
        "into fixed market databases; use real production samples for PostgreSQL roundtrip gates"
    )


__all__ = [
    "EVAL_FIXTURE_MARKETS",
    "FIXTURE_PATH_MARKER",
    "IDENTITY_SCOPE",
    "FixtureDatabaseWriteProhibitedError",
    "FixtureIdentityError",
    "assert_safe_fixture_identity",
    "assert_safe_fixture_rows",
    "fixture_identity_errors",
    "is_eval_fixture",
    "prohibit_fixture_database_write",
]
