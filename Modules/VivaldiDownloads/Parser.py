# -*- coding: utf-8 -*-
"""
Модуль извлечения сведений о загрузках из профилей Vivaldi.

Модуль рекурсивно ищет SQLite-базы History внутри STORAGE, читает таблицы
downloads, downloads_url_chains и downloads_slices и сохраняет сведения
о каждой загрузке в выходную базу данных.
"""
import os
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from Common.Chrom import chromium_to_datetime, format_datetime

# ----------------------------------------------------------------------
HISTORY_FILE_NAME: str = 'History'
HISTORY_SIDECAR_SUFFIXES: Tuple[str, ...] = ('-wal', '-shm', '-journal')
SQLITE_HEADER: bytes = b'SQLite format 3\x00'

# Каталоги, которые не имеют отношения к History и могут быть очень большими.
_SKIP_DIR_NAMES = {
    '.git',
    '__pycache__',
    'cache',
    'code cache',
    'gpucache',
    'shadercache',
    'grshadercache',
    'service worker',
    'logs',
    'cases',
    'temp',
}

# Значения state из components/history/core/browser/download_constants.h.
_STATE_NAMES: Dict[int, str] = {
    0: 'In progress',
    1: 'Complete',
    2: 'Cancelled',
    3: 'Interrupted (legacy bug 140687)',
    4: 'Interrupted',
}

# Значения danger_type из components/history/core/browser/download_constants.h.
_DANGER_TYPE_NAMES: Dict[int, str] = {
    0: 'Not dangerous',
    1: 'Dangerous file',
    2: 'Dangerous URL',
    3: 'Dangerous content',
    4: 'Maybe dangerous content',
    5: 'Uncommon content',
    6: 'User validated',
    7: 'Dangerous host',
    8: 'Potentially unwanted',
    9: 'Allowlisted by policy',
    10: 'Async scanning',
    11: 'Blocked password protected',
    12: 'Blocked too large',
    13: 'Sensitive content warning',
    14: 'Sensitive content block',
    15: 'Deep scanned safe',
    16: 'Deep scanned opened dangerous',
    17: 'Prompt for scanning',
    18: 'Blocked unsupported filetype (deprecated)',
    19: 'Dangerous account compromise',
    20: 'Deep scanned failed',
    21: 'Prompt for local password scanning',
    22: 'Async local password scanning',
    23: 'Blocked scan failed',
    24: 'Forced save to Google Drive',
    25: 'Forced save to OneDrive',
}

# Значения interrupt_reason из download_interrupt_reason_values.h.
_INTERRUPT_REASON_NAMES: Dict[int, str] = {
    0: 'None',
    1: 'File failed',
    2: 'File access denied',
    3: 'File no space',
    5: 'File name too long',
    6: 'File too large',
    7: 'File virus infected',
    10: 'File transient error',
    11: 'File blocked',
    12: 'File security check failed',
    13: 'File too short',
    14: 'File hash mismatch',
    15: 'File same as source',
    20: 'Network failed',
    21: 'Network timeout',
    22: 'Network disconnected',
    23: 'Network server down',
    24: 'Network invalid request',
    30: 'Server failed',
    31: 'Server no range',
    33: 'Server bad content',
    34: 'Server unauthorized',
    35: 'Server certificate problem',
    36: 'Server forbidden',
    37: 'Server unreachable',
    38: 'Server content length mismatch',
    39: 'Server cross-origin redirect',
    40: 'User canceled',
    41: 'User shutdown',
    50: 'Crash',
    51: 'Local download blocked',
}


