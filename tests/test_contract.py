# -*- coding: utf-8 -*-
"""
Код-ревью контракта ТЗ (п.1-11 "Общих правил") средствами pytest.

Статические проверки исходников Modules/Vivaldi*: структура класса,
docstring'и, отсутствие пути разработчика, единый пересчёт времени,
независимость модулей друг от друга и от каркаса.
"""

import ast
import re

import pytest

from conftest import MODULE_NAMES, REPO_ROOT

ABSOLUTE_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\\\|/home/|/Users/|C:\\Users)")
EPOCH_HACK_PATTERN = re.compile(r"(116444736000000000|11644473600000000|utcfromtimestamp)")
CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


def module_dir(module_name):
    """Каталог модуля в репозитории."""
    return REPO_ROOT / "Modules" / module_name


def parser_source(module_name):
    """Исходный код Parser.py модуля."""
    return (module_dir(module_name) / "Parser.py").read_text(encoding="utf-8")


def parser_tree(module_name):
    """AST Parser.py модуля."""
    return ast.parse(parser_source(module_name), filename=str(module_name))


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_layout(module_name):
    """п.1: Modules/<Имя>/Parser.py существует, рядом есть __init__.py."""
    folder = module_dir(module_name)
    assert folder.is_dir(), f"нет каталога {folder}"
    assert (folder / "Parser.py").is_file(), f"нет {folder}/Parser.py"
    assert (folder / "__init__.py").is_file(), f"нет {folder}/__init__.py"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_entry_point_contract(module_name):
    """п.2: class Parser с __init__(self, parameters) и async def Start(self)."""
    tree = parser_tree(module_name)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "Parser" in classes, "нет класса Parser верхнего уровня"
    parser_class = classes["Parser"]

    init = next(
        (node for node in parser_class.body
         if isinstance(node, ast.FunctionDef) and node.name == "__init__"),
        None,
    )
    assert init is not None, "у Parser нет __init__"
    arguments = [argument.arg for argument in init.args.args]
    assert arguments[:2] == ["self", "parameters"], f"сигнатура __init__: {arguments}"

    start = next(
        (node for node in parser_class.body
         if isinstance(node, ast.AsyncFunctionDef) and node.name == "Start"),
        None,
    )
    assert start is not None, "у Parser нет асинхронного метода Start"
    assert [argument.arg for argument in start.args.args] == ["self"], \
        f"сигнатура Start: {[argument.arg for argument in start.args.args]}"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_returns_db_name(module_name):
    """п.3: Start возвращает {MODULENAME: outputWriter.GetDBName()}."""
    source = parser_source(module_name)
    assert "GetDBName()" in source, "нет возврата имени БД"
    assert "MODULENAME" in source, "ключ результата не берётся из параметра MODULENAME"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_has_no_developer_paths(module_name):
    """п.4: папка только через STORAGE, локальные пути не зашиты."""
    offenders = [
        line.strip() for line in parser_source(module_name).splitlines()
        if ABSOLUTE_PATH_PATTERN.search(line)
    ]
    assert not offenders, f"абсолютные пути в коде: {offenders[:3]}"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_uses_shared_time_converter(module_name):
    """п.6: Chromium-время пересчитывается через общий модуль Common.Chrom."""
    tree = parser_tree(module_name)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "Common.Chrom":
            imported.update(alias.name for alias in node.names)
    assert imported, f"{module_name} не использует общий модуль времени"
    assert imported <= {
        "chromium_to_datetime", "chromium_to_filetime", "format_datetime", "FILETIME_EPOCH",
    }, f"{module_name}: неизвестные функции общего модуля: {imported}"


