# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compatibility statistics APIs backed by the reporting database."""

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, HttpResponseNotModified, JsonResponse
from django.views.decorators.http import require_GET

from .category_labels import CATEGORY_LABELS
from .analytics import read_statistics
from .models import Config


SORT_TYPE_MAP = {
    0: "minutesSaved",
    1: "viewCount",
    2: "totalSubmissions",
    3: "userVotes",
    4: "portVideoSubmissions",
}

CATEGORY_STATS_FIELDS = (
    "categorySumSponsor",
    "categorySumIntro",
    "categorySumOutro",
    "categorySumInteraction",
    "categorySumSelfpromo",
    "categorySumMusicOfftopic",
    "categorySumPreview",
    "categorySumHighlight",
    "categorySumFiller",
    "categorySumExclusiveAccess",
)

CHROME_EXTENSION_ID = "eaoelafamejbnggahofapllmfhlhajdd"
EDGE_EXTENSION_ID = "khkeolgobhdoloioehjgfpobjnmagfha"
FIREFOX_ADDON_SLUG = "bilisponsorblock"

STATS_CACHE_SECONDS = 24 * 60 * 60
AUDIENCE_REFRESH_SECONDS = 14 * 60 * 60
AUDIENCE_CACHE_KEY = "stats-api:audience-counts:v1"
SOURCE_VERSION_CACHE_KEY = "stats-api:source-version:v1"


def _json_response(data: dict[str, Any]) -> JsonResponse:
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})


def _bad_request() -> HttpResponse:
    return HttpResponse("Bad Request", status=400, content_type="text/plain")


def _parse_javascript_integer(value: str | None) -> int | None:
    """Match the useful part of JavaScript's parseInt behaviour."""
    if value is None:
        return None
    match = re.match(r"^[\s]*([+-]?\d+)", value)
    if not match:
        return None
    return int(match.group(1))


def _source_version() -> str:
    cached = cache.get(SOURCE_VERSION_CACHE_KEY)
    if cached is not None:
        return str(cached)
    version = Config.objects.filter(key="updated").values_list("value", flat=True).first() or "unknown"
    cache.set(SOURCE_VERSION_CACHE_KEY, version, timeout=None)
    return version


def set_source_version(version: str) -> None:
    """Publish a new mirror version only after its hourly refresh has completed."""
    cache.set(SOURCE_VERSION_CACHE_KEY, version, timeout=None)


def _cached_database_result(name: str, loader, *key_parts: object):
    version = _source_version()
    suffix = ":".join(str(part) for part in key_parts)
    key = f"stats-api:{name}:{version}:{suffix}"
    latest_key = f"stats-api:{name}:latest:{suffix}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        result = loader()
    except DatabaseError:
        stale = cache.get(latest_key)
        if stale is not None:
            return stale
        raise
    cache.set(key, result, STATS_CACHE_SECONDS)
    cache.set(latest_key, result, timeout=None)
    return result


def refresh_compatibility_cache(source_version: str) -> None:
    """Warm the legacy statistics used by the homepage and leaderboard."""
    set_source_version(source_version)
    for count_contributing_users in (False, True):
        _cached_database_result(
            "total",
            lambda enabled=count_contributing_users: _fetch_total_stats(enabled),
            str(count_contributing_users).lower(),
        )
    for sort_by in SORT_TYPE_MAP.values():
        for category_stats_enabled in (False, True):
            _cached_database_result(
                "top-users",
                lambda field=sort_by, enabled=category_stats_enabled: _fetch_top_users(field, enabled),
                sort_by,
                str(category_stats_enabled).lower(),
            )