# ----------------------------------------------------------------------
class _VivaldiDownloadsParser:
    """
    Класс реализует поиск баз History, чтение загрузок и запись их в выходную БД.
    """
    def __init__(self, parserParameters: dict, recordFields: dict):
        self._storage: Optional[str] = parserParameters.get('STORAGE')
        self._tempFolder: Optional[str] = parserParameters.get('TEMP')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._redrawUI: Any = parserParameters.get('UIREDRAW')

        self._recordFields: dict = recordFields
        self._record: dict = {}
        for fieldName, fieldType in recordFields.items():
            self._record[fieldName] = self._DefaultValue(fieldType)

    @staticmethod
    def _DefaultValue(fieldType: str) -> Any:
        """
        Вернуть значение по умолчанию для типа поля выходной БД.

        Args:
            fieldType (str): SQL-тип поля

        Returns:
            Any: значение по умолчанию
        """
        normalizedType = str(fieldType).upper()
        if normalizedType == 'INTEGER':
            return 0
        if normalizedType == 'BLOB':
            return b''
        return ''

    def FindHistoryDatabases(self) -> List[str]:
        """
        Найти все файлы History внутри STORAGE.

        Поиск выполняется рекурсивно, потому что в дампе может быть несколько
        профилей Vivaldi (Default, Profile 1, Profile 2 и т.д.). Файлы, которые
        не являются SQLite-базами, пропускаются.

        Returns:
            List[str]: список абсолютных путей до найденных баз History
        """
        result: List[str] = []
        storage: Optional[str] = self._storage

        if not storage:
            return result

        storage = os.path.abspath(storage)

        # На случай, если в STORAGE передан сам файл History, а не каталог.
        if os.path.isfile(storage):
            if (
                os.path.basename(storage).lower() == HISTORY_FILE_NAME.lower()
                and self._IsSQLiteDatabase(storage)
            ):
                return [storage]
            return result

        if not os.path.isdir(storage):
            return result

        seenPaths = set()

        for root, dirNames, fileNames in os.walk(storage):
            # Не заходим в заведомо тяжёлые и не относящиеся к делу каталоги.
            dirNames[:] = sorted(
                dirName for dirName in dirNames
                if dirName.lower() not in _SKIP_DIR_NAMES
            )

            for fileName in fileNames:
                if fileName.lower() != HISTORY_FILE_NAME.lower():
                    continue

                fullPath = os.path.abspath(os.path.join(root, fileName))
                normalizedPath = os.path.normcase(fullPath)

                if normalizedPath in seenPaths:
                    continue

                if not self._IsSQLiteDatabase(fullPath):
                    continue

                seenPaths.add(normalizedPath)
                result.append(fullPath)

        result.sort(key=lambda path: path.lower())
        return result

    @staticmethod
    def _IsSQLiteDatabase(filePath: str) -> bool:
        """
        Проверить, что файл является SQLite-базой по его заголовку.

        Args:
            filePath (str): путь до проверяемого файла

        Returns:
            bool: True, если файл начинается с заголовка SQLite
        """
        try:
            with open(filePath, 'rb') as fileHandle:
                return fileHandle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
        except OSError:
            return False

    async def Start(self, sourceFiles: List[str]) -> int:
        """
        Обработать все найденные базы History и записать загрузки в выходную БД.

        Args:
            sourceFiles (List[str]): пути до найденных баз History

        Returns:
            int: количество записанных записей
        """
        if self._wr is None or not sourceFiles:
            return 0

        totalRecords: int = 0
        totalFiles: int = len(sourceFiles)

        for index, sourcePath in enumerate(sourceFiles, start=1):
            startPercent = int((index - 1) / totalFiles * 100)
            await self._Redraw(
                f'VivaldiDownloads: чтение {sourcePath}',
                startPercent
            )

            records = self._ReadDownloadsDatabase(sourcePath)

            for record in records:
                await self._wr.WriteRecord(record)
                totalRecords += 1

            self._Info(
                f'Обработана база History: {sourcePath}, загрузок: {len(records)}'
            )

            finishPercent = int(index / totalFiles * 100)
            await self._Redraw(
                f'VivaldiDownloads: обработано {index} из {totalFiles} баз',
                finishPercent
            )

        return totalRecords

    def _ReadDownloadsDatabase(self, sourcePath: str) -> List[Tuple]:
        """
        Прочитать одну базу History и вернуть подготовленные кортежи записей.

        База и её WAL/SHM/Journal-файлы копируются во временный каталог,
        чтобы не изменять улики и корректно прочитать незавершённые транзакции.

        Args:
            sourcePath (str): путь до исходной базы History

        Returns:
            List[Tuple]: список кортежей для записи в таблицу Data
        """
        tempFolder: Optional[str] = None
        connection: Optional[sqlite3.Connection] = None

        try:
            tempFolder = self._CreateTempCopy(sourcePath)
            if tempFolder is None:
                return []

            copyPath = os.path.join(tempFolder, HISTORY_FILE_NAME)
            connection = sqlite3.connect(copyPath)
            connection.row_factory = sqlite3.Row

            if not self._TableExists(connection, 'downloads'):
                self._Warn(f'В базе History нет таблицы downloads: {sourcePath}')
                return []

            urlChains: Dict[int, List[str]] = self._ReadUrlChains(connection)
            slices: Dict[int, Tuple[int, int]] = self._ReadSlices(connection)

            cursor = connection.cursor()
            cursor.execute('SELECT * FROM downloads;')

            columnNames = [description[0].lower() for description in cursor.description]
            records: List[Tuple] = []

            for row in cursor:
                downloadId = self._AsInt(row['id'])
                records.append(self._BuildRecord(
                    row,
                    columnNames,
                    urlChains.get(downloadId, []),
                    slices.get(downloadId, (0, 0)),
                    sourcePath
                ))

            return records

        except (sqlite3.Error, OSError, ValueError) as error:
            self._Warn(f'Не удалось прочитать базу History {sourcePath}: {error}')
            return []

        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

            if tempFolder is not None:
                shutil.rmtree(tempFolder, ignore_errors=True)

    @staticmethod
    def _TableExists(connection: sqlite3.Connection, tableName: str) -> bool:
        """
        Проверить наличие таблицы в SQLite-базе.

        Args:
            connection (sqlite3.Connection): соединение с базой
            tableName (str): имя таблицы

        Returns:
            bool: True, если таблица существует
        """
        cursor = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
            (tableName,)
        )
        return cursor.fetchone() is not None

    def _ReadUrlChains(self, connection: sqlite3.Connection) -> Dict[int, List[str]]:
        """
        Прочитать цепочки URL загрузок.

        URL с chain_index 0 — исходный адрес, последний элемент — адрес,
        с которого файл был фактически получен после редиректов.

        Args:
            connection (sqlite3.Connection): соединение с базой History

        Returns:
            Dict[int, List[str]]: соответствие ID загрузки и списка URL
        """
        result: Dict[int, List[str]] = {}

        if not self._TableExists(connection, 'downloads_url_chains'):
            return result

        cursor = connection.execute(
            'SELECT id, chain_index, url '
            'FROM downloads_url_chains '
            'ORDER BY id, chain_index;'
        )

        for row in cursor:
            downloadId = self._AsInt(row['id'])
            result.setdefault(downloadId, []).append(self._AsText(row['url']))

        return result

    def _ReadSlices(self, connection: sqlite3.Connection) -> Dict[int, Tuple[int, int]]:
        """
        Прочитать сведения о параллельных частях загрузок.

        Args:
            connection (sqlite3.Connection): соединение с базой History

        Returns:
            Dict[int, Tuple[int, int]]: ID загрузки, количество частей и сумма байт
        """
        result: Dict[int, Tuple[int, int]] = {}

        if not self._TableExists(connection, 'downloads_slices'):
            return result

        cursor = connection.execute(
            'SELECT download_id, COUNT(*) AS slices_count, '
            'COALESCE(SUM(received_bytes), 0) AS received_bytes '
            'FROM downloads_slices '
            'GROUP BY download_id;'
        )

        for row in cursor:
            downloadId = self._AsInt(row['download_id'])
            result[downloadId] = (
                self._AsInt(row['slices_count']),
                self._AsInt(row['received_bytes'])
            )

        return result

    def _CreateTempCopy(self, sourcePath: str) -> Optional[str]:
        """
        Скопировать базу History и её служебные файлы во временный каталог.

        Args:
            sourcePath (str): путь до исходной базы History

        Returns:
            Optional[str]: путь до временного каталога или None при ошибке
        """
        tempFolder: Optional[str] = None

        try:
            tempRoot = self._tempFolder or tempfile.gettempdir()
            os.makedirs(tempRoot, exist_ok=True)

            tempFolder = tempfile.mkdtemp(prefix='VivaldiDownloads_', dir=tempRoot)
            targetPath = os.path.join(tempFolder, HISTORY_FILE_NAME)
            shutil.copy(sourcePath, targetPath)

            # Копируем WAL/SHM/Journal, иначе SQLite может не увидеть
            # последние изменения из незавершённой транзакции.
            for suffix in HISTORY_SIDECAR_SUFFIXES:
                sidecarPath = f'{sourcePath}{suffix}'
                if os.path.isfile(sidecarPath):
                    shutil.copy(sidecarPath, f'{targetPath}{suffix}')

            return tempFolder

        except (OSError, shutil.Error) as error:
            self._Warn(f'Не удалось скопировать базу History {sourcePath}: {error}')
            if tempFolder is not None:
                shutil.rmtree(tempFolder, ignore_errors=True)
            return None

    def _BuildRecord(
        self,
        row: sqlite3.Row,
        columnNames: List[str],
        urlChain: List[str],
        sliceInfo: Tuple[int, int],
        sourcePath: str
    ) -> Tuple:
        """
        Преобразовать строку таблицы downloads в кортеж полей выходной БД.

        Args:
            row (sqlite3.Row): строка исходной базы
            columnNames (List[str]): список колонок, полученных из SELECT
            urlChain (List[str]): цепочка URL загрузки
            sliceInfo (Tuple[int, int]): количество частей и сумма их байт
            sourcePath (str): путь до исходной базы History

        Returns:
            Tuple: значения полей в порядке self._recordFields
        """
        values: Dict[str, Any] = {
            columnName: row[columnName] for columnName in columnNames
        }

        def getValue(columnName: str, defaultValue: Any = None) -> Any:
            return values.get(columnName, defaultValue)

        startTime = self._AsInt(getValue('start_time'))
        endTime = self._AsInt(getValue('end_time'))
        lastAccessTime = self._AsInt(getValue('last_access_time'))

        state = self._AsInt(getValue('state'))
        dangerType = self._AsInt(getValue('danger_type'))
        interruptReason = self._AsInt(getValue('interrupt_reason'))

        self._record['URL'] = urlChain[-1] if urlChain else ''
        self._record['OriginalURL'] = urlChain[0] if urlChain else ''
        self._record['URLChain'] = '\n'.join(urlChain)
        self._record['TargetPath'] = self._AsText(getValue('target_path'))
        self._record['CurrentPath'] = self._AsText(getValue('current_path'))

        self._record['TimestampStart_UTC'] = startTime
        self._record['DateTimeStart_UTC'] = self._FormatChromiumTime(startTime)
        self._record['TimestampEnd_UTC'] = endTime
        self._record['DateTimeEnd_UTC'] = self._FormatChromiumTime(endTime)

        self._record['ReceivedBytes'] = self._AsInt(getValue('received_bytes'))
        self._record['TotalBytes'] = self._AsInt(getValue('total_bytes'))

        self._record['State'] = state
        self._record['StateName'] = _STATE_NAMES.get(state, str(state))
        self._record['DangerType'] = dangerType
        self._record['DangerTypeName'] = _DANGER_TYPE_NAMES.get(dangerType, str(dangerType))
        self._record['InterruptReason'] = interruptReason
        self._record['InterruptReasonName'] = _INTERRUPT_REASON_NAMES.get(
            interruptReason,
            str(interruptReason)
        )

        self._record['DownloadID'] = self._AsInt(getValue('id'))
        self._record['GUID'] = self._AsText(getValue('guid'))
        self._record['Hash'] = self._AsBlob(getValue('hash'))
        self._record['Opened'] = self._AsInt(getValue('opened'))
        self._record['Transient'] = self._AsInt(getValue('transient'))

        self._record['TimestampLastAccess_UTC'] = lastAccessTime
        self._record['DateTimeLastAccess_UTC'] = self._FormatChromiumTime(lastAccessTime)

        self._record['Referrer'] = self._AsText(getValue('referrer'))
        self._record['SiteURL'] = self._AsText(getValue('site_url'))
        self._record['EmbedderDownloadData'] = self._AsText(getValue('embedder_download_data'))
        self._record['TabURL'] = self._AsText(getValue('tab_url'))
        self._record['TabReferrerURL'] = self._AsText(getValue('tab_referrer_url'))
        self._record['HttpMethod'] = self._AsText(getValue('http_method'))
        self._record['ByExtID'] = self._AsText(getValue('by_ext_id'))
        self._record['ByExtName'] = self._AsText(getValue('by_ext_name'))
        self._record['ByWebAppID'] = self._AsText(getValue('by_web_app_id'))
        self._record['ETag'] = self._AsText(getValue('etag'))
        self._record['LastModified'] = self._AsText(getValue('last_modified'))
        self._record['MIMEType'] = self._AsText(getValue('mime_type'))
        self._record['OriginalMIMEType'] = self._AsText(getValue('original_mime_type'))

        self._record['SlicesCount'] = sliceInfo[0]
        self._record['SlicesReceivedBytes'] = sliceInfo[1]
        self._record['DataSource'] = os.path.abspath(sourcePath)

        return tuple(self._record[fieldName] for fieldName in self._recordFields.keys())

    @staticmethod
    def _AsInt(value: Any, defaultValue: int = 0) -> int:
        """
        Привести значение из SQLite к целому числу.

        Args:
            value (Any): исходное значение
            defaultValue (int, optional): значение при ошибке. Defaults to 0.

        Returns:
            int: целое число
        """
        if value is None or value == '':
            return defaultValue

        try:
            return int(value)
        except (TypeError, ValueError):
            return defaultValue

    @staticmethod
    def _AsText(value: Any) -> str:
        """
        Привести значение из SQLite к строке.

        Args:
            value (Any): исходное значение

        Returns:
            str: строка
        """
        if value is None:
            return ''
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).decode('utf-8', errors='replace')
        return str(value)

    @staticmethod
    def _AsBlob(value: Any) -> bytes:
        """
        Привести значение из SQLite к BLOB.

        Args:
            value (Any): исходное значение

        Returns:
            bytes: двоичные данные
        """
        if value is None:
            return b''
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        if isinstance(value, str):
            return value.encode('utf-8', errors='replace')
        return str(value).encode('utf-8', errors='replace')

    @staticmethod
    def _FormatChromiumTime(microseconds: Any) -> str:
        """
        Перевести Chromium-время в строку UTC.

        Args:
            microseconds (Any): значение времени в микросекундах с 1601-01-01

        Returns:
            str: строка времени или пустая строка, если значение некорректно
        """
        if microseconds is None or microseconds == '':
            return ''

        try:
            timeValue = int(microseconds)
        except (TypeError, ValueError):
            return ''

        if timeValue <= 0:
            return ''

        try:
            return format_datetime(chromium_to_datetime(timeValue))
        except (OSError, OverflowError, ValueError):
            return ''

    async def _Redraw(self, message: str, percent: int) -> None:
        """
        Обновить прогресс выполнения, если интерфейс передан каркасом.

        Args:
            message (str): сообщение
            percent (int): процент выполнения
        """
        if self._redrawUI is None:
            return

        try:
            await self._redrawUI(message, percent)
        except Exception:
            pass

    def _Info(self, message: str) -> None:
        """
        Записать информационное сообщение в журнал.

        Args:
            message (str): сообщение
        """
        if self._log is None:
            return

        try:
            self._log.Info('VivaldiDownloads', message)
        except Exception:
            pass

    def _Warn(self, message: str) -> None:
        """
        Записать предупреждение в журнал.

        Args:
            message (str): сообщение
        """
        if self._log is None:
            return

        try:
            self._log.Warn('VivaldiDownloads', message)
        except Exception:
            pass


