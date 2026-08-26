# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and persist public statistics from the reporting database."""

import fcntl
import json
import os
import tempfile
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from django.conf import settings
from django.db import connection

from .models import Config


STATS_SCHEMA_VERSION = 2
STATS_TIME_ZONE = "Asia/Shanghai"
PROJECT_START_DATE = date(2024, 1, 1)
SYSTEM_USER_IDS = ("PORT",)
HOURLY_DISTRIBUTION_DAYS = 90


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_updated_at() -> str:
    return Config.objects.filter(key="updated").values_list("value", flat=True).first() or _iso_utc_now()


def _source_timestamp(source_updated_at: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_end_date(source_updated_at: str) -> date:
    return (_source_timestamp(source_updated_at) + timedelta(hours=8)).date()


def _fetch_daily_user_submissions() -> list[tuple[date, str, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT (to_timestamp("timeSubmitted" / 1000.0)
                    AT TIME ZONE 'Asia/Shanghai')::date AS submission_day,
                   "userID",
                   COUNT(*) AS submissions
            FROM "sponsorTimes"
            WHERE "userID" <> ALL(%s)
              AND "timeSubmitted" > 0
            GROUP BY submission_day, "userID"
            ORDER BY submission_day, "userID"
            ''',
            [list(SYSTEM_USER_IDS)],
        )
        return [(row[0], row[1], int(row[2])) for row in cursor.fetchall()]


def _fetch_rolling_24h_contributors(source_updated_at: str) -> int:
    window_end_ms = int(_source_timestamp(source_updated_at).timestamp() * 1000)
    window_start_ms = window_end_ms - int(timedelta(hours=24).total_seconds() * 1000)
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT COUNT(DISTINCT "userID")
            FROM "sponsorTimes"
            WHERE "userID" <> ALL(%s)
              AND "timeSubmitted" > %s
              AND "timeSubmitted" <= %s
            ''',
            [list(SYSTEM_USER_IDS), window_start_ms, window_end_ms],
        )
        row = cursor.fetchone()
    return int(row[0] or 0)


def _fetch_current_totals() -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT COUNT(*) FILTER (
                       WHERE "shadowHidden" != 1 AND "votes" >= 0
                   ) AS total_submissions,
                   COALESCE(SUM("views") FILTER (
                       WHERE "shadowHidden" != 1 AND "votes" >= 0
                   ), 0) AS skip_count,
                   COALESCE(SUM(("endTime" - "startTime") / 60 * "views") FILTER (
                       WHERE "shadowHidden" != 1 AND "votes" >= 0
                   ), 0) AS minutes_saved,
                   COUNT(DISTINCT ("service", "videoID")) FILTER (
                       WHERE "userID" <> ALL(%s) AND "timeSubmitted" > 0
                         AND "shadowHidden" != 1 AND "votes" >= 0
                   ) AS covered_videos,
                   COUNT(DISTINCT "userID") FILTER (
                       WHERE "userID" <> ALL(%s) AND "timeSubmitted" > 0
                   ) AS contributor_count
            FROM "sponsorTimes"
            ''',
            [list(SYSTEM_USER_IDS), list(SYSTEM_USER_IDS)],
        )
        row = cursor.fetchone()
    return {
        "totalSubmissions": int(row[0] or 0),
        "skipCount": int(row[1] or 0),
        "minutesSaved": round(float(row[2] or 0)),
        "coveredVideos": int(row[3] or 0),
        "contributorCount": int(row[4] or 0),
    }


def _fetch_daily_video_coverage() -> list[tuple[date, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            WITH first_coverage AS (
                SELECT MIN((to_timestamp("timeSubmitted" / 1000.0)
                            AT TIME ZONE 'Asia/Shanghai')::date) AS coverage_day
                FROM "sponsorTimes"
                WHERE "userID" <> ALL(%s)
                  AND "timeSubmitted" > 0
                  AND "videoID" <> ''
                  AND "shadowHidden" != 1
                  AND "votes" >= 0
                GROUP BY "service", "videoID"
            )
            SELECT coverage_day, COUNT(*)
            FROM first_coverage
            GROUP BY coverage_day
            ORDER BY coverage_day
            ''',
            [list(SYSTEM_USER_IDS)],
        )
        return [(row[0], int(row[1])) for row in cursor.fetchall()]


def _fetch_hourly_contributions(start_date: date, end_date: date) -> list[tuple[int, int, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            WITH hourly AS (
                SELECT (to_timestamp("timeSubmitted" / 1000.0)
                        AT TIME ZONE 'Asia/Shanghai')::date AS submission_day,
                       EXTRACT(hour FROM to_timestamp("timeSubmitted" / 1000.0)
                               AT TIME ZONE 'Asia/Shanghai')::integer AS submission_hour,
                       COUNT(DISTINCT "userID") AS contributors,
                       COUNT(*) AS submissions
                FROM "sponsorTimes"
                WHERE "userID" <> ALL(%s)
                  AND "timeSubmitted" > 0
                  AND (to_timestamp("timeSubmitted" / 1000.0)
                       AT TIME ZONE 'Asia/Shanghai')::date BETWEEN %s AND %s
                GROUP BY submission_day, submission_hour
            )
            SELECT submission_hour,
                   SUM(contributors),
                   SUM(submissions)
            FROM hourly
            GROUP BY submission_hour
            ORDER BY submission_hour
            ''',
            [list(SYSTEM_USER_IDS), start_date, end_date],
        )
        return [(int(row[0]), int(row[1]), int(row[2])) for row in cursor.fetchall()]


def _fetch_contributor_distribution() -> list[dict[str, int | str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            WITH per_user AS (
                SELECT "userID", COUNT(*) AS submissions
                FROM "sponsorTimes"
                WHERE "userID" <> ALL(%s)
                  AND "timeSubmitted" > 0
                GROUP BY "userID"
            ), bucketed AS (
                SELECT CASE
                           WHEN submissions = 1 THEN 1
                           WHEN submissions <= 5 THEN 2
                           WHEN submissions <= 20 THEN 3
                           WHEN submissions <= 100 THEN 4
                           WHEN submissions <= 500 THEN 5
                           ELSE 6
                       END AS bucket,
                       COUNT(*) AS contributors,
                       SUM(submissions) AS submissions
                FROM per_user
                GROUP BY bucket
            )
            SELECT bucket, contributors, submissions
            FROM bucketed
            ORDER BY bucket
            ''',
            [list(SYSTEM_USER_IDS)],
        )
        labels = {1: "1", 2: "2–5", 3: "6–20", 4: "21–100", 5: "101–500", 6: "501+"}
        return [
            {
                "range": labels[int(row[0])],
                "contributors": int(row[1]),
                "submissions": int(row[2]),
            }
            for row in cursor.fetchall()
        ]


def _fetch_category_distribution() -> list[dict[str, int | str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT "category",
                   COUNT(*) AS submissions,
                   COALESCE(SUM("views"), 0) AS skip_count,
                   COALESCE(SUM(("endTime" - "startTime") / 60 * "views"), 0) AS minutes_saved
            FROM "sponsorTimes"
            WHERE "userID" <> ALL(%s)
              AND "timeSubmitted" > 0
              AND "shadowHidden" != 1
              AND "votes" >= 0
            GROUP BY "category"
            ORDER BY submissions DESC
            ''',
            [list(SYSTEM_USER_IDS)],
        )
        return [
            {
                "category": row[0],
                "submissions": int(row[1]),
                "skipCount": int(row[2] or 0),
                "minutesSaved": round(float(row[3] or 0)),
            }
            for row in cursor.fetchall()
        ]


def _build_activity_series(
    rows: Iterable[tuple[date, str, int]],
    end_date: date,
    start_date: date | None = None,
) -> list[dict[str, Any]]:
    submissions_by_day: Counter[date] = Counter()
    users_by_day: dict[date, set[str]] = defaultdict(set)
    first_date: date | None = None

    for submission_day, user_id, submission_count in rows:
        if submission_day > end_date or (start_date is not None and submission_day < start_date):
            continue
        submissions_by_day[submission_day] += submission_count
        users_by_day[submission_day].add(user_id)
        if first_date is None or submission_day < first_date:
            first_date = submission_day

    if first_date is None and start_date is None:
        return []

    rolling_days: deque[tuple[date, set[str]]] = deque()
    rolling_users: Counter[str] = Counter()
    cumulative_submissions = 0
    result = []
    current_day = start_date or first_date

    while current_day <= end_date:
        current_users = users_by_day.get(current_day, set())
        rolling_days.append((current_day, current_users))
        rolling_users.update(current_users)

        oldest_allowed = current_day - timedelta(days=29)
        while rolling_days and rolling_days[0][0] < oldest_allowed:
            _, expired_users = rolling_days.popleft()
            for user_id in expired_users:
                rolling_users[user_id] -= 1
                if rolling_users[user_id] == 0:
                    del rolling_users[user_id]

        daily_submissions = submissions_by_day[current_day]
        cumulative_submissions += daily_submissions
        result.append({
            "date": current_day.isoformat(),
            "dau": len(current_users),
            "mau30": len(rolling_users),
            "dailySubmissions": daily_submissions,
            "cumulativeSubmissions": cumulative_submissions,
        })
        current_day += timedelta(days=1)

    return result


def _add_video_coverage(
    activity: list[dict[str, Any]],
    rows: Iterable[tuple[date, int]],
) -> list[dict[str, Any]]:
    coverage_by_day: Counter[date] = Counter()
    for coverage_day, video_count in rows:
        coverage_by_day[coverage_day] += video_count
    cumulative_coverage = 0
    result = []
    for item in activity:
        daily_coverage = coverage_by_day[date.fromisoformat(item["date"])]
        cumulative_coverage += daily_coverage
        result.append({
            **item,
            "dailyCoveredVideos": daily_coverage,
            "cumulativeCoveredVideos": cumulative_coverage,
        })
    return result


def _build_hourly_distribution(
    rows: Iterable[tuple[int, int, int]],
    period_days: int,
) -> list[dict[str, int | float]]:
    values = {hour: (contributors, submissions) for hour, contributors, submissions in rows}
    divisor = max(1, period_days)
    return [
        {
            "hour": hour,
            "averageContributors": round(values.get(hour, (0, 0))[0] / divisor, 1),
            "averageSubmissions": round(values.get(hour, (0, 0))[1] / divisor, 1),
        }
        for hour in range(24)
    ]


def _read_existing_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Statistics file does not contain an object: {path}")
    return value


def _merge_skip_snapshot(
    existing: dict[str, Any] | None,
    source_updated_at: str,
    captured_at: str,
    totals: dict[str, int],
) -> list[dict[str, Any]]:
    snapshots = list(existing.get("skipSnapshots", [])) if existing else []
    snapshot = {
        "capturedAt": captured_at,
        "sourceUpdatedAt": source_updated_at,
        "skipCount": totals["skipCount"],
        "totalSubmissions": totals["totalSubmissions"],
        "minutesSaved": totals["minutesSaved"],
    }
    replaced = False
    for index, current in enumerate(snapshots):
        if current.get("sourceUpdatedAt") == source_updated_at:
            snapshots[index] = snapshot
            replaced = True
            break
    if not replaced:
        snapshots.append(snapshot)
    snapshots.sort(key=lambda item: item.get("sourceUpdatedAt", item.get("capturedAt", "")))
    return snapshots


def _write_atomically(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def refresh_statistics() -> dict[str, Any]:
    path = Path(settings.STATS_DATA_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        existing = _read_existing_file(path)
        source_updated_at = _source_updated_at()
        captured_at = _iso_utc_now()
        rolling_24h_contributors = _fetch_rolling_24h_contributors(source_updated_at)
        activity = _build_activity_series(
            _fetch_daily_user_submissions(),
            _source_end_date(source_updated_at),
            PROJECT_START_DATE,
        )
        activity = _add_video_coverage(activity, _fetch_daily_video_coverage())
        source_end_date = _source_end_date(source_updated_at)
        hourly_start_date = source_end_date - timedelta(days=HOURLY_DISTRIBUTION_DAYS - 1)
        hourly_contribution = _build_hourly_distribution(
            _fetch_hourly_contributions(hourly_start_date, source_end_date),
            HOURLY_DISTRIBUTION_DAYS,
        )
        totals = _fetch_current_totals()
        contributor_distribution = _fetch_contributor_distribution()
        category_distribution = _fetch_category_distribution()
        latest_activity = activity[-1] if activity else {
            "dau": 0,
            "mau30": 0,
            "cumulativeSubmissions": 0,
        }
        data = {
            "schemaVersion": STATS_SCHEMA_VERSION,
            "generatedAt": captured_at,
            "sourceUpdatedAt": source_updated_at,
            "projectStartedAt": PROJECT_START_DATE.isoformat(),
            "timeZone": STATS_TIME_ZONE,
            "definitions": {
                "dau": "当天至少提交一个片段的唯一用户数，不含系统用户 PORT",
                "dau24h": "截至镜像刷新时间最近 24 小时至少提交一个片段的唯一用户数，不含系统用户 PORT",
                "mau30": "截至当天最近 30 天至少提交一个片段的唯一用户数，不含系统用户 PORT",
                "submissions": "真实用户提交的片段数，不含系统用户 PORT",
                "skipCount": "计算时有效片段的累计跳过次数",
                "coveredVideos": "至少有一个当前有效社区片段的唯一视频数",
                "hourlyContribution": "最近 90 天按北京时间计算的日均小时贡献人数和提交量",
                "minutesSaved": "按当前有效片段时长和累计跳过次数估算的节省分钟数",
            },
            "summary": {
                "dau": latest_activity["dau"],
                "dau24h": rolling_24h_contributors,
                "mau30": latest_activity["mau30"],
                "communitySubmissions": latest_activity["cumulativeSubmissions"],
                **totals,
            },
            "activity": activity,
            "hourlyContribution": {
                "periodDays": HOURLY_DISTRIBUTION_DAYS,
                "points": hourly_contribution,
            },
            "contributorDistribution": contributor_distribution,
            "categoryDistribution": category_distribution,
            "skipSnapshots": _merge_skip_snapshot(
                existing,
                source_updated_at,
                captured_at,
                totals,
            ),
        }
        _write_atomically(path, data)
        return data


def read_statistics() -> dict[str, Any] | None:
    return _read_existing_file(Path(settings.STATS_DATA_FILE))
