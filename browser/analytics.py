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


STATS_SCHEMA_VERSION = 1
STATS_TIME_ZONE = "Asia/Shanghai"
PROJECT_START_DATE = date(2024, 1, 1)
SYSTEM_USER_IDS = ("PORT",)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_updated_at() -> str:
    return Config.objects.filter(key="updated").values_list("value", flat=True).first() or _iso_utc_now()


def _source_end_date(source_updated_at: str) -> date:
    try:
        parsed = datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).date()
    return (parsed + timedelta(hours=8)).date()


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


def _fetch_current_totals() -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT COUNT(*) AS total_submissions,
                   COALESCE(SUM("views"), 0) AS skip_count,
                   COALESCE(SUM(("endTime" - "startTime") / 60 * "views"), 0) AS minutes_saved
            FROM "sponsorTimes"
            WHERE "shadowHidden" != 1 AND "votes" >= 0
            '''
        )
        row = cursor.fetchone()
    return {
        "totalSubmissions": int(row[0] or 0),
        "skipCount": int(row[1] or 0),
        "minutesSaved": round(float(row[2] or 0)),
    }


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
        activity = _build_activity_series(
            _fetch_daily_user_submissions(),
            _source_end_date(source_updated_at),
            PROJECT_START_DATE,
        )
        totals = _fetch_current_totals()
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
                "mau30": "截至当天最近 30 天至少提交一个片段的唯一用户数，不含系统用户 PORT",
                "submissions": "真实用户提交的片段数，不含系统用户 PORT",
                "skipCount": "计算时有效片段的累计跳过次数",
            },
            "summary": {
                "dau": latest_activity["dau"],
                "mau30": latest_activity["mau30"],
                "communitySubmissions": latest_activity["cumulativeSubmissions"],
                **totals,
            },
            "activity": activity,
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
