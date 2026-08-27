# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import models


class StatisticsState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    source_updated_at = models.TextField(db_index=True)
    generated_at = models.TextField()
    payload = models.JSONField()

    class Meta:
        db_table = 'stats_state'


class DailyStatistic(models.Model):
    date = models.DateField(primary_key=True)
    payload = models.JSONField()

    class Meta:
        db_table = 'stats_daily'
        ordering = ('date',)


class HourlySnapshot(models.Model):
    source_updated_at = models.TextField(primary_key=True)
    captured_at = models.TextField()
    payload = models.JSONField()

    class Meta:
        db_table = 'stats_hourly_snapshot'
        ordering = ('source_updated_at',)
