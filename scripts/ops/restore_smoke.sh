#!/usr/bin/env bash
# Restore one logical PostgreSQL dump into an automatically-created disposable
# database, verify required relations, then remove the database.

set -euo pipefail

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

if [ "${SIQ_RESTORE_SMOKE:-0}" != "1" ]; then
  log "跳过恢复冒烟：设置 SIQ_RESTORE_SMOKE=1 后才会创建临时数据库"
  exit 0
fi

SOURCE="${SIQ_RESTORE_SMOKE_SOURCE:-}"
ADMIN_URL="${SIQ_RESTORE_SMOKE_ADMIN_URL:-}"
EXPECTED_RELATIONS="${SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS:-}"
AGENT_VIEW="${SIQ_RESTORE_SMOKE_AGENT_VIEW:-}"
CHECKSUM_MANIFEST="${SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST:-}"

if [ -z "$SOURCE" ] || [ -z "$ADMIN_URL" ] || [ -z "$EXPECTED_RELATIONS" ] || [ -z "$AGENT_VIEW" ]; then
  log "恢复冒烟配置不完整：必须设置 SOURCE、ADMIN_URL、EXPECTED_RELATIONS 和 AGENT_VIEW"
  exit 2
fi
if [ ! -f "$SOURCE" ]; then
  log "恢复源不存在：$SOURCE"
  exit 2
fi
for command in createdb dropdb psql; do
  command -v "$command" >/dev/null 2>&1 || { log "缺少命令：$command"; exit 2; }
done

validate_relation_name() {
  [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$ ]]
}

IFS=',' read -r -a relations <<< "$EXPECTED_RELATIONS"
for relation in "${relations[@]}" "$AGENT_VIEW"; do
  relation="${relation//[[:space:]]/}"
  validate_relation_name "$relation" || { log "非法 relation 名称：$relation"; exit 2; }
done

if [ -n "$CHECKSUM_MANIFEST" ]; then
  [ -f "$CHECKSUM_MANIFEST" ] || { log "校验清单不存在：$CHECKSUM_MANIFEST"; exit 2; }
  log "验证备份校验清单"
  (cd "$(dirname "$CHECKSUM_MANIFEST")" && sha256sum --check "$(basename "$CHECKSUM_MANIFEST")")
fi

TEMP_DATABASE="siq_restore_smoke_$(date +%Y%m%d%H%M%S)_$$"
TARGET_URL="$(python3 - "$ADMIN_URL" "$TEMP_DATABASE" <<'PY'
import sys
from urllib.parse import quote, urlsplit, urlunsplit

source, database = sys.argv[1:3]
parsed = urlsplit(source)
if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
    raise SystemExit("SIQ_RESTORE_SMOKE_ADMIN_URL must be a PostgreSQL URL")
scheme = "postgresql" if parsed.scheme.startswith("postgresql+") else parsed.scheme
print(urlunsplit((scheme, parsed.netloc, "/" + quote(database, safe=""), parsed.query, "")))
PY
)"

cleanup() {
  log "删除临时数据库：$TEMP_DATABASE"
  dropdb --if-exists --maintenance-db "$ADMIN_URL" "$TEMP_DATABASE" >/dev/null
}
trap cleanup EXIT

log "创建临时数据库：$TEMP_DATABASE"
createdb --maintenance-db "$ADMIN_URL" "$TEMP_DATABASE"
log "恢复逻辑导出：$SOURCE"
case "$SOURCE" in
  *.sql.gz) gzip -dc "$SOURCE" | psql "$TARGET_URL" -v ON_ERROR_STOP=1 >/dev/null ;;
  *.sql) psql "$TARGET_URL" -v ON_ERROR_STOP=1 -f "$SOURCE" >/dev/null ;;
  *) log "仅支持 .sql 或 .sql.gz 恢复源"; exit 2 ;;
esac

for relation in "${relations[@]}"; do
  relation="${relation//[[:space:]]/}"
  resolved="$(psql "$TARGET_URL" -v ON_ERROR_STOP=1 -Atqc "select to_regclass('$relation')")"
  [ "$resolved" = "$relation" ] || { log "缺少关键 relation：$relation"; exit 1; }
done

view_kind="$(psql "$TARGET_URL" -v ON_ERROR_STOP=1 -Atqc "select relkind from pg_class where oid = to_regclass('$AGENT_VIEW')")"
case "$view_kind" in
  v|m) ;;
  *) log "Agent view 不存在或类型错误：$AGENT_VIEW"; exit 1 ;;
esac
psql "$TARGET_URL" -v ON_ERROR_STOP=1 -qc "select * from $AGENT_VIEW limit 1" >/dev/null

log "恢复冒烟通过：关键 relation 和 Agent view 均可查询"
