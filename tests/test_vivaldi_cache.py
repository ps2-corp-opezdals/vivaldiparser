# -*- coding: utf-8 -*-
"""
Функциональные тесты модуля VivaldiCache (ПВ-6).

Источник: настоящий срез Simple Cache профиля Vivaldi
(tests/fixtures/cache/vivaldi/Default/Cache/Cache_Data, 21 файл:
17 читаемых записей, 3 записи нулевого размера, 1 заведомо битая).

Формат записи проверен по дампу:
    смещение 4  — magic 0xFCFB6D1B;
    смещение 12 — длина ключа;
    смещение 24 — ключ вида "1/0/_dk_<URL> <URL> ...";
    времени создания записи в заголовке этого дампа нет.
"""

import os
import shutil
import time

import pytest

from conftest import (
    CACHE_FIXTURE_DATA,
    CACHE_FIXTURE_STORAGE,
    REAL_CACHE_DIR,
    ModuleRunner,
    db_columns,
    db_rows,
    db_select,
    simple_cache_key,
)

FIXTURE_READABLE_ENTRIES = 17
FIXTURE_ALL_FILES = 21


@pytest.fixture(scope="module")
def cache_run(tmp_path_factory):
    """Прогон модуля по фикстуре Simple Cache."""
    runner = ModuleRunner(tmp_path_factory.mktemp("cache"))
    result, db_path = runner.run("VivaldiCache", CACHE_FIXTURE_STORAGE)
    return runner, result, db_path


@pytest.fixture(scope="module")
def cache_rows(cache_run):
    """Строки таблицы Data результата VivaldiCache."""
    _, _, db_path = cache_run
    return db_select(db_path, "select * from Data order by ID")


def test_cache_returns_db_name(cache_run):
    """Модуль возвращает {MODULENAME: имя БД} и пишет записи."""
    _, result, db_path = cache_run
    assert result == {"VivaldiCache": "VivaldiCache_result.sqlite"}
    assert db_path.exists()
    assert db_rows(db_path) > 0


def test_cache_has_tz_fields(cache_run, tz_fields):
    """Поля ТЗ: URL, FileName, DateTimeCreate_UTC, TimestampCreate_UTC, Size, Content, DataSource."""
    _, _, db_path = cache_run
    columns = db_columns(db_path)
    missing = [name for name in tz_fields["VivaldiCache"] if name not in columns]
    assert not missing, f"нет полей: {missing}"


def test_cache_url_equals_key_from_header(cache_rows, cache_parsed_entries):
    """
    URL в БД обязан совпадать с первым адресом из ключа записи.
    Сейчас достигается регуляркой по файлу — тест сторожит регрессию.
    """
    parsed, _ = cache_parsed_entries
    by_name = {row["FileName"]: row for row in cache_rows}
    assert len(parsed) == FIXTURE_READABLE_ENTRIES
    for name, url in parsed.items():
        assert url, f"пустой ключ у {name}"
        assert name in by_name, f"запись {name} потеряна"
        assert by_name[name]["URL"] == url, (name, by_name[name]["URL"], url)


def test_cache_size_and_content_match_file(cache_rows, cache_fixture_data):
    """Size и Content соответствуют содержимому файла на диске."""
    for row in cache_rows:
        path = cache_fixture_data / row["FileName"]
        assert path.is_file(), row["FileName"]
        assert row["Size"] == path.stat().st_size, row["FileName"]
        assert len(row["Content"] or b"") == row["Size"], row["FileName"]


def test_cache_data_source_under_cache_data(cache_rows, cache_fixture_data):
    """DataSource ведёт внутрь Cache_Data, файл существует."""
    expected = str(cache_fixture_data).lower().replace("/", "\\")
    for row in cache_rows:
        source = row["DataSource"].lower().replace("/", "\\")
        assert source.startswith(expected), source
        assert "cache_data" in source, source
        assert os.path.isfile(cache_fixture_data / row["FileName"]), row["FileName"]


def test_cache_no_records_without_urls(cache_rows, cache_parsed_entries):
    """
    Для читаемых записей URL не должен совпадать с именем файла:
    аналитику нужен адрес ресурса, а не имя файла кэша.
    """
    parsed, empty = cache_parsed_entries
    for row in cache_rows:
        if row["FileName"] in empty or row["FileName"] not in parsed:
            continue
        assert row["URL"] != row["FileName"], f"URL подставлен из имени: {row['FileName']}"


