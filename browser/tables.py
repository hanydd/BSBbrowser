# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timedelta, timezone

from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.db.models import F, QuerySet

import django_tables2 as tables

from .models import Sponsortime, Vipuser
from .columns import LengthColumn


class SponsortimeTable(tables.Table):
    videoid = tables.TemplateColumn('<a href="/video/{{ value }}/">{{ value }}</a>'
                                    '<button class="clip" data-value="{{ value }}">✂</button>'
                                    '<a href="https://www.bilibili.com/video/{{ value }}">Bili</a>', verbose_name=_('视频ID'))
    uuid = tables.TemplateColumn('<textarea class="form-control uuid" name="UUID" readonly>{{ value }}</textarea>'
                                 '<button class="clip" data-value="{{ value }}">✂</button>'
                                 '<a href="/uuid/{{ value }}/">🔗</a>', verbose_name=_('UUID'))
    userid = tables.TemplateColumn('<textarea class="form-control userid" name="UserID" readonly>{{ value }}</textarea>'
                                   '<button class="clip" data-value="{{ value }}">✂</button>'
                                   '<a href="/userid/{{ value }}/">🔗</a>',
                                   verbose_name=_('用户公开ID'), accessor='user_id')
    username = tables.TemplateColumn('{% if value %}'
                                     '<textarea class="form-control" name="Username" readonly>{{ value }}</textarea>'
                                     '<button class="clip" data-value="{{ value }}">✂</button>'
                                     '<a href="/username/{{ value|urlencode }}/">🔗</a>'
                                     '{% else %}—{% endif %}', accessor='user__username', verbose_name=_('用户名'))
    length = LengthColumn(initial_sort_descending=True, verbose_name=_('时长'))
    votes = tables.Column(initial_sort_descending=True, verbose_name=_('投票'))
    views = tables.Column(initial_sort_descending=True, verbose_name=_('观看'))
    actiontype = tables.Column(verbose_name=_('操作'))
    category = tables.Column(verbose_name=_('类型'))
    hidden = tables.Column(verbose_name=_('隐藏'))
    shadowhidden = tables.Column(verbose_name=_('伪隐藏'))

    class Meta: # noqa
        model = Sponsortime
        exclude = ('locked', 'incorrectvotes', 'user', 'service', 'videoduration', 'reputation', 'hashedvideoid',
                   'useragent', 'description')
        sequence = ('timesubmitted', 'videoid', 'starttime', 'endtime', 'length', 'votes', 'views', 'category',
                    'actiontype', 'hidden', 'shadowhidden', 'uuid', 'username')

    @staticmethod
    def render_timesubmitted(value: float) -> str:
        return datetime.fromtimestamp(float(value) / 1000., tz=timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def render_time(value: float) -> str:
        # Format as mm:ss.SSS starting from minutes (no hours)
        is_negative = value < 0
        total_ms = int(round(abs(float(value)) * 1000))
        minutes = total_ms // (60 * 1000)
        seconds = (total_ms // 1000) % 60
        millis = total_ms % 1000
        formatted = f"{minutes:02d}:{seconds:02d}.{millis:03d}"
        return f"-{formatted}" if is_negative else formatted

    @staticmethod
    def render_starttime(value: float) -> str:
        return SponsortimeTable.render_time(value)

    @staticmethod
    def render_endtime(value: float) -> str:
        return SponsortimeTable.render_time(value)

    @staticmethod
    def render_votes(value: int, record) -> str:
        hidden = ''
        locked = ''
        if record.locked == 1:
            locked = '<span title="This segment is locked by a VIP">🔒</span>'
        if value <= -2:
            hidden = '<span title="This segment is not sent to users">❌</span>'
        if Vipuser.objects.filter(userid=record.user_id).exists():
            return format_html(f'{value}{hidden}{locked}<span title="This user is a VIP">👑</span>')
        return format_html(f'{value}{hidden}{locked}')

    @staticmethod
    def render_actiontype(value: str) -> str:
        if value == 'skip':
            return format_html('<span title="Skip">⏭️</span>')
        if value == 'mute':
            return format_html('<span title="Mute">🔇</span>')
        if value == 'full':
            return format_html('<span title="Full Video Label">♾️</span>')
        if value == 'poi':
            return format_html('<span title="Highlight">✨️</span>')
        if value == 'chapter':
            return format_html('<span title="Chapter">🏷️</span>')
        return '—'

    @staticmethod
    def render_hidden(value: int) -> str:
        if value == 1:
            return format_html('<span title="This segment is hidden due to video duration change.">❌</span>')
        return '—'

    @staticmethod
    def render_shadowhidden(value: int) -> str:
        if value >= 1:
            return format_html('<span title="This segment has been shadowhidden.">❌</span>')
        return '—'

    @staticmethod
    def render_category(value: str, record) -> str:
        if record.description:
            return format_html('<span title="{}">{}</span>', record.description, value)
        return value

    @staticmethod
    def order_username(queryset: QuerySet, is_descending: bool) -> tuple[QuerySet, bool]:
        if is_descending:
            queryset = queryset.select_related('user').order_by(F('user__username').desc(nulls_last=True))
        else:
            queryset = queryset.select_related('user').order_by(F('user__username').asc(nulls_last=True))
        return queryset, True


class VideoTable(SponsortimeTable):
    class Meta: # noqa
        exclude = ('videoid',)
        sequence = ('timesubmitted', 'starttime', 'endtime', 'length', 'votes', 'views', 'category', 'shadowhidden',
                    'uuid', 'username')


class UsernameTable(SponsortimeTable):
    class Meta: # noqa
        exclude = ('username',)
        sequence = ('timesubmitted', 'videoid', 'starttime', 'endtime', 'length', 'votes', 'views', 'category',
                    'shadowhidden', 'uuid')


class UserIDTable(SponsortimeTable):
    class Meta: # noqa
        exclude = ('username', 'userid')
        sequence = ('timesubmitted', 'videoid', 'starttime', 'endtime', 'length', 'votes', 'views', 'category',
                    'shadowhidden')