def test_no_manual_filetime_math():
    """
    C2 исправлен (574ed3e): пересчёт микросекунд Chromium в FILETIME
    больше не дублируется в модулях, используется общий chromium_to_filetime.
    """
    offenders = {}
    for name in MODULE_NAMES:
        tree = parser_tree(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                constants = [
                    operand for operand in (node.left, node.right)
                    if isinstance(operand, ast.Constant) and operand.value == 10
                ]
                if constants:
                    offenders.setdefault(name, []).append(node.lineno)
    assert not offenders, f"ручной пересчёт времени в модулях: {offenders}"


DOCSTRING_MODULES = [
    pytest.param(name) for name in MODULE_NAMES
]


def test_module_docstrings_russian():
    """
    п.8 исправлен: у класса Parser, у всех методов класса и у модуля в целом
    есть docstring на русском. Служебные __init__ и вложенные функции-
    помощники не проверяются (в каркасе они тоже без docstring).
    """
    problems = {}
    for module_name in MODULE_NAMES:
        tree = parser_tree(module_name)
        missing = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                if not doc or not CYRILLIC.search(doc):
                    missing.append(node.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            doc = ast.get_docstring(node)
            if not doc or not CYRILLIC.search(doc):
                missing.append(node.name)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name.startswith("__"):
                        continue
                    member_doc = ast.get_docstring(member)
                    if not member_doc or not CYRILLIC.search(member_doc):
                        missing.append(f"{node.name}.{member.name}")
        if missing:
            problems[module_name] = missing

    assert not problems, f"нет русскоязычных docstring у: {problems}"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_does_not_import_other_modules(module_name):
    """п.10: модуль независим, чужие модули не импортирует."""
    tree = parser_tree(module_name)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    forbidden = {name for name in imports if name.startswith("Modules")}
    assert not forbidden, f"{module_name} импортирует другие модули: {forbidden}"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_uses_storage_for_folder(module_name):
    """п.4: каталог берётся из параметра STORAGE."""
    assert "STORAGE" in parser_source(module_name), f"{module_name} не читает STORAGE"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_handles_missing_files_quietly(module_name):
    """п.5: в коде есть явный пустой возврат при отсутствии артефакта."""
    assert re.search(r"return\s*\{\s*\}", parser_source(module_name)), \
        f"{module_name} нигде не возвращает пустой словарь"


def test_no_epoch_hacks_in_modules():
    """В модулях нет собственных костылей с эпохами (п.6)."""
    for name in MODULE_NAMES:
        assert not EPOCH_HACK_PATTERN.search(parser_source(name)), \
            f"{name}: своя работа с эпохами"


def test_framework_files_untouched_by_modules():
    """
    п.10: код каркаса не должен знать конкретных модулей.
    Проверяются Run.py и Interfaces/ (Common/Chrom.py намеренно общий
    модуль времени для Vivaldi-артефактов, упоминание в его docstring
    допустимо).
    """
    offenders = []
    for relative in ("Run.py", "Interfaces/Main.py", "Interfaces/Solver.py",
                     "Interfaces/OutputInterface.py", "Interfaces/SettingsInterface.py"):
        path = REPO_ROOT / relative
        if path.is_file() and "Vivaldi" in path.read_text(encoding="utf-8"):
            offenders.append(relative)
    assert not offenders, f"каркас знает про конкретные модули: {offenders}"


@pytest.mark.xfail(
    strict=True,
    reason="B5 (лидер): примеры Modules/DrWebAlerts, CompatibilityAssistant, "
           "MuiCache удалены коммитом d2bbf67, а замена для п.11 ТЗ "
           "(стартовая точка модуля) нигде не зафиксирована",
)
def test_examples_or_replacement_documented():
    """ТЗ считает стартовой точкой Modules/DrWebAlerts/Parser.py (п.11)."""
    has_example = (REPO_ROOT / "Modules" / "DrWebAlerts" / "Parser.py").is_file()
    readme = REPO_ROOT / "README.md"
    documented = readme.is_file() and "DrWebAlerts" in readme.read_text(encoding="utf-8")
    assert has_example or documented, (
        "пример DrWebAlerts удалён, замена не описана — нарушен п.11 ТЗ"
    )
