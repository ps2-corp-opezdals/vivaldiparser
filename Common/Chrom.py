"""
chrome_time.py — общий пересчёт Chromium-времени для модулей Vivaldi*.

Chromium хранит время как МИКРОсекунды с 1601-01-01 UTC.
FILETIME (его принимает каркасный TimeConverter) — 100-нс интервалы с той же эпохи,
поэтому перевод: filetime = chromium_microseconds * 10.
"""
from datetime import datetime, timezone

from Common.Routines import TimeConverter

FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def chromium_to_filetime(microseconds: int) -> int:
    """Перевести Chromium-время (мкс с 1601-01-01 UTC) в FILETIME."""
    return int(microseconds) * 10


def chromium_to_datetime(microseconds: int, tz=timezone.utc) -> datetime:
    """Перевести Chromium-время в aware-datetime в заданной таймзоне."""
    filetime = chromium_to_filetime(microseconds)
    dt = TimeConverter.FILETIMEToDatetime(filetime)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def format_datetime(dt: datetime) -> str:
    """Строка времени в едином формате для полей БД (ISO 8601 с пробелом)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(sep=" ")