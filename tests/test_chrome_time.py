# -*- coding: utf-8 -*-
"""
Тесты общего модуля пересчёта Chromium-времени (Common/Chrom.py).

По ТЗ это п.3 ПВ-7: для конкретной даты сверить ожидаемое и фактическое
значение. Все модули обязаны использовать именно эти функции.
"""

from datetime import datetime, timezone

import pytest

from Common.Chrom import (
    FILETIME_EPOCH,
    chromium_to_datetime,
    chromium_to_filetime,
    format_datetime,
)
from Common.Routines import FixedOffset as tzInfo

# Время первого визита из дампа Source-vivaldi (таблица visits, id=1)
REAL_VISIT_MICROSECONDS = 13438450097139433
EPOCH_MICROSECONDS = 11644473600000000  # 1970-01-01 00:00:00 UTC


def test_filetime_epoch_is_unix_epoch():
    """Chromium-время эпохи Unix должно давать ровно FILETIME эпохи Unix."""
    assert chromium_to_filetime(EPOCH_MICROSECONDS) == 116_444_736_000_000_000


def test_filetime_is_microseconds_times_ten():
    """Пересчёт микросекунд Chromium в 100-нс интервалы FILETIME."""
    assert chromium_to_filetime(REAL_VISIT_MICROSECONDS) == 134_384_500_971_394_330
    assert chromium_to_filetime(0) == 0


def test_datetime_epoch():
    """Эпоха Unix через Chromium-время."""
    assert FILETIME_EPOCH == datetime(1601, 1, 1, tzinfo=timezone.utc)
    assert chromium_to_datetime(EPOCH_MICROSECONDS) == datetime(
        1970, 1, 1, tzinfo=timezone.utc
    )


def test_zero_chromium_time_raises():
    """
    Фактическое поведение: нулевое Chromium-время (в базе это "времени нет")
    приводит к OSError внутри каркасного TimeConverter.
    Осталось зафиксировано, чтобы видеть, где именно узкое место.
    """
    with pytest.raises(OSError):
        chromium_to_datetime(0)


@pytest.mark.xfail(
    strict=True,
    reason="Z1 (ПВ-1): chromium_to_datetime обязан корректно обрабатывать 0, "
           "иначе модули падают на артефактах с пустым временем",
)
def test_zero_chromium_time_should_be_safe():
    """Ожидаемое поведение: 0 -> 1601-01-01 либо явный None, без исключения."""
    assert chromium_to_datetime(0) == datetime(1601, 1, 1, tzinfo=timezone.utc)


def test_datetime_real_visit_utc():
    """Сверка реальной даты из дампа с эталоном, посчитанным вручную."""
    assert chromium_to_datetime(REAL_VISIT_MICROSECONDS) == datetime(
        2026, 11, 6, 14, 48, 17, 139433, tzinfo=timezone.utc
    )


def test_datetime_real_visit_local():
    """Локальное время: +05:00 (Екатеринбург) от того же момента."""
    offset = tzInfo(300, "Asia/Yekaterinburg")
    local = chromium_to_datetime(REAL_VISIT_MICROSECONDS, tz=offset)
    assert local.isoformat() == "2026-11-06T19:48:17.139433+05:00"
    # один и тот же момент времени, разные представления
    assert local == chromium_to_datetime(REAL_VISIT_MICROSECONDS)


def test_format_datetime_software_format():
    """Единый формат строки для полей DateTime_* в БД модулей."""
    assert format_datetime(chromium_to_datetime(REAL_VISIT_MICROSECONDS)) == (
        "2026-11-06 14:48:17.139433+00:00"
    )
    offset = tzInfo(300, "Asia/Yekaterinburg")
    assert format_datetime(chromium_to_datetime(REAL_VISIT_MICROSECONDS, tz=offset)) == (
        "2026-11-06 19:48:17.139433+05:00"
    )


def test_format_datetime_naive_and_none():
    """Naive-datetime считается UTC, None отдаётся пустой строкой."""
    assert format_datetime(None) == ""
    assert format_datetime(datetime(2026, 1, 1, 12, 0, 0)) == (
        "2026-01-01 12:00:00+00:00"
    )


def test_roundtrip_with_framework_converter():
    """Обратный ход: FILETIME -> datetime -> FILETIME совпадает."""
    filetime = chromium_to_filetime(REAL_VISIT_MICROSECONDS)
    back = datetime.fromtimestamp(
        (filetime - 116444736000000000) / 10_000_000, tz=timezone.utc
    )
    assert back == chromium_to_datetime(REAL_VISIT_MICROSECONDS)


@pytest.mark.parametrize(
    "microseconds,expected_utc",
    [
        (13438450155728229, "2026-11-06 14:49:15.728229+00:00"),
        (13438450574673643, "2026-11-06 14:56:14.673643+00:00"),
        (13438450578590492, "2026-11-06 14:56:18.590492+00:00"),
    ],
)
def test_known_values_from_dump(microseconds, expected_utc):
    """Точки из реального дампа: визиты и начала/концы загрузок."""
    assert format_datetime(chromium_to_datetime(microseconds)) == expected_utc
