# -*- coding: utf-8 -*-
"""модуль извлечения куки из профилей vivaldi."""
import os
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from Common.Chrom import chromium_to_datetime, format_datetime

COOKIE_FILE_NAME = 'Cookies'
COOKIE_SIDECAR_SUFFIXES = ('-wal', '-shm', '-journal')

# каталоги, которые не относятся к кукам и могут быть большими
_SKIP_DIR_NAMES = {
    '.git', '__pycache__', 'cache', 'code cache', 'gpucache',
    'shadercache', 'grshadercache', 'service worker', 'logs', 'cases', 'temp',
}

# человекочитаемые значения полей chromium
_SAME_SITE_NAMES = {-1: 'Unspecified', 0: 'NoRestriction', 1: 'Lax', 2: 'Strict'}
_PRIORITY_NAMES = {0: 'Low', 1: 'Medium', 2: 'High'}
_SOURCE_SCHEME_NAMES = {0: 'Unset', 1: 'NonSecure', 2: 'Secure'}
_SOURCE_TYPE_NAMES = {0: 'Unknown', 1: 'HTTP', 2: 'Script', 3: 'Other'}


def TryDecryptCookieValue(encryptedValue: bytes, localStatePath: Optional[str] = None) -> Optional[str]:
    """заготовка расшифровки значения куки, пока возвращает none."""
    return None


