#!/usr/bin/env bash
# Restore one logical PostgreSQL dump into an automatically-created disposable
# database, verify required relations, then remove the database.

set -euo pipefail
umask 077

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

if [ "${SIQ_RESTORE_SMOKE:-0}" != "1" ]; then
  log "跳过恢复冒烟：设置 SIQ_RESTORE_SMOKE=1 后才会创建临时数据库"
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="${SIQ_RESTORE_SMOKE_SOURCE:-}"
ADMIN_URL="${SIQ_RESTORE_SMOKE_ADMIN_URL:-}"
unset SIQ_RESTORE_SMOKE_ADMIN_URL
EXPECTED_RELATIONS="${SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS:-}"
AGENT_VIEW="${SIQ_RESTORE_SMOKE_AGENT_VIEW:-}"
CHECKSUM_MANIFEST="${SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST:-}"
RESTORE_MODE="${SIQ_RESTORE_SMOKE_MODE:-optional}"
NONEMPTY_RELATIONS_CONFIGURED="${SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS+x}"
NONEMPTY_RELATIONS="${SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS:-}"
REQUIRE_AGENT_VIEW="${SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW:-1}"
DATABASE_NAME="${SIQ_RESTORE_SMOKE_DATABASE_NAME:-}"
VOICEPRINT_TOMBSTONE_REQUIRED="${SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED:-0}"
VOICEPRINT_TOMBSTONE_EXPECTED_COUNT="${SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT:-}"
VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC="${SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC:-}"
EXPECTED_SCHEMA_SNAPSHOT="${SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT:-}"
COMPATIBILITY_MIGRATION="${SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATION:-}"
COMPATIBILITY_MIGRATIONS="${SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATIONS:-}"
COMPATIBILITY_MIGRATION_FILES=()
AUTHORITY_CHAIN_CONFIGURED=0
if [ -n "$COMPATIBILITY_MIGRATIONS" ]; then
  AUTHORITY_CHAIN_CONFIGURED=1
  while IFS= read -r migration; do
    [ -n "$migration" ] || continue
    COMPATIBILITY_MIGRATION_FILES+=("$migration")
  done <<< "$COMPATIBILITY_MIGRATIONS"
elif [ -n "$COMPATIBILITY_MIGRATION" ]; then
  COMPATIBILITY_MIGRATION_FILES+=("$COMPATIBILITY_MIGRATION")
fi
if [[ "${SIQ_RESTORE_SMOKE_REQUIRED:-0}" =~ ^(1|true|yes|on)$ ]]; then
  RESTORE_MODE="required"
fi

case "$RESTORE_MODE" in
  optional|development|dev) REQUIRED_MODE=0 ;;
  required|release) REQUIRED_MODE=1 ;;
  *) log "未知 SIQ_RESTORE_SMOKE_MODE：$RESTORE_MODE（支持 optional、required、release）"; exit 2 ;;
esac

if [ -z "$SOURCE" ] || [ -z "$ADMIN_URL" ] || [ -z "$EXPECTED_RELATIONS" ] || [ -z "$AGENT_VIEW" ]; then
  log "恢复冒烟配置不完整：必须设置 SOURCE、ADMIN_URL、EXPECTED_RELATIONS 和 AGENT_VIEW"
  exit 2
fi
if [ ! -f "$SOURCE" ]; then
  log "恢复源不存在：$SOURCE"
  exit 2
fi
if [ -n "$EXPECTED_SCHEMA_SNAPSHOT" ] && [ ! -f "$EXPECTED_SCHEMA_SNAPSHOT" ]; then
  log "schema 快照不存在"
  exit 2
fi
for migration in "${COMPATIBILITY_MIGRATION_FILES[@]}"; do
  [ -s "$migration" ] || { log "兼容性 migration 不存在或为空"; exit 2; }
