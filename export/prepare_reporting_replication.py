#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Provision staged reporting databases. Run as root on the database host."""
import argparse
import datetime
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess
import time

ROOT = Path('/etc/bsb-reporting')
STATE = Path('/var/lib/bsb-reporting')
DATABASES = ('bsb_reporting_a', 'bsb_reporting_b')
PUBLICATION = 'bsb_reporting_publication'
REPL_USER = 'bsb_reporting_repl'
READER = 'bsb_reporting_reader'


def write_file(path, content, mode=0o600, postgres=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    os.chmod(temporary, mode)
    if postgres:
        user = pwd.getpwnam('postgres')
        os.chown(temporary, user.pw_uid, user.pw_gid)
    os.replace(temporary, path)


def load_credentials():
    return json.loads((ROOT / 'credentials.json').read_text())


def sql(query, database='postgres', timeout=20):
    credentials = load_credentials()
    env = dict(os.environ, PGPASSWORD=credentials['admin_password'],
               PGAPPNAME='bsb_reporting_bootstrap',
               PGOPTIONS='-c lock_timeout=2000 -c statement_timeout=15000')
    result = subprocess.run(['psql', '-X', '-qAt', '-h', '127.0.0.1', '-U', 'postgres',
                             '-d', database, '-v', 'ON_ERROR_STOP=1'],
                            input=query, text=True, capture_output=True, env=env, timeout=timeout)
    if result.returncode:
        # CREATE SUBSCRIPTION and role statements must never be logged verbatim.
        raise RuntimeError('PostgreSQL operation failed: ' +
                           re.sub(r'(?i)password[^\n]*', '[redacted]', result.stderr.splitlines()[0]))
    return result.stdout.strip()


def configure():
    if (STATE / 'configured.json').exists():
        raise RuntimeError('Already configured; use bootstrap or inspect the existing state')
    if not (ROOT / 'credentials.json').exists():
        legacy = Path('/home/ecs/project/BSBbrowser/export/sync_same_server.sh').read_text()
        match = re.search(r'PGPASSWORD:-([^}]+)', legacy)
        if not match:
            raise RuntimeError('Legacy admin credential was not found')
        write_file(ROOT / 'credentials.json', json.dumps({
            'admin_password': match.group(1),
            'replication_password': secrets.token_hex(32),
            'reader_password': secrets.token_hex(32),
            'pool_admin_password': secrets.token_hex(32),
        }))
    credentials = load_credentials()
    user = pwd.getpwnam('postgres')
    os.chown(ROOT, 0, user.pw_gid)
    os.chmod(ROOT, 0o750)
    if sql("SELECT count(*) FROM pg_database WHERE datname IN ('bsb_reporting_a','bsb_reporting_b')") != '0':
        raise RuntimeError('Reporting databases already exist; refuse to overwrite')
    tables = sql("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                 "WHERE n.nspname='public' AND c.relkind='r' AND c.relname <> 'videoInfo' "
                 "ORDER BY c.relname", 'sponsorTimes').splitlines()
    if not tables or 'sponsorTimes' not in tables:
        raise RuntimeError('Public source tables were not found')
    table_sql = ','.join('public."' + name.replace('"', '""') + '"' for name in tables)
    sql(f"CREATE ROLE {REPL_USER} LOGIN REPLICATION CONNECTION LIMIT 3 PASSWORD "
        f"'{credentials['replication_password']}'; "
        f"CREATE ROLE {READER} LOGIN CONNECTION LIMIT 20 PASSWORD '{credentials['reader_password']}'; "
        f"ALTER ROLE {REPL_USER} SET max_parallel_workers_per_gather=0; "
        f"ALTER ROLE {REPL_USER} SET work_mem='4MB'; "
        f"ALTER ROLE {REPL_USER} SET lock_timeout='2s'; "
        f"ALTER ROLE {READER} SET default_transaction_read_only=on; "
        f"ALTER ROLE {READER} SET statement_timeout='30s'; "
        f"GRANT CONNECT ON DATABASE \"sponsorTimes\" TO {REPL_USER};")
    sql(f'GRANT USAGE ON SCHEMA public TO {REPL_USER}; '
        f'GRANT SELECT ON {table_sql} TO {REPL_USER}; '
        f'CREATE PUBLICATION {PUBLICATION} FOR TABLE {table_sql};', 'sponsorTimes')
    # This setting is reloadable. Do not increase worker limits or restart PG.
    sql('ALTER SYSTEM SET max_sync_workers_per_subscription=1;')
    sql('SELECT pg_reload_conf();')
    write_file(ROOT / 'replication.pgpass',
               f"127.0.0.1:5432:sponsorTimes:{REPL_USER}:{credentials['replication_password']}\n",
               postgres=True)
    dump_env = dict(os.environ, PGPASSWORD=credentials['admin_password'])
    schema = subprocess.run([
        'pg_dump', '-h', '127.0.0.1', '-U', 'postgres', '--schema-only', '--schema=public',
        '--extension=pg_trgm', '--extension=pgcrypto', '--no-owner', '--no-privileges',
        '--no-publications', '--no-subscriptions', '--exclude-table=public."videoInfo"',
        '--exclude-table=public."topUser"', 'sponsorTimes'],
        env=dump_env, text=True, capture_output=True, timeout=30)
    if schema.returncode:
        raise RuntimeError('Schema export failed')
    for database in DATABASES:
        sql(f'CREATE DATABASE {database} CONNECTION LIMIT 20;')
        sql('DROP SCHEMA public;', database)
        restore_env = dict(dump_env, PGOPTIONS='-c lock_timeout=2000 -c max_parallel_workers_per_gather=0')
        restore = subprocess.run(['psql', '-X', '-q', '-h', '127.0.0.1', '-U', 'postgres',
                                  '-d', database, '-v', 'ON_ERROR_STOP=1'],
                                 input=schema.stdout, env=restore_env, capture_output=True, text=True, timeout=45)
        if restore.returncode:
            raise RuntimeError('Schema restore failed for ' + database)
        sql(f'GRANT CONNECT ON DATABASE {database} TO {READER}; '
            f'GRANT USAGE ON SCHEMA public TO {READER}; '
            f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}; '
            f'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
            f'GRANT SELECT ON TABLES TO {READER};', database)
        defer_indexes(database)
        connection = (f'host=127.0.0.1 port=5432 dbname=sponsorTimes user={REPL_USER} '
                      f'passfile={ROOT}/replication.pgpass application_name={database} connect_timeout=5')
        sql(f"CREATE SUBSCRIPTION {database}_sub CONNECTION '{connection}' "
            f"PUBLICATION {PUBLICATION} WITH (create_slot=false, enabled=false, copy_data=true, "
            f"slot_name='{database}_slot', streaming=off, disable_on_error=true);", database)
    pool_config = f'''[databases]
browser_data = host=127.0.0.1 port=5432 dbname=bsb_reporting_a
reporting_a = host=127.0.0.1 port=5432 dbname=bsb_reporting_a
reporting_b = host=127.0.0.1 port=5432 dbname=bsb_reporting_b

[pgbouncer]
listen_addr = 127.0.0.1
listen_port = 6432
unix_socket_dir = /var/run/postgresql
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/bsb-userlist.txt
admin_users = bsb_reporting_pool_admin
pool_mode = transaction
max_client_conn = 80
default_pool_size = 6
reserve_pool_size = 0
max_db_connections = 8
max_user_connections = 12
server_idle_timeout = 60
server_lifetime = 600
server_connect_timeout = 5
query_timeout = 30
query_wait_timeout = 15
logfile = /var/log/bsb-reporting/pgbouncer.log
pidfile = /var/run/postgresql/pgbouncer.pid
log_connections = 0
log_disconnections = 0
log_stats = 0
'''
    shutil.copy2('/etc/pgbouncer/pgbouncer.ini', STATE / 'pgbouncer-package-default.ini')
    write_file(Path('/etc/pgbouncer/pgbouncer.ini'), pool_config, 0o640, postgres=True)
    write_file(Path('/etc/pgbouncer/bsb-userlist.txt'),
               f'"{READER}" "{credentials["reader_password"]}"\n'
               f'"bsb_reporting_pool_admin" "{credentials["pool_admin_password"]}"\n',
               0o640, postgres=True)
    write_file(STATE / 'configured.json', json.dumps({'tables': tables, 'databases': DATABASES}))
    print(json.dumps({'configured': True, 'public_table_count': len(tables)}), flush=True)


