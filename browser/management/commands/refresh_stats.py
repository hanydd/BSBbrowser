# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.management.base import BaseCommand

from browser.analytics import refresh_statistics


class Command(BaseCommand):
    help = "Refresh public statistics from the reporting database"

    def handle(self, *args, **options):
        data = refresh_statistics()
        self.stdout.write(self.style.SUCCESS(
            f"Statistics refreshed from {data['sourceUpdatedAt']}: "
            f"DAU={data['summary']['dau']} MAU={data['summary']['mau30']} "
            f"skips={data['summary']['skipCount']}"
        ))
