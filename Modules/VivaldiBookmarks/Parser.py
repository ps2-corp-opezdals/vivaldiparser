# -*- coding: utf-8 -*-
"""
Модуль извлечения закладок браузера Vivaldi.

Источник: JSON-файл <профиль>/Default/Bookmarks.
Структура файла: roots -> ветви bookmark_bar / other / synced (/ trash),
у каждой ветви — массив children; узел имеет type ('url' или 'folder'),
name, url (только у ссылок), date_added и date_modified (Chromium-время,
микросекунды с 1601-01-01 UTC), у папок — собственные children.

В БД попадают и закладки, и папки: тип узла хранится в поле Type,
полный путь от корня — в PathInTree.

Соглашения едины с остальными модулями Vivaldi*:
- TimestampAdded_UTC / TimestampModified_UTC — Chromium-микросекунды (не FILETIME);
- отсутствующее или нулевое время даётся через SAFE_EPOCH (Common/Chrom.py),
  в БД это 1970-01-01 и метка 0;
- локальное время и TimeZoneOffset считаются с машины (как в VivaldiHistory);
- если записей нет (файл отсутствует, битый JSON, пустое дерево) — Start()
  возвращает {}, согласно п. 5 контракта.
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from Common.Chrom import chromium_to_datetime, format_datetime
from Common.Routines import FixedOffset as tzInfo


# Корневые ветви дерева, которые попадают в результат (раздел ПВ-4 ТЗ).
ROOT_BRANCHES: Dict[str, str] = {
    'bookmark_bar': 'Bookmarks bar',
    'other': 'Other bookmarks',
    'synced': 'Synced bookmarks',
}

# Включать ли ветвь trash (удалённые закладки). По ТЗ она не требуется.
INCLUDE_TRASH: bool = False
TRASH_BRANCH: str = 'trash'
TRASH_LABEL: str = 'Trash'

# Разделитель уровней в поле PathInTree.
PATH_SEPARATOR: str = ' > '


class _VivaldiBookmarksParser:
    """Обходит дерево закладок и записывает узлы в БД каркаса."""

    def __init__(self, parserParameters: dict, recordFields: dict):
        """Инициализировать парсер параметрами каркаса и полями записи."""
        self._storage: str = parserParameters.get('STORAGE')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._record: dict = {field: '' for field in recordFields.keys()}
        self._currentTzInfo, self._tzOffsetMinutes = self._LocalTimeZone()
        self._recordsCount: int = 0

    @staticmethod
    def _LocalTimeZone() -> Tuple[Any, int]:
        """
        Определить локальный часовой пояс машины для полей DateTime_*_Local.

        Returns:
            Tuple[Any, int]: объект часового пояса и смещение в минутах
        """
        try:
            localNow = datetime.now().astimezone()
            offset = localNow.utcoffset()
            minutes = int(offset.total_seconds() // 60) if offset else 0
            name = localNow.tzname() or ''
        except (OSError, ValueError):
            return tzInfo(0, 'UTC'), 0
        return tzInfo(minutes, name), minutes

    def _CleanRecord(self) -> None:
        """Очистить поля текущей записи."""
        for key in self._record.keys():
            self._record[key] = ''

    def GetBookmarksPath(self) -> Optional[str]:
        """
        Найти файл Bookmarks в дампе профиля Vivaldi.

        Пробуем несколько вариантов, потому что STORAGE может указывать
        на корень дампа (Source-vivaldi/) или на профиль (…/Default/).

        Returns:
            Optional[str]: путь до файла Bookmarks либо None, если файла нет
        """
        if not self._storage:
            return None
        candidates = [
            os.path.join(self._storage, 'config', 'vivaldi', 'Default', 'Bookmarks'),
            os.path.join(self._storage, 'config', 'Default', 'Bookmarks'),
            os.path.join(self._storage, 'vivaldi', 'Default', 'Bookmarks'),
            os.path.join(self._storage, 'Default', 'Bookmarks'),
            os.path.join(self._storage, 'Bookmarks'),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _LoadBookmarks(self, path: str) -> Optional[dict]:
        """
        Прочитать JSON-файл Bookmarks.

        Args:
            path (str): путь до файла Bookmarks

        Returns:
            Optional[dict]: корневой объект JSON либо None при ошибке
        """
        try:
            # utf-8-sig: в начале файла может оказаться BOM
            with open(path, 'r', encoding='utf-8-sig') as bookmarksFile:
                data = json.load(bookmarksFile)
        except RecursionError:
            # json.load сам рекурсивен и не осиливает патологически глубокий JSON
            if self._log:
                self._log.Error('VivaldiBookmarks', f'Слишком глубокая вложенность в {path}')
            return None
        except (OSError, ValueError) as e:
            if self._log:
                self._log.Error('VivaldiBookmarks', f'Не удалось прочитать {path}: {e}')
            return None

        if not isinstance(data, dict):
            if self._log:
                self._log.Error('VivaldiBookmarks', f'Неожиданная структура JSON в {path}')
            return None
        return data

    @staticmethod
    def _NodeName(node: dict) -> str:
        """Вернуть имя узла дерева закладок."""
        return str(node.get('name') or '')

    def _ConvertTime(self, chromiumMicroseconds: Any) -> Tuple[str, int, str]:
        """
        Пересчитать Chromium-время (мкс с 1601-01-01 UTC) в поля записи.

        Args:
            chromiumMicroseconds (Any): значение date_added/date_modified из JSON

        Returns:
            Tuple[str, int, str]: дата UTC, Chromium-микросекунды, дата локальная.
            Для отсутствующего или нулевого значения — SAFE_EPOCH (1970-01-01) и 0,
            как в VivaldiHistory / VivaldiDownloads / VivaldiCookies.
        """
        try:
            value = int(chromiumMicroseconds) if chromiumMicroseconds not in (None, '') else 0
        except (TypeError, ValueError, OverflowError):
            value = 0

        dtUtc = chromium_to_datetime(value)
        dtLocal = chromium_to_datetime(value, tz=self._currentTzInfo)

        return format_datetime(dtUtc), value, format_datetime(dtLocal)

    async def _WriteNode(self, node: dict, pathParts: List[str], dataSource: str) -> None:
        """
        Записать один узел дерева (закладку или папку) в БД.

        Args:
            node (dict): узел дерева из JSON
            pathParts (List[str]): полный путь до узла, включая его имя
            dataSource (str): путь до файла Bookmarks
        """
        nodeType = str(node.get('type') or 'unknown')

        addedUtc, addedTimestamp, addedLocal = self._ConvertTime(node.get('date_added'))
        modifiedUtc, modifiedTimestamp, modifiedLocal = self._ConvertTime(node.get('date_modified'))

        self._record['Type'] = nodeType
        self._record['PathInTree'] = PATH_SEPARATOR.join(pathParts)
        self._record['Name'] = self._NodeName(node)
        self._record['URL'] = str(node.get('url') or '') if nodeType == 'url' else ''
        self._record['DateTimeAdded_UTC'] = addedUtc
        self._record['TimestampAdded_UTC'] = addedTimestamp
        self._record['DateTimeAdded_Local'] = addedLocal
        self._record['DateTimeModified_UTC'] = modifiedUtc
        self._record['TimestampModified_UTC'] = modifiedTimestamp
        self._record['DateTimeModified_Local'] = modifiedLocal
        self._record['TimeZoneOffset'] = self._tzOffsetMinutes
        self._record['DataSource'] = dataSource

        await self._wr.WriteRecord(tuple(self._record.values()))

        self._recordsCount += 1
        self._CleanRecord()

    async def _WalkBranch(self, node: dict, parentParts: List[str],
                          dataSource: str, nodeLabel: Optional[str] = None) -> None:
        """
        Обойти ветвь дерева закладок в глубину и записать все её узлы.

        Обход реализован итеративно (явный стек), поэтому глубина вложенности
        не ограничена лимитом рекурсии Python.

        Args:
            node (dict): корневой узел ветви
            parentParts (List[str]): путь до родителя
            dataSource (str): путь до файла Bookmarks
            nodeLabel (Optional[str]): подпись корня (для «красивых» имён ветвей)
        """
        stack: List[Tuple[dict, List[str], Optional[str]]] = [(node, parentParts, nodeLabel)]

        while stack:
            current, parts, label = stack.pop()
            if not isinstance(current, dict):
                continue

            currentParts = parts + [label if label is not None else self._NodeName(current)]
            await self._WriteNode(current, currentParts, dataSource)

            children = current.get('children')
            if not isinstance(children, list):
                continue
            # reversed + pop(): дети обрабатываются в исходном порядке
            for child in reversed(children):
                stack.append((child, currentParts, None))

    async def Start(self) -> int:
        """
        Точка входа логики: найти Bookmarks, обойти дерево и записать узлы.

        Returns:
            int: количество записанных записей (0, если файла нет, он битый
                 или дерево закладок пустое)
        """
        self._recordsCount = 0

        bookmarksPath = self.GetBookmarksPath()
        if bookmarksPath is None:
            message = 'Файл Bookmarks не найден в дампе Vivaldi — модуль пропущен'
            if self._log:
                self._log.Info('VivaldiBookmarks', message)
            return 0

        if self._log:
            self._log.Info('VivaldiBookmarks', f'Читаю {bookmarksPath}')

        data = self._LoadBookmarks(bookmarksPath)
        if data is None:
            return 0

        roots = data.get('roots')
        if not isinstance(roots, dict):
            if self._log:
                self._log.Error('VivaldiBookmarks', 'В файле Bookmarks нет объекта roots')
            return 0

        for branchKey, rootNode in roots.items():
            if not isinstance(rootNode, dict):
                continue
            if branchKey == TRASH_BRANCH and not INCLUDE_TRASH:
                continue

            label = ROOT_BRANCHES.get(branchKey)
            if label is None:
                label = TRASH_LABEL if branchKey == TRASH_BRANCH else (self._NodeName(rootNode) or str(branchKey))

            await self._WalkBranch(rootNode, [], bookmarksPath, label)

        if self._log:
            self._log.Info('VivaldiBookmarks', f'Записано узлов: {self._recordsCount}')

        return self._recordsCount


class Parser:
    """
    Интерфейс модуля: готовит поля БД, вызывает парсер и финализирует запись.
    """

    def __init__(self, parameters: dict):
        """Сохранить параметры каркаса и описать поля таблицы Data."""
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'Type': 'TEXT',
            'PathInTree': 'TEXT',
            'Name': 'TEXT',
            'URL': 'TEXT',
            'DateTimeAdded_UTC': 'TEXT',
            'TimestampAdded_UTC': 'INTEGER',
            'DateTimeAdded_Local': 'TEXT',
            'DateTimeModified_UTC': 'TEXT',
            'TimestampModified_UTC': 'INTEGER',
            'DateTimeModified_Local': 'TEXT',
            'TimeZoneOffset': 'INTEGER',
            'DataSource': 'TEXT',
        }

        self.__bookmarksParser: Any = _VivaldiBookmarksParser(
            parameters, self.__recordFields
        )

    async def Start(self) -> Dict:
        """
        Запустить обработку Bookmarks и записать результат в БД.

        Returns:
            Dict: {MODULENAME: имя БД} либо {}, если записей нет
        """
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')
        dbConnection: Any = self.__parameters.get('DBCONNECTION')
        log: Any = self.__parameters.get('LOG')

        if outputWriter is None or dbConnection is None or not dbConnection.IsConnected():
            return {}

        fields: dict = {
            'Type':                  ('Тип узла',             50,  'string',   'url — закладка, folder — папка'),
            'PathInTree':            ('Путь в дереве',        500, 'string',   'Полный путь от корня, включая имя узла'),
            'Name':                  ('Название',             300, 'string',   'Имя закладки или папки'),
            'URL':                   ('URL',                  500, 'string',   'Адрес закладки (для папок пусто)'),
            'DateTimeAdded_UTC':     ('Дата добавления UTC',   50,  'datetime', 'date_added в UTC'),
            'TimestampAdded_UTC':    ('Timestamp добавления', 100,  'integer',  'Chromium-время (мкс с 1601-01-01 UTC)'),
            'DateTimeAdded_Local':   ('Дата добавления',       50,  'datetime', 'date_added в локальном поясе'),
            'DateTimeModified_UTC':  ('Дата изменения UTC',    50,  'datetime', 'date_modified в UTC'),
            'TimestampModified_UTC': ('Timestamp изменения',  100,  'integer',  'Chromium-время (мкс с 1601-01-01 UTC)'),
            'DateTimeModified_Local':('Дата изменения',        50,  'datetime', 'date_modified в локальном поясе'),
            'TimeZoneOffset':        ('Смещение пояса',        50,  'integer',  'Смещение локального пояса в минутах'),
            'DataSource':            ('Источник данных',      250, 'string',   'Путь к файлу Bookmarks'),
        }

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """:
        Анализ закладок Vivaldi (JSON-файл Bookmarks).
        Извлекаются: тип узла (закладка/папка), полный путь в дереве, имя, URL,
        время добавления и изменения (Chromium-микросекунды, UTC и локальное).
        Ветка trash (удалённые закладки) по ТЗ не разбирается.
        """

        infoTableData: dict = {
            'Name': self.__parameters.get('MODULENAME'),
            'Help': HELP_TEXT,
            'Timestamp': self.__parameters.get('CASENAME'),
            'Vendor': 'LabFramework',
        }

        recordsCount = 0

        try:
            outputWriter.SetFields(fields, self.__recordFields)
            outputWriter.CreateDatabaseTables()

            recordsCount = await self.__bookmarksParser.Start()

            outputWriter.RemoveTempTables()
            if recordsCount > 0:
                await outputWriter.CreateDatabaseIndexes(self.__parameters.get('MODULENAME'))
                outputWriter.SetInfo(infoTableData)
                outputWriter.WriteMeta()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiBookmarks', f'Ошибка обработки Bookmarks: {error}')

        try:
            await outputWriter.CloseOutput()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiBookmarks', f'Ошибка закрытия выходной БД: {error}')

        if recordsCount == 0:
            return {}

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
