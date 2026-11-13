# -*- coding: utf-8 -*-
"""модуль извлечения сведений о загрузках из профилей vivaldi."""
import os
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from Common.Chrom import chromium_to_datetime, format_datetime

HISTORY_FILE_NAME = 'History'
HISTORY_SIDECAR_SUFFIXES = ('-wal', '-shm', '-journal')
SQLITE_HEADER = b'SQLite format 3\x00'

# каталоги, которые не относятся к history и могут быть большими
_SKIP_DIR_NAMES = {
    '.git', '__pycache__', 'cache', 'code cache', 'gpucache',
    'shadercache', 'grshadercache', 'service worker', 'logs', 'cases', 'temp',
}

# человекочитаемые значения полей chromium из download_constants.h
_STATE_NAMES = {
    0: 'In progress',
    1: 'Complete',
    2: 'Cancelled',
    3: 'Interrupted (legacy bug 140687)',
    4: 'Interrupted',
}
_DANGER_TYPE_NAMES = {
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
_INTERRUPT_REASON_NAMES = {
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


class _VivaldiDownloadsParser:
    """поиск баз history, чтение загрузок и запись в выходную бд."""

    def __init__(self, parserParameters: dict, recordFields: dict):
        self._storage: Optional[str] = parserParameters.get('STORAGE')
        self._tempFolder: Optional[str] = parserParameters.get('TEMP')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._redrawUI: Any = parserParameters.get('UIREDRAW')
        self._recordFields: dict = recordFields

    def FindHistoryDatabases(self) -> List[str]:
        """найти все файлы history в storage."""
        result: List[str] = []
        storage = self._storage
        if not storage:
            return result

        storage = os.path.abspath(storage)

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
        """проверить, что файл является sqlite-базой по заголовку."""
        try:
            with open(filePath, 'rb') as fileHandle:
                return fileHandle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
        except OSError:
            return False

    async def Start(self, sourceFiles: List[str]) -> int:
        """обработать найденные базы history и записать загрузки."""
        if self._wr is None or not sourceFiles:
            return 0

        totalRecords = 0
        totalFiles = len(sourceFiles)

        for index, sourcePath in enumerate(sourceFiles, start=1):
            await self._Redraw(
                f'VivaldiDownloads: чтение {sourcePath}',
                int((index - 1) / totalFiles * 100)
            )

            records = self._ReadDownloadsDatabase(sourcePath)
            for record in records:
                await self._wr.WriteRecord(record)
                totalRecords += 1

            self._Info(
                f'Обработана база History: {sourcePath}, загрузок: {len(records)}'
            )
            await self._Redraw(
                f'VivaldiDownloads: обработано {index} из {totalFiles} баз',
                int(index / totalFiles * 100)
            )

        return totalRecords

    def _ReadDownloadsDatabase(self, sourcePath: str) -> List[Tuple]:
        """прочитать базу history и вернуть записи о загрузках."""
        tempFolder = None
        connection = None

        try:
            tempFolder = self._CreateTempCopy(sourcePath)
            if tempFolder is None:
                return []

            connection = sqlite3.connect(os.path.join(tempFolder, HISTORY_FILE_NAME))
            connection.row_factory = sqlite3.Row

            if not self._TableExists(connection, 'downloads'):
                self._Warn(f'В базе History нет таблицы downloads: {sourcePath}')
                return []

            urlChains = self._ReadUrlChains(connection)
            slices = self._ReadSlices(connection)

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
        """проверить наличие таблицы в базе."""
        cursor = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
            (tableName,)
        )
        return cursor.fetchone() is not None

    def _ReadUrlChains(self, connection: sqlite3.Connection) -> Dict[int, List[str]]:
        """прочитать цепочки url: 0 — исходный адрес, последний — после редиректов."""
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
        """прочитать количество частей загрузки и сумму их байт."""
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
        """скопировать базу history и её служебные файлы во временный каталог."""
        tempFolder = None

        try:
            tempRoot = self._tempFolder or tempfile.gettempdir()
            os.makedirs(tempRoot, exist_ok=True)

            tempFolder = tempfile.mkdtemp(prefix='VivaldiDownloads_', dir=tempRoot)
            targetPath = os.path.join(tempFolder, HISTORY_FILE_NAME)
            shutil.copy(sourcePath, targetPath)

            # wal/shm/journal нужны, чтобы sqlite увидел незавершённые транзакции
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
        """собрать кортеж полей из строки downloads."""
        values = {columnName: row[columnName] for columnName in columnNames}

        def getValue(columnName: str, defaultValue: Any = None) -> Any:
            return values.get(columnName, defaultValue)

        startTime = self._AsInt(getValue('start_time'))
        endTime = self._AsInt(getValue('end_time'))
        lastAccessTime = self._AsInt(getValue('last_access_time'))

        state = self._AsInt(getValue('state'))
        dangerType = self._AsInt(getValue('danger_type'))
        interruptReason = self._AsInt(getValue('interrupt_reason'))

        record = {
            'URL': urlChain[-1] if urlChain else '',
            'OriginalURL': urlChain[0] if urlChain else '',
            'URLChain': '\n'.join(urlChain),
            'TargetPath': self._AsText(getValue('target_path')),
            'CurrentPath': self._AsText(getValue('current_path')),
            'TimestampStart_UTC': startTime,
            'DateTimeStart_UTC': self._FormatChromiumTime(startTime),
            'TimestampEnd_UTC': endTime,
            'DateTimeEnd_UTC': self._FormatChromiumTime(endTime),
            'ReceivedBytes': self._AsInt(getValue('received_bytes')),
            'TotalBytes': self._AsInt(getValue('total_bytes')),
            'State': state,
            'StateName': _STATE_NAMES.get(state, str(state)),
            'DangerType': dangerType,
            'DangerTypeName': _DANGER_TYPE_NAMES.get(dangerType, str(dangerType)),
            'InterruptReason': interruptReason,
            'InterruptReasonName': _INTERRUPT_REASON_NAMES.get(
                interruptReason, str(interruptReason)
            ),
            'DownloadID': self._AsInt(getValue('id')),
            'GUID': self._AsText(getValue('guid')),
            'Hash': self._AsBlob(getValue('hash')),
            'Opened': self._AsInt(getValue('opened')),
            'Transient': self._AsInt(getValue('transient')),
            'TimestampLastAccess_UTC': lastAccessTime,
            'DateTimeLastAccess_UTC': self._FormatChromiumTime(lastAccessTime),
            'Referrer': self._AsText(getValue('referrer')),
            'SiteURL': self._AsText(getValue('site_url')),
            'EmbedderDownloadData': self._AsText(getValue('embedder_download_data')),
            'TabURL': self._AsText(getValue('tab_url')),
            'TabReferrerURL': self._AsText(getValue('tab_referrer_url')),
            'HttpMethod': self._AsText(getValue('http_method')),
            'ByExtID': self._AsText(getValue('by_ext_id')),
            'ByExtName': self._AsText(getValue('by_ext_name')),
            'ByWebAppID': self._AsText(getValue('by_web_app_id')),
            'ETag': self._AsText(getValue('etag')),
            'LastModified': self._AsText(getValue('last_modified')),
            'MIMEType': self._AsText(getValue('mime_type')),
            'OriginalMIMEType': self._AsText(getValue('original_mime_type')),
            'SlicesCount': sliceInfo[0],
            'SlicesReceivedBytes': sliceInfo[1],
            'DataSource': os.path.abspath(sourcePath),
        }

        return tuple(record[fieldName] for fieldName in self._recordFields)

    @staticmethod
    def _AsInt(value: Any, defaultValue: int = 0) -> int:
        """привести значение к целому числу."""
        if value is None or value == '':
            return defaultValue
        try:
            return int(value)
        except (TypeError, ValueError):
            return defaultValue

    @staticmethod
    def _AsText(value: Any) -> str:
        """привести значение к строке."""
        if value is None:
            return ''
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).decode('utf-8', errors='replace')
        return str(value)

    @staticmethod
    def _AsBlob(value: Any) -> bytes:
        """привести значение к blob."""
        if value is None:
            return b''
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        if isinstance(value, str):
            return value.encode('utf-8', errors='replace')
        return str(value).encode('utf-8', errors='replace')

    @staticmethod
    def _FormatChromiumTime(microseconds: Any) -> str:
        """перевести chromium-время в строку utc."""
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
        """обновить прогресс, если интерфейс передан каркасом."""
        if self._redrawUI is None:
            return
        try:
            await self._redrawUI(message, percent)
        except Exception:
            pass

    def _Info(self, message: str) -> None:
        """записать информационное сообщение в журнал."""
        if self._log is None:
            return
        try:
            self._log.Info('VivaldiDownloads', message)
        except Exception:
            pass

    def _Warn(self, message: str) -> None:
        """записать предупреждение в журнал."""
        if self._log is None:
            return
        try:
            self._log.Warn('VivaldiDownloads', message)
        except Exception:
            pass


class Parser:
    """интерфейс модуля: поля, запуск обработки и запись в выходную бд."""

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
        """запустить обработку загрузок и вернуть имя созданной бд."""
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

        HELP_TEXT: str = (
            self.__parameters.get('MODULENAME') +
            ': извлечение загрузок из sqlite-баз history профилей vivaldi.'
        )

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

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