done
if [ "$AUTHORITY_CHAIN_CONFIGURED" = "1" ]; then
  [ "${#COMPATIBILITY_MIGRATION_FILES[@]}" -gt 0 ] || {
    log "兼容性 authority 链为空"
    exit 2
  }
  EXPECTED_AUTHORITY_FILES=()
  case "$DATABASE_NAME" in
    siq_app)
      while IFS= read -r migration; do
        EXPECTED_AUTHORITY_FILES+=("$migration")
      done < <(find "$REPO_ROOT/apps/api/migrations" -maxdepth 1 -type f -name '*.sql' -print | LC_ALL=C sort)
      ;;
    siq_document_parser) EXPECTED_AUTHORITY_FILES+=("$REPO_ROOT/db/ddl/060_create_document_parser_schema.sql") ;;
    siq_us) EXPECTED_AUTHORITY_FILES+=("$REPO_ROOT/db/ddl/010_create_sec_us_schema.sql") ;;
    siq_hk) EXPECTED_AUTHORITY_FILES+=("$REPO_ROOT/db/ddl/020_create_pdf2md_hk_schema.sql") ;;
    siq_jp) EXPECTED_AUTHORITY_FILES+=("$REPO_ROOT/db/ddl/030_create_edinet_jp_schema.sql") ;;
    siq_kr) EXPECTED_AUTHORITY_FILES+=("$REPO_ROOT/db/ddl/040_create_dart_kr_schema.sql") ;;
    siq_eu) EXPECTED_AUTHORITY_FILES+=("$REPO_ROOT/db/ddl/050_create_eu_ifrs_schema.sql") ;;
    *) log "数据库没有已登记的兼容性 authority：$DATABASE_NAME"; exit 2 ;;
  esac
  [ "${#COMPATIBILITY_MIGRATION_FILES[@]}" -eq "${#EXPECTED_AUTHORITY_FILES[@]}" ] || {
    log "兼容性 authority 链不完整：$DATABASE_NAME"
    exit 2
  }
  for index in "${!EXPECTED_AUTHORITY_FILES[@]}"; do
    [ "$(realpath "${COMPATIBILITY_MIGRATION_FILES[$index]}")" = "$(realpath "${EXPECTED_AUTHORITY_FILES[$index]}")" ] || {
      log "兼容性 authority 链不是仓库登记文件：$DATABASE_NAME"
      exit 2
    }
  done
fi
if [ "$REQUIRED_MODE" = "1" ] && [ -z "$CHECKSUM_MANIFEST" ]; then
  log "required/release 模式必须设置 SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST"
  exit 2
fi
if [[ "$VOICEPRINT_TOMBSTONE_REQUIRED" =~ ^(1|true|yes|on)$ ]]; then
  [ "$DATABASE_NAME" = "siq_app" ] || {
    log "声纹 tombstone 恢复验收只允许用于 siq_app"
    exit 2
  }
  [[ "$VOICEPRINT_TOMBSTONE_EXPECTED_COUNT" =~ ^(0|[1-9][0-9]*)$ ]] || {
    log "required 声纹 tombstone 验收必须提供非负 EXPECTED_COUNT"
    exit 2
  }
  [[ "$VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC" =~ ^[0-9a-fA-F]{64}$ ]] || {
    log "required 声纹 tombstone 验收必须提供 64 位 EXPECTED_HEAD_HMAC"
    exit 2
  }
  VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC="${VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC,,}"
  if [ "$VOICEPRINT_TOMBSTONE_EXPECTED_COUNT" = "0" ] \
    && [ "$VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC" != "$(printf '0%.0s' {1..64})" ]; then
    log "空声纹 tombstone 链必须使用全零 EXPECTED_HEAD_HMAC"
    exit 2
  fi
  export SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT="$VOICEPRINT_TOMBSTONE_EXPECTED_COUNT"
  export SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC="$VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC"
fi

manifest_contains_path() {
  local candidate="$1"
  local candidate_relative entry entry_path
  candidate_relative="$(realpath --relative-to="$(dirname "$CHECKSUM_MANIFEST")" "$candidate")"
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    entry_path="${entry#* }"
    entry_path="${entry_path# }"
    entry_path="${entry_path#\*}"
    [ "$entry_path" = "$candidate_relative" ] && return 0
  done < "$CHECKSUM_MANIFEST"
  return 1
}

