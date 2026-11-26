# -*- coding: utf-8 -*-
"""
Функциональные тесты модуля VivaldiDownloads (ПВ-3).

Источник: таблицы downloads и downloads_url_chains базы History.
Проверяются критерии ТЗ: каждая реальная загрузка отражена, время начало/
конец согласовано с History, состояние расшифровано.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import SOURCE_DUMP, db_columns, db_rows, db_select

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

KNOWN_STATE_NAMES = {
    "In progress",
    "Complete",
    "Cancelled",
    "Interrupted (legacy bug 140687)",
    "Interrupted",
}


def chromium_to_utc(microseconds):
    """Независимый пересчёт Chromium-времени (мкс с 1601-01-01)."""
    return CHROMIUM_EPOCH + timedelta(microseconds=microseconds)


@pytest.fixture(scope="module")
def downloads_run(tmp_path_factory):
    """Прогон модуля на реальном дампе."""
    from conftest import ModuleRunner

    runner = ModuleRunner(tmp_path_factory.mktemp("downloads"))
    result, db_path = runner.run("VivaldiDownloads", SOURCE_DUMP)
    return runner, result, db_path


@pytest.fixture(scope="module")
def downloads_rows(downloads_run):
    """Строки таблицы Data результата VivaldiDownloads."""
    _, _, db_path = downloads_run
    return db_select(db_path, "select * from Data order by ID")


def test_downloads_returns_db_name(downloads_run, expected_rows):
    """Модуль возвращает имя БД и пишет все загрузки дампа."""
    _, result, db_path = downloads_run
    assert result == {"VivaldiDownloads": "VivaldiDownloads_result.sqlite"}
    assert db_rows(db_path) == expected_rows["VivaldiDownloads"]


def test_downloads_matches_source_table(downloads_rows):
    """Каждая загрузка из базы History попала в результат."""
    import sqlite3

    connection = sqlite3.connect(
        str(SOURCE_DUMP / "config" / "vivaldi" / "Default" / "History")
    )
    try:
        source_ids = {
            row[0] for row in connection.execute("select id from downloads")
        }
    finally:
        connection.close()
    assert {row["DownloadID"] for row in downloads_rows} == source_ids


def test_downloads_has_tz_fields(downloads_run, tz_fields):
    """Поля из ТЗ присутствуют (часть из них расширена модулем)."""
    _, _, db_path = downloads_run
    columns = db_columns(db_path)
    missing = [name for name in tz_fields["VivaldiDownloads"] if name not in columns]
    assert not missing, f"нет полей: {missing}"


def test_downloads_all_complete(downloads_rows):
    """В дампе все загрузки завершены: состояние расшифровано корректно."""
    for row in downloads_rows:
        assert row["StateName"] in KNOWN_STATE_NAMES, row
        assert row["State"] == 1, row
        assert row["StateName"] == "Complete", row


def test_downloads_bytes_consistent(downloads_rows):
    """Полученные байты не больше полных, обе величины неотрицательные."""
    for row in downloads_rows:
        assert row["ReceivedBytes"] >= 0, row
        assert row["TotalBytes"] >= row["ReceivedBytes"], row
        if row["State"] == 1:
            assert row["ReceivedBytes"] == row["TotalBytes"], row


def test_downloads_time_order_and_conversion(downloads_rows):
    """Время начала не позже конца, строки соответствуют chromium-меткам."""
    for row in downloads_rows:
        assert row["TimestampStart_UTC"] > 0, row
        assert chromium_to_utc(row["TimestampStart_UTC"]).isoformat().replace(
            "T", " "
        ) == row["DateTimeStart_UTC"], row
        if row["TimestampEnd_UTC"]:
            assert row["TimestampEnd_UTC"] >= row["TimestampStart_UTC"], row
            assert chromium_to_utc(row["TimestampEnd_UTC"]).isoformat().replace(
                "T", " "
            ) == row["DateTimeEnd_UTC"], row


def test_downloads_url_chain(downloads_rows):
    """URL = последний адрес цепочки, OriginalURL = первый."""
    for row in downloads_rows:
        chain = [line for line in row["URLChain"].splitlines() if line]
        assert chain, row
        assert row["OriginalURL"] == chain[0], row
        assert row["URL"] == chain[-1], row
        assert row["URL"].startswith("http"), row


def test_downloads_paths_extracted(downloads_rows):
    """Целевой путь загрузки заполнен и выглядит как путь ОС."""
    for row in downloads_rows:
        assert row["TargetPath"], row
        assert "/" in row["TargetPath"] or "\\" in row["TargetPath"], row


def test_downloads_danger_and_interrupt_decoded(downloads_rows):
    """Числовые коды опасности и прерывания имеют текстовые пары."""
    for row in downloads_rows:
        assert row["DangerTypeName"], row
        assert row["InterruptReasonName"], row
        assert str(row["DangerType"]) in row["DangerTypeName"] or not row["DangerType"], row


def test_downloads_guid_unique(downloads_rows):
    """GUID загрузки уникален, DataSource указывает на базу History."""
    guids = [row["GUID"] for row in downloads_rows]
    assert len(set(guids)) == len(guids), guids
    for row in downloads_rows:
        assert row["DataSource"].replace("\\", "/").endswith("History"), row


@pytest.mark.xfail(
    strict=True,
    reason="C1 (контракт п.7): Timestamp_*_UTC в Downloads/Cookies хранится в "
           "мкс Chromium, а в History — в 100-нс FILETIME. Единый формат не выдержан",
)
def test_downloads_timestamp_unit_matches_history(downloads_rows, tmp_path_factory):
    """Одинаковые по имени метки должны быть в одинаковых единицах."""
    from conftest import ModuleRunner

    runner = ModuleRunner(tmp_path_factory.mktemp("units"))
    _, history_db = runner.run("VivaldiHistory", SOURCE_DUMP)
    history_row = db_select(history_db, "select Timestamp_UTC from Data order by ID limit 1")[0]

    # масштаб обеих меток должен быть сопоставим (одна система единиц)
    ratio = history_row["Timestamp_UTC"] / downloads_rows[0]["TimestampStart_UTC"]
    assert 0.9 < ratio < 1.1, f"разные единицы измерения: ratio={ratio}"
