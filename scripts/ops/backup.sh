#!/usr/bin/env bash
# SIQ 轻量备份脚本。
#
# 默认备份到仓库外，避免把备份产物写进源码树：
#   /home/maoyd/backups/siq
#
# 可按需覆盖：
#   SIQ_BACKUP_DIR=/mnt/backup/siq ./scripts/ops/backup.sh

set -euo pipefail
umask 077

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
BACKUP_MODE="${SIQ_BACKUP_MODE:-optional}"
POSTGRES_DATABASE_URL="${DATABASE_URL:-}"
unset DATABASE_URL
if [[ "${SIQ_BACKUP_REQUIRED:-0}" =~ ^(1|true|yes|on)$ ]]; then
  BACKUP_MODE="required"
fi
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$BACKUP_DIR/$TIMESTAMP"
OBJECT_RECORDS=()
SCHEMA_CONTRACT_VERSION="siq_postgres_schema_contract_v1"

case "$BACKUP_MODE" in
  optional|development|dev) REQUIRED_MODE=0 ;;
  required|release) REQUIRED_MODE=1 ;;
  *) printf '未知 SIQ_BACKUP_MODE：%s（支持 optional、required、release）\n' "$BACKUP_MODE" >&2; exit 2 ;;
esac

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

validate_database_name() {
  [[ "$1" =~ ^[a-z][a-z0-9_]{1,62}$ ]]
}

schema_authority_files() {
  case "$1" in
    siq_app) find "$SIQ_PROJECT_ROOT/apps/api/migrations" -maxdepth 1 -type f -name '*.sql' -print | LC_ALL=C sort ;;
    siq_document_parser) printf '%s\n' "$SIQ_PROJECT_ROOT/db/ddl/060_create_document_parser_schema.sql" ;;
    siq_us) printf '%s\n' "$SIQ_PROJECT_ROOT/db/ddl/010_create_sec_us_schema.sql" ;;
    siq_hk) printf '%s\n' "$SIQ_PROJECT_ROOT/db/ddl/020_create_pdf2md_hk_schema.sql" ;;
    siq_jp) printf '%s\n' "$SIQ_PROJECT_ROOT/db/ddl/030_create_edinet_jp_schema.sql" ;;
    siq_kr) printf '%s\n' "$SIQ_PROJECT_ROOT/db/ddl/040_create_dart_kr_schema.sql" ;;
    siq_eu) printf '%s\n' "$SIQ_PROJECT_ROOT/db/ddl/050_create_eu_ifrs_schema.sql" ;;
    *) return 1 ;;
  esac
}

schema_authority_sha256() {
  local database="$1"
  local path relative
  local files=()
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ -s "$path" ] || {
      log "schema/migration authority 不存在或为空：$database"
      return 1
    }
    files+=("$path")
  done < <(schema_authority_files "$database")
  [ "${#files[@]}" -gt 0 ] || {
    log "schema/migration authority 为空：$database"
    return 1
  }
  {
    for path in "${files[@]}"; do
      relative="${path#"$SIQ_PROJECT_ROOT"/}"
      printf '%s\0' "$relative"
      sha256sum "$path" | awk '{print $1}' | tr -d '\n'
      printf '\0'
    done
  } | sha256sum | awk '{print $1}'
}

preflight() {
  local database source
  local databases=()

  case "$SKIP_LARGE" in
    0|1) ;;
    *) log "SIQ_BACKUP_SKIP_LARGE 必须为 0 或 1"; return 2 ;;
  esac
  if [ "$REQUIRED_MODE" = "1" ] && [ "$SKIP_LARGE" = "1" ]; then
    log "required/release 模式禁止跳过大目录"
    return 1
  fi
  if [ "$REQUIRED_MODE" = "1" ] && [ -z "$POSTGRES_DATABASE_URL" ]; then
    log "required/release 模式必须设置 DATABASE_URL"
    return 1
  fi
  if [ -n "$POSTGRES_DATABASE_URL" ] || [ "$REQUIRED_MODE" = "1" ]; then
    for command in python3 pg_dump gzip sha256sum; do
      command -v "$command" >/dev/null 2>&1 || {
        log "缺少备份命令：$command"
        return 1
      }
    done
  fi
  command -v tar >/dev/null 2>&1 || {
    log "缺少备份命令：tar"
    return 1
  }

  IFS=',' read -r -a databases <<< "$BACKUP_DATABASES"
  [ "${#databases[@]}" -gt 0 ] || { log "SIQ_BACKUP_DATABASES 不能为空"; return 2; }
  for database in "${databases[@]}"; do
    database="${database//[[:space:]]/}"
    validate_database_name "$database" || { log "非法数据库名称：$database"; return 2; }
    schema_authority_sha256 "$database" >/dev/null || return 1
  done

  if [ "$REQUIRED_MODE" = "1" ]; then
    for source in \
      "$SIQ_BACKEND_DATA_ROOT" \
      "$SIQ_PDF2MD_DATA_DIR" \
      "$SIQ_WIKI_ROOT" \
      "$SIQ_REPORT_DOWNLOADS_ROOT" \
      "$SIQ_HERMES_HOME"; do
      [ -e "$source" ] || { log "required/release 模式缺少备份目标：$source"; return 1; }
      if [ -d "$source" ]; then
        find "$source" -mindepth 1 -print -quit | grep -q . || {
          log "required/release 模式拒绝空目录：$source"
          return 1
        }
      elif [ ! -s "$source" ]; then
        log "required/release 模式拒绝空文件：$source"
        return 1
      fi
    done
  fi
}

