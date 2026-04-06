# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from .views import view_404


class NotFoundPageTests(SimpleTestCase):
    def test_404_page_renders_without_table_context(self):
        request = RequestFactory().get('/username/missing/')

        with patch('browser.views.updated', return_value='2026-04-06 00:00:00'):
            response = view_404(request, None)

        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            "Whatever you just tried to look for couldn't be found",
            status_code=404,
        )
