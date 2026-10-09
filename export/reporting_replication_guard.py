#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Limit new reporting replication processes and bound retained WAL."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

from prepare_reporting_replication import DATABASES, STATE, sql, stop_new_replication, write_file


def resources():
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return int(memory['MemAvailable'].split()[0]) // 1024, shutil.disk_usage('/').free // (1024**2)


def abandon_slots():
    # Preserve databases and the current Browser service. Only remove our own slots.
    stop_new_replication()
    suboids = [sql(f"SELECT oid FROM pg_subscription WHERE subname='{db}_sub'", db) for db in DATABASES]
    for _ in range(20):
        active = sql("SELECT count(*) FROM pg_stat_activity WHERE "
                     "backend_type IN ('logical replication worker','walsender') AND "
                     "(datname IN ('bsb_reporting_a','bsb_reporting_b') OR usename='bsb_reporting_repl')")
        if active == '0':
            break
        time.sleep(0.5)
    for db in DATABASES:
        sql(f'ALTER SUBSCRIPTION {db}_sub SET (slot_name=NONE);', db)
    prefixes = [f'pg_{oid}_sync_%' for oid in suboids]
    where = "slot_name IN ('bsb_reporting_a_slot','bsb_reporting_b_slot') OR " + \
            ' OR '.join("slot_name LIKE '" + p + "'" for p in prefixes)
    sql('SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE NOT active AND (' + where + ');')
    write_file(STATE / 'guard-abandoned.json', json.dumps({'at': time.time(),
               'reason': 'WAL or disk budget exceeded; new replicas require reinitialization'}))


def main():
    bad = 0
    maximum_ready = 0.0
    minimum_memory = 999999
    limited = set()
    while True:
        available, free = resources()
        minimum_memory = min(minimum_memory, available)
        rows = json.loads(sql("SELECT coalesce(json_agg(json_build_object('pid',pid,'kind',backend_type)),"
                              "'[]'::json) FROM pg_stat_activity WHERE "
                              "(datname IN ('bsb_reporting_a','bsb_reporting_b') AND "
                              "(backend_type='logical replication worker' OR "
                              "application_name='bsb_reporting_bootstrap')) OR usename='bsb_reporting_repl';"))
        for row in rows:
            pid = row['pid']
            if pid in limited:
                continue
            try:
                os.setpriority(os.PRIO_PROCESS, pid, 19)
                os.sched_setaffinity(pid, {max(os.sched_getaffinity(0))})
                subprocess.run(['ionice', '-c', '3', '-p', str(pid)], capture_output=True, check=False)
                limited.add(pid)
            except ProcessLookupError:
                pass
        wal = int(sql("SELECT coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn)),0)::bigint "
                      "FROM pg_replication_slots WHERE slot_name IN "
                      "('bsb_reporting_a_slot','bsb_reporting_b_slot');"))
        if wal > 2 * 1024**3 or free < 3072:
            abandon_slots()
            raise RuntimeError('Reporting slots released to protect primary disk space')
        started = time.monotonic()
        try:
            with urllib.request.urlopen('http://127.0.0.1:9876/api/ready', timeout=1.5) as response:
                healthy = response.status == 200
        except Exception:
            healthy = False
        ready_seconds = time.monotonic() - started
        maximum_ready = max(maximum_ready, ready_seconds)
        bad = bad + 1 if not healthy or ready_seconds > 0.8 or available < 768 else 0
        if bad >= 2 and rows:
            stop_new_replication()
            write_file(STATE / 'guard-paused.json', json.dumps({'at': time.time(),
                       'reason': 'Primary health or memory threshold exceeded'}))
            bad = 0
            print('Paused new reporting replication to protect primary health', flush=True)
        write_file(STATE / 'guard-health.json', json.dumps({'at': time.time(),
                   'ready_seconds': round(ready_seconds, 4), 'maximum_ready_seconds': round(maximum_ready, 4),
                   'available_mb': available, 'minimum_available_mb': minimum_memory,
                   'free_disk_mb': free, 'max_slot_wal_bytes': wal, 'limited_processes': len(limited)}))
        time.sleep(5 if (STATE / 'bootstrap-complete.json').exists() else 2)


if __name__ == '__main__':
    main()
