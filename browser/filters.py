# SPDX-License-Identifier: AGPL-3.0-or-later
from django_filters import FilterSet, CharFilter, ChoiceFilter, MultipleChoiceFilter, RangeFilter
from django.utils.translation import gettext_lazy as _
from django_filters.widgets import RangeWidget

from .models import Sponsortime

FIELDS = ['videoid', 'votes', 'views', 'category', 'shadowhidden', 'uuid', 'username', 'user']


class CustomRangeWidget(RangeWidget):
    def __init__(self, attrs=None, from_attrs=None, to_attrs=None):
        super().__init__(attrs)

        if from_attrs:
            self.widgets[0].attrs.update(from_attrs)
        if to_attrs:
            self.widgets[1].attrs.update(to_attrs)


class UserIDFilter(FilterSet):
    votes = RangeFilter(label=_('投票'), widget=CustomRangeWidget(attrs={'type': 'number', 'step': 1},
                                                 from_attrs={'placeholder': _('投票下限')},
                                                 to_attrs={'placeholder': _('投票上限')}))
    views = RangeFilter(label=_('观看'), widget=CustomRangeWidget(attrs={'type': 'number', 'step': 1},
                                                 from_attrs={'placeholder': _('观看下限')},
                                                 to_attrs={'placeholder': _('观看上限')}))
    category = MultipleChoiceFilter(choices=(('exclusive_access', 'Exclusive Access'),
                                             ('filler', 'Filler'), ('poi_highlight', 'Highlight'),
                                             ('interaction', 'Interaction'), ('intro', 'Intro'),
                                             ('music_offtopic', 'Non-Music'), ('outro', 'Outro'),
                                             ('preview', 'Preview'), ('selfpromo', 'Selfpromo'),
                                             ('sponsor', 'Sponsor'),), distinct=False)
    category.always_filter = False
    shadowhidden = ChoiceFilter(label=_('伪隐藏'), choices=((0, _('否')), (1, _('是'))), empty_label=_('伪隐藏'),
                                method='shadowhidden_filter')
    actiontype = MultipleChoiceFilter(choices=(('full', 'Full Video Label'),
                                               ('poi', 'Highlight'), ('mute', 'Mute'), ('skip', 'Skip')),
                                      distinct=False)

    class Meta:
        model = Sponsortime
        fields = FIELDS
        exclude = ['username', 'user']

    @staticmethod
    def shadowhidden_filter(queryset, _name, value):
        if value == 1:
            return queryset.filter(shadowhidden__gte=1)
        return queryset.filter(shadowhidden=0)


class UsernameFilter(UserIDFilter):
    user = CharFilter(label=_('用户公开ID'))

    class Meta:
        model = Sponsortime
        fields = FIELDS
        exclude = 'username'


class SponsortimeFilter(UsernameFilter):
    username = CharFilter(field_name='user__username', label=_('用户名'), lookup_expr='icontains')

    class Meta:
        model = Sponsortime
        fields = FIELDS


class VideoFilter(SponsortimeFilter):
    class Meta:
        model = Sponsortime
        fields = FIELDS
        exclude = 'videoid'
