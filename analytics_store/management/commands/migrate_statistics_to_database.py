# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analytics_store.storage import (
    database_storage_enabled,
    read_statistics_from_database,
    validate_statistics_document,
    write_statistics_to_database,
)


class Command(BaseCommand):
    help = 'Copy the existing statistics JSON into the dedicated analytics database and verify it'

    def add_arguments(self, parser):
        parser.add_argument('--source', default=settings.STATS_DATA_FILE)

    def handle(self, *args, **options):
        if not database_storage_enabled():
            raise CommandError('ANALYTICS_DB_NAME is not configured')
        source = Path(options['source']).resolve()
        if not source.is_file():
            raise CommandError(f'Statistics JSON does not exist: {source}')
        with source.open(encoding='utf-8') as stream:
            data = json.load(stream)
        validate_statistics_document(data)

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = source.with_name(f'{source.name}.pre-database-{timestamp}.backup')
        suffix = 1
        while backup.exists():
            backup = source.with_name(f'{source.name}.pre-database-{timestamp}.{suffix}.backup')
            suffix += 1
        shutil.copy2(source, backup)

        write_statistics_to_database(data)
        restored = read_statistics_from_database()
        if restored != data:
            raise CommandError(
                'Database verification failed; the JSON and its backup were retained and database reads must not be enabled'
            )

        self.stdout.write(self.style.SUCCESS(
            f'Migrated and verified {len(data["activity"])} daily rows and '
            f'{len(data["skipSnapshots"])} hourly snapshots from {source}; backup: {backup}'
        ))