if [ -n "$CHECKSUM_MANIFEST" ]; then
  [ -f "$CHECKSUM_MANIFEST" ] || { log "校验清单不存在：$CHECKSUM_MANIFEST"; exit 2; }
  if ! manifest_contains_path "$SOURCE"; then
    log "校验清单未包含恢复源"
    exit 2
  fi
  if [ -n "$EXPECTED_SCHEMA_SNAPSHOT" ] && ! manifest_contains_path "$EXPECTED_SCHEMA_SNAPSHOT"; then
    log "校验清单未包含 schema 快照"
    exit 2
  fi
fi
for command in createdb dropdb psql; do
  command -v "$command" >/dev/null 2>&1 || { log "缺少命令：$command"; exit 2; }
done
if [ -n "$EXPECTED_SCHEMA_SNAPSHOT" ]; then
  command -v pg_dump >/dev/null 2>&1 || { log "缺少命令：pg_dump"; exit 2; }
  command -v cmp >/dev/null 2>&1 || { log "缺少命令：cmp"; exit 2; }
fi

validate_relation_name() {
  [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$ ]]
}

IFS=',' read -r -a relations <<< "$EXPECTED_RELATIONS"
for relation in "${relations[@]}" "$AGENT_VIEW"; do
  relation="${relation//[[:space:]]/}"
  validate_relation_name "$relation" || { log "非法 relation 名称：$relation"; exit 2; }
done

if [ -n "$CHECKSUM_MANIFEST" ]; then
  log "验证备份校验清单"
  (cd "$(dirname "$CHECKSUM_MANIFEST")" && sha256sum --check "$(basename "$CHECKSUM_MANIFEST")")
fi

postgres_url_for_database() {
  SIQ_POSTGRES_CONNECTION_URL="$1" SIQ_POSTGRES_DATABASE="$2" python3 - <<'PY'
import os
from urllib.parse import quote, urlsplit, urlunsplit

source = os.environ["SIQ_POSTGRES_CONNECTION_URL"]
database = os.environ["SIQ_POSTGRES_DATABASE"]
parsed = urlsplit(source)
if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
    raise SystemExit("SIQ_RESTORE_SMOKE_ADMIN_URL must be a PostgreSQL URL")
scheme = "postgresql" if parsed.scheme.startswith("postgresql+") else parsed.scheme
print(urlunsplit((scheme, parsed.netloc, "/" + quote(database, safe=""), parsed.query, "")))
PY
}

TEMP_DATABASE="siq_restore_smoke_$(date +%Y%m%d%H%M%S)_$$"
TARGET_URL="$(postgres_url_for_database "$ADMIN_URL" "$TEMP_DATABASE")"
TEMP_DATABASE_OWNED=0
SCHEMA_DATABASE=""
SCHEMA_TARGET_URL=""
SCHEMA_DATABASE_OWNED=0

cleanup() {
  if [ "$SCHEMA_DATABASE_OWNED" = "1" ]; then
    log "删除 schema 校验数据库：$SCHEMA_DATABASE"
    PGDATABASE="$ADMIN_URL" dropdb --if-exists "$SCHEMA_DATABASE" >/dev/null
  fi
  if [ "$TEMP_DATABASE_OWNED" = "1" ]; then
    log "删除临时数据库：$TEMP_DATABASE"
    PGDATABASE="$ADMIN_URL" dropdb --if-exists "$TEMP_DATABASE" >/dev/null
  fi
  if [ -n "${SCHEMA_COMPARE_DIR:-}" ]; then
    rm -rf "$SCHEMA_COMPARE_DIR"
  fi
}
trap cleanup EXIT

log "创建临时数据库：$TEMP_DATABASE"
PGDATABASE="$ADMIN_URL" createdb "$TEMP_DATABASE"
TEMP_DATABASE_OWNED=1
log "恢复逻辑导出：$SOURCE"
case "$SOURCE" in
  *.sql.gz) gzip -dc "$SOURCE" | PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 >/dev/null ;;
  *.sql) PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 -f "$SOURCE" >/dev/null ;;
  *) log "仅支持 .sql 或 .sql.gz 恢复源"; exit 2 ;;
esac

normalize_schema_dump() {
  sed -E \
    -e '/^-- Dumped from database version /d' \
    -e '/^-- Dumped by pg_dump version /d' \
    -e '/^\\(un)?restrict [^[:space:]]+$/d'
}

