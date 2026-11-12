# -*- coding: utf-8 -*-
"""
Модуль извлечения куки из профилей Vivaldi.

Модуль читает SQLite-базу Cookies из всех найденных профилей внутри STORAGE,
извлекает метаданные и сохраняет зашифрованное значение как BLOB.
Расшифровка значений в основном сценарии не выполняется: для неё нужны
файл Local State и DPAPI (Windows) либо Keychain (macOS).
"""
import os
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from Common.Chrom import chromium_to_datetime, format_datetime

# ----------------------------------------------------------------------
COOKIE_FILE_NAME: str = 'Cookies'
COOKIE_SIDECAR_SUFFIXES: Tuple[str, ...] = ('-wal', '-shm', '-journal')

# Каталоги, которые не имеют отношения к Cookies и могут быть очень большими.
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

# Справочники для человекочитаемых значений некоторых полей Chromium.
_SAME_SITE_NAMES: Dict[int, str] = {
    -1: 'Unspecified',
    0: 'NoRestriction',
    1: 'Lax',
    2: 'Strict',
}
_PRIORITY_NAMES: Dict[int, str] = {
    0: 'Low',
    1: 'Medium',
    2: 'High',
}
_SOURCE_SCHEME_NAMES: Dict[int, str] = {
    0: 'Unset',
    1: 'NonSecure',
    2: 'Secure',
}
_SOURCE_TYPE_NAMES: Dict[int, str] = {
    0: 'Unknown',
    1: 'HTTP',
    2: 'Script',
    3: 'Other',
}


def TryDecryptCookieValue(encryptedValue: bytes, localStatePath: Optional[str] = None) -> Optional[str]:
    """
    Заготовка для опциональной расшифровки значения куки.

    Для реализации потребуется:
        1) прочитать ключ os_crypt.encrypted_key из файла Local State;
        2) на Windows расшифровать ключ через DPAPI;
        3) расшифровать значение куки алгоритмом AES-GCM.

    Сейчас расшифровка не реализована: в исследуемом дампе нет Local State,
    поэтому проверить её невозможно. Функция возвращает None.

    Args:
        encryptedValue (bytes): зашифрованное значение из поля encrypted_value
        localStatePath (Optional[str]): путь до файла Local State

    Returns:
        Optional[str]: расшифрованное значение или None, если расшифровка недоступна
    """
    return None


