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
SIQ_REPORT_FINDER_ROOT="${SIQ_REPORT_FINDER_ROOT:-$SIQ_PROJECT_ROOT/services/report-finder}"
SIQ_REPORT_DOWNLOADS_ROOT="${SIQ_REPORT_DOWNLOADS_ROOT:-$SIQ_DATA_ROOT/report-finder/downloads}"

BACKUP_DIR="${SIQ_BACKUP_DIR:-/home/maoyd/backups/siq}"
RETENTION_DAYS="${SIQ_BACKUP_RETENTION_DAYS:-30}"
SKIP_LARGE="${SIQ_BACKUP_SKIP_LARGE:-0}"
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

  log "备份 PostgreSQL 逻辑导出"
  pg_dump "$DATABASE_URL" | gzip > "$RUN_DIR/postgres.sql.gz"
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
EOF
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
  cleanup_old_backups

  log "备份完成"
  du -sh "$RUN_DIR" 2>/dev/null || true
}

main "$@"