def _normalise_number(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _javascript_round(value: float) -> int:
    """Return the integer produced by Math.round for finite values."""
    return math.floor(value + 0.5)


def _fetch_total_stats(count_contributing_users: bool) -> dict[str, Any]:
    user_count_sql = (
        '(SELECT COUNT(DISTINCT "userID") FROM "sponsorTimes") AS "userCount",'
        if count_contributing_users
        else ""
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f'''
            SELECT {user_count_sql}
                   COUNT(*) AS "totalSubmissions",
                   COALESCE(SUM("views"), 0) AS "viewCount",
                   COALESCE(SUM(("endTime" - "startTime") / 60 * "views"), 0) AS "minutesSaved"
            FROM "sponsorTimes"
            WHERE "shadowHidden" != 1 AND "votes" >= 0
            '''
        )
        row = cursor.fetchone()

    offset = 1 if count_contributing_users else 0
    return {
        "userCount": int(row[0]) if count_contributing_users else 0,
        "totalSubmissions": int(row[offset] or 0),
        "viewCount": int(row[offset + 1] or 0),
        "minutesSaved": _javascript_round(float(row[offset + 2] or 0)),
    }


def _fetch_top_users(sort_by: str, category_stats_enabled: bool) -> dict[str, Any]:
    fields = [
        "userName",
        "viewCount",
        "totalSubmissions",
        "minutesSaved",
        "userVotes",
        "portVideoSubmissions",
    ]
    if category_stats_enabled:
        fields.extend(CATEGORY_STATS_FIELDS)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)

    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT {quoted_fields} FROM "topUser" ORDER BY "{sort_by}" DESC LIMIT 100'
        )
        rows = cursor.fetchall()

    result = {
        "userNames": [],
        "viewCounts": [],
        "totalSubmissions": [],
        "minutesSaved": [],
        "votes": [],
        "portVideo": [],
    }
    if category_stats_enabled:
        result["categoryStats"] = []

    for row in rows:
        result["userNames"].append(row[0])
        result["viewCounts"].append(_normalise_number(row[1]))
        result["totalSubmissions"].append(_normalise_number(row[2]))
        result["minutesSaved"].append(_normalise_number(row[3]))
        result["votes"].append(_normalise_number(row[4]))
        result["portVideo"].append(_normalise_number(row[5]))
        if category_stats_enabled:
            result["categoryStats"].append([_normalise_number(value) for value in row[6:]])

    return result


