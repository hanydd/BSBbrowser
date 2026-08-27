# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistent statistics storage in the dedicated analytics database."""

from copy import deepcopy
from datetime import date
from typing import Any

from django.conf import settings
from django.db import connections, transaction

from .models import DailyStatistic, HourlySnapshot, StatisticsState


ANALYTICS_DATABASE_ALIAS = 'analytics'
STATE_PRIMARY_KEY = 1
SEPARATE_SERIES_KEYS = ('activity', 'skipSnapshots')


def database_storage_enabled() -> bool:
    return ANALYTICS_DATABASE_ALIAS in settings.DATABASES


def validate_statistics_document(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError('Statistics data must be an object')
    for key in ('sourceUpdatedAt', 'generatedAt', 'activity', 'skipSnapshots'):
        if key not in data:
            raise ValueError(f'Statistics data is missing {key}')
    if not isinstance(data['activity'], list) or not isinstance(data['skipSnapshots'], list):
        raise ValueError('Statistics activity and skipSnapshots must be arrays')


def _database_is_reachable() -> None:
    connections[ANALYTICS_DATABASE_ALIAS].ensure_connection()


def write_statistics_to_database(data: dict[str, Any], *, allow_older: bool = False) -> None:
    if not database_storage_enabled():
        return
    validate_statistics_document(data)
    source_updated_at = str(data['sourceUpdatedAt'])
    state_payload = {key: deepcopy(value) for key, value in data.items() if key not in SEPARATE_SERIES_KEYS}
    daily_rows = [
        DailyStatistic(date=date.fromisoformat(str(item['date'])), payload=deepcopy(item))
        for item in data['activity']
    ]
    hourly_rows = [
        HourlySnapshot(
            source_updated_at=str(item['sourceUpdatedAt']),
            captured_at=str(item.get('capturedAt', item['sourceUpdatedAt'])),
            payload=deepcopy(item),
        )
        for item in data['skipSnapshots']
    ]

    _database_is_reachable()
    with transaction.atomic(using=ANALYTICS_DATABASE_ALIAS):
        current = (
            StatisticsState.objects.using(ANALYTICS_DATABASE_ALIAS)
            .select_for_update()
            .filter(pk=STATE_PRIMARY_KEY)
            .first()
        )
        if current and source_updated_at < current.source_updated_at and not allow_older:
            raise ValueError(
                f'Refusing to replace statistics state {current.source_updated_at} with older {source_updated_at}'
            )
        if daily_rows:
            DailyStatistic.objects.using(ANALYTICS_DATABASE_ALIAS).bulk_create(
                daily_rows,
                update_conflicts=True,
                update_fields=('payload',),
                unique_fields=('date',),
            )
        if hourly_rows:
            HourlySnapshot.objects.using(ANALYTICS_DATABASE_ALIAS).bulk_create(
                hourly_rows,
                update_conflicts=True,
                update_fields=('captured_at', 'payload'),
                unique_fields=('source_updated_at',),
            )
        StatisticsState.objects.using(ANALYTICS_DATABASE_ALIAS).update_or_create(
            pk=STATE_PRIMARY_KEY,
            defaults={
                'source_updated_at': source_updated_at,
                'generated_at': str(data['generatedAt']),
                'payload': state_payload,
            },
        )


def read_statistics_from_database() -> dict[str, Any] | None:
    if not database_storage_enabled():
        return None
    _database_is_reachable()
    state = StatisticsState.objects.using(ANALYTICS_DATABASE_ALIAS).filter(pk=STATE_PRIMARY_KEY).first()
    if state is None:
        return None
    data = deepcopy(state.payload)
    data['activity'] = [
        deepcopy(payload)
        for payload in DailyStatistic.objects.using(ANALYTICS_DATABASE_ALIAS)
        .order_by('date')
        .values_list('payload', flat=True)
    ]
    data['skipSnapshots'] = [
        deepcopy(payload)
        for payload in HourlySnapshot.objects.using(ANALYTICS_DATABASE_ALIAS)
        .order_by('source_updated_at')
        .values_list('payload', flat=True)
    ]
    return data
