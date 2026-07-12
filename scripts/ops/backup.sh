#!/usr/bin/env bash
# SIQ 轻量备份脚本。
#
# 默认备份到仓库外，避免把备份产物写进源码树：
#   /home/maoyd/backups/siq
#
# 可按需覆盖：
#   SIQ_BACKUP_DIR=/mnt/backup/siq ./scripts/ops/backup.sh

set -euo pipefail

SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SIQ_DATA_ROOT="${SIQ_DATA_ROOT:-$SIQ_PROJECT_ROOT/data}"
SIQ_WIKI_ROOT="${SIQ_WIKI_ROOT:-$SIQ_DATA_ROOT/wiki}"
SIQ_PDF2MD_DATA_DIR="${SIQ_PDF2MD_DATA_DIR:-$SIQ_DATA_ROOT/pdf-parser}"
SIQ_BACKEND_DATA_ROOT="${SIQ_BACKEND_DATA_ROOT:-$SIQ_DATA_ROOT/backend}"
SIQ_HERMES_HOME="${SIQ_HERMES_HOME:-$SIQ_DATA_ROOT/hermes/home}"
SIQ_REPORT_FINDER_ROOT="${SIQ_REPORT_FINDER_ROOT:-$SIQ_PROJECT_ROOT/services/market-report-finder}"
SIQ_REPORT_DOWNLOADS_ROOT="${SIQ_REPORT_DOWNLOADS_ROOT:-$SIQ_DATA_ROOT/market-report-finder/downloads}"

BACKUP_DIR="${SIQ_BACKUP_DIR:-/home/maoyd/backups/siq}"
RETENTION_DAYS="${SIQ_BACKUP_RETENTION_DAYS:-30}"
SKIP_LARGE="${SIQ_BACKUP_SKIP_LARGE:-0}"
BACKUP_DATABASES="${SIQ_BACKUP_DATABASES:-siq_app,siq_document_parser,siq_us,siq_hk,siq_jp,siq_kr,siq_eu}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$BACKUP_DIR/$TIMESTAMP"

mkdir -p "$RUN_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

archive_if_exists() {
  local label="$1"
  local source="$2"
  local target="$RUN_DIR/$label.tar.gz"

  if [ ! -e "$source" ]; then
    log "跳过 $label：路径不存在 $source"
    return 0
  fi

  log "备份 $label <- $source"
  tar -czf "$target" -C "$(dirname "$source")" "$(basename "$source")"
}

dump_postgres_if_configured() {
  if [ -z "${DATABASE_URL:-}" ]; then
    log "跳过 PostgreSQL：未设置 DATABASE_URL"
    return 0
  fi

  if ! command -v pg_dump >/dev/null 2>&1; then
    log "跳过 PostgreSQL：pg_dump 不在 PATH 中"
    return 0
  fi

  mkdir -p "$RUN_DIR/postgres"
  local database
  local database_url
  IFS=',' read -r -a databases <<< "$BACKUP_DATABASES"
  for database in "${databases[@]}"; do
    database="${database//[[:space:]]/}"
    [ -n "$database" ] || continue
    database_url="$(postgres_url_for_database "$DATABASE_URL" "$database")"
    log "备份 PostgreSQL 逻辑导出：$database"
    pg_dump --no-owner --no-privileges --dbname "$database_url" | gzip > "$RUN_DIR/postgres/$database.sql.gz"
  done
}

postgres_url_for_database() {
  python3 - "$1" "$2" <<'PY'
import sys
from urllib.parse import quote, urlsplit, urlunsplit

source, database = sys.argv[1:3]
parsed = urlsplit(source)
if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
    raise SystemExit("DATABASE_URL must be a PostgreSQL URL")
scheme = "postgresql" if parsed.scheme.startswith("postgresql+") else parsed.scheme
print(urlunsplit((scheme, parsed.netloc, "/" + quote(database, safe=""), parsed.query, "")))
PY
}

write_manifest() {
  cat > "$RUN_DIR/manifest.txt" <<EOF
timestamp=$TIMESTAMP
project_root=$SIQ_PROJECT_ROOT
data_root=$SIQ_DATA_ROOT
wiki_root=$SIQ_WIKI_ROOT
pdf2md_data_dir=$SIQ_PDF2MD_DATA_DIR
backend_data_root=$SIQ_BACKEND_DATA_ROOT
hermes_home=$SIQ_HERMES_HOME
report_downloads_root=$SIQ_REPORT_DOWNLOADS_ROOT
retention_days=$RETENTION_DAYS
skip_large=$SKIP_LARGE
postgres_databases=$BACKUP_DATABASES
checksum_manifest=checksums.sha256
EOF
}

write_checksum_manifest() {
  if ! command -v sha256sum >/dev/null 2>&1; then
    log "无法生成校验清单：sha256sum 不在 PATH 中"
    return 1
  fi
  (
    cd "$RUN_DIR"
    find . -type f ! -name checksums.sha256 -printf '%P\n' \
      | LC_ALL=C sort \
      | while IFS= read -r path; do sha256sum "$path"; done
  ) > "$RUN_DIR/checksums.sha256"
}

cleanup_old_backups() {
  if [ "$RETENTION_DAYS" = "0" ]; then
    log "跳过旧备份清理：SIQ_BACKUP_RETENTION_DAYS=0"
    return 0
  fi

  log "清理 $RETENTION_DAYS 天前的备份目录"
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -print -exec rm -rf {} +
}

main() {
  log "开始 SIQ 备份：$RUN_DIR"

  dump_postgres_if_configured
  archive_if_exists "backend-data" "$SIQ_BACKEND_DATA_ROOT"
  if [ "$SKIP_LARGE" = "1" ]; then
    log "跳过大目录：SIQ_BACKUP_SKIP_LARGE=1"
  else
    archive_if_exists "pdf-parser-data" "$SIQ_PDF2MD_DATA_DIR"
    archive_if_exists "wiki" "$SIQ_WIKI_ROOT"
    archive_if_exists "report-downloads" "$SIQ_REPORT_DOWNLOADS_ROOT"
    archive_if_exists "hermes-profiles" "$SIQ_HERMES_HOME/profiles"
  fi
  write_manifest
  write_checksum_manifest
  cleanup_old_backups

  log "备份完成"
  du -sh "$RUN_DIR" 2>/dev/null || true
}

main "$@"
