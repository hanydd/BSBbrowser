#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rotate frozen snapshots through PgBouncer and optionally refresh statistics."""
import argparse
import csv
import datetime
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from prepare_reporting_replication import DATABASES, STATE, freeze, load_credentials, sql, write_file

POOL_CONFIG = Path('/etc/pgbouncer/pgbouncer.ini')
STATISTICS_TIMEOUT = 60

# The timeout runs inside the container: killing docker exec alone would leave
# its statistics process running after the host released the rotation lock.
STATISTICS_RUNNER = '''import subprocess, sys
try:
    result = subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2]],
                            timeout=int(sys.argv[3]))
except subprocess.TimeoutExpired:
    raise SystemExit('Statistics refresh exceeded its time budget')
raise SystemExit(result.returncode)
'''
STATISTICS_COMMAND = '''import os, sys
import django
django.setup()
from django.conf import settings
from django.db import connection
from django.core.management import call_command
database = settings.DATABASES['default']
if (database['NAME'] != 'browser_data' or str(database['PORT']) != '6432'
        or database['USER'] != 'bsb_reporting_reader'
        or not database.get('DISABLE_SERVER_SIDE_CURSORS')):
    raise RuntimeError('Browser is not configured for the snapshot pool')
with connection.cursor() as cursor:
    cursor.execute("SELECT current_database(), current_setting('transaction_read_only')")
    if cursor.fetchone() != (sys.argv[1], 'on'):
        raise RuntimeError('Statistics connection does not match the frozen snapshot')
os.nice(10)
call_command('refresh_stats')
'''


