# -*- coding: utf-8 -*-
"""
Модуль для извлечения записей кэша браузера Vivaldi (каталог Cache/Cache_Data).

Источник: Simple Cache Chromium (папка Cache_Data внутри профиля Vivaldi).
Записи представляют собой файлы вида <hash>_<n> с бинарным заголовком и содержимым.
"""
import os
import re
from typing import Any, Dict, Optional
from Common.Routines import TimeConverter, FixedOffset as tzInfo
from Common.Chrom import format_datetime

URL_PATTERN = re.compile(rb'https?://[A-Za-z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]+')


class _VivaldiCacheParser:
    """
    Базовый класс, иницилизирующий параметры для извлечения и обработки данных кэша браузера Vivaldi.
    """
    def __init__(self, parserParameters: dict, recordFields: dict):
        self._storage: str = parserParameters.get('STORAGE')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._record: dict = {field: '' for field in recordFields.keys()}
        self._tc: Any = TimeConverter()
        self._currentTzInfo: tzInfo = tzInfo(300, 'Asia/Yekaterinburg')

    def _CleanRecord(self) -> None:
        """
        Метод для фрагментарной очистки полей записи обработанных данных
        """
        for key in self._record.keys():
            self._record[key] = ''

    def _CachePath(self) -> Optional[str]:
        """
        Метод поиска каталога Cache_Data в дампе Vivaldi.

        Returns:
            Optional[str]: полный путь к каталогу Cache_Data
        """
        if not self._storage:
            return None
        candidates = [
            os.path.join(self._storage, 'cache', 'vivaldi', 'Default', 'Cache', 'Cache_Data'),
            os.path.join(self._storage, 'cache', 'Default', 'Cache', 'Cache_Data'),
            os.path.join(self._storage, 'cache', 'vivaldi', 'Cache_Data'),
            os.path.join(self._storage, 'Cache', 'Cache_Data'),
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path
        return None

    async def Start(self) -> None:
        """
        Метод-обертка для вызова логики обработки данных
        """
        cacheDir: Optional[str] = self._CachePath()
        if cacheDir is None:
            message = 'Каталог Cache_Data не найден в дампе Vivaldi — модуль пропущен'
            if self._log is not None:
                self._log.Info('VivaldiCache', message)
            return

        if self._log is not None:
            self._log.Info('VivaldiCache', f'Читаю {cacheDir}')

        try:
            entryNames: list = sorted(os.listdir(cacheDir))
        except OSError as e:
            if self._log is not None:
                self._log.Error('VivaldiCache', f'Не удалось получить список файлов кэша: {e}')
            return

        for entryName in entryNames:
            entryPath: str = os.path.join(cacheDir, entryName)
            if not os.path.isfile(entryPath):
                continue
            try:
                await self._ProcessEntry(entryPath, entryName)
            except Exception as e:
                if self._log is not None:
                    self._log.Warn('VivaldiCache', f'Пропуск записи кэша {entryName}: {e}')

    async def _ProcessEntry(self, entryPath: str, fileName: str) -> None:
        """
        Метод обработки одной записи кэша.

        Args:
            entryPath (str): полный путь до файла записи кэша
            fileName (str): имя файла записи кэша
        """
        size: int = os.path.getsize(entryPath)
        content: bytes = b''
        with open(entryPath, 'rb') as entryFile:
            content = entryFile.read()

        dt_utc: Any = TimeConverter.UnixTimestampToDatetime(os.path.getmtime(entryPath))
        dt_local: Any = dt_utc.astimezone(self._currentTzInfo)
        urlMatch = URL_PATTERN.search(content)
        url: str = urlMatch.group(0).decode('utf-8', 'replace') if urlMatch is not None else fileName

        self._record['URL'] = url
        self._record['FileName'] = fileName
        self._record['DateTimeCreate_UTC'] = format_datetime(dt_utc)
        self._record['TimestampCreate_UTC'] = self._tc.DatetimeToFILETIME(dt_utc)
        self._record['Size'] = size
        self._record['Content'] = content
        self._record['DataSource'] = entryPath

        await self._wr.WriteRecord((
            self._record['URL'],
            self._record['FileName'],
            self._record['DateTimeCreate_UTC'],
            self._record['TimestampCreate_UTC'],
            self._record['Size'],
            self._record['Content'],
            self._record['DataSource'],
        ))

        self._CleanRecord()


class Parser:
    """
    Интерфейс модуля: готовит поля БД, вызывает парсер и финализирует запись.
    """
    def __init__(self, parameters: dict):
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'URL': 'TEXT',
            'FileName': 'TEXT',
            'DateTimeCreate_UTC': 'TEXT',
            'TimestampCreate_UTC': 'INTEGER',
            'Size': 'INTEGER',
            'Content': 'BLOB',
            'DataSource': 'TEXT',
        }

        self.__cacheParser: Any = _VivaldiCacheParser(parameters, self.__recordFields)

    async def Start(self) -> Dict:
        """
        Запустить обработку кэша Vivaldi и записать результат в БД.

        Returns:
            Dict: {MODULENAME: имя БД}
        """
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')
        dbConnection: Any = self.__parameters.get('DBCONNECTION')

        if not dbConnection.IsConnected():
            return {}

        fields: dict = {
            'URL':            ('Ключ кэша',         500, 'string',  'Адрес или ключ ресурса из Simple Cache'),
            'FileName':       ('Имя файла',         300, 'string',  'Имя файла записи кэша'),
            'DateTimeCreate_UTC': ('Дата создания UTC',  50, 'datetime','Дата создания записи кэша'),
            'TimestampCreate_UTC': ('Timestamp создания UTC', 50, 'datetime','Timestamp создания записи кэша'),
            'Size':           ('Размер',            100, 'integer', 'Размер данных записи кэша'),
            'Content':        ('Содержимое',        100, 'blob',   'Содержимое записи кэша (BLOB)'),
            'DataSource':     ('Источник данных',  250, 'string',  'Путь к файлу Cache_Data'),
        }

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """:
        Анализ кэша Vivaldi (Simple Cache, каталог Cache_Data).
        Извлекаются: ключ/URL записи, имя файла, дата создания, timestamp, размер и содержимое.
        """

        infoTableData: dict = {
            'Name': self.__parameters.get('MODULENAME'),
            'Help': HELP_TEXT,
            'Timestamp': self.__parameters.get('CASENAME'),
            'Vendor': 'LabFramework',
        }

        outputWriter.SetFields(fields, self.__recordFields)
        outputWriter.CreateDatabaseTables()

        await self.__cacheParser.Start()

        outputWriter.RemoveTempTables()
        await outputWriter.CreateDatabaseIndexes(self.__parameters.get('MODULENAME'))
        outputWriter.SetInfo(infoTableData)
        outputWriter.WriteMeta()
        await outputWriter.CloseOutput()

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