def freeze(database):
    sql(f'ALTER SUBSCRIPTION {database}_sub DISABLE;', database)
    for _ in range(20):
        if sql(f"SELECT count(*) FROM pg_stat_subscription WHERE subname='{database}_sub' "
               'AND pid IS NOT NULL', database) == '0':
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
            sql("INSERT INTO public.config(key,value) VALUES ('updated','" + timestamp +
                "') ON CONFLICT(key) DO UPDATE SET value=excluded.value;", database)
            return timestamp
        time.sleep(0.5)
    raise RuntimeError('Replication worker did not stop for ' + database)


def defer_indexes(database):
    definitions = json.loads(sql("SELECT coalesce(json_agg(json_build_object('name',c.relname,"
                                  "'definition',pg_get_indexdef(i.indexrelid))),'[]'::json) "
                                  "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
                                  "JOIN pg_namespace n ON n.oid=c.relnamespace "
                                  "WHERE n.nspname='public' AND NOT i.indisprimary AND NOT i.indisreplident "
                                  "AND NOT EXISTS(SELECT 1 FROM pg_constraint p WHERE p.conindid=i.indexrelid);",
                                  database))
    write_file(STATE / (database + '-indexes.json'), json.dumps(definitions))
    for index in definitions:
        sql('DROP INDEX public."' + index['name'].replace('"', '""') + '";', database)


