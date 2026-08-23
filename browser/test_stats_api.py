# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from . import stats_api


TEST_CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=TEST_CACHES)
class StatsCompatibilityApiTests(SimpleTestCase):
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