MIGRATION_COMPATIBILITY_VALIDATED=0
if [ -n "$EXPECTED_SCHEMA_SNAPSHOT" ]; then
  log "restore_phase=schema_snapshot status=started"
  SCHEMA_COMPARE_DIR="$(mktemp -d)"
  SCHEMA_DATABASE="${TEMP_DATABASE}_schema"
  SCHEMA_TARGET_URL="$(postgres_url_for_database "$ADMIN_URL" "$SCHEMA_DATABASE")"
  log "创建 schema 校验数据库：$SCHEMA_DATABASE"
  PGDATABASE="$ADMIN_URL" createdb "$SCHEMA_DATABASE"
  SCHEMA_DATABASE_OWNED=1
  case "$EXPECTED_SCHEMA_SNAPSHOT" in
    *.sql.gz) gzip -dc "$EXPECTED_SCHEMA_SNAPSHOT" | PGDATABASE="$SCHEMA_TARGET_URL" psql -v ON_ERROR_STOP=1 >/dev/null ;;
    *.sql) PGDATABASE="$SCHEMA_TARGET_URL" psql -v ON_ERROR_STOP=1 -f "$EXPECTED_SCHEMA_SNAPSHOT" >/dev/null ;;
    *) log "schema 快照仅支持 .sql 或 .sql.gz"; exit 2 ;;
  esac
  # PostgreSQL may rewrite equivalent CHECK expressions while restoring them,
  # and a no-op public schema stanza is not guaranteed to survive a round trip.
  # Redump both databases so the comparison is semantic at PostgreSQL's own
  # canonical DDL boundary instead of comparing raw pg_dump formatting.
  PGDATABASE="$SCHEMA_TARGET_URL" pg_dump --schema-only --no-owner --no-privileges \
    | normalize_schema_dump > "$SCHEMA_COMPARE_DIR/expected.sql"
  PGDATABASE="$TARGET_URL" pg_dump --schema-only --no-owner --no-privileges \
    | normalize_schema_dump > "$SCHEMA_COMPARE_DIR/current.sql"
  if ! cmp -s "$SCHEMA_COMPARE_DIR/expected.sql" "$SCHEMA_COMPARE_DIR/current.sql"; then
    log "restore_phase=schema_snapshot status=failed"
    log "恢复后的 schema 版本与备份快照不一致"
    exit 1
  fi
  log "restore_phase=schema_snapshot status=passed"
  if [ "$AUTHORITY_CHAIN_CONFIGURED" = "1" ]; then
    log "在隔离 schema 数据库中应用完整 authority 链"
    log "restore_phase=migration_compatibility status=started"
    for migration in "${COMPATIBILITY_MIGRATION_FILES[@]}"; do
      if ! PGDATABASE="$SCHEMA_TARGET_URL" psql -v ON_ERROR_STOP=1 -f "$migration" >/dev/null; then
        log "restore_phase=migration_compatibility status=failed"
        log "backup_schema_authority_apply_failed"
        exit 1
      fi
    done
    PGDATABASE="$SCHEMA_TARGET_URL" pg_dump --schema-only --no-owner --no-privileges \
      | normalize_schema_dump > "$SCHEMA_COMPARE_DIR/authority.sql"
    if ! cmp -s "$SCHEMA_COMPARE_DIR/authority.sql" "$SCHEMA_COMPARE_DIR/current.sql"; then
      log "restore_phase=migration_compatibility status=failed"
      log "backup_schema_behind_authority"
      exit 1
    fi
    log "restore_phase=migration_compatibility status=passed"
    MIGRATION_COMPATIBILITY_VALIDATED=1
  fi
fi

if [ "${#COMPATIBILITY_MIGRATION_FILES[@]}" -gt 0 ] && [ "$MIGRATION_COMPATIBILITY_VALIDATED" = "0" ]; then
  log "事务性验证当前 schema/migration 兼容性"
  log "restore_phase=migration_compatibility status=started"
  if ! {
    printf 'BEGIN;\n'
    for migration in "${COMPATIBILITY_MIGRATION_FILES[@]}"; do
      cat "$migration"
      printf '\n'
    done
    printf 'ROLLBACK;\n'
  } | PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 >/dev/null; then
    log "restore_phase=migration_compatibility status=failed"
    exit 1
  fi
  log "restore_phase=migration_compatibility status=passed"
