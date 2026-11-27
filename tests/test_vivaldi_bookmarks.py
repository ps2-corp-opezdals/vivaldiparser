# -*- coding: utf-8 -*-
"""
Функциональные тесты модуля VivaldiBookmarks (ПВ-4).

Проверяются критерии ТЗ: все закладки и папки на месте, пути в дереве
корректны, рекурсия работает на любой глубине, время пересчитано верно.
"""

import json
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from conftest import SOURCE_DUMP, db_columns, db_rows, db_select

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
REAL_BOOKMARKS = SOURCE_DUMP / "config" / "vivaldi" / "Default" / "Bookmarks"

ROOT_LABELS = {"Bookmarks bar", "Other bookmarks", "Synced bookmarks"}


def chromium_to_utc(microseconds):
    """Независимый пересчёт Chromium-времени."""
    return CHROMIUM_EPOCH + timedelta(microseconds=microseconds)


@pytest.fixture(scope="module")
def bookmarks_run(tmp_path_factory):
    """Прогон модуля на реальном дампе."""
    from conftest import ModuleRunner

    runner = ModuleRunner(tmp_path_factory.mktemp("bookmarks"))
    result, db_path = runner.run("VivaldiBookmarks", SOURCE_DUMP)
    return runner, result, db_path


@pytest.fixture(scope="module")
def bookmarks_rows(bookmarks_run):
    """Строки таблицы Data результата VivaldiBookmarks."""
    _, _, db_path = bookmarks_run
    return db_select(db_path, "select * from Data order by ID")


@pytest.fixture(scope="module")
def bookmarks_json():
    """Исходный JSON Bookmarks для сверки."""
    with open(REAL_BOOKMARKS, encoding="utf-8-sig") as handle:
        return json.load(handle)


def count_nodes(node):
    """Подсчёт узлов дерева закладок (без ветки trash)."""
    total = 1
    for child in node.get("children", []) or []:
        total += count_nodes(child)
    return total


def test_bookmarks_returns_db_name(bookmarks_run, expected_rows):
    """Модуль возвращает имя БД и пишет ожидаемое число узлов."""
    _, result, db_path = bookmarks_run
    assert result == {"VivaldiBookmarks": "VivaldiBookmarks_result.sqlite"}
    assert db_rows(db_path) == expected_rows["VivaldiBookmarks"]


def test_bookmarks_all_nodes_present(bookmarks_rows, bookmarks_json):
    """Количество строк равно числу узлов JSON без ветки trash."""
    expected = sum(
        count_nodes(node)
        for key, node in bookmarks_json["roots"].items()
        if isinstance(node, dict) and key != "trash"
    )
    assert len(bookmarks_rows) == expected


def test_bookmarks_types_split(bookmarks_rows):
    """Тип узла сохранён: закладки и папки различимы."""
    types = {row["Type"] for row in bookmarks_rows}
    assert types == {"url", "folder"}, types
    urls = [row for row in bookmarks_rows if row["Type"] == "url"]
    folders = [row for row in bookmarks_rows if row["Type"] == "folder"]
    assert len(urls) + len(folders) == len(bookmarks_rows)
    assert len(urls) == 38 and len(folders) == 8


def test_bookmarks_has_tz_fields(bookmarks_run, tz_fields):
    """Поля из ТЗ присутствуют в выходной таблице."""
    _, _, db_path = bookmarks_run
    columns = db_columns(db_path)
    missing = [name for name in tz_fields["VivaldiBookmarks"] if name not in columns]
    assert not missing, f"нет полей: {missing}"


def test_bookmarks_root_labels(bookmarks_rows):
    """Пусти начинаются с человекочитаемых имён ветвей, trash исключён."""
    roots = {row["PathInTree"].split(" > ")[0] for row in bookmarks_rows}
    assert roots <= ROOT_LABELS, roots
    assert "Trash" not in roots


def test_bookmarks_paths_match_tree(bookmarks_rows):
    """PathInTree совпадает с реальным путём в дереве JSON."""
    by_name = {row["Name"]: row for row in bookmarks_rows}
    target = by_name.get("Vivaldi Browser")
    assert target is not None
    assert target["PathInTree"] == "Bookmarks bar > Home > Vivaldi Browser"
    assert target["URL"] == "https://vivaldi.com/"


def test_bookmarks_url_only_for_bookmarks(bookmarks_rows):
    """У папок URL пустой, у закладок — абсолютный адрес."""
    for row in bookmarks_rows:
        if row["Type"] == "folder":
            assert row["URL"] == "", row
        else:
            assert row["URL"].startswith(("http://", "https://")), row


def test_bookmarks_time_matches_json(bookmarks_rows, bookmarks_json):
    """date_added из JSON пересчитан в FILETIME и в читаемую дату."""
    wanted = None

    def walk(node):
        nonlocal wanted
        if node.get("type") == "url" and node.get("name") == "Vivaldi Browser":
            wanted = node
        for child in node.get("children", []) or []:
            walk(child)

    for node in bookmarks_json["roots"].values():
        if isinstance(node, dict):
            walk(node)

    assert wanted is not None
    row = next(r for r in bookmarks_rows if r["PathInTree"].endswith("Vivaldi Browser"))
    # C1: метки во всех модулях теперь в микросекундах Chromium
    assert row["TimestampAdded_UTC"] == int(wanted["date_added"])
    assert row["DateTimeAdded_UTC"] == chromium_to_utc(
        int(wanted["date_added"])
    ).isoformat().replace("T", " ")


