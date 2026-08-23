# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .analytics import _build_activity_series, read_statistics, refresh_statistics


class ActivitySeriesTests(SimpleTestCase):
    def test_builds_dau_rolling_mau_and_fills_empty_days(self):
        rows = [
            (date(2026, 1, 1), 'a', 2),
            (date(2026, 1, 1), 'b', 1),
            (date(2026, 1, 3), 'a', 1),
            (date(2026, 1, 31), 'c', 4),
        ]

        result = _build_activity_series(rows, date(2026, 1, 31))

        self.assertEqual(result[0], {
            'date': '2026-01-01',
            'dau': 2,
            'mau30': 2,
            'dailySubmissions': 3,
            'cumulativeSubmissions': 3,
        })
        self.assertEqual(result[1]['dau'], 0)
        self.assertEqual(result[2]['mau30'], 2)
        self.assertEqual(result[-1], {
            'date': '2026-01-31',
            'dau': 1,
            'mau30': 2,
            'dailySubmissions': 4,
            'cumulativeSubmissions': 8,
        })

    def test_ignores_future_rows(self):
        result = _build_activity_series(
            [(date(2026, 1, 2), 'future', 1)],
            date(2026, 1, 1),
        )

        self.assertEqual(result, [])


class StatisticsPersistenceTests(SimpleTestCase):
    def test_refresh_persists_and_replaces_same_source_snapshot(self):
        with TemporaryDirectory() as directory:
            stats_file = str(Path(directory) / 'statistics.json')
            with override_settings(STATS_DATA_FILE=stats_file):
                with patch('browser.analytics._source_updated_at', return_value='2026-01-01T00:00:00Z'), \
                        patch('browser.analytics._iso_utc_now', side_effect=[
                            '2026-01-01T00:01:00Z',
                            '2026-01-01T00:02:00Z',
                        ]), \
                        patch('browser.analytics._fetch_daily_user_submissions', return_value=[
                            (date(2026, 1, 1), 'a', 2),
                        ]), \
                        patch('browser.analytics._fetch_current_totals', side_effect=[
                            {'totalSubmissions': 2, 'skipCount': 10, 'minutesSaved': 3},
                            {'totalSubmissions': 2, 'skipCount': 12, 'minutesSaved': 4},
                        ]):
                    refresh_statistics()
                    result = refresh_statistics()

                self.assertEqual(len(result['skipSnapshots']), 1)
                self.assertEqual(result['skipSnapshots'][0]['skipCount'], 12)
                self.assertEqual(read_statistics()['summary']['mau30'], 1)
                with open(stats_file, encoding='utf-8') as stream:
                    self.assertEqual(json.load(stream)['schemaVersion'], 1)