fi

for relation in "${relations[@]}"; do
  relation="${relation//[[:space:]]/}"
  exists="$(PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 -Atqc "select to_regclass('$relation') is not null")"
  [ "$exists" = "t" ] || { log "缺少关键 relation：$relation"; exit 1; }
done

probe_kind="$(PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 -Atqc "select relkind from pg_class where oid = to_regclass('$AGENT_VIEW')")"
if [[ "$REQUIRE_AGENT_VIEW" =~ ^(1|true|yes|on)$ ]]; then
  case "$probe_kind" in
    v|m) ;;
    *) log "Agent view 不存在或类型错误：$AGENT_VIEW"; exit 1 ;;
  esac
else
  case "$probe_kind" in
    r|p|v|m|f) ;;
    *) log "恢复探针 relation 不存在或类型错误：$AGENT_VIEW"; exit 1 ;;
  esac
fi

if [ -z "$NONEMPTY_RELATIONS" ] && [ "$REQUIRED_MODE" = "1" ] && [ -z "$NONEMPTY_RELATIONS_CONFIGURED" ]; then
  # The default release probe is deliberately the Agent view itself. Markets
  # whose canonical Agent view is sparse can provide a dataful statement/view
  # through SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS.
  NONEMPTY_RELATIONS="$AGENT_VIEW"
fi
if [ -n "$NONEMPTY_RELATIONS" ]; then
  IFS=',' read -r -a nonempty_relations <<< "$NONEMPTY_RELATIONS"
  for relation in "${nonempty_relations[@]}"; do
    relation="${relation//[[:space:]]/}"
    validate_relation_name "$relation" || { log "非法非空探针 relation 名称：$relation"; exit 2; }
    count="$(PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 -Atqc "select count(*) from $relation")"
    [[ "$count" =~ ^[0-9]+$ ]] || { log "非空探针返回非法计数：$relation ($count)"; exit 1; }
    [ "$count" -gt 0 ] || { log "非空探针失败：$relation 为空"; exit 1; }
  done
else
  PGDATABASE="$TARGET_URL" psql -v ON_ERROR_STOP=1 -qc "select * from $AGENT_VIEW limit 1" >/dev/null
fi

if [[ "$VOICEPRINT_TOMBSTONE_REQUIRED" =~ ^(1|true|yes|on)$ ]]; then
  API_ROOT="$REPO_ROOT/apps/api"
  RECONCILER="$API_ROOT/scripts/reconcile_meeting_voiceprint_tombstones.py"
  [ -f "$RECONCILER" ] || { log "缺少声纹 tombstone 恢复验收工具"; exit 2; }
  APP_TARGET_URL="${TARGET_URL/#postgresql:\/\//postgresql+psycopg:\/\/}"
  API_PYTHON="${SIQ_API_PYTHON:-$API_ROOT/.venv/bin/python}"
  log "重放并验证域外声纹删除 tombstone"
  log "restore_phase=voiceprint_tombstone status=started"
  voiceprint_status=0
  if [ -x "$API_PYTHON" ]; then
    SIQ_APP_DATABASE_URL="$APP_TARGET_URL" "$API_PYTHON" "$RECONCILER" \
      --apply --require-ledger-file --require-ledger-checkpoint || voiceprint_status=$?
  else
    if ! command -v uv >/dev/null 2>&1; then
      log "缺少 API Python 环境或 uv"
      voiceprint_status=2
    else
      (
        cd "$API_ROOT"
        SIQ_APP_DATABASE_URL="$APP_TARGET_URL" uv run --frozen python \
          scripts/reconcile_meeting_voiceprint_tombstones.py \
          --apply --require-ledger-file --require-ledger-checkpoint
      ) || voiceprint_status=$?
    fi
  fi
  if [ "$voiceprint_status" -ne 0 ]; then
    log "restore_phase=voiceprint_tombstone status=failed"
    exit "$voiceprint_status"
  fi
  log "restore_phase=voiceprint_tombstone status=passed"
fi

log "恢复冒烟通过：关键 relation、校验清单和非空探针均通过"
