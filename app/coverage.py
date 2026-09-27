"""开放时段照明覆盖巡检服务：按日裁剪展柜灯光时间线并找低于门槛的缺口。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from .timeline import (
    coverage_in_window,
    merge_intervals,
    parse_utc,
)

# 数值输出统一保留 6 位小数，抑制浮点累计噪声
_ROUND = 6


def _r(value: float) -> float:
    return round(float(value), _ROUND)


def load_range_segments(
    conn: sqlite3.Connection,
    showcase_id: int,
    range_start: datetime,
    range_end: datetime,
):
    """取出展柜与时间范围 [range_start, range_end) 有交集的全部区间并合并。

    跨年范围按 UTC 字符串粗过滤（定长 ISO 时间戳字典序与时间序一致），
    跨年区间照常取出，逐日裁剪时只取落在当天开放窗口内的部分。
    """
    rows = conn.execute(
        """
        SELECT start_ts, end_ts, lux FROM light_intervals
        WHERE showcase_id = ?
          AND end_ts > ? AND start_ts < ?
        ORDER BY start_ts
        """,
        (showcase_id, range_start.isoformat(), range_end.isoformat()),
    ).fetchall()
    intervals = [
        (parse_utc(r["start_ts"]), parse_utc(r["end_ts"]), float(r["lux"]))
        for r in rows
    ]
    return merge_intervals(intervals)


def _at(day: date, clock: time) -> datetime:
    """把 UTC 日期与（已归一化为 UTC 的）钟点组合成 UTC datetime。"""
    return datetime.combine(day, clock).replace(tzinfo=timezone.utc)


def build_lighting_coverage(
    showcase: sqlite3.Row,
    segments,
    date_from: date,
    date_to: date,
    open_from: time,
    open_to: time,
    min_lux: float,
) -> dict:
    """逐开放日巡检：裁剪已合并时间线 → 统计达标时长、比例与连续缺口。

    完全无灯的开放日会得到一个覆盖整个窗口的缺口；跨日灯光区间只在其落入
    当天开放窗口的部分计入；恰好达到门槛算达标。
    """
    span = (date_to - date_from).days
    days = []
    for offset in range(span + 1):
        day = date_from + timedelta(days=offset)
        window_start = _at(day, open_from)
        window_end = _at(day, open_to)
        result = coverage_in_window(segments, window_start, window_end, min_lux)
        days.append(
            {
                "day": day,
                "open_start": window_start,
                "open_end": window_end,
                "min_lux": float(min_lux),
                "met_hours": _r(result.met_hours),
                "met_ratio": _r(result.met_ratio),
                "gaps": [{"start": start, "end": end} for start, end in result.gaps],
            }
        )

    return {
        "showcase_id": showcase["id"],
        "showcase_name": showcase["name"],
        "date_from": date_from,
        "date_to": date_to,
        "open_from": open_from,
        "open_to": open_to,
        "min_lux": float(min_lux),
        "days": days,
    }
