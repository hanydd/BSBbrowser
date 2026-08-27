# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings

from .analytics import (
    _add_video_coverage,
    _build_activity_series,
    _build_hourly_distribution,
    _fetch_rolling_24h_contributors,
    _merge_persisted_history,
    read_statistics,
    refresh_statistics,
)


class ActivitySeriesTests(SimpleTestCase):
    @patch('browser.analytics.connection')
    def test_rolling_24h_contributors_uses_source_refresh_time(self, database_connection):
        query_cursor = database_connection.cursor.return_value.__enter__.return_value
        query_cursor.fetchone.return_value = (17,)

        result = _fetch_rolling_24h_contributors('2026-01-02T12:34:56Z')

        self.assertEqual(result, 17)
        parameters = query_cursor.execute.call_args.args[1]
        self.assertEqual(parameters[0], ['PORT'])
        self.assertEqual(parameters[2], 1767357296000)
        self.assertEqual(parameters[2] - parameters[1], 24 * 60 * 60 * 1000)

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

    def test_can_start_series_before_first_submission(self):
        result = _build_activity_series(
            [(date(2024, 1, 3), 'first-user', 2)],
            date(2024, 1, 3),
            date(2024, 1, 1),
        )

        self.assertEqual(result[0]['date'], '2024-01-01')
        self.assertEqual(result[0]['dailySubmissions'], 0)
        self.assertEqual(result[-1]['cumulativeSubmissions'], 2)

    def test_adds_daily_and_cumulative_video_coverage(self):
        activity = [
            {'date': '2024-01-01'},
            {'date': '2024-01-02'},
            {'date': '2024-01-03'},
        ]

        result = _add_video_coverage(activity, [
            (date(2024, 1, 1), 2),
            (date(2024, 1, 3), 4),
        ])

        self.assertEqual(
            [(item['dailyCoveredVideos'], item['cumulativeCoveredVideos']) for item in result],
            [(2, 2), (0, 2), (4, 6)],
        )

    def test_builds_hourly_distribution_and_fills_empty_hours(self):
        result = _build_hourly_distribution([(0, 9, 18), (18, 27, 54)], 9)

        self.assertEqual(len(result), 24)
        self.assertEqual(result[0], {
            'hour': 0,
            'averageContributors': 1.0,
            'averageSubmissions': 2.0,
        })
        self.assertEqual(result[1]['averageSubmissions'], 0.0)
        self.assertEqual(result[18]['averageContributors'], 3.0)

    def test_merges_database_and_json_snapshot_history_without_loss(self):
        database_data = {
            'sourceUpdatedAt': '2026-01-03T00:00:00Z',
            'skipSnapshots': [
                {'sourceUpdatedAt': '2026-01-01T00:00:00Z', 'skipCount': 1},
                {'sourceUpdatedAt': '2026-01-03T00:00:00Z', 'skipCount': 3},
            ],
        }
        file_data = {
            'sourceUpdatedAt': '2026-01-02T00:00:00Z',
            'skipSnapshots': [
                {'sourceUpdatedAt': '2026-01-01T00:00:00Z', 'skipCount': 2},
                {'sourceUpdatedAt': '2026-01-02T00:00:00Z', 'skipCount': 2},
            ],
        }

        result = _merge_persisted_history(database_data, file_data)

        self.assertEqual(
            [(item['sourceUpdatedAt'], item['skipCount']) for item in result['skipSnapshots']],
            [
                ('2026-01-01T00:00:00Z', 2),
                ('2026-01-02T00:00:00Z', 2),
                ('2026-01-03T00:00:00Z', 3),
            ],
        )


class StatisticsPersistenceTests(SimpleTestCase):
    def test_refresh_persists_and_replaces_same_source_snapshot(self):
        with TemporaryDirectory() as directory:
            stats_file = str(Path(directory) / 'statistics.json')
            with override_settings(STATS_DATA_FILE=stats_file):
                with patch('browser.analytics.write_statistics_to_database') as database_writer, \
                        patch('browser.analytics._source_updated_at', return_value='2026-01-01T00:00:00Z'), \
                        patch('browser.analytics._iso_utc_now', side_effect=[
                            '2026-01-01T00:01:00Z',
                            '2026-01-01T00:02:00Z',
                        ]), \
                        patch('browser.analytics._fetch_daily_user_submissions', return_value=[
                            (date(2026, 1, 1), 'a', 2),
                        ]), \
                        patch('browser.analytics._fetch_rolling_24h_contributors', return_value=7), \
                        patch('browser.analytics._fetch_daily_video_coverage', return_value=[
                            (date(2026, 1, 1), 1),
                        ]), \
                        patch('browser.analytics._fetch_hourly_contributions', return_value=[
                            (10, 1, 2),
                        ]), \
                        patch('browser.analytics._fetch_contributor_distribution', return_value=[
                            {'range': '2–5', 'contributors': 1, 'submissions': 2},
                        ]), \
                        patch('browser.analytics._fetch_category_distribution', return_value=[
                            {'category': 'sponsor', 'submissions': 2, 'skipCount': 10, 'minutesSaved': 3},
                        ]), \
                        patch('browser.analytics._fetch_current_totals', side_effect=[
                            {
                                'totalSubmissions': 2, 'skipCount': 10, 'minutesSaved': 3,
                                'coveredVideos': 1, 'contributorCount': 1,
                            },
                            {
                                'totalSubmissions': 2, 'skipCount': 12, 'minutesSaved': 4,
                                'coveredVideos': 1, 'contributorCount': 1,
                            },
                        ]):
                    refresh_statistics()
                    result = refresh_statistics()

                self.assertEqual(len(result['skipSnapshots']), 1)
                self.assertEqual(result['skipSnapshots'][0]['skipCount'], 12)
                self.assertEqual(result['activity'][-1]['cumulativeCoveredVideos'], 1)
                self.assertEqual(result['hourlyContribution']['points'][10]['averageSubmissions'], 0.0)
                self.assertEqual(result['contributorDistribution'][0]['range'], '2–5')
                self.assertEqual(read_statistics()['summary']['dau'], 1)
                self.assertEqual(read_statistics()['summary']['dau24h'], 7)
                self.assertEqual(read_statistics()['summary']['mau30'], 1)
                self.assertEqual(database_writer.call_count, 2)
                self.assertEqual(database_writer.call_args.args[0]['skipSnapshots'][0]['skipCount'], 12)
                with open(stats_file, encoding='utf-8') as stream:
                    self.assertEqual(json.load(stream)['schemaVersion'], 2)


class StatisticsReadFallbackTests(SimpleTestCase):
    @patch('browser.analytics.database_storage_enabled', return_value=True)
    @patch('browser.analytics.read_statistics_from_database', return_value={'source': 'database'})
    def test_prefers_database_statistics(self, _database_reader, _storage_enabled):
        self.assertEqual(read_statistics(), {'source': 'database'})

    @patch('browser.analytics.database_storage_enabled', return_value=True)
    @patch('browser.analytics.read_statistics_from_database', side_effect=OperationalError('offline'))
    def test_falls_back_to_json_when_database_is_unavailable(self, _database_reader, _storage_enabled):
        with TemporaryDirectory() as directory:
            stats_file = Path(directory) / 'statistics.json'
            expected = {'source': 'json'}
            stats_file.write_text(json.dumps(expected), encoding='utf-8')

            with override_settings(STATS_DATA_FILE=str(stats_file)):
                self.assertEqual(read_statistics(), expected)
