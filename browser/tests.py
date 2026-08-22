# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from .views import updated, view_404


class NotFoundPageTests(SimpleTestCase):
    def test_404_page_renders_without_table_context(self):
        request = RequestFactory().get('/username/missing/')

        with patch('browser.views.updated', return_value='2026-04-06 00:00:00'):
            response = view_404(request, exception=None)

        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            "Whatever you just tried to look for couldn't be found",
            status_code=404,
        )

    @override_settings(
        DEBUG=False,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_missing_route_uses_404_handler(self):
        with patch('browser.views.updated', return_value='2026-04-06 00:00:00'):
            response = self.client.get('/definitely-missing/')

        self.assertEqual(response.status_code, 404)


class UpdatedTimestampTests(SimpleTestCase):
    @patch('browser.views.Config.objects.filter')
    def test_missing_updated_value_has_fallback(self, filter_mock):
        filter_mock.return_value.values_list.return_value.first.return_value = None

        self.assertEqual(updated(), '更新时间未知')

    @patch('browser.views.Config.objects.filter')
    def test_invalid_updated_value_has_fallback(self, filter_mock):
        filter_mock.return_value.values_list.return_value.first.return_value = 'invalid'

        self.assertEqual(updated(), '更新时间未知')