@pytest.mark.xfail(
    strict=True,
    reason="Z1-1 (ПВ-1/ПВ-4): у узла без date_modified в БД попадает "
           "1970-01-01 вместо пустой строки; VivaldiDownloads и VivaldiCookies "
           "при нулевом времени пишут пустое значение — надо так же",
)
def test_bookmarks_missing_date_modified_is_empty(bookmarks_rows):
    """Отсутствующее время не должно превращаться в реальную дату."""
    row = next(r for r in bookmarks_rows if r["PathInTree"].endswith("Vivaldi Browser"))
    assert row["TimestampAdded_UTC"] > 0
    assert row["DateTimeModified_UTC"] == "", row["DateTimeModified_UTC"]
    assert row["TimestampModified_UTC"] in (0, None, ""), row["TimestampModified_UTC"]


def test_bookmarks_local_time_matches_offset(bookmarks_rows):
    """Локальное время согласовано с UTC и TimeZoneOffset на любой машине."""
    for row in bookmarks_rows:
        if not row["DateTimeAdded_UTC"]:
            continue
        utc = datetime.fromisoformat(row["DateTimeAdded_UTC"])
        local = datetime.fromisoformat(row["DateTimeAdded_Local"])
        assert local.utcoffset() == timedelta(minutes=row["TimeZoneOffset"]), row
        assert (local - utc) == timedelta(0), row


def test_bookmarks_deep_tree_supported(runner, tmp_path):
    """
    ТЗ ПВ-4: рекурсия работает на любой глубине.
    Проверяем дерево из 60 вложенных папок — рекурсивный обход на этом
    расстоянии падает по RecursionError, итеративный — нет.
    """
    depth = 60
    node = {"type": "url", "id": "999", "name": "Leaf", "url": "https://example.com/deep",
            "date_added": "13438450045387078", "date_modified": ""}
    for index in range(depth, 0, -1):
        node = {"type": "folder", "id": str(index), "name": f"Level{index}",
                "date_added": "13438450045387078", "date_modified": "", "children": [node]}
    document = {"checksum": "x", "roots": {"bookmark_bar": {
        "type": "folder", "id": "0", "name": "Bookmarks", "children": [node]}}}

    profile = tmp_path / "deep" / "config" / "vivaldi" / "Default"
    profile.mkdir(parents=True)
    (profile / "Bookmarks").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )

    result, db_path = runner.run("VivaldiBookmarks", tmp_path / "deep")
    assert result, "модуль не вернул имя БД для валидного дерева"
    rows = db_select(db_path, "select * from Data order by ID")
    assert len(rows) == depth + 2, f"ожидались все {depth + 2} узла"
    deepest = rows[-1]
    assert deepest["PathInTree"].endswith("Level60 > Leaf"), deepest["PathInTree"]
    assert deepest["PathInTree"].count(" > ") == depth + 1


def test_bookmarks_invalid_json_skipped(runner, tmp_path):
    """Битый JSON Bookmarks не роняет модуль."""
    profile = tmp_path / "bad" / "config" / "vivaldi" / "Default"
    profile.mkdir(parents=True)
    (profile / "Bookmarks").write_text("{это не json", encoding="utf-8")

    _, db_path = runner.run("VivaldiBookmarks", tmp_path / "bad")
    assert db_rows(db_path) == 0, "на битом JSON не должно быть записей"


def test_bookmarks_empty_tree_returns_empty_dict(runner, tmp_path):
    """D2 исправлен: при пустом дереве модуль тихо возвращает {}."""
    profile = tmp_path / "bad2" / "config" / "vivaldi" / "Default"
    profile.mkdir(parents=True)
    (profile / "Bookmarks").write_text(
        json.dumps({"checksum": "x", "roots": {}}), encoding="utf-8"
    )

    result, db_path = runner.run("VivaldiBookmarks", tmp_path / "bad2")
    assert result == {}
    assert db_rows(db_path) == 0



def test_bookmarks_missing_trash_branch_tolerated(runner, tmp_path):
    """Отсутствие части ветвей (synced/other) не считается ошибкой."""
    document = {"checksum": "x", "roots": {"bookmark_bar": {
        "type": "folder", "id": "1", "name": "Bookmarks", "date_added": "13438450045387078",
        "children": [{"type": "url", "id": "2", "name": "Only",
                      "url": "https://example.com", "date_added": "13438450045387078"}]}}}
    profile = tmp_path / "partial" / "config" / "vivaldi" / "Default"
    profile.mkdir(parents=True)
    (profile / "Bookmarks").write_text(json.dumps(document), encoding="utf-8")

    _, db_path = runner.run("VivaldiBookmarks", tmp_path / "partial")
    rows = db_select(db_path, "select * from Data order by ID")
    assert len(rows) == 2
    assert rows[1]["PathInTree"] == "Bookmarks bar > Only"
