# -*- coding: utf-8 -*-
"""
Общие фикстуры и помощники ПВ-7.

Тесты работают на двух источниках данных:
    1) Source-vivaldi/   — реальный отфильтрованный дамп профиля (лежит в репозитории);
    2) tests/fixtures/cache/ — небольшой срез настоящего Simple Cache (21 файл).

Здесь же собраны эталонные количества записей для текущего дампа: если дамп
обновят, править нужно только словарь EXPECTED_ROWS.
"""

import asyncio
import importlib.util
import os
import re
import shutil
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SOURCE_DUMP = REPO_ROOT / "Source-vivaldi"
FIXTURES_DIR = TESTS_DIR / "fixtures"
CACHE_FIXTURE_STORAGE = FIXTURES_DIR          # содержит cache/vivaldi/Default/Cache/Cache_Data
CACHE_FIXTURE_DATA = FIXTURES_DIR / "cache" / "vivaldi" / "Default" / "Cache" / "Cache_Data"
# Полный локальный срез кэша (в git не попадает): допустимы оба размещения
REAL_CACHE_DIR = next(
    (candidate for candidate in (
        REPO_ROOT / "Source-vivaldi" / "cache",
        REPO_ROOT / "cache",
    ) if candidate.is_dir()),
    REPO_ROOT / "Source-vivaldi" / "cache",
)

MODULE_NAMES = [
    "VivaldiBookmarks",
    "VivaldiCache",
    "VivaldiCookies",
    "VivaldiDownloads",
    "VivaldiHistory",
]

# Эталон по текущему дампу Source-vivaldi (проверено прогоном Run.py).
EXPECTED_ROWS = {
    "VivaldiBookmarks": 46,
    "VivaldiCookies": 395,
    "VivaldiDownloads": 6,
    "VivaldiHistory": 537,
}

# Поля, обязательные по ТЗ (раздел "Общие правила", п.7 + списки по модулям).
TZ_FIELDS = {
    "VivaldiHistory": [
        "URL", "Title", "VisitCount", "DateTime_UTC", "Timestamp_UTC",
        "DateTime_Local", "TimeZoneOffset", "Transition", "TransitionName",
        "VisitDuration", "DataSource",
    ],
    "VivaldiDownloads": [
        "URL", "TargetPath", "CurrentPath", "DateTimeStart_UTC",
        "TimestampStart_UTC", "DateTimeEnd_UTC", "TimestampEnd_UTC",
        "ReceivedBytes", "TotalBytes", "State", "DangerType",
        "InterruptReason", "DataSource",
    ],
    "VivaldiBookmarks": [
        "Type", "PathInTree", "Name", "URL", "DateTimeAdded_UTC",
        "TimestampAdded_UTC", "DateTimeModified_UTC", "DataSource",
    ],
    "VivaldiCookies": [
        "HostKey", "Name", "Path", "DateTimeExpires_UTC",
        "DateTimeLastAccess_UTC", "DateTimeCreation_UTC", "IsSecure",
        "IsHttpOnly", "HasExpires", "IsPersistent", "DataSource",
    ],
    "VivaldiCache": [
        "URL", "FileName", "DateTimeCreate_UTC", "TimestampCreate_UTC",
        "Size", "Content", "DataSource",
    ],
}

SIMPLE_CACHE_MAGIC = 0xFCFB6D1B


class DummyLog:
    """Заглушка интерфейса LOGGER: тесты не должны писать в Logs/."""

    def __init__(self):
        self.records = []

    def _add(self, level, source, message):
        self.records.append((level, source, message))

    def Info(self, sourceName, message):
        self._add("INFO", sourceName, message)

    def Warn(self, sourceName, message):
        self._add("WARN", sourceName, message)

    def Error(self, sourceName, message):
        self._add("ERROR", sourceName, message)


async def _noopUiRedraw(message, percent):
    """Заглушка UIREDRAW: модули вызывают её как корутину."""
    return None


