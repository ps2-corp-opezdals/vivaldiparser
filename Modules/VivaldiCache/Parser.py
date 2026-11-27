# -*- coding: utf-8 -*-
"""
Модуль для извлечения записей кэша браузера Vivaldi (каталог Cache/Cache_Data).

Источник: Simple Cache Chromium (папка Cache_Data внутри профиля Vivaldi).
Записи представляют собой файлы вида <hash>_<n> с бинарным заголовком и содержимым.
URL и длина ключа читаются из заголовка Simple Cache, а не ищутся по всему файлу.
"""
import os
import re
import struct
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from Common.Chrom import FILETIME_EPOCH, chromium_to_filetime, format_datetime

# Сигнатура Simple Cache Chromium (смещение 4 в заголовке)
SIMPLE_CACHE_MAGIC = 0xFCFB6D1B
# Размер служебного заголовка перед ключом
CACHE_HEADER_SIZE = 24
# Разумный верхний предел длины ключа, чтобы не читать мусорные значения
MAX_KEY_LENGTH = 1 << 20

URL_PATTERN = re.compile(rb'https?://[A-Za-z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]+')


class _VivaldiCacheParser:
    """
    Извлекает записи Simple Cache и пишет их в БД каркаса.
    """
    def __init__(self, parserParameters: dict, recordFields: dict):
        """Инициализировать парсер параметрами каркаса и полями записи."""
        self._storage: str = parserParameters.get('STORAGE')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._record: dict = {field: '' for field in recordFields.keys()}

    def _CleanRecord(self) -> None:
        """
        Метод для фрагментарной очистки полей записи обработанных данных
        """
        for key in self._record.keys():
            self._record[key] = ''

    def _CachePath(self) -> Optional[str]:
        """
        Метод поиска каталога Cache_Data с записями в дампе Vivaldi.

        Пустой каталог не считается найденным: при нуле записей модуль
        должен вернуть пустой результат.

        Returns:
            Optional[str]: путь к каталогу с файлами записей либо None
        """
        if not self._storage:
            return None
        candidates = [
            os.path.join(self._storage, 'config', 'vivaldi', 'Default', 'Cache', 'Cache_Data'),
            os.path.join(self._storage, 'cache', 'vivaldi', 'Default', 'Cache', 'Cache_Data'),
            os.path.join(self._storage, 'cache', 'Default', 'Cache', 'Cache_Data'),
            os.path.join(self._storage, 'cache', 'vivaldi', 'Cache_Data'),
            os.path.join(self._storage, 'Cache', 'Cache_Data'),
        ]
        for path in candidates:
            if not os.path.isdir(path):
                continue
            try:
                with os.scandir(path) as entries:
                    if any(entry.is_file() for entry in entries):
                        return path
            except OSError:
                continue
        return None

    @staticmethod
    def _ExtractUrl(key: bytes) -> Optional[str]:
        """
        Извлечь URL из ключа записи Simple Cache.

        Ключ имеет вид 1/0/_dk_<URL>, возможны служебные префиксы вида _dk_s_,
        поэтому URL ищется в первом токене ключа.

        Args:
            key (bytes): ключ записи кэша из заголовка

        Returns:
            Optional[str]: URL либо None, если ключ не разобран
        """
        try:
            keyText = key.decode('utf-8', 'replace')
        except Exception:
            return None

        stripped = keyText.strip()
        if not stripped:
            return None

        firstToken = stripped.split(None, 1)[0]
        match = URL_PATTERN.search(firstToken.encode('utf-8', 'replace'))
        if match is None:
            return None
        return match.group(0).decode('utf-8', 'replace')

    async def Start(self) -> int:
        """
        Найти каталог Cache_Data и записать все разобранные записи кэша.

        Returns:
            int: количество записанных записей (0, если каталога нет или он пуст)
        """
        cacheDir: Optional[str] = self._CachePath()
        if cacheDir is None:
            if self._log is not None:
                self._log.Info('VivaldiCache', 'Каталог Cache_Data не найден или пуст — модуль пропущен')
            return 0

        if self._log is not None:
            self._log.Info('VivaldiCache', f'Читаю {cacheDir}')

        try:
            entryNames: list = sorted(os.listdir(cacheDir))
        except OSError as e:
            if self._log is not None:
                self._log.Error('VivaldiCache', f'Не удалось получить список файлов кэша: {e}')
            return 0

        recordsCount = 0
        for entryName in entryNames:
            entryPath: str = os.path.join(cacheDir, entryName)
            if not os.path.isfile(entryPath):
                continue
            try:
                await self._ProcessEntry(entryPath, entryName)
                recordsCount += 1
            except Exception as e:
                if self._log is not None:
                    self._log.Warn('VivaldiCache', f'Пропуск записи кэша {entryName}: {e}')
        return recordsCount

    async def _ProcessEntry(self, entryPath: str, fileName: str) -> None:
        """
        Метод обработки одной записи кэша по заголовку Simple Cache.

        Args:
            entryPath (str): полный путь до файла записи кэша
            fileName (str): имя файла записи кэша

        Raises:
            ValueError: если запись повреждена или ключ не удалось разобрать
        """
        fileSize: int = os.path.getsize(entryPath)
        with open(entryPath, 'rb') as entryFile:
            header: bytes = entryFile.read(CACHE_HEADER_SIZE)
            if len(header) < CACHE_HEADER_SIZE:
                raise ValueError('файл короче заголовка Simple Cache')

            magic: int = struct.unpack_from('<I', header, 4)[0]
            if magic != SIMPLE_CACHE_MAGIC:
                raise ValueError('неверная сигнатура Simple Cache')

            keyLength: int = struct.unpack_from('<I', header, 12)[0]
            if (keyLength == 0 or keyLength > MAX_KEY_LENGTH
                    or CACHE_HEADER_SIZE + keyLength > fileSize):
                raise ValueError(f'некорректная длина ключа: {keyLength}')

            key: bytes = entryFile.read(keyLength)
            if len(key) < keyLength:
                raise ValueError('ключ записи кэша обрезан')

            entryFile.seek(0)
            content: bytes = entryFile.read()

        url: Optional[str] = self._ExtractUrl(key)
        if url is None:
            raise ValueError('не удалось извлечь URL из ключа кэша')

        mtime: float = os.path.getmtime(entryPath)
        dt_utc: datetime = datetime.fromtimestamp(mtime, tz=timezone.utc)
        delta = dt_utc - FILETIME_EPOCH
        chromiumMicroseconds: int = (
            (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        )

        self._record['URL'] = url
        self._record['FileName'] = fileName
        self._record['DateTimeCreate_UTC'] = format_datetime(dt_utc)
        self._record['TimestampCreate_UTC'] = chromium_to_filetime(chromiumMicroseconds)
        self._record['Size'] = fileSize
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
        """Инициализировать модуль параметрами каркаса и описанием полей БД."""
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
            Dict: {MODULENAME: имя БД} либо {}, если записей нет
        """
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')
        dbConnection: Any = self.__parameters.get('DBCONNECTION')
        log: Any = self.__parameters.get('LOG')

        if outputWriter is None or dbConnection is None or not dbConnection.IsConnected():
            return {}

        fields: dict = {
            'URL':            ('Ключ кэша',         500, 'string',  'Адрес или ключ ресурса из Simple Cache'),
            'FileName':       ('Имя файла',         300, 'string',  'Имя файла записи кэша'),
            'DateTimeCreate_UTC': ('Дата создания UTC',  50, 'datetime','Дата создания записи кэша'),
            'TimestampCreate_UTC': ('Timestamp создания UTC', 50, 'datetime','FILETIME (100 нс)'),
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

        recordsCount = 0

        try:
            outputWriter.SetFields(fields, self.__recordFields)
            outputWriter.CreateDatabaseTables()

            recordsCount = await self.__cacheParser.Start()

            outputWriter.RemoveTempTables()
            if recordsCount > 0:
                await outputWriter.CreateDatabaseIndexes(
                    self.__parameters.get('MODULENAME')
                )
                outputWriter.SetInfo(infoTableData)
                outputWriter.WriteMeta()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiCache', f'Ошибка обработки кэша: {error}')

        try:
            await outputWriter.CloseOutput()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiCache', f'Ошибка закрытия выходной БД: {error}')

        if recordsCount == 0:
            return {}

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
