# -*- coding: utf-8 -*-
"""
Тест "нет файла" (п.5 ПВ-7) и инвариант результата модуля.

Правило из ТЗ (общие правила, п.5): если артефакта нет — модуль тихо
возвращает {} и не падает. Дополнительно: если модуль всё же вернул имя БД,
файл обязан существовать и содержать записи.

D1 был исправлен в 574ed3e, поэтому отметок xfail здесь больше нет.
"""

import pytest

from conftest import db_rows

MODULES_ALL = [
    pytest.param("VivaldiBookmarks"),
    pytest.param("VivaldiCache"),
    pytest.param("VivaldiCookies"),
    pytest.param("VivaldiDownloads"),
    pytest.param("VivaldiHistory"),
]


@pytest.mark.parametrize("module_name", MODULES_ALL)
def test_missing_artifact_returns_empty_dict(module_name, runner, empty_storage):
    """Хранилище без артефактов: каждый модуль возвращает {} без записей."""
    result, db_path = runner.run(module_name, empty_storage)
    assert result == {}, f"{module_name} вернул {result} при отсутствии данных"
    assert db_rows(db_path) == 0, f"{module_name}: записи при пустом хранилище"


@pytest.mark.parametrize("module_name", MODULES_ALL)
def test_declared_database_always_exists(module_name, runner, empty_storage):
    """Если модуль заявил имя БД — файл существует и непустой."""
    result, db_path = runner.run(module_name, empty_storage)
    if not result:
        return
    assert db_path.exists(), f"{module_name} заявил {result}, файла нет на диске"
    assert db_path.stat().st_size > 0, f"{module_name} заявил {result}, файл 0 байт"


@pytest.mark.parametrize("module_name", MODULES_ALL)
def test_no_records_without_artifact(module_name, runner, empty_storage):
    """Таблица Data пуста, когда артефактов в хранилище нет."""
    _, db_path = runner.run(module_name, empty_storage)
    assert db_rows(db_path) == 0


@pytest.mark.parametrize("module_name", MODULES_ALL)
def test_no_junk_database_file(module_name, runner, empty_storage):
    """При пустом результате в кейсе не остаётся непустого файла БД."""
    _, db_path = runner.run(module_name, empty_storage)
    if db_path.exists():
        assert db_path.stat().st_size == 0, f"{module_name}: файл {db_path.name} не пустой"


def test_framework_survives_empty_storage(e2e_workspace):
    """Полный прогон Run.py на пустом хранилище завершается без падения."""
    from conftest import run_framework

    workspace, _ = e2e_workspace
    empty = workspace / "EmptySource"
    empty.mkdir(exist_ok=True)

    process = run_framework(workspace, empty, "empty.sqlite")
    assert process.returncode == 0, process.stdout + process.stderr