@pytest.mark.xfail(
    strict=True,
    reason="D4 (ПВ-6, ТЗ п.3): модуль не проверяет magic 0xFCFB6D1B и пишет "
           "0-байтовые/битые записи как валидные вместо пропуска с предупреждением",
)
def test_cache_skips_unparsable_entries(runner, tmp_path):
    """Ожидание по ТЗ: разбираются только корректные записи, остальные — в лог."""
    storage = tmp_path / "storage"
    shutil.copytree(CACHE_FIXTURE_DATA.parents[3], storage / "cache")

    result, db_path = runner.run("VivaldiCache", storage)
    rows = db_select(db_path, "select * from Data")
    assert len(rows) == FIXTURE_READABLE_ENTRIES, (
        f"получено {len(rows)} вместо {FIXTURE_READABLE_ENTRIES} читаемых записей"
    )
    names = {row["FileName"] for row in rows}
    assert "deadbeefdeadbeef_0" not in names, "битая запись попала в результат"


@pytest.mark.xfail(
    strict=True,
    reason="D3 (ПВ-6, контракт п.5): пустой Cache_Data даёт имя БД без записей",
)
def test_cache_empty_directory_returns_empty_dict(runner, tmp_path):
    """Каталог Cache_Data без файлов — модуль должен тихо вернуться с {}."""
    empty = tmp_path / "empty_cache" / "cache" / "vivaldi" / "Default" / "Cache" / "Cache_Data"
    empty.mkdir(parents=True)

    result, db_path = runner.run("VivaldiCache", tmp_path / "empty_cache")
    assert result == {}
    assert db_rows(db_path) == 0


def test_cache_creation_time_is_filesystem_mtime(tmp_path):
    """
    Характеризующий тест: DateTimeCreate_UTC = mtime файла, а не момент
    кэширования. При копировании дампа mtime теряется, поэтому поле нельзя
    использовать как доказательство времени (замечание Z2 в отчёте).
    """
    storage = tmp_path / "mtime_probe"
    shutil.copytree(CACHE_FIXTURE_DATA.parents[3], storage / "cache")
    marker = 1000000000  # 2001-09-09 01:46:40 UTC
    for entry in (storage / "cache" / "vivaldi" / "Default" / "Cache" / "Cache_Data").iterdir():
        os.utime(entry, (marker, marker))

    runner = ModuleRunner(tmp_path / "mtime_case")
    _, db_path = runner.run("VivaldiCache", storage)
    rows = db_select(db_path, "select * from Data")
    assert rows, "нет записей для проверки источника времени"
    expected = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(marker))
    for row in rows:
        assert row["DateTimeCreate_UTC"].startswith(expected), row["FileName"]


@pytest.mark.slow
@pytest.mark.skipif(
    not REAL_CACHE_DIR.is_dir(),
    reason="Полный локальный срез кэша отсутствует",
)
def test_cache_real_dump_coverage():
    """
    Критерий ТЗ ПВ-6: разобрано не менее 50% записей реального кэша.
    Считается по заголовкам записей (магия + ключ), поэтому быстрый.
    """
    data = REAL_CACHE_DIR / "vivaldi" / "Default" / "Cache" / "Cache_Data"
    files = [n for n in os.listdir(data) if os.path.isfile(os.path.join(data, n))]
    readable = sum(1 for n in files if simple_cache_key(os.path.join(data, n)))
    coverage = readable / len(files) if files else 0
    assert coverage >= 0.5, f"разобрано только {coverage:.1%} записей кэша"
    assert len(files) > 100, "подозрительно маленький срез кэша"


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("PV7_RUN_FULL_CACHE") != "1",
    reason="задайте PV7_RUN_FULL_CACHE=1 для прогона модуля по всему кэшу "
           "(~12 тысяч файлов, несколько минут и больше гигабайта результата)",
)
def test_cache_full_dump_smoke(runner):
    """
    Тяжёлая проверка самого модуля на полном кэше.
    Выключена по умолчанию: 11711 файлов -> ~3.5 минуты и ~1.3 ГБ БД.
    """
    result, db_path = runner.run("VivaldiCache", REAL_CACHE_DIR.parent)
    assert result, "модуль не отработал по полному кэшу"
    rows = db_select(db_path, "select URL from Data")
    with_url = sum(1 for row in rows if str(row["URL"]).startswith("http"))
    assert with_url / max(len(rows), 1) >= 0.5, f"URL распознан лишь в {with_url} записях"