def reset_unpublished():
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'pgbouncer']).returncode == 0:
        raise RuntimeError('Refuse to reset databases while PgBouncer is active')
    if sql("SELECT count(*) FROM pg_stat_activity WHERE datname IN "
           "('bsb_reporting_a','bsb_reporting_b') AND backend_type='client backend'") != '0':
        raise RuntimeError('Refuse to reset databases with active client connections')
    stop_new_replication()
    if sql("SELECT count(*) FROM pg_subscription WHERE subname IN "
           "('bsb_reporting_a_sub','bsb_reporting_b_sub') AND subslotname IS NOT NULL") != '0':
        raise RuntimeError('Reset requires detached subscriptions after a guarded abort')
    env = dict(os.environ, PGPASSWORD=load_credentials()['admin_password'])
    schema = subprocess.run(['pg_dump', '-h', '127.0.0.1', '-U', 'postgres', '--schema-only',
                             '--schema=public', '--extension=pg_trgm', '--extension=pgcrypto',
                             '--no-owner', '--no-privileges', '--no-publications', '--no-subscriptions',
                             '--exclude-table=public."videoInfo"', '--exclude-table=public."topUser"',
                             'sponsorTimes'], env=env, text=True, capture_output=True, timeout=30)
    if schema.returncode:
        raise RuntimeError('Schema export failed')
    for database in DATABASES:
        sql(f'DROP SUBSCRIPTION {database}_sub; DROP SCHEMA public CASCADE;', database)
        result = subprocess.run(['psql', '-X', '-q', '-h', '127.0.0.1', '-U', 'postgres',
                                 '-d', database, '-v', 'ON_ERROR_STOP=1'],
                                input=schema.stdout, env=env, text=True, capture_output=True, timeout=45)
        if result.returncode:
            raise RuntimeError('Schema reset failed for ' + database)
        sql(f'GRANT USAGE ON SCHEMA public TO {READER}; '
            f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}; '
            f'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
            f'GRANT SELECT ON TABLES TO {READER};', database)
        defer_indexes(database)
        connection = (f'host=127.0.0.1 port=5432 dbname=sponsorTimes user={REPL_USER} '
                      f'passfile={ROOT}/replication.pgpass application_name={database} connect_timeout=5')
        sql(f"CREATE SUBSCRIPTION {database}_sub CONNECTION '{connection}' "
            f"PUBLICATION {PUBLICATION} WITH(create_slot=false,enabled=false,copy_data=true,"
            f"slot_name='{database}_slot',streaming=off,disable_on_error=true);", database)
    archive = STATE / ('previous-attempt-' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S'))
    archive.mkdir(mode=0o700)
    for name in ['bsb_reporting_a.json', 'bsb_reporting_b.json', 'bootstrap-complete.json',
                 'guard-abandoned.json', 'guard-paused.json']:
        path = STATE / name
        if path.exists():
            path.rename(archive / name)
    print('Unpublished replicas reset with secondary indexes deferred', flush=True)


def bootstrap():
    total_started = time.monotonic()
    for database in DATABASES:
        ready_path = STATE / (database + '.json')
        if ready_path.exists():
            continue
        while datetime.datetime.now().minute < 4 or datetime.datetime.now().minute >= 50:
            time.sleep(10)
        started = time.monotonic()
        # Precreate same-cluster slots separately, immediately before copying.
        if sql(f"SELECT count(*) FROM pg_replication_slots WHERE slot_name='{database}_slot'") == '0':
            sql(f"SELECT slot_name FROM pg_create_logical_replication_slot('{database}_slot','pgoutput');",
                'sponsorTimes')
        sql(f'ALTER SUBSCRIPTION {database}_sub ENABLE;', database)
        while time.monotonic() - started < 1200:
            if datetime.datetime.now().minute >= 56:
                raise RuntimeError('Stopping initial copy before the next hourly dump')
            rows = sql("SELECT json_build_object('tables',count(*),'ready',count(*) FILTER "
                       "(WHERE srsubstate='r')) FROM pg_subscription_rel;", database)
            progress = json.loads(rows)
            print(json.dumps({'database': database, **progress}), flush=True)
            if progress['tables'] and progress['ready'] == progress['tables']:
                timestamp = freeze(database)
                indexes_path = STATE / (database + '-indexes.json')
                if indexes_path.exists():
                    for index in json.loads(indexes_path.read_text()):
                        if datetime.datetime.now().minute >= 56:
                            raise RuntimeError('Stopping index creation before the next hourly dump')
                        print(json.dumps({'database': database, 'building_index': index['name']}), flush=True)
                        definition = index['definition'].replace('CREATE UNIQUE INDEX ',
                                      'CREATE UNIQUE INDEX IF NOT EXISTS ', 1) if index['definition'].startswith(
                                      'CREATE UNIQUE INDEX ') else index['definition'].replace(
                                      'CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ', 1)
                        sql('SET statement_timeout=120000; SET maintenance_work_mem=\'16MB\'; '
                            'SET max_parallel_maintenance_workers=0; ' + definition,
                            database, timeout=125)
                    indexes_path.rename(STATE / (database + '-indexes-complete.json'))
                sql('ANALYZE;', database, timeout=45)
                # Build local derived data only after replication has stopped.
                views = Path('/home/ecs/project/BSBbrowser/export/reporting_views.sql').read_text()
                sql('SET statement_timeout=30000; SET max_parallel_workers_per_gather=0; ' + views,
                    database, timeout=40)
                write_file(ready_path, json.dumps({'frozen_at': timestamp,
                                                 'initialization_seconds': round(time.monotonic()-started, 2)}))
                print(json.dumps({'database': database, 'ready': True, 'frozen_at': timestamp,
                                  'seconds': round(time.monotonic()-started, 2)}), flush=True)
                break
            if not sql(f"SELECT subenabled FROM pg_subscription WHERE subname='{database}_sub'", database) == 't':
                raise RuntimeError('Subscription disabled by an error or resource guard')
            time.sleep(5)
        else:
            raise RuntimeError('Initial replication exceeded the time budget')
    # A remains a frozen candidate; B receives updates in the background.
    sql('ALTER SUBSCRIPTION bsb_reporting_b_sub ENABLE;', 'bsb_reporting_b')
    write_file(STATE / 'bootstrap-complete.json', json.dumps({'active_candidate': DATABASES[0],
              'updating': DATABASES[1], 'seconds': round(time.monotonic()-total_started, 2)}))
    print('Bootstrap complete: A frozen, B updating; application has not been switched.', flush=True)


def stop_new_replication():
    for database in DATABASES:
        if sql(f"SELECT 1 FROM pg_database WHERE datname='{database}'") == '1':
            sql(f'ALTER SUBSCRIPTION {database}_sub DISABLE;', database)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['configure', 'bootstrap', 'freeze', 'stop',
                                         'stop-on-failure', 'reset-unpublished'])
    parser.add_argument('--database', choices=DATABASES)
    args = parser.parse_args()
    try:
        if args.action == 'configure':
            configure()
        elif args.action == 'bootstrap':
            bootstrap()
        elif args.action == 'freeze':
            print(freeze(args.database))
        elif args.action == 'reset-unpublished':
            reset_unpublished()
        elif args.action != 'stop-on-failure' or os.environ.get('SERVICE_RESULT') != 'success':
            stop_new_replication()
    except Exception as error:
        if args.action == 'bootstrap':
            stop_new_replication()
        raise SystemExit(str(error))