class _VivaldiCookiesParser:
    """поиск баз cookies, чтение записей и запись в выходную бд."""

    def __init__(self, parserParameters: dict, recordFields: dict):
        self._storage: Optional[str] = parserParameters.get('STORAGE')
        self._tempFolder: Optional[str] = parserParameters.get('TEMP')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._redrawUI: Any = parserParameters.get('UIREDRAW')
        self._recordFields: dict = recordFields

    def FindCookieDatabases(self) -> List[str]:
        """найти все файлы cookies в storage."""
        result: List[str] = []
        storage = self._storage
        if not storage:
            return result

        storage = os.path.abspath(storage)

        if os.path.isfile(storage):
            if os.path.basename(storage).lower() == COOKIE_FILE_NAME.lower():
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
                if fileName.lower() != COOKIE_FILE_NAME.lower():
                    continue
                fullPath = os.path.abspath(os.path.join(root, fileName))
                normalizedPath = os.path.normcase(fullPath)
                if normalizedPath in seenPaths:
                    continue
                seenPaths.add(normalizedPath)
                result.append(fullPath)

        result.sort(key=lambda path: path.lower())
        return result

    async def Start(self, sourceFiles: List[str]) -> int:
        """обработать найденные базы cookies и записать записи."""
        if self._wr is None or not sourceFiles:
            return 0

        totalRecords = 0
        totalFiles = len(sourceFiles)

        for index, sourcePath in enumerate(sourceFiles, start=1):
            await self._Redraw(
                f'VivaldiCookies: чтение {sourcePath}',
                int((index - 1) / totalFiles * 100)
            )

            records = self._ReadCookieDatabase(sourcePath)
            for record in records:
                await self._wr.WriteRecord(record)
                totalRecords += 1

            self._Info(f'Обработана база Cookies: {sourcePath}, записей: {len(records)}')
            await self._Redraw(
                f'VivaldiCookies: обработано {index} из {totalFiles} баз',
                int(index / totalFiles * 100)
            )

        return totalRecords

    def _ReadCookieDatabase(self, sourcePath: str) -> List[Tuple]:
        """прочитать базу cookies и вернуть записи."""
        tempFolder = None
        connection = None

        try:
            tempFolder = self._CreateTempCopy(sourcePath)
            if tempFolder is None:
                return []

            connection = sqlite3.connect(os.path.join(tempFolder, COOKIE_FILE_NAME))
            connection.row_factory = sqlite3.Row

            cursor = connection.cursor()
            cursor.execute('SELECT * FROM cookies;')
            columnNames = [description[0].lower() for description in cursor.description]

            return [self._BuildRecord(row, columnNames, sourcePath) for row in cursor]

        except (sqlite3.Error, OSError, ValueError) as error:
            self._Warn(f'Не удалось прочитать базу Cookies {sourcePath}: {error}')
            return []

        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            if tempFolder is not None:
                shutil.rmtree(tempFolder, ignore_errors=True)

    def _CreateTempCopy(self, sourcePath: str) -> Optional[str]:
        """скопировать базу cookies и её служебные файлы во временный каталог."""
        tempFolder = None

        try:
            tempRoot = self._tempFolder or tempfile.gettempdir()
            os.makedirs(tempRoot, exist_ok=True)

            tempFolder = tempfile.mkdtemp(prefix='VivaldiCookies_', dir=tempRoot)
            targetPath = os.path.join(tempFolder, COOKIE_FILE_NAME)
            shutil.copy(sourcePath, targetPath)

            # wal/shm/journal нужны, чтобы sqlite увидел незавершённые транзакции
            for suffix in COOKIE_SIDECAR_SUFFIXES:
                sidecarPath = f'{sourcePath}{suffix}'
                if os.path.isfile(sidecarPath):
                    shutil.copy(sidecarPath, f'{targetPath}{suffix}')

            return tempFolder

        except (OSError, shutil.Error) as error:
            self._Warn(f'Не удалось скопировать базу Cookies {sourcePath}: {error}')
            if tempFolder is not None:
                shutil.rmtree(tempFolder, ignore_errors=True)
            return None

    def _BuildRecord(self, row: sqlite3.Row, columnNames: List[str], sourcePath: str) -> Tuple:
        """собрать кортеж полей из строки cookies."""
        values = {columnName: row[columnName] for columnName in columnNames}

        def getValue(columnName: str, defaultValue: Any = None) -> Any:
            return values.get(columnName, defaultValue)

        createdAt = self._AsInt(getValue('creation_utc'))
        lastAccessAt = self._AsInt(getValue('last_access_utc'))
        expiresAt = self._AsInt(getValue('expires_utc'))
        lastUpdateAt = self._AsInt(getValue('last_update_utc'))

        hasExpires = self._AsInt(getValue('has_expires'), 1 if expiresAt > 0 else 0)
        isPersistent = self._AsInt(getValue('is_persistent'), 1 if expiresAt > 0 else 0)

        priority = self._AsInt(getValue('priority'), 1)
        sameSite = self._AsInt(getValue('samesite'), -1)
        sourceScheme = self._AsInt(getValue('source_scheme'))
        sourceType = self._AsInt(getValue('source_type'))

        record = {
            'HostKey': self._AsText(getValue('host_key')),
            'TopFrameSiteKey': self._AsText(getValue('top_frame_site_key')),
            'Name': self._AsText(getValue('name')),
            'Value': self._AsText(getValue('value')),
            'EncryptedValue': self._AsBlob(getValue('encrypted_value')),
            'Path': self._AsText(getValue('path')),
            'TimestampCreation_UTC': createdAt,
            'TimestampLastAccess_UTC': lastAccessAt,
            'TimestampExpires_UTC': expiresAt,
            'TimestampLastUpdate_UTC': lastUpdateAt,
            'DateTimeCreation_UTC': self._FormatChromiumTime(createdAt),
            'DateTimeLastAccess_UTC': self._FormatChromiumTime(lastAccessAt),
            'DateTimeExpires_UTC': self._FormatChromiumTime(expiresAt) if hasExpires else '',
            'DateTimeLastUpdate_UTC': self._FormatChromiumTime(lastUpdateAt),
            'IsSecure': self._AsInt(getValue('is_secure')),
            'IsHttpOnly': self._AsInt(getValue('is_httponly')),
            'HasExpires': hasExpires,
            'IsPersistent': isPersistent,
            'Priority': priority,
            'PriorityName': _PRIORITY_NAMES.get(priority, str(priority)),
            'SameSite': sameSite,
            'SameSiteName': _SAME_SITE_NAMES.get(sameSite, str(sameSite)),
            'SourceScheme': sourceScheme,
            'SourceSchemeName': _SOURCE_SCHEME_NAMES.get(sourceScheme, str(sourceScheme)),
            'SourcePort': self._AsInt(getValue('source_port')),
            'SourceType': sourceType,
            'SourceTypeName': _SOURCE_TYPE_NAMES.get(sourceType, str(sourceType)),
            'HasCrossSiteAncestor': self._AsInt(getValue('has_cross_site_ancestor')),
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
            self._log.Info('VivaldiCookies', message)
        except Exception:
            pass

    def _Warn(self, message: str) -> None:
        """записать предупреждение в журнал."""
        if self._log is None:
            return
        try:
            self._log.Warn('VivaldiCookies', message)
        except Exception:
            pass


class Parser:
    """интерфейс модуля: поля, запуск обработки и запись в выходную бд."""

    def __init__(self, parameters: dict):
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'HostKey': 'TEXT',
            'TopFrameSiteKey': 'TEXT',
            'Name': 'TEXT',
            'Value': 'TEXT',
            'EncryptedValue': 'BLOB',
            'Path': 'TEXT',
            'TimestampCreation_UTC': 'INTEGER',
            'TimestampLastAccess_UTC': 'INTEGER',
            'TimestampExpires_UTC': 'INTEGER',
            'TimestampLastUpdate_UTC': 'INTEGER',
            'DateTimeCreation_UTC': 'TEXT',
            'DateTimeLastAccess_UTC': 'TEXT',
            'DateTimeExpires_UTC': 'TEXT',
            'DateTimeLastUpdate_UTC': 'TEXT',
            'IsSecure': 'INTEGER',
            'IsHttpOnly': 'INTEGER',
            'HasExpires': 'INTEGER',
            'IsPersistent': 'INTEGER',
            'Priority': 'INTEGER',
            'PriorityName': 'TEXT',
            'SameSite': 'INTEGER',
            'SameSiteName': 'TEXT',
            'SourceScheme': 'INTEGER',
            'SourceSchemeName': 'TEXT',
            'SourcePort': 'INTEGER',
            'SourceType': 'INTEGER',
            'SourceTypeName': 'TEXT',
            'HasCrossSiteAncestor': 'INTEGER',
            'DataSource': 'TEXT',
        }

        self.__cookiesParser: Any = _VivaldiCookiesParser(
            parameters,
            self.__recordFields
        )

    async def Start(self) -> Dict:
        """запустить обработку cookies и вернуть имя созданной бд."""
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')
        dbConnection: Any = self.__parameters.get('DBCONNECTION')
        log: Any = self.__parameters.get('LOG')

        if outputWriter is None or dbConnection is None or not dbConnection.IsConnected():
            if log is not None:
                log.Error('VivaldiCookies', 'Нет соединения с выходной БД — модуль пропущен')
            return {}

        sourceFiles = self.__cookiesParser.FindCookieDatabases()
        if not sourceFiles:
            if log is not None:
                log.Info('VivaldiCookies', 'Файлы Cookies не найдены — модуль пропущен')
            return {}

        fields: dict = {
            'HostKey': ('Хост', 200, 'string', 'Домен или хост, к которому относится куки'),
            'TopFrameSiteKey': ('Ключ top-frame', 250, 'string', 'Ключ разделения куки по top-frame site'),
            'Name': ('Имя куки', 200, 'string', 'Имя куки'),
            'Value': ('Значение', 250, 'string', 'Открытое значение куки, если оно присутствует'),
            'EncryptedValue': ('Зашифрованное значение', 250, 'blob', 'Зашифрованное значение куки'),
            'Path': ('Путь', 150, 'string', 'Путь действия куки'),
            'TimestampCreation_UTC': ('Врем. метка создания UTC', 100, 'integer', 'Chromium-время создания'),
            'TimestampLastAccess_UTC': ('Врем. метка доступа UTC', 100, 'integer', 'Chromium-время последнего доступа'),
            'TimestampExpires_UTC': ('Врем. метка истечения UTC', 100, 'integer', 'Chromium-время истечения'),
            'TimestampLastUpdate_UTC': ('Врем. метка обновления UTC', 100, 'integer', 'Chromium-время последнего обновления'),
            'DateTimeCreation_UTC': ('Дата и время создания UTC', 200, 'datetime', 'Дата и время создания куки'),
            'DateTimeLastAccess_UTC': ('Дата и время доступа UTC', 200, 'datetime', 'Дата и время последнего доступа'),
            'DateTimeExpires_UTC': ('Дата и время истечения UTC', 200, 'datetime', 'Дата и время истечения куки'),
            'DateTimeLastUpdate_UTC': ('Дата и время обновления UTC', 200, 'datetime', 'Дата и время последнего обновления'),
            'IsSecure': ('Secure', 80, 'integer', 'Флаг secure'),
            'IsHttpOnly': ('HttpOnly', 80, 'integer', 'Флаг http-only'),
            'HasExpires': ('Есть срок действия', 100, 'integer', 'Флаг наличия срока действия'),
            'IsPersistent': ('Постоянная', 80, 'integer', 'Флаг постоянного хранения'),
            'Priority': ('Приоритет', 80, 'integer', 'Числовой приоритет куки'),
            'PriorityName': ('Приоритет (текст)', 120, 'string', 'Расшифровка приоритета'),
            'SameSite': ('SameSite', 80, 'integer', 'Числовое значение SameSite'),
            'SameSiteName': ('SameSite (текст)', 120, 'string', 'Расшифровка SameSite'),
            'SourceScheme': ('Схема источника', 100, 'integer', 'Числовая схема источника'),
            'SourceSchemeName': ('Схема источника (текст)', 150, 'string', 'Расшифровка схемы источника'),
            'SourcePort': ('Порт источника', 80, 'integer', 'Порт источника'),
            'SourceType': ('Тип источника', 100, 'integer', 'Числовой тип источника'),
            'SourceTypeName': ('Тип источника (текст)', 150, 'string', 'Расшифровка типа источника'),
            'HasCrossSiteAncestor': ('Cross-site предок', 100, 'integer', 'Флаг наличия cross-site предка'),
            'DataSource': ('Источник данных', 350, 'string', 'Путь до исходной базы Cookies'),
        }

        HELP_TEXT: str = (
            self.__parameters.get('MODULENAME') +
            ': извлечение куки из sqlite-баз cookies профилей vivaldi.'
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

            recordsCount = await self.__cookiesParser.Start(sourceFiles)

            outputWriter.RemoveTempTables()
            if recordsCount > 0:
                await outputWriter.CreateDatabaseIndexes(
                    self.__parameters.get('MODULENAME')
                )
                outputWriter.SetInfo(infoTableData)
                outputWriter.WriteMeta()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiCookies', f'Ошибка обработки куки: {error}')

        try:
            await outputWriter.CloseOutput()
        except Exception as error:
            if log is not None:
                log.Error('VivaldiCookies', f'Ошибка закрытия выходной БД: {error}')

        if recordsCount == 0:
            return {}

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
