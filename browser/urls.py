# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path

from . import views
from . import stats_api

urlpatterns = [
    path('', views.FilteredSponsortimeListView.as_view(), name='index'),
    path('api/getTotalStats', stats_api.get_total_stats, name='get_total_stats'),
    path('api/getDaysSavedFormatted', stats_api.get_days_saved_formatted, name='get_days_saved_formatted'),
    path('api/getTopUsers', stats_api.get_top_users, name='get_top_users'),
    path('api/getTopCategoryUsers', stats_api.get_top_category_users, name='get_top_category_users'),
    path('stats/api/overview', stats_api.get_stats_overview, name='stats_overview'),
    path('video/<videoid>/', views.FilteredVideoListView.as_view(), name='video'),
    path('userid/<userid>/', views.FilteredUserIDListView.as_view(), name='userid'),
    path('username/<path:username>/', views.FilteredUsernameListView.as_view(), name='username'),
    path('uuid/<uuid>/', views.FilteredUUIDListView.as_view(), name='uuid'),
]