def simple_cache_key(file_path):
    """
    Референсный разбор заголовка Simple Cache (используется только в тестах).

    Формат записи в дампе Vivaldi:
        смещение 0  — uint32, константа заголовка файла;
        смещение 4  — uint32 magic 0xFCFB6D1B;
        смещение 12 — uint32 длина ключа;
        смещение 24 — ключ вида "1/0/_dk_<URL> <URL> ...".

    Args:
        file_path (str|Path): путь к файлу записи кэша

    Returns:
        Optional[str]: ключ записи либо None, если запись не читается
    """
    from typing import Optional  # локальный импорт, чтобы не светить его в тесте

    path = Path(file_path)
    if not path.is_file() or path.stat().st_size < 32:
        return None
    with open(path, "rb") as handle:
        head = handle.read(min(path.stat().st_size, 4096))
    if struct.unpack_from("<I", head, 4)[0] != SIMPLE_CACHE_MAGIC:
        return None
    key_len = struct.unpack_from("<I", head, 12)[0]
    if key_len == 0 or 24 + key_len > len(head):
        return None
    try:
        return head[24:24 + key_len].decode("utf-8")
    except UnicodeDecodeError:
        return None


def first_url(text):
    """Выделить первый http(s)-адрес из ключа Simple Cache."""
    if not text:
        return ""
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else ""


def db_rows(db_path):
    """
    Вернуть количество записей в таблице Data результата модуля.

    Args:
        db_path (Path): путь к sqlite-файлу

    Returns:
        int: число строк, 0 если таблицы/файла нет
    """
    db_path = Path(db_path)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return 0
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute("select count(*) from Data").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        connection.close()


def db_columns(db_path):
    """Вернуть список колонок таблицы Data."""
    connection = sqlite3.connect(str(db_path))
    try:
        cursor = connection.execute("select * from Data limit 1")
        return [column[0] for column in cursor.description]
    finally:
        connection.close()


def db_select(db_path, query, params=()):
    """Выполнить запрос к результирующей БД модуля и вернуть строки."""
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params)]
    finally:
        connection.close()