# ----------------------------------------------------------------------
class Parser:
    """
    Класс представляет собой интерфейс для подготовки данных и инициализации
    классов логики обработки данных, а также записи их в базу данных SQLite.
    """
    def __init__(self, parameters: dict):
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'URL': 'TEXT',
            'OriginalURL': 'TEXT',
            'URLChain': 'TEXT',
            'TargetPath': 'TEXT',
            'CurrentPath': 'TEXT',
            'TimestampStart_UTC': 'INTEGER',
            'DateTimeStart_UTC': 'TEXT',
            'TimestampEnd_UTC': 'INTEGER',
            'DateTimeEnd_UTC': 'TEXT',
            'ReceivedBytes': 'INTEGER',
            'TotalBytes': 'INTEGER',
            'State': 'INTEGER',
            'StateName': 'TEXT',
            'DangerType': 'INTEGER',
            'DangerTypeName': 'TEXT',
            'InterruptReason': 'INTEGER',
            'InterruptReasonName': 'TEXT',
            'DownloadID': 'INTEGER',
            'GUID': 'TEXT',
            'Hash': 'BLOB',
            'Opened': 'INTEGER',
            'Transient': 'INTEGER',
            'TimestampLastAccess_UTC': 'INTEGER',
            'DateTimeLastAccess_UTC': 'TEXT',
            'Referrer': 'TEXT',
            'SiteURL': 'TEXT',
            'EmbedderDownloadData': 'TEXT',
            'TabURL': 'TEXT',
            'TabReferrerURL': 'TEXT',
            'HttpMethod': 'TEXT',
            'ByExtID': 'TEXT',
            'ByExtName': 'TEXT',
            'ByWebAppID': 'TEXT',
            'ETag': 'TEXT',
            'LastModified': 'TEXT',
            'MIMEType': 'TEXT',
            'OriginalMIMEType': 'TEXT',
            'SlicesCount': 'INTEGER',
            'SlicesReceivedBytes': 'INTEGER',
            'DataSource': 'TEXT',
        }

        self.__downloadsParser: Any = _VivaldiDownloadsParser(
            parameters,
            self.__recordFields
        )

    async def Start(self) -> Dict:
        """
        Начать обработку баз History и сформировать выходную БД.

        Returns:
            Dict: словарь с именем модуля и именем созданной БД
        """
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')
        dbConnection: Any = self.__parameters.get('DBCONNECTION')
        log: Any = self.__parameters.get('LOG')

        if outputWriter is None or dbConnection is None or not dbConnection.IsConnected():
            if log is not None:
                log.Error('VivaldiDownloads', 'Нет соединения с выходной БД — модуль пропущен')
            return {}

        sourceFiles = self.__downloadsParser.FindHistoryDatabases()

        if not sourceFiles:
            if log is not None:
                log.Info('VivaldiDownloads', 'Файлы History не найдены — модуль пропущен')
            return {}

        fields: dict = {
            'URL': ('URL загрузки', 350, 'string', 'Фактический адрес, с которого получен файл'),
            'OriginalURL': ('Исходный URL', 350, 'string', 'Первый адрес в цепочке редиректов'),
            'URLChain': ('Цепочка URL', 500, 'string', 'Все адреса цепочки редиректов через перевод строки'),
            'TargetPath': ('Целевой путь', 350, 'string', 'Путь, по которому должен был быть сохранён файл'),
            'CurrentPath': ('Текущий путь', 350, 'string', 'Текущее расположение файла на диске'),
            'TimestampStart_UTC': ('Врем. метка начала UTC', 100, 'integer', 'Chromium-время начала загрузки'),
            'DateTimeStart_UTC': ('Дата и время начала UTC', 200, 'datetime', 'Дата и время начала загрузки'),
            'TimestampEnd_UTC': ('Врем. метка окончания UTC', 100, 'integer', 'Chromium-время окончания загрузки'),
            'DateTimeEnd_UTC': ('Дата и время окончания UTC', 200, 'datetime', 'Дата и время окончания загрузки'),
            'ReceivedBytes': ('Получено байт', 120, 'integer', 'Количество полученных байт'),
            'TotalBytes': ('Всего байт', 120, 'integer', 'Полный размер загрузки в байтах'),
            'State': ('Состояние', 80, 'integer', 'Числовое состояние загрузки'),
            'StateName': ('Состояние (текст)', 180, 'string', 'Расшифровка состояния загрузки'),
            'DangerType': ('Тип опасности', 100, 'integer', 'Числовой тип опасности загрузки'),
            'DangerTypeName': ('Тип опасности (текст)', 250, 'string', 'Расшифровка типа опасности'),
            'InterruptReason': ('Причина прерывания', 120, 'integer', 'Числовая причина прерывания'),
            'InterruptReasonName': ('Причина прерывания (текст)', 250, 'string', 'Расшифровка причины прерывания'),
            'DownloadID': ('ID загрузки', 80, 'integer', 'Идентификатор загрузки в базе History'),
            'GUID': ('GUID', 200, 'string', 'Уникальный идентификатор загрузки'),
            'Hash': ('Хеш', 200, 'blob', 'SHA-256 хеш содержимого файла'),
            'Opened': ('Открывалась', 80, 'integer', 'Флаг открытия загрузки'),
            'Transient': ('Временная', 80, 'integer', 'Флаг временной загрузки'),
            'TimestampLastAccess_UTC': ('Врем. метка доступа UTC', 100, 'integer', 'Chromium-время последнего доступа'),
            'DateTimeLastAccess_UTC': ('Дата и время доступа UTC', 200, 'datetime', 'Дата и время последнего доступа'),
            'Referrer': ('Referrer', 350, 'string', 'HTTP Referer исходного запроса'),
            'SiteURL': ('URL сайта', 350, 'string', 'URL сайта, инициировавшего загрузку'),
            'EmbedderDownloadData': ('Данные embedder', 350, 'string', 'Сериализованные данные инициатора загрузки'),
            'TabURL': ('URL вкладки', 350, 'string', 'URL вкладки, из которой начата загрузка'),
            'TabReferrerURL': ('Referrer вкладки', 350, 'string', 'Referer вкладки, из которой начата загрузка'),
            'HttpMethod': ('HTTP-метод', 100, 'string', 'HTTP-метод запроса загрузки'),
            'ByExtID': ('ID расширения', 200, 'string', 'Идентификатор расширения, начавшего загрузку'),
            'ByExtName': ('Имя расширения', 200, 'string', 'Имя расширения, начавшего загрузку'),
            'ByWebAppID': ('ID web-приложения', 200, 'string', 'Идентификатор web-приложения, начавшего загрузку'),
            'ETag': ('ETag', 200, 'string', 'Значение HTTP-заголовка ETag'),
            'LastModified': ('Last-Modified', 200, 'string', 'Значение HTTP-заголовка Last-Modified'),
            'MIMEType': ('MIME-тип', 200, 'string', 'Фактический MIME-тип загруженного файла'),
            'OriginalMIMEType': ('Исходный MIME-тип', 200, 'string', 'MIME-тип из HTTP-заголовка'),
            'SlicesCount': ('Количество частей', 100, 'integer', 'Количество параллельно загруженных частей'),
            'SlicesReceivedBytes': ('Байт в частях', 120, 'integer', 'Суммарно получено байт по частям загрузки'),
            'DataSource': ('Источник данных', 350, 'string', 'Путь до исходной базы History'),
        }

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """:
        Извлечение сведений о загрузках из SQLite-баз History профилей Vivaldi.

        Модуль рекурсивно ищет файлы History внутри STORAGE, включая профили
        Default, Profile 1, Profile 2 и другие.

        Для каждой загрузки сохраняются:
        - URL и цепочка редиректов;
        - целевой и текущий путь файла;
        - время начала, окончания и последнего доступа;
        - полученные и полные байты;
        - состояние загрузки, тип опасности и причина прерывания;
        - GUID, хеш, MIME-тип, Referer, URL вкладки и служебные поля Chromium.
        """

        infoTableData: dict = {
            'Name': self.__parameters.get('MODULENAME'),
            'Help': HELP_TEXT,
            'Timestamp': self.__parameters.get('CASENAME'),
            'Vendor': 'LabFramework',
        }

        recordsCount: int = 0

        try:
            outputWriter.SetFields(fields, self.__recordFields)
            outputWriter.CreateDatabaseTables()

            recordsCount = await self.__downloadsParser.Start(sourceFiles)

            outputWriter.RemoveTempTables()

            if recordsCount > 0:
                await outputWriter.CreateDatabaseIndexes(
                    self.__parameters.get('MODULENAME')
                )
                outputWriter.SetInfo(infoTableData)
                outputWriter.WriteMeta()

        except Exception as error:
            if log is not None:
                log.Error('VivaldiDownloads', f'Ошибка обработки загрузок: {error}')

        try:
            await outputWriter.CloseOutput()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiDownloads', f'Ошибка закрытия выходной БД: {error}')

        if recordsCount == 0:
            return {}

        return {
            self.__parameters.get('MODULENAME'): outputWriter.GetDBName()
        }
