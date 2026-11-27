"""
Модуль извлечения истории посещений браузера Vivaldi.

Источник: SQLite-база <профиль>/Default/History, таблицы urls и visits.
Время в базе — Chromium-микросекунды с 1601-01-01 UTC;
"""
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

from Common.Chrom import chromium_to_datetime, format_datetime
from Common.Routines import FixedOffset as tzInfo


# Базовые типы переходов Chromium (младший байт transition)
TRANSITION_TYPES = {
    0: 'LINK',
    1: 'TYPED',
    2: 'AUTO_BOOKMARK',
    3: 'AUTO_SUBFRAME',
    4: 'MANUAL_SUBFRAME',
    5: 'GENERATED',
    6: 'AUTO_TOPLEVEL',
    7: 'FORM_SUBMIT',
    8: 'RELOAD',
    9: 'KEYWORD',
    10: 'KEYWORD_GENERATED',
}


class _VivaldiHistoryParser:
    """Извлекает визиты из базы History и пишет их в БД каркаса."""

    def __init__(self, parserParameters: dict, recordFields: dict):
        """Инициализировать парсер параметрами каркаса и полями записи."""
        self._storage: str = parserParameters.get('STORAGE')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._record: dict = {field: '' for field in recordFields.keys()}
        self._currentTzInfo, self._tzOffsetMinutes = self._LocalTimeZone()

    @staticmethod
    def _LocalTimeZone() -> tuple:
        """
        Определить локальный часовой пояс машины для поля DateTime_Local.

        Returns:
            tuple: объект часового пояса и смещение в минутах
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

    def _HistoryPath(self) -> Optional[str]:
        """
        Найти файл History в дампе Vivaldi.

        Пробуем несколько вариантов, потому что STORAGE может указывать
        на корень дампа (Source-vivaldi/) или на профиль (…/config/Default/).
        """
        if not self._storage:
            return None
        candidates = [
            os.path.join(self._storage, 'config', 'vivaldi', 'Default', 'History'),
            os.path.join(self._storage, 'config', 'Default', 'History'),
            os.path.join(self._storage, 'vivaldi', 'Default', 'History'),
            os.path.join(self._storage, 'Default', 'History'),
            os.path.join(self._storage, 'History'),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _DecodeTransition(transition: int) -> str:
        """Расшифровать базовый тип перехода (младший байт)."""
        base = transition & 0xFF
        return TRANSITION_TYPES.get(base, f'UNKNOWN({base})')

    async def Start(self) -> int:
        """
        Точка входа логики: найти History и записать визиты.

        Returns:
            int: количество записанных записей (0, если файла нет или он битый)
        """
        historyPath = self._HistoryPath()
        if historyPath is None:
            message = 'Файл History не найден в дампе Vivaldi — модуль пропущен'
            if self._log:
                self._log.Info('VivaldiHistory', message)
            return 0

        if self._log:
            self._log.Info('VivaldiHistory', f'Читаю {historyPath}')

        try:
            # mode=ro — только чтение; SQLite сам подхватит -wal / -shm
            conn = sqlite3.connect(f'file:{historyPath}?mode=ro', uri=True)
        except sqlite3.Error as e:
            if self._log:
                self._log.Error('VivaldiHistory', f'Не удалось открыть History: {e}')
            return 0

        recordsCount = 0
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT  u.url,
                        u.title,
                        u.visit_count,
                        v.visit_time,
                        v.transition,
                        v.visit_duration
                FROM visits AS v
                JOIN urls   AS u ON u.id = v.url
                ORDER BY v.visit_time
            """)
            for url, title, visit_count, visit_time, transition, duration in cur:
                dt_utc = chromium_to_datetime(visit_time)
                dt_local = chromium_to_datetime(visit_time, tz=self._currentTzInfo)

                self._record['URL'] = url or ''
                self._record['Title'] = title or ''
                self._record['VisitCount'] = visit_count or 0
                self._record['DateTime_UTC'] = format_datetime(dt_utc)
                # Как в Downloads/Cookies: Chromium-микросекунды, а не FILETIME
                try:
                    timestampUtc = int(visit_time or 0)
                except (TypeError, ValueError):
                    timestampUtc = 0
                self._record['Timestamp_UTC'] = timestampUtc
                self._record['DateTime_Local'] = format_datetime(dt_local)
                self._record['TimeZoneOffset'] = self._tzOffsetMinutes
                self._record['Transition'] = transition or 0
                self._record['TransitionName'] = self._DecodeTransition(transition or 0)
                self._record['VisitDuration'] = duration or 0
                self._record['DataSource'] = historyPath

                await self._wr.WriteRecord((
                    self._record['URL'],
                    self._record['Title'],
                    self._record['VisitCount'],
                    self._record['DateTime_UTC'],
                    self._record['Timestamp_UTC'],
                    self._record['DateTime_Local'],
                    self._record['TimeZoneOffset'],
                    self._record['Transition'],
                    self._record['TransitionName'],
                    self._record['VisitDuration'],
                    self._record['DataSource'],
                ))

                recordsCount += 1
                self._CleanRecord()
        except sqlite3.Error as e:
            if self._log:
                self._log.Error('VivaldiHistory', f'Ошибка чтения History: {e}')
        finally:
            conn.close()

        return recordsCount


