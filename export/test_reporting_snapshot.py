# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for snapshot rotation and its existing-command statistics hook."""
import contextlib
import fcntl
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import sys
import unittest
from unittest.mock import Mock, patch

import rotate_reporting_snapshot as snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        (self.state / 'bootstrap-complete.json').touch()
        self.config = self.state / 'pgbouncer.ini'
        self.original = '[databases]\nbrowser_data = host=127.0.0.1 port=5432 dbname=bsb_reporting_a\n'
        self.config.write_text(self.original)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(snapshot, 'STATE', self.state))
        self.patches.enter_context(patch.object(snapshot, 'POOL_CONFIG', self.config))
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def statistics_mocks(self, sql_result=None, result=None):
        self.patches.enter_context(patch.object(snapshot, 'current_database', return_value='bsb_reporting_a'))
        self.patches.enter_context(patch.object(snapshot, 'sql', side_effect=sql_result or ['f', '2026-10-09T10:10:00+00:00']))
        return self.patches.enter_context(patch.object(
            snapshot.subprocess, 'run', return_value=result or
            subprocess.CompletedProcess([], 0, 'Statistics refreshed\n', '')))

    def rotation_mocks(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        self.patches.enter_context(patch.object(snapshot.urllib.request, 'urlopen', return_value=response))
        self.patches.enter_context(patch.object(snapshot, 'current_database', side_effect=['bsb_reporting_a', 'bsb_reporting_b']))
        def query(value, *args, **kwargs):
            if 'pg_subscription_rel' in value:
                return '0'
            if 'pg_current_wal_lsn' in value:
                return '0/100'
            if 'SELECT count(*)' in value:
                return '1'
            return ''
        self.sql = self.patches.enter_context(patch.object(snapshot, 'sql', side_effect=query))
        self.pool = self.patches.enter_context(patch.object(snapshot, 'pool_command', return_value=''))
        self.freeze = self.patches.enter_context(patch.object(snapshot, 'freeze', return_value='2026-10-09T10:10:00+00:00'))
        self.patches.enter_context(patch.object(snapshot.Path, 'read_text', return_value=self.original))
        # Avoid postgres ownership changes; preserve real local file writes.
        def write(path, content, *args, **kwargs):
            path.write_text(content)
        self.patches.enter_context(patch.object(snapshot, 'write_file', side_effect=write))

    def test_statistics_success_records_version_and_container_timeout(self):
        command = self.statistics_mocks()
        snapshot.refresh_statistics('bsb_reporting_a')
        record = json.loads((self.state / 'statistics-refresh.json').read_text())
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['source_updated_at'], '2026-10-09T10:10:00+00:00')
        argv = command.call_args.args[0]
        self.assertEqual(argv[:5], ['docker', 'exec', 'bsbbrowser-web-1', 'python', '-c'])
        self.assertIn('subprocess.run', argv[5])
        self.assertEqual(argv[-1], str(snapshot.STATISTICS_TIMEOUT))

    def test_statistics_failure_is_recorded(self):
        self.statistics_mocks(result=subprocess.CompletedProcess([], 1, '', 'Command failed'))
        with self.assertRaisesRegex(RuntimeError, 'snapshot is unchanged'):
            snapshot.refresh_statistics('bsb_reporting_a')
        self.assertEqual(json.loads((self.state / 'statistics-refresh.json').read_text())['status'], 'failed')

    def test_statistics_timeout_is_recorded(self):
        command = self.statistics_mocks()
        command.side_effect = subprocess.TimeoutExpired('docker', 75)
        with self.assertRaises(subprocess.TimeoutExpired):
            snapshot.refresh_statistics('bsb_reporting_a')
        self.assertEqual(json.loads((self.state / 'statistics-refresh.json').read_text())['status'], 'failed')

    def test_statistics_refuses_updating_database(self):
        command = self.statistics_mocks(sql_result=['t'])
        with self.assertRaisesRegex(RuntimeError, 'not frozen'):
            snapshot.refresh_statistics('bsb_reporting_a')
        command.assert_not_called()

    def test_refresh_only_does_not_rotate_and_holds_lock(self):
        self.rotation_mocks()
        def refresh(database):
            self.assertEqual(database, 'bsb_reporting_a')
            with (self.state / 'rotation.lock').open('a') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(snapshot, 'refresh_statistics', side_effect=refresh):
            snapshot.rotate(refresh_only=True)
        self.freeze.assert_not_called()
        self.pool.assert_not_called()
        self.sql.assert_not_called()

    def test_statistics_failure_keeps_published_backend(self):
        self.rotation_mocks()
        with patch.object(snapshot, 'refresh_statistics', side_effect=RuntimeError('statistics failed')) as refresh:
            with self.assertRaisesRegex(RuntimeError, 'statistics failed'):
                snapshot.rotate(refresh=True)
        refresh.assert_called_once_with('bsb_reporting_b')
        # read_text was mocked for the views input, so inspect the actual file.
        with self.config.open() as stream:
            self.assertIn('dbname=bsb_reporting_b', stream.read())
        commands = [call.args[0] for call in self.pool.call_args_list]
        self.assertEqual(commands, ['PAUSE browser_data;', 'RELOAD;', 'RESUME browser_data;'])
        self.assertIn("ALTER SUBSCRIPTION bsb_reporting_a_sub ENABLE;", [call.args[0] for call in self.sql.call_args_list])

    def test_freeze_failure_keeps_old_backend_without_pause(self):
        self.rotation_mocks()
        self.freeze.side_effect = RuntimeError('freeze failed')
        with self.assertRaisesRegex(RuntimeError, 'freeze failed'):
            snapshot.rotate(refresh=True)
        self.assertNotIn('PAUSE browser_data;', [call.args[0] for call in self.pool.call_args_list])
        with self.config.open() as stream:
            self.assertEqual(stream.read(), self.original)

    def test_container_runner_kills_timed_out_statistics(self):
        started = time.monotonic()
        result = subprocess.run([
            sys.executable, '-c', snapshot.STATISTICS_RUNNER,
            "import time; time.sleep(5); print('late publication')", 'unused', '1',
        ], capture_output=True, text=True, timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exceeded its time budget', result.stderr)
        self.assertNotIn('late publication', result.stdout)
        self.assertLess(time.monotonic() - started, 4)


if __name__ == '__main__':
    unittest.main()
