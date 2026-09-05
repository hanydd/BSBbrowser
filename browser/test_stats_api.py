# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock, patch

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings

from . import stats_api


TEST_CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=TEST_CACHES)
class StatsCompatibilityApiTests(SimpleTestCase):
    def setUp(self):
        stats_api.cache.clear()

    @patch('browser.stats_api._get_audience_counts', return_value=(103_000, 104_000))
    @patch('browser.stats_api._cached_database_result')
    def test_namespaced_routes_match_legacy_routes(self, cached_result, _audience):
        cases = [
            ('/stats/api/total', '/api/getTotalStats', '?countContributingUsers=true',
             {'userCount': 3, 'viewCount': 4, 'totalSubmissions': 5, 'minutesSaved': 6}),
            ('/stats/api/top-users', '/api/getTopUsers', '?sortType=2&categoryStats=true',
             {'userNames': ['user'], 'categoryStats': [[1]]}),
            ('/stats/api/top-category-users', '/api/getTopCategoryUsers', '?sortType=2&category=sponsor',
             {'userNames': ['user']}),
            ('/stats/api/days-saved', '/api/getDaysSavedFormatted', '', {'daysSaved': '1.13'}),
        ]
        for canonical, legacy, query, data in cases:
            with self.subTest(path=canonical):
                cached_result.return_value = data
                new_response = self.client.get(canonical + query)
                new_call = cached_result.call_args
                old_response = self.client.get(legacy + query)
                self.assertEqual(new_response.status_code, 200)
                self.assertEqual(new_response.json(), old_response.json())
                self.assertEqual(new_call.args[0], cached_result.call_args.args[0])
                self.assertEqual(new_call.args[2:], cached_result.call_args.args[2:])

    def test_namespaced_leaderboards_preserve_validation(self):
        for path in ('/stats/api/top-users', '/stats/api/top-category-users?sortType=2&category=unknown'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 400)

    @patch('browser.stats_api._source_version', return_value='days-version')
    @patch('browser.stats_api.connection')
    def test_days_saved_preserves_legacy_filter_and_caches_result(self, connection, _version):
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1.23456789,)
        for _ in range(2):
            response = self.client.get('/api/getDaysSavedFormatted')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'daysSaved': '1.23'})
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args.args[0]
        self.assertIn('"shadowHidden" != 1', sql)
        self.assertNotIn('votes', sql)

    @patch('browser.stats_api.connection')
    def test_days_saved_empty_result_is_legacy_zero_string(self, connection):
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (None,)
        self.assertEqual(stats_api._fetch_days_saved_formatted(), {'daysSaved': '0'})

    @patch('browser.stats_api.connection')
    def test_days_saved_rounds_half_values_like_javascript(self, connection):
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1.125,)
        self.assertEqual(stats_api._fetch_days_saved_formatted(), {'daysSaved': '1.13'})

    @patch('browser.stats_api._source_version', return_value='next-version')
    @patch('browser.stats_api._fetch_days_saved_formatted', side_effect=OperationalError('cutover'))
    def test_days_saved_serves_previous_snapshot_during_cutover(self, _fetch, _version):
        stats_api.cache.set('stats-api:days-saved:latest:', {'daysSaved': '42.00'}, timeout=None)
        response = self.client.get('/api/getDaysSavedFormatted')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'daysSaved': '42.00'})

    @patch('browser.stats_api._get_audience_counts', return_value=(103_000, 104_000))
    @patch('browser.stats_api._cached_database_result')
    def test_total_stats_keeps_legacy_response_shape(self, cached_result, _audience_counts):
        cached_result.return_value = {
            'userCount': 45_000,
            'viewCount': 20_000_000,
            'totalSubmissions': 390_000,
            'minutesSaved': 21_000_000,
        }

        response = self.client.get('/api/getTotalStats?countContributingUsers=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'userCount': 45_000,
            'activeUsers': 103_000,
            'apiUsers': 104_000,
            'viewCount': 20_000_000,
            'totalSubmissions': 390_000,
            'minutesSaved': 21_000_000,
        })

    @patch('browser.stats_api._cached_database_result')
    def test_top_users_accepts_legacy_parse_int_input(self, cached_result):
        cached_result.return_value = {
            'userNames': ['user'],
            'viewCounts': [2],
            'totalSubmissions': [1],
            'minutesSaved': [3],
            'votes': [4],
            'portVideo': [0],
        }

        response = self.client.get('/api/getTopUsers?sortType=2legacy')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), cached_result.return_value)

    def test_top_users_rejects_missing_sort_type(self):
        response = self.client.get('/api/getTopUsers')

        self.assertEqual(response.status_code, 400)

    @patch('browser.stats_api._cached_database_result')
    def test_top_category_users_accepts_supported_category(self, cached_result):
        cached_result.return_value = {
            'userNames': [],
            'viewCounts': [],
            'totalSubmissions': [],
            'votes': [],
            'portVideo': [],
            'minutesSaved': [],
        }

        response = self.client.get('/api/getTopCategoryUsers?sortType=2&category=sponsor')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), cached_result.return_value)

    def test_top_category_users_rejects_unknown_category(self):
        response = self.client.get('/api/getTopCategoryUsers?sortType=2&category=unknown')

        self.assertEqual(response.status_code, 400)

    @patch('browser.stats_api.Config.objects')
    def test_source_version_is_read_from_persistent_cache(self, config_objects):
        config_objects.filter.return_value.values_list.return_value.first.return_value = 'version-1'

        self.assertEqual(stats_api._source_version(), 'version-1')
        self.assertEqual(stats_api._source_version(), 'version-1')

        self.assertEqual(config_objects.filter.call_count, 1)

    @patch('browser.stats_api._source_version', return_value='version-2')
    def test_database_cache_falls_back_to_latest_success_during_cutover(self, _source_version):
        stats_api.cache.set('stats-api:top-users:latest:totalSubmissions:false', {'stale': True}, timeout=None)

        result = stats_api._cached_database_result(
            'top-users',
            MagicMock(side_effect=OperationalError('database is not accepting connections')),
            'totalSubmissions',
            'false',
        )

        self.assertEqual(result, {'stale': True})

    @patch('browser.stats_api._source_version', return_value='version-2')
    def test_existing_versioned_cache_populates_latest_success(self, _source_version):
        stats_api.cache.set('stats-api:top-users:version-2:totalSubmissions:false', {'cached': True})

        result = stats_api._cached_database_result(
            'top-users',
            MagicMock(side_effect=AssertionError('loader should not run')),
            'totalSubmissions',
            'false',
        )

        self.assertEqual(result, {'cached': True})
        self.assertEqual(
            stats_api.cache.get('stats-api:top-users:latest:totalSubmissions:false'),
            {'cached': True},
        )

    @patch('browser.stats_api._fetch_top_users', return_value={'top': True})
    @patch('browser.stats_api._fetch_total_stats', return_value={'total': True})
    @patch('browser.stats_api._fetch_days_saved_formatted', return_value={'daysSaved': '1.23'})
    def test_refresh_compatibility_cache_warms_default_statistics(self, fetch_days, fetch_total, fetch_top):
        stats_api.refresh_compatibility_cache('version-3')
        fetch_days.assert_called_once_with()


        self.assertEqual(stats_api.cache.get(stats_api.SOURCE_VERSION_CACHE_KEY), 'version-3')
        self.assertEqual(fetch_total.call_count, 2)
        self.assertEqual(fetch_top.call_count, len(stats_api.SORT_TYPE_MAP) * 2)

    @patch('browser.stats_api.read_statistics', return_value={
        'schemaVersion': 1,
        'summary': {'dau': 10, 'mau30': 20, 'skipCount': 30},
        'activity': [],
        'skipSnapshots': [],
    })
    def test_stats_overview_uses_new_namespace(self, _read_statistics):
        response = self.client.get('/stats/api/overview')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['mau30'], 20)
        self.assertEqual(response['Cache-Control'], 'public, max-age=300, stale-while-revalidate=60')
        self.assertTrue(response['ETag'].startswith('"stats-'))

    @patch('browser.stats_api.read_statistics', return_value={
        'schemaVersion': 1,
        'sourceUpdatedAt': '2026-08-23T06:00:04Z',
        'summary': {},
        'activity': [],
        'skipSnapshots': [],
    })
    def test_stats_overview_supports_conditional_requests(self, _read_statistics):
        response = self.client.get(
            '/stats/api/overview',
            HTTP_IF_NONE_MATCH='"stats-2026-08-23T06:00:04Z"',
        )

        self.assertEqual(response.status_code, 304)

    @patch('browser.stats_api.read_statistics', return_value=None)
    def test_stats_overview_returns_503_before_first_refresh(self, _read_statistics):
        response = self.client.get('/stats/api/overview')

        self.assertEqual(response.status_code, 503)