def _fetch_port_video_user_counts() -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT COUNT(*) AS "portVideoSubmissions",
                   COALESCE("userNames"."userName", "portVideo"."userID") AS "userName"
            FROM "portVideo"
            LEFT JOIN "userNames" ON "portVideo"."userID" = "userNames"."userID"
            LEFT JOIN "shadowBannedUsers" ON "portVideo"."userID" = "shadowBannedUsers"."userID"
            WHERE "portVideo"."votes" > -1
              AND "portVideo"."hidden" = 0
              AND "shadowBannedUsers"."userID" IS NULL
            GROUP BY COALESCE("userNames"."userName", "portVideo"."userID")
            '''
        )
        return {row[1]: int(row[0]) for row in cursor.fetchall()}


def _fetch_top_category_users(sort_by: str, category: str) -> dict[str, Any]:
    max_reward_seconds = settings.STATS_MAX_REWARD_TIME_PER_SEGMENT_SECONDS
    with connection.cursor() as cursor:
        cursor.execute(
            f'''
            SELECT COUNT(*) AS "totalSubmissions",
                   SUM("sponsorTimes"."views") AS "viewCount",
                   SUM(((CASE
                       WHEN "sponsorTimes"."endTime" - "sponsorTimes"."startTime" > %s THEN %s
                       ELSE "sponsorTimes"."endTime" - "sponsorTimes"."startTime"
                   END) / 60) * "sponsorTimes"."views") AS "minutesSaved",
                   SUM("sponsorTimes"."votes") AS "userVotes",
                   COALESCE("userNames"."userName", "sponsorTimes"."userID") AS "userName"
            FROM "sponsorTimes"
            LEFT JOIN "userNames" ON "sponsorTimes"."userID" = "userNames"."userID"
            LEFT JOIN "shadowBannedUsers" ON "sponsorTimes"."userID" = "shadowBannedUsers"."userID"
            WHERE "sponsorTimes"."category" = %s
              AND "sponsorTimes"."votes" > -1
              AND "sponsorTimes"."shadowHidden" != 1
              AND "shadowBannedUsers"."userID" IS NULL
            GROUP BY COALESCE("userNames"."userName", "sponsorTimes"."userID")
            HAVING SUM("sponsorTimes"."votes") >= 0
            ORDER BY "{sort_by}" DESC
            LIMIT 100
            ''',
            [max_reward_seconds, max_reward_seconds, category],
        )
        rows = cursor.fetchall()

    port_video_counts = _fetch_port_video_user_counts()
    result = {
        "userNames": [],
        "viewCounts": [],
        "totalSubmissions": [],
        "votes": [],
        "portVideo": [],
        "minutesSaved": [],
    }
    for row in rows:
        user_name = row[4]
        result["userNames"].append(user_name)
        result["viewCounts"].append(_normalise_number(row[1]))
        result["totalSubmissions"].append(_normalise_number(row[0]))
        result["votes"].append(_normalise_number(row[3]))
        result["portVideo"].append(port_video_counts.get(user_name, 0))
        result["minutesSaved"].append(_normalise_number(row[2]))
    return result


def _http_get_text(url: str) -> str | None:
    request = Request(url, headers={"User-Agent": "BSBbrowser statistics/1.0"})
    try:
        with urlopen(request, timeout=settings.STATS_HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, ValueError):
        return None


def _http_get_json(url: str) -> dict[str, Any] | None:
    body = _http_get_text(url)
    if body is None:
        return None
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _fetch_chrome_users() -> int | None:
    body = _http_get_text(f"https://img.shields.io/chrome-web-store/users/{CHROME_EXTENSION_ID}")
    if body is None:
        return None
    match = re.search(r"<title>users: ([\d,.]+)([kKmM]?)</title>", body)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()]
    return int(number * multiplier)


def _fetch_firefox_users() -> int | None:
    data = _http_get_json(f"https://addons.mozilla.org/api/v5/addons/addon/{FIREFOX_ADDON_SLUG}/")
    value = data.get("average_daily_users") if data else None
    return int(value) if isinstance(value, (int, float)) else None


def _fetch_edge_users() -> int | None:
    data = _http_get_json(
        f"https://microsoftedge.microsoft.com/addons/getproductdetailsbycrxid/{EDGE_EXTENSION_ID}"
    )
    value = data.get("activeInstallCount") if data else None
    return int(value) if isinstance(value, (int, float)) else None


def _fetch_counter_users() -> int | None:
    base_url = settings.STATS_USER_COUNTER_URL
    if not base_url:
        return None
    data = _http_get_json(f"{base_url.rstrip('/')}/api/v1/userCount")
    value = data.get("userCount") if data else None
    return int(value) if isinstance(value, (int, float)) else None


def _get_audience_counts() -> tuple[int, int]:
    now = int(time.time())
    state = cache.get(AUDIENCE_CACHE_KEY) or {
        "checkedAt": 0,
        "chrome": 0,
        "firefox": 0,
        "edge": 0,
        "counter": 0,
    }
    if now - int(state.get("checkedAt", 0)) >= AUDIENCE_REFRESH_SECONDS:
        loaders = {
            "chrome": _fetch_chrome_users,
            "firefox": _fetch_firefox_users,
            "edge": _fetch_edge_users,
            "counter": _fetch_counter_users,
        }
        with ThreadPoolExecutor(max_workers=len(loaders)) as executor:
            futures = {executor.submit(loader): name for name, loader in loaders.items()}
            for future in as_completed(futures):
                try:
                    value = future.result()
                except Exception:  # A failed external counter must not fail database statistics.
                    value = None
                if value is not None:
                    state[futures[future]] = value
        state["checkedAt"] = now
        cache.set(AUDIENCE_CACHE_KEY, state, timeout=None)

    active_users = sum(int(state.get(name, 0)) for name in ("chrome", "firefox", "edge"))
    api_users = max(active_users, int(state.get("counter", 0)))
    return active_users, api_users


@require_GET
def get_total_stats(request: HttpRequest) -> JsonResponse:
    count_contributing_users = request.GET.get("countContributingUsers") == "true"
    stats = _cached_database_result(
        "total",
        lambda: _fetch_total_stats(count_contributing_users),
        str(count_contributing_users).lower(),
    )
    active_users, api_users = _get_audience_counts()
    return _json_response({
        "userCount": stats["userCount"],
        "activeUsers": active_users,
        "apiUsers": api_users,
        "viewCount": stats["viewCount"],
        "totalSubmissions": stats["totalSubmissions"],
        "minutesSaved": stats["minutesSaved"],
    })


@require_GET
def get_top_users(request: HttpRequest) -> HttpResponse:
    sort_type = _parse_javascript_integer(request.GET.get("sortType"))
    sort_by = SORT_TYPE_MAP.get(sort_type)
    if sort_by is None:
        return _bad_request()
    category_stats_enabled = request.GET.get("categoryStats") == "true"
    result = _cached_database_result(
        "top-users",
        lambda: _fetch_top_users(sort_by, category_stats_enabled),
        sort_by,
        str(category_stats_enabled).lower(),
    )
    return _json_response(result)


@require_GET
def get_top_category_users(request: HttpRequest) -> HttpResponse:
    sort_type = _parse_javascript_integer(request.GET.get("sortType"))
    sort_by = SORT_TYPE_MAP.get(sort_type)
    category = request.GET.get("category")
    if sort_by is None or category not in CATEGORY_LABELS or category == "chapter":
        return _bad_request()
    result = _cached_database_result(
        "top-category-users",
        lambda: _fetch_top_category_users(sort_by, category),
        sort_by,
        category,
    )
    return _json_response(result)


@require_GET
def get_stats_overview(request: HttpRequest) -> HttpResponse:
    data = read_statistics()
    if data is None:
        return JsonResponse({"detail": "Statistics have not been generated yet"}, status=503)
    etag = f'"stats-{data.get("sourceUpdatedAt", data.get("generatedAt", "unknown"))}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
    else:
        response = _json_response(data)
    response["ETag"] = etag
    response["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
    return response