def refresh_statistics(database):
    """Call the existing command with the caller holding the rotation lock."""
    if current_database() != database:
        raise RuntimeError('Statistics target no longer matches the pool alias')
    if sql(f"SELECT subenabled FROM pg_subscription WHERE subname='{database}_sub';", database) != 'f':
        raise RuntimeError('Statistics target is not frozen')
    frozen = sql("SELECT value FROM public.config WHERE key='updated';", database)
    started = time.monotonic()
    state = {'database': database, 'source_updated_at': frozen,
             'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'status': 'running'}
    write_file(STATE / 'statistics-refresh.json', json.dumps(state))
    try:
        result = subprocess.run([
            'docker', 'exec', 'bsbbrowser-web-1', 'python', '-c', STATISTICS_RUNNER,
            STATISTICS_COMMAND, database, str(STATISTICS_TIMEOUT),
        ], capture_output=True, text=True, timeout=STATISTICS_TIMEOUT + 15)
        if result.stdout:
            print(result.stdout[-2000:].strip(), flush=True)
        if result.returncode:
            # Keep detailed command errors in the protected service journal.
            if result.stderr:
                print(result.stderr[-4000:].strip(), flush=True)
            raise RuntimeError('Statistics refresh failed; the published snapshot is unchanged')
        state['status'] = 'success'
    except Exception:
        state['status'] = 'failed'
        raise
    finally:
        state['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        state['seconds'] = round(time.monotonic() - started, 2)
        write_file(STATE / 'statistics-refresh.json', json.dumps(state))
        print(json.dumps({'statistics': state}), flush=True)


def pool_command(command):
    env = dict(os.environ, PGPASSWORD=load_credentials()['pool_admin_password'])
    result = subprocess.run(['psql', '-X', '-q', '--csv', '-h', '127.0.0.1', '-p', '6432',
                             '-U', 'bsb_reporting_pool_admin', '-d', 'pgbouncer',
                             '-v', 'ON_ERROR_STOP=1', '-c', command],
                            env=env, capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError('PgBouncer command failed: ' + command.split()[0])
    return result.stdout


def current_database():
    rows = csv.DictReader(io.StringIO(pool_command('SHOW DATABASES;')))
    for row in rows:
        if row['name'] == 'browser_data':
            database = row['database']
            if database in DATABASES:
                return database
    raise RuntimeError('Stable database alias was not found')


def rotate(refresh=False, refresh_only=False):
    if not (STATE / 'bootstrap-complete.json').exists():
        raise RuntimeError('Initial replication has not completed')
    with (STATE / 'rotation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with urllib.request.urlopen('http://127.0.0.1:9876/api/ready', timeout=1) as response:
            if response.status != 200:
                raise RuntimeError('Primary is not ready')
        old = current_database()
        if refresh_only:
            refresh_statistics(old)
            return
        incoming = next(database for database in DATABASES if database != old)
        if sql('SELECT count(*) FROM pg_subscription_rel WHERE srsubstate <> \'r\';', incoming) != '0':
            raise RuntimeError('Incoming database has incomplete initial tables')
        # Enable idempotently, then ensure a worker is running with fresh replication feedback.
        sql(f'ALTER SUBSCRIPTION {incoming}_sub ENABLE;', incoming)
        watermark = sql('SELECT pg_current_wal_lsn();', 'sponsorTimes')
        for _ in range(30):
            fresh = sql(f"SELECT count(*) FROM pg_stat_subscription WHERE subname='{incoming}_sub' "
                        "AND relid IS NULL AND pid IS NOT NULL "
                        "AND latest_end_time > now()-interval '10 seconds';", incoming)
            caught_up = sql(f"SELECT count(*) FROM pg_replication_slots WHERE slot_name='{incoming}_slot' "
                            f"AND confirmed_flush_lsn >= '{watermark}'::pg_lsn;")
            if fresh == '1' and caught_up == '1':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Incoming replication feedback is stale')
        original = POOL_CONFIG.read_text()
        paused = False
        published = False
        started = time.monotonic()
        try:
            frozen = freeze(incoming)
            views = Path('/home/ecs/project/BSBbrowser/export/reporting_views.sql').read_text()
            sql('SET statement_timeout=30000; SET max_parallel_workers_per_gather=0; ' + views,
                incoming, timeout=40)
            # Existing PgBouncer transactions finish before replacing its backend mapping.
            paused = True
            pool_command('PAUSE browser_data;')
            lines = original.splitlines()
            for index, line in enumerate(lines):
                if line.startswith('browser_data = '):
                    lines[index] = f'browser_data = host=127.0.0.1 port=5432 dbname={incoming}'
                    break
            else:
                raise RuntimeError('Stable alias is missing from the configuration')
            write_file(POOL_CONFIG, '\n'.join(lines) + '\n', 0o640, postgres=True)
            pool_command('RELOAD;')
            if current_database() != incoming:
                raise RuntimeError('PgBouncer did not activate the new backend')
            pool_command('RESUME browser_data;')
            paused = False
            published = True
            write_file(STATE / 'current-snapshot.json', json.dumps({'database': incoming,
                       'frozen_at': frozen, 'rotated_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}))
            sql(f'ALTER SUBSCRIPTION {old}_sub ENABLE;', old)
            print(json.dumps({'staged_database': incoming, 'frozen_at': frozen,
                              'updating': old, 'seconds': round(time.monotonic()-started, 2)}), flush=True)
        except Exception:
            if not published:
                write_file(POOL_CONFIG, original, 0o640, postgres=True)
                pool_command('RELOAD;')
                if paused:
                    pool_command('RESUME browser_data;')
                sql(f'ALTER SUBSCRIPTION {incoming}_sub ENABLE;', incoming)
            raise
        # Statistics failure must not roll back a database already serving
        # Browser queries or start another copy. Retry it with --refresh-only.
        if refresh:
            refresh_statistics(incoming)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--refresh-statistics', action='store_true',
                      help='Run the existing Browser statistics command after rotation')
    mode.add_argument('--refresh-only', action='store_true',
                      help='Refresh the current snapshot without rotating or recopying')
    arguments = parser.parse_args()
    rotate(refresh=arguments.refresh_statistics, refresh_only=arguments.refresh_only)