archive_if_exists() {
  local label="$1"
  local source="$2"
  local target="$RUN_DIR/$label.tar.gz"

  if [ ! -e "$source" ]; then
    log "跳过 $label：路径不存在 $source"
    if [ "$REQUIRED_MODE" = "1" ]; then
      log "required/release 模式禁止跳过已配置目标：$label"
      return 1
    fi
    OBJECT_RECORDS+=("object=$label.tar.gz status=skipped size=0 source=$source")
    return 0
  fi

  if [ "$REQUIRED_MODE" = "1" ]; then
    if [ -d "$source" ]; then
      find "$source" -mindepth 1 -print -quit | grep -q . || {
        log "required/release 模式拒绝空目录：$source"
        return 1
      }
    elif [ ! -s "$source" ]; then
      log "required/release 模式拒绝空文件：$source"
      return 1
    fi
  fi

  log "备份 $label <- $source"
  tar -czf "$target" -C "$(dirname "$source")" "$(basename "$source")"
  [ -s "$target" ] || { log "备份产物为空：$target"; return 1; }
  OBJECT_RECORDS+=("object=$label.tar.gz status=ok size=$(stat -c '%s' "$target") source=$source")
}

dump_postgres_if_configured() {
  if [ -z "$POSTGRES_DATABASE_URL" ]; then
    log "跳过 PostgreSQL：未设置 DATABASE_URL"
    if [ "$REQUIRED_MODE" = "1" ]; then
      log "required/release 模式必须设置 DATABASE_URL"
      return 1
    fi
    IFS=',' read -r -a skipped_databases <<< "$BACKUP_DATABASES"
    for database in "${skipped_databases[@]}"; do
      database="${database//[[:space:]]/}"
      [ -n "$database" ] || continue
      OBJECT_RECORDS+=("object=postgres/$database.sql.gz status=skipped size=0 source=DATABASE_URL database=$database")
    done
    return 0
  fi

  if ! command -v pg_dump >/dev/null 2>&1; then
    log "跳过 PostgreSQL：pg_dump 不在 PATH 中"
    if [ "$REQUIRED_MODE" = "1" ]; then
      log "required/release 模式必须提供 pg_dump"
      return 1
    fi
    IFS=',' read -r -a skipped_databases <<< "$BACKUP_DATABASES"
    for database in "${skipped_databases[@]}"; do
      database="${database//[[:space:]]/}"
      [ -n "$database" ] || continue
      OBJECT_RECORDS+=("object=postgres/$database.sql.gz status=skipped size=0 source=pg_dump database=$database")
    done
    return 0
  fi

  mkdir -p "$RUN_DIR/postgres"
  local database
  local database_url
  IFS=',' read -r -a databases <<< "$BACKUP_DATABASES"
  for database in "${databases[@]}"; do
    database="${database//[[:space:]]/}"
    [ -n "$database" ] || continue
    database_url="$(postgres_url_for_database "$POSTGRES_DATABASE_URL" "$database")"
    log "备份 PostgreSQL 逻辑导出：$database"
    PGDATABASE="$database_url" pg_dump --no-owner --no-privileges | gzip > "$RUN_DIR/postgres/$database.sql.gz"
    [ -s "$RUN_DIR/postgres/$database.sql.gz" ] || { log "PostgreSQL 备份产物为空：$database"; return 1; }
    # Consume the whole stream: with pipefail, grep -q can close the pipe early
    # and turn a valid large dump into a false failure via gzip's SIGPIPE.
    gzip -dc "$RUN_DIR/postgres/$database.sql.gz" | awk 'NF { found = 1 } END { exit(found ? 0 : 1) }' || {
      log "PostgreSQL 备份内容为空：$database"
      return 1
    }
    OBJECT_RECORDS+=("object=postgres/$database.sql.gz status=ok size=$(stat -c '%s' "$RUN_DIR/postgres/$database.sql.gz") source=DATABASE_URL database=$database")

    log "记录 PostgreSQL schema 快照：$database"
    PGDATABASE="$database_url" pg_dump --schema-only --no-owner --no-privileges \
      | gzip > "$RUN_DIR/postgres/$database.schema.sql.gz"
    [ -s "$RUN_DIR/postgres/$database.schema.sql.gz" ] || {
      log "PostgreSQL schema 快照为空：$database"
      return 1
    }
    gzip -dc "$RUN_DIR/postgres/$database.schema.sql.gz" | awk 'NF { found = 1 } END { exit(found ? 0 : 1) }' || {
      log "PostgreSQL schema 快照内容为空：$database"
      return 1
    }
    OBJECT_RECORDS+=("object=postgres/$database.schema.sql.gz status=ok size=$(stat -c '%s' "$RUN_DIR/postgres/$database.schema.sql.gz") source=DATABASE_URL database=$database kind=schema_snapshot")
  done
}

