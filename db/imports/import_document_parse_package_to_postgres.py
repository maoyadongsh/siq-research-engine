#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("psycopg is required: pip install psycopg[binary]") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = REPO_ROOT / "db" / "ddl" / "060_create_document_parser_schema.sql"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_hashes(package_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(package_dir.rglob("*")):
        if path.is_file():
            hashes[path.relative_to(package_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


PACKAGE_TO_SOURCE_ARTIFACT = {
    "qa/parse_manifest.json": "manifest.json",
    "sections/document.md": "document.md",
    "document_full.json": "document_full.json",
    "sections/blocks.json": "blocks.json",
    "tables/tables.json": "tables.json",
    "logical_tables/logical_tables.json": "logical_tables.json",
    "logical_tables/table_relations.json": "table_relations.json",
    "figures/figures.json": "figures.json",
    "figures/figure_index.json": "figure_index.json",
    "qa/source_map.json": "source_map.json",
    "qa/quality_report.json": "quality_report.json",
    "extraction/schema.json": "extraction/schema.json",
    "extraction/result.json": "extraction/result.json",
    "extraction/evidence_map.json": "extraction/evidence_map.json",
    "extraction/validation_report.json": "extraction/validation_report.json",
}


def artifact_manifest(package_dir: Path, package_manifest: dict[str, Any]) -> dict[str, Any]:
    payload = read_json(package_dir / "artifact_manifest.json", {})
    if isinstance(payload, dict) and isinstance(payload.get("artifacts"), dict):
        return payload
    return {
        "source_result_dir": package_manifest.get("source_result_dir") or "",
        "artifacts": package_manifest.get("artifacts") if isinstance(package_manifest.get("artifacts"), dict) else {},
    }


def source_artifact_path(package_dir: Path, package_manifest: dict[str, Any], package_rel: str) -> Path | None:
    source_name = PACKAGE_TO_SOURCE_ARTIFACT.get(package_rel, package_rel)
    manifest = artifact_manifest(package_dir, package_manifest)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    item = artifacts.get(source_name) if isinstance(artifacts, dict) else None
    source = item.get("source") if isinstance(item, dict) else None
    if source:
        return Path(str(source))
    source_root = manifest.get("source_result_dir") or package_manifest.get("source_result_dir")
    if source_root:
        return Path(str(source_root)) / source_name
    return None


class PackageReader:
    def __init__(self, package_dir: Path, manifest: dict[str, Any]):
        self.package_dir = package_dir
        self.manifest = manifest

    def path(self, package_rel: str) -> Path:
        local = self.package_dir / package_rel
        if local.is_file():
            return local
        source = source_artifact_path(self.package_dir, self.manifest, package_rel)
        return source if source and source.is_file() else local

    def json(self, package_rel: str, default: Any | None = None) -> Any:
        return read_json(self.path(package_rel), default)

    def hashes(self) -> dict[str, str]:
        hashes = package_hashes(self.package_dir)
        manifest = artifact_manifest(self.package_dir, self.manifest)
        artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
        if isinstance(artifacts, dict):
            for name, item in artifacts.items():
                if isinstance(item, dict) and item.get("sha256"):
                    hashes[f"source:{name}"] = str(item["sha256"])
        return hashes


def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    db = os.environ.get("SIQ_PGDATABASE") or os.environ.get("PGDATABASE") or "siq"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def validate_schema(schema: str) -> None:
    if schema != "document_parser":
        raise SystemExit("generic document imports must target schema document_parser")


def run_ddl(conn: Any) -> None:
    conn.execute(DDL_PATH.read_text(encoding="utf-8"))


def import_package(conn: Any, package_dir: Path, schema: str = "document_parser") -> str:
    validate_schema(schema)
    package_dir = package_dir.resolve()
    manifest = read_json(package_dir / "manifest.json")
    if manifest.get("schema_version") != "generic_document_package_v1":
        raise SystemExit("manifest schema_version must be generic_document_package_v1")

    reader = PackageReader(package_dir, manifest)
    parse_manifest = reader.json("qa/parse_manifest.json")
    quality = reader.json("qa/quality_report.json")
    artifact_hashes = reader.hashes()
    document_id = manifest.get("document_id") or f"doc-{manifest['task_id']}"
    parse_run_id = stable_parse_run_id(manifest, artifact_hashes)
    status = quality.get("overall_status") or parse_manifest.get("quality_status") or "warning"
    warnings = quality.get("warnings") or []

    with conn.transaction():
        _upsert_document(conn, schema, package_dir, manifest, parse_manifest, quality, document_id)
        _upsert_parse_run(conn, schema, package_dir, manifest, parse_manifest, parse_run_id, document_id, artifact_hashes, status, warnings)
        _delete_run_rows(conn, schema, parse_run_id)
        _insert_artifacts(conn, schema, reader, parse_run_id)
        _insert_blocks(conn, schema, reader, parse_run_id, document_id)
        _insert_tables(conn, schema, reader, parse_run_id, document_id)
        _insert_logical_tables(conn, schema, reader, parse_run_id, document_id)
        _insert_table_relations(conn, schema, reader, parse_run_id)
        _insert_figures(conn, schema, reader, parse_run_id, document_id)
        _insert_sources(conn, schema, reader, parse_run_id, document_id)
        _insert_extraction(conn, schema, reader, parse_run_id, document_id)
    return parse_run_id


def stable_parse_run_id(manifest: dict[str, Any], artifact_hashes: dict[str, str]) -> str:
    return stable_id(manifest.get("task_id"), manifest.get("document_full_sha256"), json.dumps(artifact_hashes, sort_keys=True))


def _upsert_document(conn: Any, schema: str, package_dir: Path, manifest: dict[str, Any], parse_manifest: dict[str, Any], quality: dict[str, Any], document_id: str) -> None:
    conn.execute(
        f"""
        insert into {schema}.documents (
          document_id, task_id, collection, document_key, filename, document_kind,
          parser_provider, file_sha256, package_path, quality_status, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (document_id) do update set
          collection = excluded.collection,
          document_key = excluded.document_key,
          filename = excluded.filename,
          document_kind = excluded.document_kind,
          parser_provider = excluded.parser_provider,
          file_sha256 = excluded.file_sha256,
          package_path = excluded.package_path,
          quality_status = excluded.quality_status,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            document_id,
            manifest["task_id"],
            manifest.get("collection") or "default",
            manifest.get("document_key") or package_dir.name,
            manifest.get("filename") or parse_manifest.get("filename"),
            manifest.get("document_kind") or parse_manifest.get("document_kind"),
            manifest.get("parser_provider") or parse_manifest.get("parser_provider"),
            parse_manifest.get("file_sha256"),
            str(package_dir),
            quality.get("overall_status") or parse_manifest.get("quality_status"),
            Jsonb({"package_manifest": manifest, "parse_manifest": parse_manifest, "quality": quality}),
        ),
    )


def _upsert_parse_run(conn: Any, schema: str, package_dir: Path, manifest: dict[str, Any], parse_manifest: dict[str, Any], parse_run_id: str, document_id: str, artifact_hashes: dict[str, str], status: str, warnings: list[Any]) -> None:
    conn.execute(
        f"""
        insert into {schema}.parse_runs (
          parse_run_id, document_id, task_id, parser_version, parser_provider, package_path,
          status, completed_at, warnings, artifact_hashes, raw, updated_at
        ) values (%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,now())
        on conflict (parse_run_id) do update set
          status = excluded.status,
          completed_at = now(),
          warnings = excluded.warnings,
          artifact_hashes = excluded.artifact_hashes,
          raw = excluded.raw,
          updated_at = now()
        """,
        (
            parse_run_id,
            document_id,
            manifest["task_id"],
            parse_manifest.get("parser_version"),
            manifest.get("parser_provider") or parse_manifest.get("parser_provider"),
            str(package_dir),
            status,
            Jsonb(warnings),
            Jsonb(artifact_hashes),
            Jsonb({"package_manifest": manifest, "parse_manifest": parse_manifest}),
        ),
    )


def _delete_run_rows(conn: Any, schema: str, parse_run_id: str) -> None:
    for table in (
        "artifacts",
        "extractions",
        "sources",
        "figures",
        "table_relations",
        "logical_tables",
        "table_cells",
        "tables",
        "blocks",
    ):
        conn.execute(f"delete from {schema}.{table} where parse_run_id = %s", (parse_run_id,))


def _insert_artifacts(conn: Any, schema: str, reader: PackageReader, parse_run_id: str) -> None:
    for path in sorted(reader.package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(reader.package_dir).as_posix()
        conn.execute(
            f"insert into {schema}.artifacts (parse_run_id, artifact_path, artifact_type, sha256, size_bytes, raw) values (%s,%s,%s,%s,%s,%s)",
            (parse_run_id, rel, rel.replace("/", "."), file_sha256(path), path.stat().st_size, Jsonb({})),
        )
    manifest = artifact_manifest(reader.package_dir, reader.manifest)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    if isinstance(artifacts, dict):
        for name, item in sorted(artifacts.items()):
            if not isinstance(item, dict):
                continue
            if item.get("package_path") and (reader.package_dir / str(item["package_path"])).is_file():
                continue
            conn.execute(
                f"insert into {schema}.artifacts (parse_run_id, artifact_path, artifact_type, sha256, size_bytes, raw) values (%s,%s,%s,%s,%s,%s)",
                (
                    parse_run_id,
                    f"source:{name}",
                    str(name).replace("/", "."),
                    item.get("sha256"),
                    item.get("size_bytes"),
                    Jsonb(item),
                ),
            )


def _insert_blocks(conn: Any, schema: str, reader: PackageReader, parse_run_id: str, document_id: str) -> None:
    payload = reader.json("sections/blocks.json")
    for block in payload.get("blocks") or []:
        source_ref = block.get("source_ref") or {}
        conn.execute(
            f"""
            insert into {schema}.blocks (
              parse_run_id, document_id, block_id, block_type, sub_type, page_number,
              reading_order, text, markdown, bbox, evidence_id, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                document_id,
                block.get("block_id"),
                block.get("type"),
                block.get("sub_type"),
                block.get("page_number"),
                block.get("reading_order"),
                block.get("text"),
                block.get("markdown"),
                Jsonb(block.get("bbox") or []),
                source_ref.get("evidence_id"),
                Jsonb(block),
            ),
        )


def _insert_tables(conn: Any, schema: str, reader: PackageReader, parse_run_id: str, document_id: str) -> None:
    payload = reader.json("tables/tables.json")
    for table in payload.get("physical_tables") or payload.get("tables") or []:
        quality = table.get("quality") or {}
        table_id = table.get("table_id")
        conn.execute(
            f"""
            insert into {schema}.tables (
              parse_run_id, document_id, table_id, block_id, title, caption, page_number,
              sheet_name, row_count, column_count, markdown, html, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                document_id,
                table_id,
                table.get("block_id"),
                table.get("title"),
                table.get("caption"),
                table.get("page_number"),
                table.get("sheet_name"),
                quality.get("row_count"),
                quality.get("column_count"),
                table.get("markdown"),
                table.get("html"),
                Jsonb(table),
            ),
        )
        for cell in table.get("cells") or []:
            conn.execute(
                f"""
                insert into {schema}.table_cells (
                  parse_run_id, table_id, row_index, column_index, text, bbox, evidence_id, raw
                ) values (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    parse_run_id,
                    table_id,
                    int(cell.get("row_index") or 0),
                    int(cell.get("column_index") or 0),
                    cell.get("text"),
                    Jsonb(cell.get("bbox") or []),
                    cell.get("evidence_id"),
                    Jsonb(cell),
                ),
            )


def _insert_logical_tables(conn: Any, schema: str, reader: PackageReader, parse_run_id: str, document_id: str) -> None:
    payload = reader.json("logical_tables/logical_tables.json")
    for item in payload.get("logical_tables") or []:
        conn.execute(
            f"""
            insert into {schema}.logical_tables (
              parse_run_id, document_id, logical_table_id, title, fragment_table_ids,
              merge_status, merge_confidence, markdown, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                document_id,
                item.get("logical_table_id"),
                item.get("title"),
                Jsonb(item.get("fragment_table_ids") or []),
                item.get("merge_status"),
                item.get("merge_confidence"),
                item.get("markdown"),
                Jsonb(item),
            ),
        )


def _insert_table_relations(conn: Any, schema: str, reader: PackageReader, parse_run_id: str) -> None:
    payload = reader.json("logical_tables/table_relations.json")
    for index, item in enumerate(payload.get("relations") or [], start=1):
        relation_id = item.get("relation_id") or f"rel-{index:06d}"
        conn.execute(
            f"""
            insert into {schema}.table_relations (
              parse_run_id, relation_id, source_table_id, target_table_id,
              relation_type, confidence, review_status, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                relation_id,
                item.get("source_table_id"),
                item.get("target_table_id"),
                item.get("relation_type"),
                item.get("confidence"),
                item.get("review_status"),
                Jsonb(item),
            ),
        )


def _insert_figures(conn: Any, schema: str, reader: PackageReader, parse_run_id: str, document_id: str) -> None:
    payload = reader.json("figures/figures.json")
    for item in payload.get("figures") or []:
        conn.execute(
            f"""
            insert into {schema}.figures (
              parse_run_id, document_id, image_id, block_id, figure_type, page_number,
              image_path, caption, ocr_text, evidence_id, bbox, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                document_id,
                item.get("image_id"),
                item.get("block_id"),
                item.get("type"),
                item.get("page_number"),
                item.get("image_path"),
                item.get("caption"),
                item.get("ocr_text"),
                item.get("evidence_id"),
                Jsonb(item.get("bbox") or []),
                Jsonb(item),
            ),
        )


def _insert_sources(conn: Any, schema: str, reader: PackageReader, parse_run_id: str, document_id: str) -> None:
    payload = reader.json("qa/source_map.json")
    for item in payload.get("sources") or []:
        evidence_id = item.get("evidence_id")
        if not evidence_id:
            continue
        conn.execute(
            f"""
            insert into {schema}.sources (
              parse_run_id, evidence_id, document_id, source_type, artifact, block_id,
              table_id, logical_table_id, image_id, page_number, bbox, quote,
              open_source_url, open_artifact_url, raw
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_run_id,
                evidence_id,
                document_id,
                item.get("source_type"),
                item.get("artifact"),
                item.get("block_id"),
                item.get("table_id"),
                item.get("logical_table_id"),
                item.get("image_id"),
                item.get("page_number"),
                Jsonb(item.get("bbox") or []),
                item.get("quote"),
                item.get("open_source_url"),
                item.get("open_artifact_url"),
                Jsonb(item),
            ),
        )


def _insert_extraction(conn: Any, schema: str, reader: PackageReader, parse_run_id: str, document_id: str) -> None:
    result = reader.json("extraction/result.json")
    if not result:
        return
    schema_payload = reader.json("extraction/schema.json")
    evidence = reader.json("extraction/evidence_map.json")
    validation = reader.json("extraction/validation_report.json")
    extract_id = result.get("extract_id") or stable_id(parse_run_id, "default_extraction")
    conn.execute(
        f"""
        insert into {schema}.extractions (
          extract_id, parse_run_id, document_id, status, schema_json, result_json,
          evidence_map, validation_report, raw
        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (extract_id) do update set
          status = excluded.status,
          schema_json = excluded.schema_json,
          result_json = excluded.result_json,
          evidence_map = excluded.evidence_map,
          validation_report = excluded.validation_report,
          raw = excluded.raw
        """,
        (
            extract_id,
            parse_run_id,
            document_id,
            result.get("status"),
            Jsonb(schema_payload.get("schema") or schema_payload),
            Jsonb(result.get("result") or {}),
            Jsonb(evidence.get("evidence_map") or {}),
            Jsonb(validation),
            Jsonb({"schema": schema_payload, "result": result, "evidence": evidence, "validation": validation}),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a generic document Wiki package into PostgreSQL.")
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default="document_parser")
    parser.add_argument("--skip-ddl", action="store_true")
    args = parser.parse_args()

    url = database_url(args.database_url)
    with psycopg.connect(url) as conn:
        if not args.skip_ddl:
            run_ddl(conn)
        parse_run_id = import_package(conn, args.package_dir, schema=args.schema)
        conn.commit()
    print(json.dumps({"ok": True, "parse_run_id": parse_run_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
