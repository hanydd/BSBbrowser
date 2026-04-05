#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Same-host sync: read-only dump from the primary DB(s), full replace of mirror DB(s).
# - Never drops or writes to SOURCE_* databases (only pg_dump reads).
# - TARGET_* databases are dropped and recreated — must not match SOURCE_*.
#
# Cron (hourly): 0 * * * * /path/to/sync_same_server.sh >>/var/log/sponsorblock_sync.log 2>&1
#
set -euo pipefail

# ========== CONFIGURATION ==========
# Primary (authoritative) databases — read-only via pg_dump.
SOURCE_DB="${SOURCE_DB:-sponsorTimes}"
SOURCE_PRIVATE_DB="${SOURCE_PRIVATE_DB:-privateDB}"

# Mirror databases for SBbrowser / reporting — must differ from sources on the same cluster.
TARGET_DB="${TARGET_DB:-sponsorblock}"
TARGET_PRIVATE_DB="${TARGET_PRIVATE_DB:-private_mirror}"

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-postgres}"

# Set to 1 to also sync privateDB -> TARGET_PRIVATE_DB (same safety rules).
SYNC_PRIVATE_DB="${SYNC_PRIVATE_DB:-0}"

# Working directory for dump files (same host; no scp).
WORKDIR="${WORKDIR:-/tmp/sponsorblock_sync}"
# Exclude large/unneeded table (same as export_to_file.sh).
EXCLUDE_VIDEOINFO=(--exclude-table=videoInfo)

# ========== SAFETY ==========
require_distinct() {
  local a="$1" b="$2" msg="$3"
  if [[ "$a" == "$b" ]]; then
    echo "Refusing to run: $msg ('$a' and '$b' must differ on the same server)." >&2
    exit 1
  fi
}

require_distinct "$SOURCE_DB" "$TARGET_DB" "SOURCE_DB and TARGET_DB"
if [[ "$SYNC_PRIVATE_DB" == "1" ]]; then
  require_distinct "$SOURCE_PRIVATE_DB" "$TARGET_PRIVATE_DB" "SOURCE_PRIVATE_DB and TARGET_PRIVATE_DB"
  require_distinct "$SOURCE_DB" "$TARGET_PRIVATE_DB" "SOURCE_DB and TARGET_PRIVATE_DB"
  require_distinct "$TARGET_DB" "$SOURCE_PRIVATE_DB" "TARGET_DB and SOURCE_PRIVATE_DB"
fi

psql_base=(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -v ON_ERROR_STOP=1)
pg_dump_base=(pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER")

terminate_connections() {
  local dbname="$1"
  "${psql_base[@]}" -d postgres -tAc \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$dbname' AND pid <> pg_backend_pid();" \
    >/dev/null 2>&1 || true
}

drop_recreate_restore() {
  local target_db="$1"
  local dump_file="$2"

  echo "Replacing mirror database: $target_db"
  terminate_connections "$target_db"
  dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --if-exists "$target_db"
  createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$target_db"
  "${psql_base[@]}" -d "$target_db" -f "$dump_file"
}

append_config_updated() {
  local sql_file="$1"
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  {
    echo ""
    echo "-- sync_same_server: refresh config.updated"
    echo "INSERT INTO public.config AS c (key, value) VALUES ('updated', '$ts')"
    echo "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;"
  } >>"$sql_file"
}

mkdir -p "$WORKDIR"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
MAIN_DUMP="$WORKDIR/sponsorTimes_full_${STAMP}.sql"

echo "========================================="
echo "Same-server sync (read-only on primary)"
echo "SOURCE: $SOURCE_DB -> TARGET: $TARGET_DB"
if [[ "$SYNC_PRIVATE_DB" == "1" ]]; then
  echo "SOURCE: $SOURCE_PRIVATE_DB -> TARGET: $TARGET_PRIVATE_DB"
fi
echo "Host: $PGHOST:$PGPORT user: $PGUSER"
echo "========================================="

echo "Checking read access to primary database '$SOURCE_DB'..."
"${psql_base[@]}" -d "$SOURCE_DB" -c "SELECT 1;" >/dev/null

echo "Dumping primary (read-only, full schema + data, excluding videoInfo)..."
"${pg_dump_base[@]}" \
  --no-owner \
  --no-privileges \
  "${EXCLUDE_VIDEOINFO[@]}" \
  "$SOURCE_DB" >"$MAIN_DUMP"

append_config_updated "$MAIN_DUMP"
drop_recreate_restore "$TARGET_DB" "$MAIN_DUMP"

if [[ "$SYNC_PRIVATE_DB" == "1" ]]; then
  echo "Checking read access to primary database '$SOURCE_PRIVATE_DB'..."
  "${psql_base[@]}" -d "$SOURCE_PRIVATE_DB" -c "SELECT 1;" >/dev/null

  PRIV_DUMP="$WORKDIR/privateDB_full_${STAMP}.sql"
  echo "Dumping private primary (read-only)..."
  "${pg_dump_base[@]}" \
    --no-owner \
    --no-privileges \
    "$SOURCE_PRIVATE_DB" >"$PRIV_DUMP"

  drop_recreate_restore "$TARGET_PRIVATE_DB" "$PRIV_DUMP"
  rm -f "$PRIV_DUMP"
fi

rm -f "$MAIN_DUMP"

echo "Done. Mirror(s) updated; primary database(s) were not written or dropped."
unset PGPASSWORD