class ModuleRunner:
    """Запуск одного динамического модуля каркаса вне Run.py."""

    def __init__(self, tmp_path):
        self.tmp_path = Path(tmp_path)
        self.case_folder = self.tmp_path / "Cases"
        self.temp_folder = self.tmp_path / "Temp"
        self.case_name = "pv7"
        self.case_dir = self.case_folder / self.case_name
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.temp_folder.mkdir(parents=True, exist_ok=True)

    def run(self, module_name, storage, output_name="result.sqlite"):
        """
        Инициализировать интерфейсы каркаса и выполнить Parser.Start().

        Args:
            module_name (str): имя модуля из Modules/
            storage (str|Path): значение STORAGE (каталог с уликами)
            output_name (str): базовая часть имени выходной БД

        Returns:
            Tuple[dict, Path]: результат модуля и путь к ожидаемой БД
        """
        from Common.Routines import SQLiteDatabaseInterface
        from Interfaces.OutputInterface import SQLiteDBOutputWriter

        parser_path = REPO_ROOT / "Modules" / module_name / "Parser.py"
        spec = importlib.util.spec_from_file_location("Parser", str(parser_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        db_name = f"{module_name}_{output_name}"
        db_path = self.case_dir / db_name
        log = DummyLog()

        connection = SQLiteDatabaseInterface(
            dbPath=str(db_path),
            log=log,
            moduleName=module_name,
            moduleRAMProcessing=True,
        )
        writer = SQLiteDBOutputWriter({
            "DBNAME": db_name,
            "CASENAME": self.case_name,
            "CASEFOLDER": str(self.case_folder),
            "MODULENAME": module_name,
        })
        writer.SetDBConnection(connection)

        parameters = {
            "TEMP": str(self.temp_folder),
            "LOG": log,
            "CASEFOLDER": str(self.case_folder),
            "CASENAME": self.case_name,
            "UIREDRAW": _noopUiRedraw,
            "STORAGE": str(storage),
            "MODULENAME": module_name,
            "FILECONTENTREADER": None,
            "OUTPUTFILENAME": db_name,
            "RAMPROCESSING": True,
            "DBCONNECTION": connection,
            "OUTPUTWRITER": writer,
            "REGISTRYFILEHANDLER": None,
        }

        result = asyncio.run(module.Parser(parameters).Start())
        return result or {}, db_path


@pytest.fixture
def runner(tmp_path):
    """Фикстура: экземпляр ModuleRunner в изолированном tmp_path."""
    return ModuleRunner(tmp_path)


@pytest.fixture(scope="session")
def source_dump():
    """Каталог с реальным дампом профиля Vivaldi."""
    if not SOURCE_DUMP.is_dir():
        pytest.skip(f"Нет дампа {SOURCE_DUMP}")
    return SOURCE_DUMP


@pytest.fixture(scope="session")
def cache_fixture_storage():
    """STORAGE для модуля VivaldiCache: каталог tests/fixtures."""
    if not CACHE_FIXTURE_DATA.is_dir():
        pytest.skip("Нет фикстуры Simple Cache")
    return CACHE_FIXTURE_STORAGE


@pytest.fixture(scope="session")
def empty_storage(tmp_path_factory):
    """Пустой каталог-хранилище: артефактов нет вообще."""
    folder = tmp_path_factory.mktemp("empty_storage")
    return folder


@pytest.fixture(scope="session")
def cache_fixture_data():
    """Каталог Cache_Data с реальным срезом Simple Cache."""
    return CACHE_FIXTURE_DATA


@pytest.fixture(scope="session")
def cache_parsed_entries(cache_fixture_data):
    """
    Референсный разбор фикстуры кэша.

    Returns:
        Tuple[dict, list]: (имя файла -> URL из ключа записи, список 0-байтовых файлов)
    """
    parsed = {}
    empty = []
    for path in sorted(cache_fixture_data.iterdir()):
        if path.stat().st_size == 0:
            empty.append(path.name)
            continue
        key = simple_cache_key(path)
        if key:
            parsed[path.name] = first_url(key)
    return parsed, empty


@pytest.fixture(scope="session")
def expected_rows():
    """Эталон количества записей по текущему дампу."""
    return EXPECTED_ROWS


@pytest.fixture(scope="session")
def module_names():
    """Список тестируемых модулей."""
    return MODULE_NAMES


@pytest.fixture(scope="session")
def tz_fields():
    """Поля выходной БД, обязательные по ТЗ."""
    return TZ_FIELDS


def make_workspace(base):
    """
    Собрать изолированный рабочий каталог каркаса.

    Копируются код (Run.py, Settings.json, Interfaces, Common, Modules)
    и улики: config из Source-vivaldi + срез кэша из tests/fixtures.
    Так тест не трогает Cases/, Logs/, Temp/ разработчика и воспроизводится
    на любом клоне.

    Args:
        base (Path): каталог, куда всё кладётся

    Returns:
        Tuple[Path, Path]: (рабочий каталог, каталог-хранилище STORAGE)
    """
    base = Path(base)
    for item in ("Run.py", "Settings.json"):
        shutil.copy2(REPO_ROOT / item, base / item)
    for folder in ("Interfaces", "Common", "Modules"):
        shutil.copytree(
            REPO_ROOT / folder, base / folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    (base / "Cases").mkdir(exist_ok=True)

    storage = base / "Source-vivaldi"
    shutil.copytree(SOURCE_DUMP / "config", storage / "config")
    shutil.copytree(CACHE_FIXTURE_STORAGE / "cache", storage / "cache")
    return base, storage


def run_framework(workspace, source, output_name="result.sqlite"):
    """
    Запустить каркас так же, как это делает участник ( subprocess Run.py ).

    Args:
        workspace (Path): рабочий каталог с кодом и Settings.json
        source (Path): значение --source_folder
        output_name (str): значение --output_name

    Returns:
        subprocess.CompletedProcess: результат запуска
    """
    import subprocess

    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "Run.py",
         "--source_folder", str(source),
         "--output_name", output_name],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=900,
    )


@pytest.fixture(scope="session")
def e2e_workspace(tmp_path_factory):
    """Рабочий каталог каркаса + хранилище с уликами для e2e-прогона."""
    if not SOURCE_DUMP.is_dir():
        pytest.skip(f"Нет дампа {SOURCE_DUMP}")
    base = tmp_path_factory.mktemp("framework")
    return make_workspace(base)
