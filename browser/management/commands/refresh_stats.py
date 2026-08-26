# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.management.base import BaseCommand

from browser.analytics import refresh_statistics
from browser.stats_api import refresh_compatibility_cache


class Command(BaseCommand):
    help = "Refresh public statistics from the reporting database"

    def handle(self, *args, **options):
        data = refresh_statistics()
        refresh_compatibility_cache(data['sourceUpdatedAt'])
        self.stdout.write(self.style.SUCCESS(
            f"Statistics refreshed from {data['sourceUpdatedAt']}: "
            f"DAU24h={data['summary']['dau24h']} dailyDAU={data['summary']['dau']} "
            f"MAU={data['summary']['mau30']} "
            f"skips={data['summary']['skipCount']}"
        ))
