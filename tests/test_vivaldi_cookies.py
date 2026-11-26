# -*- coding: utf-8 -*-
"""
Функциональные тесты модуля VivaldiCookies (ПВ-5).

Проверяются критерии ТЗ: куки извлечены с корректными хостом/именем/сроком,
обработаны оба расположения файла (Default/Cookies и Default/Network/Cookies),
решение по шифрованию зафиксировано (значения не расшифровываются).
"""

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from conftest import SOURCE_DUMP, db_columns, db_rows, db_select

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
REAL_COOKIES = SOURCE_DUMP / "config" / "vivaldi" / "Default" / "Cookies"


def chromium_to_utc(microseconds):
    """Независимый пересчёт Chromium-времени."""
    return CHROMIUM_EPOCH + timedelta(microseconds=microseconds)


@pytest.fixture(scope="module")
def cookies_run(tmp_path_factory):
    """Прогон модуля на реальном дампе."""
    from conftest import ModuleRunner

    runner = ModuleRunner(tmp_path_factory.mktemp("cookies"))
    result, db_path = runner.run("VivaldiCookies", SOURCE_DUMP)
    return runner, result, db_path


@pytest.fixture(scope="module")
def cookies_rows(cookies_run):
    """Строки таблицы Data результата VivaldiCookies."""
    _, _, db_path = cookies_run
    return db_select(db_path, "select * from Data order by ID")


def test_cookies_matches_source_table(cookies_rows):
    """Число строк равно числу записей в исходной базе Cookies."""
    connection = sqlite3.connect(f"file:{REAL_COOKIES}?mode=ro", uri=True)
    try:
        source_count = connection.execute("select count(*) from cookies").fetchone()[0]
    finally:
        connection.close()
    assert len(cookies_rows) == source_count


def test_cookies_returns_db_name(cookies_run, expected_rows):
    """Модуль возвращает имя БД и пишет все куки дампа."""
    _, result, db_path = cookies_run
    assert result == {"VivaldiCookies": "VivaldiCookies_result.sqlite"}
    assert db_rows(db_path) == expected_rows["VivaldiCookies"]


def test_cookies_has_tz_fields(cookies_run, tz_fields):
    """Поля из ТЗ присутствуют в выходной таблице."""
    _, _, db_path = cookies_run
    columns = db_columns(db_path)
    missing = [name for name in tz_fields["VivaldiCookies"] if name not in columns]
    assert not missing, f"нет полей: {missing}"


def test_cookies_host_name_path_filled(cookies_rows):
    """Хост, имя и путь заполнены у каждой записи."""
    for row in cookies_rows:
        assert row["HostKey"], row
        assert row["Name"], row
        assert row["Path"], row


def test_cookies_flags_are_booleans(cookies_rows):
    """Флаги Secure/HttpOnly/HasExpires/IsPersistent — только 0 или 1."""
    for row in cookies_rows:
        for flag in ("IsSecure", "IsHttpOnly", "HasExpires", "IsPersistent"):
            assert row[flag] in (0, 1), (flag, row["HostKey"], row["Name"])


def test_cookies_expiry_consistent(cookies_rows):
    """Срок действия заполнен там, где заявлен, и не раньше создания."""
    for row in cookies_rows:
        if row["HasExpires"]:
            assert row["DateTimeExpires_UTC"], row["HostKey"]
            assert row["TimestampExpires_UTC"] > row["TimestampCreation_UTC"], row["HostKey"]
        assert chromium_to_utc(row["TimestampCreation_UTC"]).isoformat().replace(
            "T", " "
        ) == row["DateTimeCreation_UTC"], row["HostKey"]


def test_cookies_values_are_not_decrypted(cookies_rows):
    """
    Режим по умолчанию (ТЗ ПВ-5 п.2): извлекаются только метаданные.
    Открытое значение пустое, зашифрованное сохранено как есть.
    """
    for row in cookies_rows:
        assert row["Value"] == "", row["HostKey"]
    with_encrypted = [row for row in cookies_rows if row["EncryptedValue"]]
    assert len(with_encrypted) == len(cookies_rows), "потеряны зашифрованные значения"


def test_cookies_same_site_and_source_decoded(cookies_rows):
    """Числовые коды SameSite/SourceType имеют текстовые пары."""
    for row in cookies_rows:
        assert row["SameSiteName"], row
        assert row["SourceTypeName"], row
        assert row["PriorityName"], row


def test_cookies_network_location_supported(runner, tmp_path):
    """
    ТЗ ПВ-5 п.6: файл Cookies может лежать в Default/Network/Cookies.
    Модуль обязан найти его и там, и в Default/Cookies.
    """
    modern = tmp_path / "modern" / "config" / "vivaldi" / "Default" / "Network"
    modern.mkdir(parents=True)
    shutil.copy2(REAL_COOKIES, modern / "Cookies")

    result, db_path = runner.run("VivaldiCookies", tmp_path / "modern")
    assert result, "куки в Network/Cookies не найдены"
    assert db_rows(db_path) > 0


def test_cookies_both_locations_collected(runner, tmp_path):
    """Если файл есть в обоих местах, обрабатываются обе базы (нет тихой потери)."""
    profile = tmp_path / "both" / "config" / "vivaldi" / "Default"
    profile.mkdir(parents=True)
    network = profile / "Network"
    network.mkdir()
    shutil.copy2(REAL_COOKIES, profile / "Cookies")
    shutil.copy2(REAL_COOKIES, network / "Cookies")

    _, db_path = runner.run("VivaldiCookies", tmp_path / "both")
    rows = db_rows(db_path)
    assert rows >= 2 * 395, f"ожидались обе базы, получено {rows}"


def test_cookies_corrupt_database_skipped(runner, tmp_path):
    """Битая база Cookies не роняет модуль и не создаёт фантомную БД."""
    profile = tmp_path / "corrupt" / "config" / "vivaldi" / "Default"
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("это не sqlite", encoding="utf-8")

    result, db_path = runner.run("VivaldiCookies", tmp_path / "corrupt")
    assert result == {}
    assert db_rows(db_path) == 0