postgres_url_for_database() {
  SIQ_POSTGRES_CONNECTION_URL="$1" SIQ_POSTGRES_DATABASE="$2" python3 - <<'PY'
import os
from urllib.parse import quote, urlsplit, urlunsplit

source = os.environ["SIQ_POSTGRES_CONNECTION_URL"]
database = os.environ["SIQ_POSTGRES_DATABASE"]
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
backup_mode=$BACKUP_MODE
postgres_databases=$BACKUP_DATABASES
checksum_manifest=checksums.sha256
schema_contract_version=$SCHEMA_CONTRACT_VERSION
EOF
  local database authority_digest
  local databases=()
  IFS=',' read -r -a databases <<< "$BACKUP_DATABASES"
  for database in "${databases[@]}"; do
    database="${database//[[:space:]]/}"
    [ -n "$database" ] || continue
    authority_digest="$(schema_authority_sha256 "$database")"
    printf 'schema_authority_sha256_%s=%s\n' "$database" "$authority_digest" >> "$RUN_DIR/manifest.txt"
    printf 'schema_snapshot_%s=postgres/%s.schema.sql.gz\n' "$database" "$database" >> "$RUN_DIR/manifest.txt"
  done
  local record
  for record in "${OBJECT_RECORDS[@]}"; do
    printf '%s\n' "$record" >> "$RUN_DIR/manifest.txt"
  done
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

  local record object
  for record in "${OBJECT_RECORDS[@]}"; do
    case "$record" in
      *" status=ok "*)
        object="${record#object=}"
        object="${object%% status=*}"
        grep -Fq "  $object" "$RUN_DIR/checksums.sha256" || {
          log "校验清单未覆盖备份产物：$object"
          return 1
        }
        ;;
    esac
  done
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
  preflight
  mkdir -p "$RUN_DIR"
  log "开始 SIQ 备份：$RUN_DIR"

  dump_postgres_if_configured
  archive_if_exists "backend-data" "$SIQ_BACKEND_DATA_ROOT"
  if [ "$SKIP_LARGE" = "1" ]; then
    log "跳过大目录：SIQ_BACKUP_SKIP_LARGE=1"
    OBJECT_RECORDS+=("object=pdf-parser-data.tar.gz status=skipped size=0 source=$SIQ_PDF2MD_DATA_DIR")
    OBJECT_RECORDS+=("object=wiki.tar.gz status=skipped size=0 source=$SIQ_WIKI_ROOT")
    OBJECT_RECORDS+=("object=report-downloads.tar.gz status=skipped size=0 source=$SIQ_REPORT_DOWNLOADS_ROOT")
    OBJECT_RECORDS+=("object=hermes-home.tar.gz status=skipped size=0 source=$SIQ_HERMES_HOME")
  else
    archive_if_exists "pdf-parser-data" "$SIQ_PDF2MD_DATA_DIR"
    archive_if_exists "wiki" "$SIQ_WIKI_ROOT"
    archive_if_exists "report-downloads" "$SIQ_REPORT_DOWNLOADS_ROOT"
    archive_if_exists "hermes-home" "$SIQ_HERMES_HOME"
  fi
  write_manifest
  write_checksum_manifest
  if [ "$REQUIRED_MODE" = "1" ] && [ "${#OBJECT_RECORDS[@]}" -eq 0 ]; then
    log "required/release 模式未生成任何备份对象"
    exit 1
  fi
  cleanup_old_backups

  log "备份完成"
  du -sh "$RUN_DIR" 2>/dev/null || true
}

main "$@"
