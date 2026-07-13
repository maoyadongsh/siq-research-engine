#!/usr/bin/env python3
"""Atomically import a complete HK Wiki staging root into PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = REPO_ROOT / "db" / "imports"
if str(IMPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(IMPORTS_DIR))

import import_hk_evidence_package_to_postgres as hk_importer  # noqa: E402
from market_report_rules_service.evidence_package import build_quality_gates  # noqa: E402

SCHEMA = "pdf2md_hk"
SCHEMA_VERSION = "hk_staging_batch_import_v1"
PRODUCTION_WIKI_ROOT = (REPO_ROOT / "data" / "wiki" / "hk").resolve()
PRODUCTION_DATABASES = {"siq_hk"}
OVERRIDE_FIELDS = (
    "force_requested_by",
    "force_approved_by",
    "force_reason",
    "force_expires_at",
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return "<external>"


def discover_package_dirs(staging_root: Path) -> list[Path]:
    return sorted(path.parent for path in staging_root.resolve().rglob("manifest.json"))


def _canonical_decision(gates: dict[str, Any]) -> str:
    direct = gates.get("canonical_decision") if isinstance(gates, dict) else None
    if direct not in (None, ""):
        return str(direct).strip().lower()
    targets = gates.get("decisions_by_target") if isinstance(gates, dict) else {}
    canonical = targets.get("canonical") if isinstance(targets, dict) else {}
    target_decision = canonical.get("decision") if isinstance(canonical, dict) else None
    if target_decision not in (None, ""):
        return str(target_decision).strip().lower()
    return str(gates.get("decision") or "allow").strip().lower()


def _validate_expiry(value: str) -> None:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit("--force-expires-at must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise SystemExit("--force-expires-at must include a timezone")
    if parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise SystemExit("--force-expires-at must be in the future")


def validate_staging_target(
    staging_root: Path,
    *,
    staging_only: bool,
    expected_database: str | None,
) -> tuple[Path, str]:
    if not staging_only:
        raise SystemExit("--staging-only is required")
    if not str(expected_database or "").strip():
        raise SystemExit("--expected-database is required, including for --dry-run")
    database = str(expected_database).strip()
    if database.casefold() in PRODUCTION_DATABASES:
        raise SystemExit(f"Refusing production database {database!r}; use an isolated staging database")

    root = staging_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"HK staging root does not exist or is not a directory: {root}")
    if (
        root == PRODUCTION_WIKI_ROOT
        or root.is_relative_to(PRODUCTION_WIKI_ROOT)
        or PRODUCTION_WIKI_ROOT.is_relative_to(root)
    ):
        raise SystemExit("Refusing a staging root that overlaps the production HK Wiki root")
    return root, database


def _override_values(args: argparse.Namespace) -> dict[str, str | None]:
    return {field: getattr(args, field, None) for field in OVERRIDE_FIELDS}


def prepare_batch(
    staging_root: Path,
    *,
    expected_database: str,
    force_review: bool,
    force_requested_by: str | None = None,
    force_approved_by: str | None = None,
    force_reason: str | None = None,
    force_expires_at: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    package_dirs = discover_package_dirs(staging_root)
    if not package_dirs:
        raise SystemExit("HK staging root contains no evidence packages")

    override = {
        "force_requested_by": force_requested_by,
        "force_approved_by": force_approved_by,
        "force_reason": force_reason,
        "force_expires_at": force_expires_at,
    }
    inspected: list[dict[str, Any]] = []
    validation_errors: list[str] = []

    # Inspect every manifest before checking aggregate authorization.  A bad
    # package therefore cannot hide later invalid packages or trigger a DB open.
    for package_dir in package_dirs:
        package_path = _portable_path(package_dir)
        entry: dict[str, Any] = {
            "package_dir": package_dir,
            "package_path": package_path,
            "decision": None,
            "validation_ok": False,
        }
        try:
            validation = hk_importer.validate_evidence_package(package_dir)
            if not validation.ok:
                raise ValueError("; ".join(validation.errors))
            if str(validation.manifest.get("market") or "").strip().upper() != "HK":
                raise ValueError("manifest market must be HK")
            gates = build_quality_gates(package_dir)
            entry["decision"] = _canonical_decision(gates)
            entry["validation_ok"] = True
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            validation_errors.append(f"{package_path}: {type(exc).__name__}: {exc}")
        inspected.append(entry)

    if validation_errors:
        raise SystemExit("HK staging batch validation failed: " + " | ".join(validation_errors))

    review_entries = [entry for entry in inspected if entry["decision"] == "review"]
    if review_entries and not force_review:
        raise SystemExit(
            f"{len(review_entries)} review package(s) require --force-review and the complete override audit"
        )
    if review_entries:
        missing = [name for name, value in override.items() if not str(value or "").strip()]
        if missing:
            raise SystemExit("review packages require complete override audit fields: " + ", ".join(missing))
        _validate_expiry(str(force_expires_at))
    elif force_review:
        raise SystemExit("--force-review is invalid because every package has an allow/pass decision")

    prepared: list[dict[str, Any]] = []
    plan_entries: list[dict[str, Any]] = []
    preflight_errors: list[str] = []
    for entry in inspected:
        package_dir = entry["package_dir"]
        decision = str(entry["decision"])
        package_force_review = decision == "review"
        try:
            package_plan = hk_importer.build_import_plan(
                package_dir,
                force_review=package_force_review,
                force_requested_by=force_requested_by if package_force_review else None,
                force_approved_by=force_approved_by if package_force_review else None,
                force_reason=force_reason if package_force_review else None,
                force_expires_at=force_expires_at if package_force_review else None,
            )
        except SystemExit as exc:
            preflight_errors.append(f"{entry['package_path']}: {exc}")
            continue
        prepared.append(
            {
                "package_dir": package_dir,
                "decision": decision,
                "force_review": package_force_review,
            }
        )
        plan_entries.append(
            {
                **package_plan,
                "override_audit_required": package_force_review,
                "override_audit": (
                    {
                        "requested_by": force_requested_by,
                        "approved_by": force_approved_by,
                        "reason": force_reason,
                        "expires_at": force_expires_at,
                    }
                    if package_force_review
                    else None
                ),
            }
        )

    if preflight_errors:
        raise SystemExit("HK staging batch preflight failed: " + " | ".join(preflight_errors))

    decisions = Counter(str(entry["decision"]) for entry in inspected)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "market": "HK",
        "staging_only": True,
        "read_only": True,
        "execution_authorized": False,
        "database_connected": False,
        "expected_database": expected_database,
        "schema": SCHEMA,
        "staging_root": _portable_path(staging_root),
        "package_count": len(prepared),
        "quality_decisions": dict(sorted(decisions.items())),
        "transaction_scope": "single_connection_outer_transaction",
        "packages": plan_entries,
    }
    return plan, prepared


def execute_batch(
    plan: dict[str, Any],
    prepared: list[dict[str, Any]],
    *,
    expected_database: str,
    force_requested_by: str | None,
    force_approved_by: str | None,
    force_reason: str | None,
    force_expires_at: str | None,
) -> dict[str, Any]:
    connection = hk_importer.connection_kwargs()
    configured_database = str(connection.get("dbname") or "")
    if configured_database != expected_database:
        raise SystemExit(
            f"Configured database {configured_database!r} does not match "
            f"--expected-database {expected_database!r}; refusing to connect"
        )

    parse_runs: list[dict[str, str]] = []
    try:
        # Keep the connection outside any implicit transaction so this block is
        # the single top-level transaction; import_package creates savepoints.
        with hk_importer.psycopg.connect(**connection, autocommit=True) as conn:
            hk_importer.validate_connection_database(conn, expected_database)
            with conn.transaction():
                for entry in prepared:
                    force = bool(entry["force_review"])
                    parse_run_id = hk_importer.import_package(
                        conn,
                        entry["package_dir"],
                        SCHEMA,
                        force_review=force,
                        force_requested_by=force_requested_by if force else None,
                        force_approved_by=force_approved_by if force else None,
                        force_reason=force_reason if force else None,
                        force_expires_at=force_expires_at if force else None,
                    )
                    parse_runs.append(
                        {
                            "package_path": _portable_path(entry["package_dir"]),
                            "parse_run_id": parse_run_id,
                        }
                    )
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"HK staging batch import failed: {type(exc).__name__}") from None

    return {
        **plan,
        "read_only": False,
        "execution_authorized": True,
        "database_connected": True,
        "committed": True,
        "imported_package_count": len(parse_runs),
        "parse_runs": parse_runs,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomically import every HK evidence package from an isolated staging Wiki root."
    )
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--staging-only", action="store_true")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--force-review", action="store_true")
    parser.add_argument("--force-requested-by")
    parser.add_argument("--force-approved-by")
    parser.add_argument("--force-reason")
    parser.add_argument("--force-expires-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    staging_root, expected_database = validate_staging_target(
        args.staging_root,
        staging_only=args.staging_only,
        expected_database=args.expected_database,
    )
    override = _override_values(args)
    plan, prepared = prepare_batch(
        staging_root,
        expected_database=expected_database,
        force_review=args.force_review,
        **override,
    )

    result = plan
    if not args.dry_run:
        result = execute_batch(
            plan,
            prepared,
            expected_database=expected_database,
            **override,
        )
    if args.json_output:
        write_json(args.json_output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