# ----------------------------------------------------------------------
class _VivaldiCookiesParser:
    """
    Класс реализует поиск баз Cookies, чтение записей и запись их в выходную БД.
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

    def FindCookieDatabases(self) -> List[str]:
        """
        Найти все файлы Cookies внутри STORAGE.

        Поиск выполняется рекурсивно, потому что профиль может содержать
        как Default/Cookies, так и Default/Network/Cookies, а также
        несколько профилей (Default, Profile 1, Profile 2 и т.д.).

        Returns:
            List[str]: список абсолютных путей до найденных баз Cookies
        """
        result: List[str] = []
        storage: Optional[str] = self._storage

        if not storage:
            return result

        storage = os.path.abspath(storage)

        # На случай, если в STORAGE передан сам файл Cookies, а не каталог.
        if os.path.isfile(storage):
            if os.path.basename(storage).lower() == COOKIE_FILE_NAME.lower():
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
        """
        Обработать все найденные базы Cookies и записать записи в выходную БД.

        Args:
            sourceFiles (List[str]): пути до найденных баз Cookies

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
                f'VivaldiCookies: чтение {sourcePath}',
                startPercent
            )

            records = self._ReadCookieDatabase(sourcePath)

            for record in records:
                await self._wr.WriteRecord(record)
                totalRecords += 1

            self._Info(f'Обработана база Cookies: {sourcePath}, записей: {len(records)}')

            finishPercent = int(index / totalFiles * 100)
            await self._Redraw(
                f'VivaldiCookies: обработано {index} из {totalFiles} баз',
                finishPercent
            )

        return totalRecords

    def _ReadCookieDatabase(self, sourcePath: str) -> List[Tuple]:
        """
        Прочитать одну базу Cookies и вернуть подготовленные кортежи записей.

        База и её WAL/SHM/Journal-файлы копируются во временный каталог,
        чтобы не изменять улики и корректно прочитать незавершённые транзакции.

        Args:
            sourcePath (str): путь до исходной базы Cookies

        Returns:
            List[Tuple]: список кортежей для записи в таблицу Data
        """
        tempFolder: Optional[str] = None
        connection: Optional[sqlite3.Connection] = None

        try:
            tempFolder = self._CreateTempCopy(sourcePath)
            if tempFolder is None:
                return []

            copyPath = os.path.join(tempFolder, COOKIE_FILE_NAME)
            connection = sqlite3.connect(copyPath)
            connection.row_factory = sqlite3.Row

            cursor = connection.cursor()
            cursor.execute('SELECT * FROM cookies;')

            columnNames = [description[0].lower() for description in cursor.description]
            records: List[Tuple] = []

            for row in cursor:
                records.append(self._BuildRecord(row, columnNames, sourcePath))

            return records

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
        """
        Скопировать базу Cookies и её служебные файлы во временный каталог.

        Args:
            sourcePath (str): путь до исходной базы Cookies

        Returns:
            Optional[str]: путь до временного каталога или None при ошибке
        """
        tempFolder: Optional[str] = None

        try:
            tempRoot = self._tempFolder or tempfile.gettempdir()
            os.makedirs(tempRoot, exist_ok=True)

            tempFolder = tempfile.mkdtemp(prefix='VivaldiCookies_', dir=tempRoot)
            targetPath = os.path.join(tempFolder, COOKIE_FILE_NAME)
            shutil.copy(sourcePath, targetPath)

            # Копируем WAL/SHM/Journal, иначе SQLite может не увидеть
            # последние изменения из незавершённой транзакции.
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
        """
        Преобразовать строку таблицы cookies в кортеж полей выходной БД.

        Args:
            row (sqlite3.Row): строка исходной базы
            columnNames (List[str]): список колонок, полученных из SELECT
            sourcePath (str): путь до исходной базы Cookies

        Returns:
            Tuple: значения полей в порядке self._recordFields
        """
        values: Dict[str, Any] = {
            columnName: row[columnName] for columnName in columnNames
        }

        def getValue(columnName: str, defaultValue: Any = None) -> Any:
            return values.get(columnName, defaultValue)

        createdAt = self._AsInt(getValue('creation_utc'))
        lastAccessAt = self._AsInt(getValue('last_access_utc'))
        expiresAt = self._AsInt(getValue('expires_utc'))
        lastUpdateAt = self._AsInt(getValue('last_update_utc'))

        hasExpires = self._AsInt(
            getValue('has_expires'),
            1 if expiresAt > 0 else 0
        )
        isPersistent = self._AsInt(
            getValue('is_persistent'),
            1 if expiresAt > 0 else 0
        )

        priority = self._AsInt(getValue('priority'), 1)
        sameSite = self._AsInt(getValue('samesite'), -1)
        sourceScheme = self._AsInt(getValue('source_scheme'), 0)
        sourceType = self._AsInt(getValue('source_type'), 0)

        self._record['HostKey'] = self._AsText(getValue('host_key'))
        self._record['TopFrameSiteKey'] = self._AsText(getValue('top_frame_site_key'))
        self._record['Name'] = self._AsText(getValue('name'))
        self._record['Value'] = self._AsText(getValue('value'))
        self._record['EncryptedValue'] = self._AsBlob(getValue('encrypted_value'))
        self._record['Path'] = self._AsText(getValue('path'))

        self._record['TimestampCreation_UTC'] = createdAt
        self._record['TimestampLastAccess_UTC'] = lastAccessAt
        self._record['TimestampExpires_UTC'] = expiresAt
        self._record['TimestampLastUpdate_UTC'] = lastUpdateAt

        self._record['DateTimeCreation_UTC'] = self._FormatChromiumTime(createdAt)
        self._record['DateTimeLastAccess_UTC'] = self._FormatChromiumTime(lastAccessAt)
        self._record['DateTimeExpires_UTC'] = (
            self._FormatChromiumTime(expiresAt) if hasExpires else ''
        )
        self._record['DateTimeLastUpdate_UTC'] = self._FormatChromiumTime(lastUpdateAt)

        self._record['IsSecure'] = self._AsInt(getValue('is_secure'))
        self._record['IsHttpOnly'] = self._AsInt(getValue('is_httponly'))
        self._record['HasExpires'] = hasExpires
        self._record['IsPersistent'] = isPersistent

        self._record['Priority'] = priority
        self._record['PriorityName'] = _PRIORITY_NAMES.get(priority, str(priority))
        self._record['SameSite'] = sameSite
        self._record['SameSiteName'] = _SAME_SITE_NAMES.get(sameSite, str(sameSite))
        self._record['SourceScheme'] = sourceScheme
        self._record['SourceSchemeName'] = _SOURCE_SCHEME_NAMES.get(sourceScheme, str(sourceScheme))
        self._record['SourcePort'] = self._AsInt(getValue('source_port'))
        self._record['SourceType'] = sourceType
        self._record['SourceTypeName'] = _SOURCE_TYPE_NAMES.get(sourceType, str(sourceType))
        self._record['HasCrossSiteAncestor'] = self._AsInt(getValue('has_cross_site_ancestor'))
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
            self._log.Info('VivaldiCookies', message)
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
            self._log.Warn('VivaldiCookies', message)
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
        """
        Начать обработку баз Cookies и сформировать выходную БД.

        Returns:
            Dict: словарь с именем модуля и именем созданной БД
        """
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

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """:
        Извлечение куки из SQLite-баз Cookies профилей Vivaldi.

        Модуль рекурсивно ищет файлы Cookies внутри STORAGE, включая:
        - Cookies;
        - Default/Cookies;
        - Network/Cookies;
        - Default/Network/Cookies;
        - профили Profile 1, Profile 2 и другие.

        Для каждой базы сохраняются метаданные:
        хост, имя, путь, время создания, доступа и истечения, флаги,
        SameSite, приоритет, источник и служебные поля Chromium.

        Значения куки не расшифровываются: зашифрованное значение
        сохраняется в поле EncryptedValue как BLOB. Для расшифровки
        требуется файл Local State и DPAPI либо Keychain.
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

        return {
            self.__parameters.get('MODULENAME'): outputWriter.GetDBName()
        }
