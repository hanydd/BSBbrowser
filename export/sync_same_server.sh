#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Same-host sync: read-only dump from the primary DB(s), staged replacement of mirror DB(s).
# - Never drops or writes to SOURCE_* databases (only pg_dump reads).
# - Restores and validates a staging database before a short rename-based cutover.
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
LOCKFILE="${LOCKFILE:-$WORKDIR/sync.lock}"
# Exclude large/unneeded table (same as export_to_file.sh).
EXCLUDE_TABLES=(--exclude-table=videoInfo --exclude-table='public."topUser"')
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPORTING_VIEWS_SQL="$SCRIPT_DIR/reporting_views.sql"

# ========== SAFETY ==========
require_distinct() {
  local a="$1" b="$2" msg="$3"
  if [[ "$a" == "$b" ]]; then
    echo "Refusing to run: $msg ('$a' and '$b' must differ on the same server)." >&2
    exit 1
  fi
}

validate_database_name() {
  local dbname="$1"
  if [[ ! "$dbname" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "Refusing to run: unsafe database name '$dbname'." >&2
    exit 1
  fi
}

validate_database_name "$SOURCE_DB"
validate_database_name "$SOURCE_PRIVATE_DB"
validate_database_name "$TARGET_DB"
validate_database_name "$TARGET_PRIVATE_DB"
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

database_exists() {
  local dbname="$1"
  [[ "$("${psql_base[@]}" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$dbname';")" == "1" ]]
}

restore_and_swap() {
  local target_db="$1"
  local dump_file="$2"
  local staging_db="${target_db}_sync"
  local old_db="${target_db}_old"

  require_distinct "$target_db" "$staging_db" "target and staging database"
  require_distinct "$target_db" "$old_db" "target and old database"

  echo "Restoring staging database: $staging_db"
  terminate_connections "$staging_db"
  dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --if-exists "$staging_db"
  createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$staging_db"
  "${psql_base[@]}" -d "$staging_db" -f "$dump_file"

  if [[ "$target_db" == "$TARGET_DB" ]]; then
    echo "Creating browser reporting views..."
    "${psql_base[@]}" -d "$staging_db" -f "$REPORTING_VIEWS_SQL"
  fi

  echo "Validating staging database..."
  "${psql_base[@]}" -d "$staging_db" -tAc \
    "SELECT 1 FROM public.config WHERE key = 'updated' AND value <> '';" | grep -qx 1
  "${psql_base[@]}" -d "$staging_db" -tAc \
    "SELECT 1 WHERE to_regclass('public.\"sponsorTimes\"') IS NOT NULL;" | grep -qx 1

  echo "Switching mirror database: $target_db"
  terminate_connections "$old_db"
  dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --if-exists "$old_db"

  if database_exists "$target_db"; then
    "${psql_base[@]}" -d postgres -c \
      "ALTER DATABASE \"$target_db\" WITH ALLOW_CONNECTIONS false;"
    terminate_connections "$target_db"
    "${psql_base[@]}" -d postgres -c \
      "ALTER DATABASE \"$target_db\" RENAME TO \"$old_db\";"
  fi

  if ! "${psql_base[@]}" -d postgres -c \
    "ALTER DATABASE \"$staging_db\" RENAME TO \"$target_db\";"; then
    echo "Cutover failed; restoring previous mirror database." >&2
    if database_exists "$old_db"; then
      "${psql_base[@]}" -d postgres -c \
        "ALTER DATABASE \"$old_db\" RENAME TO \"$target_db\";"
      "${psql_base[@]}" -d postgres -c \
        "ALTER DATABASE \"$target_db\" WITH ALLOW_CONNECTIONS true;"
    fi
    return 1
  fi

  terminate_connections "$old_db"
  dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --if-exists "$old_db"
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

[[ -r "$REPORTING_VIEWS_SQL" ]] || { echo "Missing reporting views migration: $REPORTING_VIEWS_SQL" >&2; exit 1; }

mkdir -p "$WORKDIR"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "Another sync is already running; exiting."
  exit 0
fi

STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
MAIN_DUMP="$WORKDIR/sponsorTimes_full_${STAMP}.sql"
PRIV_DUMP=""

cleanup() {
  rm -f "$MAIN_DUMP"
  if [[ -n "$PRIV_DUMP" ]]; then
    rm -f "$PRIV_DUMP"
  fi
  unset PGPASSWORD
}
trap cleanup EXIT

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

echo "Dumping primary (read-only, full schema + data, excluding videoInfo and topUser)..."
"${pg_dump_base[@]}" \
  --no-owner \
  --no-privileges \
  "${EXCLUDE_TABLES[@]}" \
  "$SOURCE_DB" >"$MAIN_DUMP"

append_config_updated "$MAIN_DUMP"
restore_and_swap "$TARGET_DB" "$MAIN_DUMP"

if [[ "$SYNC_PRIVATE_DB" == "1" ]]; then
  echo "Checking read access to primary database '$SOURCE_PRIVATE_DB'..."
  "${psql_base[@]}" -d "$SOURCE_PRIVATE_DB" -c "SELECT 1;" >/dev/null

  PRIV_DUMP="$WORKDIR/privateDB_full_${STAMP}.sql"
  echo "Dumping private primary (read-only)..."
  "${pg_dump_base[@]}" \
    --no-owner \
    --no-privileges \
    "$SOURCE_PRIVATE_DB" >"$PRIV_DUMP"

  restore_and_swap "$TARGET_PRIVATE_DB" "$PRIV_DUMP"
fi

echo "Done. Mirror(s) updated; primary database(s) were not written or dropped."
