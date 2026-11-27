# -*- coding: utf-8 -*-
"""
Функциональные тесты модуля VivaldiHistory (ПВ-2).

Проверяются критерии приёмки ТЗ: есть записи, время пересчитано верно,
поля соответствуют контракту, при отсутствии файла модуль не падает.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import MODULE_NAMES, SOURCE_DUMP, db_columns, db_rows, db_select

# Первый визит дампа (visits.id=1 -> urls.id=1)
FIRST_URL = "https://vivaldi.com/getstarted/"
FIRST_TITLE = "Welcome to Vivaldi"
FIRST_VISIT_MICROSECONDS = 13438450097139433
FIRST_VISIT_UTC = "2026-11-06 14:48:17.139433+00:00"

TRANSITION_BASE_NAMES = {
    0: "LINK",
    1: "TYPED",
    2: "AUTO_BOOKMARK",
    3: "AUTO_SUBFRAME",
    4: "MANUAL_SUBFRAME",
    5: "GENERATED",
    6: "AUTO_TOPLEVEL",
    7: "FORM_SUBMIT",
    8: "RELOAD",
    9: "KEYWORD",
    10: "KEYWORD_GENERATED",
}


def chromium_to_utc(microseconds):
    """Независимый пересчёт Chromium-времени для сверки с модулем."""
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=microseconds)


@pytest.fixture(scope="module")
def history_run(tmp_path_factory):
    """Один прогон модуля на реальном дампе для всех тестов файла."""
    from conftest import ModuleRunner

    runner = ModuleRunner(tmp_path_factory.mktemp("history"))
    result, db_path = runner.run("VivaldiHistory", SOURCE_DUMP)
    return runner, result, db_path


@pytest.fixture(scope="module")
def history_rows(history_run):
    """Строки таблицы Data результата VivaldiHistory."""
    _, _, db_path = history_run
    return db_select(db_path, "select * from Data order by ID")


def test_history_returns_db_name(history_run, expected_rows):
    """Модуль возвращает {MODULENAME: имя БД} и пишет ожидаемое число записей."""
    _, result, db_path = history_run
    assert result == {"VivaldiHistory": "VivaldiHistory_result.sqlite"}
    assert db_path.exists()
    assert db_rows(db_path) == expected_rows["VivaldiHistory"]


def test_history_has_tz_fields(history_run, tz_fields):
    """Набор полей ТЗ присутствует в выходной таблице."""
    _, _, db_path = history_run
    columns = db_columns(db_path)
    missing = [name for name in tz_fields["VivaldiHistory"] if name not in columns]
    assert not missing, f"нет полей: {missing}"


def test_history_first_record_matches_dump(history_rows):
    """Первая запись совпадает со значением из базы History (сверка вручную)."""
    row = history_rows[0]
    assert row["URL"] == FIRST_URL
    assert row["Title"] == FIRST_TITLE
    assert row["DateTime_UTC"] == FIRST_VISIT_UTC
    # после правки C1 метка хранится в микросекундах Chromium, как в других модулях
    assert row["Timestamp_UTC"] == FIRST_VISIT_MICROSECONDS
    assert row["TransitionName"] == TRANSITION_BASE_NAMES[row["Transition"] & 0xFF]


def test_history_timestamp_is_chromium_microseconds(history_rows):
    """Timestamp_UTC и DateTime_UTC описывают один и тот же момент."""
    for row in history_rows:
        assert row["Timestamp_UTC"] >= 0, row["URL"]
        assert chromium_to_utc(row["Timestamp_UTC"]).isoformat().replace(
            "T", " "
        ) == row["DateTime_UTC"], row["URL"]


def test_history_local_time_matches_offset(history_rows):
    """DateTime_Local = DateTime_UTC + TimeZoneOffset для каждой записи."""
    for row in history_rows:
        utc = datetime.fromisoformat(row["DateTime_UTC"])
        local = datetime.fromisoformat(row["DateTime_Local"])
        assert local.utcoffset() == timedelta(minutes=row["TimeZoneOffset"]), row["URL"]
        assert (local - utc) == timedelta(0), row["URL"]


def test_history_urls_are_absolute(history_rows):
    """URL обязательно абсолютный и с расшифрованным переходом."""
    for row in history_rows:
        assert row["URL"].startswith(("http://", "https://", "file://")), row["URL"]
        assert row["TransitionName"], row["URL"]
        assert isinstance(row["Transition"], int)


def test_history_visit_count_and_duration_sane(history_rows):
    """Число визитов и длительность не отрицательные."""
    for row in history_rows:
        assert row["VisitCount"] >= 1, row["URL"]
        assert row["VisitDuration"] >= 0, row["URL"]


def test_history_data_source_points_to_dump(history_rows):
    """DataSource ведёт на файл History внутри дампа (не на путь разработчика)."""
    for row in history_rows:
        assert row["DataSource"], row["URL"]
        assert row["DataSource"].replace("\\", "/").endswith("History"), row["DataSource"]
        assert "vivaldi" in row["DataSource"].lower(), row["DataSource"]


def test_history_deduplication_between_urls_and_visits(history_run, history_rows):
    """Количество строк равно числу визитов, а не числу уникальных URL."""
    import sqlite3

    connection = sqlite3.connect(str(SOURCE_DUMP / "config" / "vivaldi" / "Default" / "History"))
    try:
        visits = connection.execute("select count(*) from visits").fetchone()[0]
        urls = connection.execute("select count(*) from urls").fetchone()[0]
    finally:
        connection.close()
    assert len(history_rows) == visits
    assert urls < visits, "ожидается объединение нескольких визитов одного URL"


def test_history_uses_machine_timezone(history_rows):
    """
    B6 исправлен: DateTime_Local и TimeZoneOffset берутся с машины,
    как в VivaldiBookmarks, а не зашиты как +05:00.
    """
    machine_offset = int(datetime.now().astimezone().utcoffset().total_seconds() // 60)
    for row in history_rows:
        assert row["TimeZoneOffset"] == machine_offset, row["URL"]
        assert datetime.fromisoformat(row["DateTime_Local"]).utcoffset() == timedelta(
            minutes=machine_offset
        ), row["URL"]


def test_history_survives_zero_visit_time(runner, tmp_path):
    """
    Z1 исправлен: запись с visit_time = 0 не роняет модуль и весь прогон.
    Отдельно проверяется, что файл с такими данными обрабатывается до конца.
    """
    import sqlite3

    storage = tmp_path / "zero_history"
    storage.mkdir()
    db_file = storage / "History"
    connection = sqlite3.connect(str(db_file))
    connection.executescript(
        """
        CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT,
                          visit_count INTEGER, last_visit_time INTEGER);
        CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER,
                             from_visit INTEGER, transition INTEGER, visit_duration INTEGER);
        INSERT INTO urls (id, url, title, visit_count, last_visit_time)
             VALUES (1, 'https://example.com/zero', 'Zero', 1, 0);
        INSERT INTO visits (id, url, visit_time, from_visit, transition, visit_duration)
             VALUES (1, 1, 0, 0, 0, 0), (2, 1, 13438450097139433, 0, 805306374, 100);
        """
    )
    connection.commit()
    connection.close()

    result, db_path = runner.run("VivaldiHistory", storage)
    rows = db_select(db_path, "select URL, Timestamp_UTC, DateTime_UTC from Data order by ID")
    assert result, "модуль не вернул результат для базы с одной нормальной записью"
    assert len(rows) == 2
    assert rows[0]["Timestamp_UTC"] == 0
    assert rows[1]["Timestamp_UTC"] == FIRST_VISIT_MICROSECONDS


@pytest.mark.xfail(
    strict=True,
    reason="Z1-1 (ПВ-1/ПВ-2): визит с visit_time = 0 получает DateTime_UTC "
           "'1970-01-01 00:00:00+00:00' вместо пустой строки",
)
def test_history_zero_time_should_be_empty(runner, tmp_path):
    """Нулевое время визита не должно выглядеть как событие 1 января 1970."""
    import sqlite3

    storage = tmp_path / "zero_time"
    storage.mkdir()
    connection = sqlite3.connect(str(storage / "History"))
    connection.executescript(
        """
        CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT,
                          visit_count INTEGER, last_visit_time INTEGER);
        CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER,
                             from_visit INTEGER, transition INTEGER, visit_duration INTEGER);
        INSERT INTO urls VALUES (1, 'https://example.com/zero', 'Zero', 1, 0);
        INSERT INTO visits VALUES (1, 1, 0, 0, 0, 0);
        """
    )
    connection.commit()
    connection.close()

    _, db_path = runner.run("VivaldiHistory", storage)
    row = db_select(db_path, "select * from Data")[0]
    assert row["DateTime_UTC"] == "", row["DateTime_UTC"]
