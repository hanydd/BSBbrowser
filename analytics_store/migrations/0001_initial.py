# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='DailyStatistic',
            fields=[
                ('date', models.DateField(primary_key=True, serialize=False)),
                ('payload', models.JSONField()),
            ],
            options={
                'db_table': 'stats_daily',
                'ordering': ('date',),
            },
        ),
        migrations.CreateModel(
            name='HourlySnapshot',
            fields=[
                ('source_updated_at', models.TextField(primary_key=True, serialize=False)),
                ('captured_at', models.TextField()),
                ('payload', models.JSONField()),
            ],
            options={
                'db_table': 'stats_hourly_snapshot',
                'ordering': ('source_updated_at',),
            },
        ),
        migrations.CreateModel(
            name='StatisticsState',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('source_updated_at', models.TextField(db_index=True)),
                ('generated_at', models.TextField()),
                ('payload', models.JSONField()),
            ],
            options={
                'db_table': 'stats_state',
            },
        ),
    ]