class Parser:
    """
    Интерфейс модуля: готовит поля БД, вызывает парсер и финализирует запись.
    """

    def __init__(self, parameters: dict):
        """Инициализировать модуль параметрами каркаса и описанием полей БД."""
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'URL': 'TEXT',
            'Title': 'TEXT',
            'VisitCount': 'INTEGER',
            'DateTime_UTC': 'TEXT',
            'Timestamp_UTC': 'INTEGER',
            'DateTime_Local': 'TEXT',
            'TimeZoneOffset': 'INTEGER',
            'Transition': 'INTEGER',
            'TransitionName': 'TEXT',
            'VisitDuration': 'INTEGER',
            'DataSource': 'TEXT',
        }

        self.__historyParser: Any = _VivaldiHistoryParser(
            parameters, self.__recordFields
        )

    async def Start(self) -> Dict:
        """
        Запустить обработку History и записать результат в БД.

        Returns:
            Dict: {MODULENAME: имя БД} либо {}, если записей нет
        """
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')
        dbConnection: Any = self.__parameters.get('DBCONNECTION')
        log: Any = self.__parameters.get('LOG')

        if outputWriter is None or dbConnection is None or not dbConnection.IsConnected():
            return {}

        fields: dict = {
            'URL':            ('URL страницы',        500, 'string',  'Адрес посещённой страницы'),
            'Title':          ('Заголовок',           300, 'string',  'Заголовок страницы'),
            'VisitCount':     ('Кол-во визитов',       50, 'integer', 'Суммарное число визитов URL'),
            'DateTime_UTC':   ('Дата/время UTC',       50, 'datetime','Время визита в UTC'),
            'Timestamp_UTC':  ('Timestamp UTC',       100, 'integer', 'Chromium-время (мкс с 1601-01-01 UTC)'),
            'DateTime_Local': ('Дата/время локальное', 50, 'datetime','Время визита в локальном поясе'),
            'TimeZoneOffset': ('Смещение пояса',       50, 'integer', 'Смещение в минутах'),
            'Transition':     ('Код перехода',         50, 'integer', 'Сырой transition из Chromium'),
            'TransitionName': ('Тип перехода',        100, 'string',  'Расшифровка transition'),
            'VisitDuration':  ('Длительность, мс',     50, 'integer', 'Длительность визита'),
            'DataSource':     ('Источник данных',     250, 'string',  'Путь к файлу History'),
        }

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """:
        Анализ истории посещений Vivaldi (таблицы urls и visits базы History).
        Извлекаются: URL, заголовок, число визитов, время визита (UTC и локальное),
        тип перехода и длительность.
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

            recordsCount = await self.__historyParser.Start()

            outputWriter.RemoveTempTables()
            if recordsCount > 0:
                await outputWriter.CreateDatabaseIndexes(
                    self.__parameters.get('MODULENAME')
                )
                outputWriter.SetInfo(infoTableData)
                outputWriter.WriteMeta()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiHistory', f'Ошибка обработки History: {error}')

        try:
            await outputWriter.CloseOutput()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiHistory', f'Ошибка закрытия выходной БД: {error}')

        if recordsCount == 0:
            return {}

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
