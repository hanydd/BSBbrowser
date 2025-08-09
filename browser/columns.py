# SPDX-License-Identifier: AGPL-3.0-or-later
import datetime
import django_tables2 as tables

from django.db.models import F, QuerySet
from django.utils.html import format_html


class LengthColumn(tables.Column):
    def render(self, value: float) -> str:
        # Format as mm:ss.SSS starting from minutes (no hours)
        total_ms = int(round(float(value) * 1000))
        minutes = total_ms // (60 * 1000)
        seconds = (total_ms // 1000) % 60
        millis = total_ms % 1000
        return f"{minutes:02d}:{seconds:02d}.{millis:03d}"

    def order(self, queryset: QuerySet, is_descending: bool) -> tuple[QuerySet, bool]:
        queryset = queryset.annotate(
            length=F("endtime") - F("starttime")
        ).order_by(("-" if is_descending else "") + "length")
        return queryset, True