class StatsQueryMappingTests(SimpleTestCase):
    @patch('browser.stats_api.connection')
    def test_total_stats_maps_optional_user_count(self, database_connection):
        cursor = MagicMock()
        database_connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = (45_000, 390_000, 20_000_000, 21_000_000.5)

        result = stats_api._fetch_total_stats(count_contributing_users=True)

        self.assertEqual(result, {
            'userCount': 45_000,
            'totalSubmissions': 390_000,
            'viewCount': 20_000_000,
            'minutesSaved': 21_000_001,
        })

    @patch('browser.stats_api.connection')
    def test_top_users_only_includes_category_stats_when_requested(self, database_connection):
        cursor = MagicMock()
        database_connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [('user', 2, 1, 3.5, 4, 0)]

        without_categories = stats_api._fetch_top_users('totalSubmissions', False)

        self.assertNotIn('categoryStats', without_categories)
        self.assertEqual(without_categories['userNames'], ['user'])

        cursor.fetchall.return_value = [('user', 2, 1, 3.5, 4, 0, *range(10))]
        with_categories = stats_api._fetch_top_users('totalSubmissions', True)

        self.assertEqual(with_categories['categoryStats'], [list(range(10))])

    @patch('browser.stats_api._http_get_text', return_value='<title>users: 12.3k</title>')
    def test_chrome_user_count_supports_decimal_shield_values(self, _get_text):
        self.assertEqual(stats_api._fetch_chrome_users(), 12_300)
