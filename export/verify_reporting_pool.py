#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify a live backend rotation through one existing client connection."""
import json
import subprocess

from prepare_reporting_replication import load_credentials
from rotate_reporting_snapshot import rotate

CLIENT = '''
import json, sys, psycopg
credentials = json.loads(sys.stdin.readline())
connection = psycopg.connect(host='127.0.0.1', port=6432, dbname='browser_data',
                             user='bsb_reporting_reader', password=credentials['reader_password'],
                             connect_timeout=5)
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute("SELECT current_database(), current_setting('transaction_read_only'), "
                   "EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='private'), "
                   "(SELECT count(*) FROM public.\\\"sponsorTimes\\\")")
    print(json.dumps({'before': cursor.fetchone()}), flush=True)
    sys.stdin.readline()
    cursor.execute("SELECT current_database(), current_setting('transaction_read_only')")
    print(json.dumps({'after': cursor.fetchone()}), flush=True)
    try:
        cursor.execute("UPDATE public.config SET value=value WHERE false")
    except psycopg.Error as error:
        print(json.dumps({'write_rejected_sqlstate': error.sqlstate}), flush=True)
    else:
        raise RuntimeError('Read-only role unexpectedly accepted an UPDATE')
connection.close()
'''


if __name__ == '__main__':
    client = subprocess.Popen(['docker', 'exec', '-i', 'bsbbrowser-web-1', 'python', '-c', CLIENT],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
    try:
        client.stdin.write(json.dumps({'reader_password': load_credentials()['reader_password']}) + '\n')
        client.stdin.flush()
        before_line = client.stdout.readline()
        if not before_line:
            raise RuntimeError('Client could not connect through PgBouncer')
        before = json.loads(before_line)
        print(json.dumps(before), flush=True)
        assert before['before'][1:3] == ['on', False]
        rotate()
        client.stdin.write('continue\n')
        client.stdin.flush()
        after = json.loads(client.stdout.readline())
        print(json.dumps(after), flush=True)
        assert after['after'][0] != before['before'][0]
        write_result = json.loads(client.stdout.readline())
        print(json.dumps(write_result), flush=True)
        assert write_result['write_rejected_sqlstate'] in ('25006', '42501')
        if client.wait(timeout=10):
            raise RuntimeError('Client verification failed')
        print('Persistent connection survived rotation; public-only and read-only checks passed.', flush=True)
    finally:
        if client.poll() is None:
            client.terminate()
        client.stdin.close()
