# -*- coding: utf-8 -*-
"""
E2E-тест ПВ-7 (п.4 ТЗ): запуск Run.py на дампе Vivaldi.

Проверяется весь путь: argparse -> Interface -> Solver -> динамическая
загрузка модулей -> БД в каталоге Cases/<время кейса>/.
Отдельно контролируется, что улики остаются только для чтения.
"""

import hashlib
import sqlite3

import pytest

from conftest import MODULE_NAMES, db_rows, run_framework

EXPECTED_E2E_ROWS = {
    "VivaldiBookmarks": 46,
    "VivaldiCache": 21,
    "VivaldiCookies": 395,
    "VivaldiDownloads": 6,
    "VivaldiHistory": 537,
}


def tree_state(root):
    """Снимок дерева улик: путь -> (размер, mtime, sha256)."""
    state = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload = path.read_bytes()
            statistic = path.stat()
            state[path.relative_to(root).as_posix()] = (
                statistic.st_size,
                round(statistic.st_mtime, 3),
                hashlib.sha256(payload).hexdigest(),
            )
    return state


@pytest.fixture(scope="module")
def e2e_run(e2e_workspace):
    """Один полный прогон каркаса для всех e2e-проверок."""
    workspace, storage = e2e_workspace
    cases_dir = workspace / "Cases"
    before = {path.name for path in cases_dir.iterdir()}
    evidence_before = tree_state(storage)

    process = run_framework(workspace, storage, "result.sqlite")

    new_cases = sorted({path.name for path in cases_dir.iterdir()} - before)
    case_dir = cases_dir / new_cases[0] if len(new_cases) == 1 else None
    return {
        "process": process,
        "workspace": workspace,
        "storage": storage,
        "case_dir": case_dir,
        "new_cases": new_cases,
        "evidence_before": evidence_before,
        "evidence_after": tree_state(storage),
    }


def test_e2e_exit_code(e2e_run):
    """Run.py завершается кодом 0 и печатает подтверждение успеха."""
    process = e2e_run["process"]
    assert process.returncode == 0, process.stdout + process.stderr
    assert "Работа завершена успешно" in process.stdout


def test_e2e_all_modules_discovered(e2e_run):
    """Solver нашёл все пять модулей в каталоге Modules."""
    stdout = e2e_run["process"].stdout
    for name in MODULE_NAMES:
        assert name in stdout, f"модуль {name} не появился в выводе"


def test_e2e_single_case_created(e2e_run):
    """Прогон создал ровно один каталог кейса."""
    assert len(e2e_run["new_cases"]) == 1, e2e_run["new_cases"]
    assert e2e_run["case_dir"] is not None and e2e_run["case_dir"].is_dir()


def test_e2e_five_databases_present(e2e_run):
    """В каталоге кейса лежат БД всех модулей с именем <Модуль>_<output>."""
    case_dir = e2e_run["case_dir"]
    for name in MODULE_NAMES:
        database = case_dir / f"{name}_result.sqlite"
        assert database.is_file(), f"нет {database.name}"
        assert database.stat().st_size > 0, f"{database.name} пустой"


def test_e2e_row_counts_match_expected(e2e_run):
    """Количество записей в каждой БД совпадает с эталоном дампа."""
    case_dir = e2e_run["case_dir"]
    for name, expected in EXPECTED_E2E_ROWS.items():
        rows = db_rows(case_dir / f"{name}_result.sqlite")
        assert rows == expected, f"{name}: записей {rows}, ожидалось {expected}"


def test_e2e_database_structure(e2e_run):
    """Каждая БД содержит Data, Headers и Info с обязательными ключами."""
    case_dir = e2e_run["case_dir"]
    for name in MODULE_NAMES:
        connection = sqlite3.connect(str(case_dir / f"{name}_result.sqlite"))
        try:
            tables = {
                row[0] for row in
                connection.execute("select name from sqlite_master where type='table'")
            }
            assert {"Data", "Headers", "Info"} <= tables, (name, tables)
            info = dict(connection.execute("select Key, Value from Info"))
            assert info.get("Name") == name, (name, info)
            assert info.get("Vendor"), (name, info)
            assert info.get("Timestamp"), (name, info)
            assert info.get("Help"), (name, info)
            headers = connection.execute("select count(*) from Headers").fetchone()[0]
            columns = len(
                connection.execute("select * from Data limit 1").description
            )
            # в Data первая колонка — служебный ID, его в Headers нет
            assert headers == columns - 1, (name, headers, columns)
            indexes = connection.execute(
                "select count(*) from sqlite_master where type='index'"
            ).fetchone()[0]
            assert indexes > 0, f"{name}: индексы не созданы"
        finally:
            connection.close()


def test_e2e_evidence_read_only(e2e_run):
    """Каркас обязан только читать улики: дамп не изменился за прогон."""
    assert e2e_run["evidence_before"] == e2e_run["evidence_after"], (
        "прогон изменил содержимое или метки времени файлов хранилища"
    )


def test_e2e_writes_only_into_workspace(e2e_run):
    """Результаты, логи и временные файлы остаются в рабочем каталоге."""
    workspace = e2e_run["workspace"]
    assert (workspace / "Cases").is_dir()
    assert (workspace / "Logs").is_dir()
    logs = list((workspace / "Logs").glob("*.log"))
    assert logs, "журнал прогона не записан"


def test_e2e_repeatable(e2e_run):
    """
    Повторный прогон даёт те же количества записей: результат детерминирован
    и не зависит от остатков предыдущего запуска (Temp очищается).
    """
    workspace, storage = e2e_run["workspace"], e2e_run["storage"]
    cases_dir = workspace / "Cases"
    before = {path.name for path in cases_dir.iterdir()}

    process = run_framework(workspace, storage, "again.sqlite")
    assert process.returncode == 0, process.stdout + process.stderr

    repeated = [
        path.parent for path in cases_dir.glob("*/VivaldiHistory_again.sqlite")
    ]
    assert repeated, "повторный прогон не создал БД с новым именем"
    case_dir = repeated[-1]
    for name, expected in EXPECTED_E2E_ROWS.items():
        database = case_dir / f"{name}_again.sqlite"
        assert database.is_file(), f"повторный прогон не создал {database.name}"
        assert db_rows(database) == expected, name


def test_e2e_history_time_matches_manual_check(e2e_run):
    """П.3 ТЗ: конкретную дату сверяем вручную по данным дампа."""
    case_dir = e2e_run["case_dir"]
    connection = sqlite3.connect(str(case_dir / "VivaldiHistory_result.sqlite"))
    try:
        row = connection.execute(
            "select URL, DateTime_UTC, Timestamp_UTC from Data order by ID limit 1"
        ).fetchone()
    finally:
        connection.close()
    assert row == (
        "https://vivaldi.com/getstarted/",
        "2026-11-06 14:48:17.139433+00:00",
        134384500971394330,
    )
