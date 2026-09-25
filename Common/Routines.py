# -*- coding: utf-8 -*-
"""
Модуль рутин по работе с файлами

"""

import os,io,re,regipy,shutil
import sqlite3
from enum import IntEnum
from construct import core
from abc import ABCMeta, abstractmethod
from typing import Any,AnyStr,List,Tuple,Dict,Optional
from datetime import datetime,timedelta,tzinfo 
from calendar import timegm


UNIX_EPOCH_AS_FILETIME:int = 116444736000000000  # January 1, 1970 as MS file time

# Разница между UnixEpoch и Cocoa/WebKit
COCOA_AT_UNIXEPOCH:int = 978307200

HUNDREDS_OF_NANOSECONDS:int = 10000000
NANOSECONDS_DELIMITER:float = 1000000000.

#----------------------------------------------------------------
class _AbstractRegistryFileHandler:
    # Абстрактный обработчик файла-улья реестра
    __metaclass__ = ABCMeta
    """
    Класс является абстрактным обработчиком файла-улья реестра. 
    Предоставляет базовую структуру и некоторые общие методы для работы с файлами ульев реестра в приложении.
    """    
    def __init__(self,tempFolder:str,log:Any):
        # Входные параметры
        self._fcr:Any = FileContentReader()
        self._tempFolder:str = tempFolder
        self._log:Any = log
                
        self._regHandle:regipy.registry.RegistryHive = None
        self._regFileFullPath:str = None
        self._storageRegFileFullPath:str = None
        
    def __del__(self):
        self._regHandle = None
        self._storageRegFileFullPath = None
        
    def SetStorageRegistryFileFullPath(self,fullPath:str) -> None:
        """
        Метод устанавливает полный путь к файлу улья реестра в хранилище - каталоге Source по-умолчанию

        Args:
            fullPath (str): полный путь к файлу улья реестра
        """        
        self._storageRegFileFullPath = fullPath
    
    @abstractmethod
    def GetRegistryHandle(self) -> Optional[regipy.registry.RegistryHive]:
        pass

    def GetRegistryPath(self) -> AnyStr:
        """
        Метод возвращает полный путь к файлу улья реестра.

        Returns:
            AnyStr: полный путь до файла улья реестра
        """        
        return self._regFileFullPath
        
    def _RemoveRegistryFile(self) -> None:
        """
        Метод удаляет файл улья реестра, если он существует и доступен для удаления. 
        В случае ошибки выводится сообщение с предупреждением в журнал событий приложения.
        """        
        if self._regFileFullPath is not None:
            try:
                os.remove(self._regFileFullPath)
            except OSError as e:
                message = f'Файл не может быть удален: {e}!'
                self._log.Warn(f'_AbstractRegistryFileHandler._RemoveRegistryFile: ',message)
        
#------------------------------------------------------------------------------
class RegistryFileHandler(_AbstractRegistryFileHandler):
    """
    Класс RegistryFileHandler является подклассом _AbstractRegistryFileHandler, который предоставляет конкретную реализацию для работы с файлами ульев реестра.
    """    
    def __init__(self,tempFolder:str,log:Any):
        super().__init__(tempFolder,log)
             
    def GetRegistryHandle(self) -> Optional[regipy.registry.RegistryHive]:
        """
        Метод переопределяет абстрактный метод из родительского класса и реализует его следующим образом:

            1) Если путь к файлу улья реестра в хранилище доступен, то копируется во временный каталог.
            2) Затем файл передаётся на вход библиотеке regipy для получения обработчика улья реестра (_regHandle). 
                При возникновении ошибок, таких как FileNotFoundError, PermissionError или BaseException, значение _regHandle устанавливается в None.
            3) После получения обработчика улья реестра и завершения обработки данных, копия файла реестра удаляется для освобождения места на диске.
        

        Returns:
            Optional[regipy.registry.RegistryHive]: хендл на улей реестра
        """        
        if self._storageRegFileFullPath is not None:
            # Сделать копию во временный каталог
            regFileName:str = os.path.basename(self._storageRegFileFullPath)
            self._regFileFullPath = os.path.join(self._tempFolder,regFileName)
            shutil.copy(self._storageRegFileFullPath,self._regFileFullPath)
            
            # Передать файл реестра на вход regipy и получить хэндл
            try:
                self._regHandle = regipy.registry.RegistryHive(self._regFileFullPath)
            except (FileNotFoundError,PermissionError,BaseException):
                self._regHandle = None
                
        else:
            self._regHandle = None
            
        # Удалить копию файла реестра - содержимое считано в оперативку
        self._RemoveRegistryFile()     

        return self._regHandle

#----------------------------------------------------------------
class UTC(tzinfo):
    # Класс для задания смещения часового пояса "по Гринвичу"
    """UTC"""
    def utcoffset(self, dt:datetime) -> timedelta:
        return timedelta(0)

    def tzname(self, dt:datetime) -> AnyStr:
        return "UTC"

    def dst(self, dt:datetime) -> timedelta:
        return timedelta(0)
#----------------------------------------------------------------
class FixedOffset(tzinfo):
    # Класс для задания смещения часового пояса
    def __init__(self,offset:int,name:str):
        if offset >= 0:
            self.__offset = timedelta(minutes = offset)
        else:
            self.__offset = timedelta(days = -1,
                                      minutes = 24*60-abs(offset))
        self.__name = name
        
    def utcoffset(self,dt:datetime=None) -> timedelta:
        return self.__offset
    
    def tzname(self,dt:str='') -> AnyStr:
        return self.__name
    
    def dst(self,dt=None) -> timedelta:
        return timedelta(0)
#----------------------------------------------------------------
class TimeConverter:
    """
    Класс предоставляет статические методы для конвертации временных меток между разными форматами, 
    такими как Unix timestamp, datetime объекты, FILETIME и Cocoa time. 
    
    Класс также позволяет получить время в ISO формате или формате программного обеспечения с возможностью указания точности до микросекунд.
    """    
    @staticmethod               
    def UnixTimestampToDatetime(unixTimestamp:int,addMicroseconds:bool=False) -> datetime: 
        """
        Метод принимает Unix timestamp и преобразует его в объект datetime. Если параметр addMicroseconds установлен в True, то микросекунды также учитываются в преобразовании.

        Args:
            unixTimestamp (int): временная метка в формате Unix
            addMicroseconds (bool, optional): флаг учета микросекунд. Defaults to False.

        Returns:
            datetime: объект типа ДатаВремя datetime
        """        
        if not addMicroseconds:
            dtObj = datetime.fromtimestamp(unixTimestamp,UTC())
            return dtObj
        else:
            ts,micro = divmod(unixTimestamp,1000000)
            dtObj = datetime.fromtimestamp(ts,UTC())
            dtObj.replace(microsecond=micro)
            return dtObj
    
    @staticmethod               
    def DatetimeToFILETIME(datetimeObject:datetime) -> int:
        """
        Converts a datetime to Microsoft filetime format. If the object is
        time zone-naive, it is forced to UTC before conversion.

        DatetimeToFILETIME(datetime(2009, 7, 25, 23, 0))
        '128930364000000000'

        DatetimeToFILETIME(datetime(1970, 1, 1, 0, 0, tzinfo=utc))
        '116444736000000000'
    
        DatetimeToFILETIME(datetime(2009, 7, 25, 23, 0, 0, 100))
        128930364000001000
        """
        if (datetimeObject.tzinfo is None) or (datetimeObject.tzinfo.utcoffset(datetimeObject) is None):
            datetimeObject = datetimeObject.replace(tzinfo=UTC())
        ft = UNIX_EPOCH_AS_FILETIME + (timegm(datetimeObject.timetuple()) * HUNDREDS_OF_NANOSECONDS)
        return ft + (datetimeObject.microsecond * 10)
    
    @staticmethod
    def FILETIMEToDatetime(FTtimestamp:int,tzInfoStruct:FixedOffset=None) -> datetime:
        """
        Converts a Microsoft filetime number to a Python datetime. The new
        datetime object is time zone-naive but is equivalent to tzinfo=utc.

        FILETIMEToDatetime(116444736000000000)
        datetime.datetime(1970, 1, 1, 0, 0)

        FILETIMEToDatetime(128930364000000000)
        datetime.datetime(2009, 7, 25, 23, 0)
    
        FILETIMEToDatetime(128930364000001000)
        datetime.datetime(2009, 7, 25, 23, 0, 0, 100)
        """
        # Get seconds and remainder in terms of Unix epoch
        (s, ns100) = divmod(FTtimestamp - UNIX_EPOCH_AS_FILETIME, HUNDREDS_OF_NANOSECONDS)
        # Convert to datetime object
        try:
            if tzInfoStruct is not None:
                dt = datetime.fromtimestamp(s,tz=tzInfoStruct)
            else:
                dt = datetime.fromtimestamp(s,tz=UTC())
        except OSError:
            dt = datetime.fromtimestamp(UNIX_EPOCH_AS_FILETIME,tz=UTC())
        # Add remainder in as microseconds. Python 3.2 requires an integer
        dt = dt.replace(microsecond=(ns100 // 10))
 
        return dt
        
    @staticmethod
    def CocoaTimeToFILETIME(cocoaTimestamp:int,nanoSec:bool=False) -> int:
        """
        Метод принимает Cocoa timestamp и преобразует его в формат FILETIME Microsoft. 
        
        Если параметр nanoSec установлен в True, то исходный timestamp считается как наносекунды перед конвертацией.

        Args:
            cocoaTimestamp (int): временная метка COCOA
            nanoSec (bool, optional): флаг учета наносекунд. Defaults to False.

        Returns:
            int: целое число - временная метка FILETIME
        """        
        # ВО Cocoa это количество секунд(наносекунд) с 00:00 01.01.2001
        # cocoaDateTimeBase = datetime(2001,1,1)
        # unixDateTimeBase = datetime(1970,1,1)
        # delta = cocoaDateTimeBase - unixDateTimeBase
        delta = timedelta(seconds=COCOA_AT_UNIXEPOCH)

        if cocoaTimestamp not in (0, None, ''):
            if type(cocoaTimestamp) == str or\
                type(cocoaTimestamp) == float:
                cocoaTimestamp = int(cocoaTimestamp)
    
            if nanoSec:
                cocoaTimestamp = int(float(cocoaTimestamp)/NANOSECONDS_DELIMITER)
   
            cocoaDateTimeObj = datetime.fromtimestamp(timestamp=cocoaTimestamp,tz=UTC()) + delta

            return TimeConverter.DatetimeToFILETIME(cocoaDateTimeObj)
    
    @staticmethod
    def GetTimeInSoftwareFormat(dt:datetime,microseconds:bool=False) -> AnyStr:
        """
        Вернуть текстовую строку ДатаВремя в формате приложения.

        Args:
            dt (datetime): объект ДатаВремя
            microseconds (bool, optional): флаг учета микросекунд. Defaults to False.

        Returns:
            AnyStr: строка ДатаВремя
        """        
        if not microseconds:
            return '{y}.{m}.{d} {HH}:{MM}:{SS}'.format(d = dt.day if dt.day > 9 else '0'+str(dt.day),
                                                       m = dt.month if dt.month > 9 else '0'+str(dt.month),
                                                       y = dt.year,
                                                       HH = dt.hour if dt.hour > 9 else '0'+str(dt.hour),
                                                       MM = dt.minute if dt.minute > 9 else '0'+str(dt.minute),
                                                       SS = dt.second if dt.second > 9 else '0'+str(dt.second))
        else:
            return '{y}.{m}.{d} {HH}:{MM}:{SS}.{mS}'.format(d = dt.day if dt.day > 9 else '0'+str(dt.day),
                                                            m = dt.month if dt.month > 9 else '0'+str(dt.month),
                                                            y = dt.year,
                                                            HH = dt.hour if dt.hour > 9 else '0'+str(dt.hour),
                                                            MM = dt.minute if dt.minute > 9 else '0'+str(dt.minute),
                                                            SS = dt.second if dt.second > 9 else '0'+str(dt.second),
                                                            mS = dt.microsecond)   

#----------------------------------------------------------------
class FileContentReader:
    """
    Класс предоставляет статические методы для работы с файловой системой, 
    такие как проверка существования файла или каталога, 
    получение содержимого SQLite, текстового и бинарного файлов. 
    
    Класс также позволяет получить временные отметки создания, изменения и доступа к файлу в формате FILETIME Microsoft.
    """    
    @staticmethod
    def IsExists(fullPath:str) -> bool:
        """
        Метод проверки наличия файла или каталога

        Args:
            fullPath (str): полный путь до файла / каталога

        Returns:
            bool: файл в наличии / отсуствует
        """        
        try:
            return os.path.exists(fullPath)
        except FileNotFoundError:
            return False
    
    @staticmethod
    def ListDir(folderFullPath:str) -> List:
        """
        Вывод списка файлов в каталоге

        Args:
            folderFullPath (str): полный путь до каталога

        Returns:
            List: список файлов
        """        
        try:
            return os.listdir(folderFullPath)
        except FileNotFoundError:
            return []
    
    @staticmethod
    def GetSQLiteDBFileContent(folderPath:str,fileName:str='',includeTimestamps:bool=True) -> Tuple:
        """
        Метод принимает путь к файлу SQLite базы данных и опционально его имя, 
        а также флаг для включения временных отметок создания, изменения и доступа. 
        
        Метод возвращает кортеж с временными отметками в формате FILETIME Microsoft, путем к файлу SQLite базы данных 
        и содержимым файлов базы данных, journal файла и write-ahead log файла.

        Args:
            folderPath (str): Путь (полный путь) до файла БД
            fileName (str, optional): Имя файла БД. Defaults to ''.
            includeTimestamps (bool, optional): флаг включения в результат значений временных меток. Defaults to True.

        Returns:
            Tuple: кортеж с содержимым и именем файла
        """        
        result:tuple = None
        dbFilePath:str = None
        shmFilePath:str = None
        walFilePath:str = None
        dbContent:bytes = None
        shmContent:bytes = None
        walContent:bytes = None
        createTimeStampUTC:int = None
        modifyTimeStampUTC:int = None
        accessTimeStampUTC:int = None
               
        # Сформировать пути до файлов
        if fileName != '':
            dbFilePath = os.path.join(folderPath,fileName)
            shmFileName = f'{fileName}-shm'
            walFileName = f'{fileName}-wal'
            shmFilePath = os.path.join(folderPath,shmFileName) 
            walFilePath = os.path.join(folderPath,walFileName)
        else:
            dbFilePath = folderPath              
            shmFilePath = f'{folderPath}-shm'
            walFilePath = f'{folderPath}-wal'
            
        if includeTimestamps:
            try:
                # Получить временные отметки файла
                unixCTime = os.stat(dbFilePath).st_ctime
                unixMTime = os.stat(dbFilePath).st_mtime
                unixATime = os.stat(dbFilePath).st_atime
                createTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixCTime))
                modifyTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixMTime))
                accessTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixATime))
            except FileNotFoundError:
                pass
            
        with open(dbFilePath,'rb') as dbf:
            try:
                dbContent = dbf.read()
            except FileNotFoundError:
                pass
        
        with open(shmFilePath,'rb') as shmf:
            try:
                shmContent = shmf.read()
            except FileNotFoundError:
                pass
        
        with open(walFilePath,'rb') as walf:
            try:
                walContent = walf.read()
            except FileNotFoundError:
                pass

   
        return ({'CREATE':createTimeStampUTC,
                   'MODIFY':modifyTimeStampUTC,
                   'ACCESS':accessTimeStampUTC},
                   dbFilePath,
                   dbContent,
                   shmContent,
                   walContent)
        
    @staticmethod
    def GetTextFileContent(folderPath:str,fileName:str='',encoding:str='utf-8',includeTimestamps:bool=True) -> Tuple:
        """
        Метод принимает путь к текстовому файлу и опционально его имя, 
        а также флаг для включения временных отметок создания, изменения и доступа. 
        
        Метод возвращает кортеж с временными отметками в формате FILETIME Microsoft, путем к текстовому файлу и его содержимым в виде словаря,
        где ключ - порядковый номер строки, а значение - сама строка.

        Args:
            folderPath (str): Путь (полный путь) до файла
            fileName (str, optional): Имя файла . Defaults to ''.
            includeTimestamps (bool, optional): флаг включения в результат значений временных меток. Defaults to True.

        Returns:
            Tuple: кортеж с содержимым и именем файла
        """
        result:tuple = None 
        content:bytes = None
        createTimeStampUTC:int = None
        modifyTimeStampUTC:int = None
        accessTimeStampUTC:int = None
        records:dict = {}
        
        # Сформировать пути до файлов
        if fileName != '':
            filePath = os.path.join(folderPath,fileName)
        else:
            filePath = folderPath
        
        
        if includeTimestamps:
            try:
                # Получить временные отметки файла
                unixCTime = os.stat(filePath).st_ctime
                unixMTime = os.stat(filePath).st_mtime
                unixATime = os.stat(filePath).st_atime
                createTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixCTime))
                modifyTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixMTime))
                accessTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixATime))
            except FileNotFoundError:
                pass
            
        with open(filePath,'rb') as f:
            try:
                content = f.read()
            except FileNotFoundError:
                pass
        
        if content is not None:
            # Буферизировать массив байт
            bufferedWrapper = io.TextIOWrapper(
                                    buffer = io.BytesIO(content),
                                    encoding = encoding,
                                    errors = 'ignore',
                                    line_buffering=True)
            
            # !!!! Костыль через словарь сделан в целях ускорения
            # !!!! Простое переключение элементов списка
            # !!!! работает в 3 раза дольше чем переключение ключа словаря
            buffLen = len(bufferedWrapper.readlines())
            bufferedWrapper.seek(0)
            for i in range(0,buffLen):
                readStr = bufferedWrapper.readline()
                if readStr != '':
                    records[i] = readStr
                if i >= buffLen:
                    break
                 
        return ({'CREATE':createTimeStampUTC,
                   'MODIFY':modifyTimeStampUTC,
                   'ACCESS':accessTimeStampUTC},
                   filePath,
                   records)
    
    @staticmethod
    def GetBinaryFileContent(folderPath:str,fileName:str='',includeTimestamps:bool=True) -> Tuple:
        """
        Метод принимает путь к двоичному файлу и опционально его имя, 
        а также флаг для включения временных отметок создания, изменения и доступа. 
        
        Метод возвращает кортеж с временными отметками в формате FILETIME Microsoft, путем к двоичному файлу и его содержимым.

        Args:
            folderPath (str): Путь (полный путь) до файла
            fileName (str, optional): Имя файла . Defaults to ''.
            includeTimestamps (bool, optional): флаг включения в результат значений временных меток. Defaults to True.

        Returns:
            Tuple: кортеж с содержимым и именем файла
        """      
        result:tuple = None 
        content:bytes = None
        createTimeStampUTC:int = None
        modifyTimeStampUTC:int = None
        accessTimeStampUTC:int = None
        
        # Сформировать пути до файлов
        if fileName != '':
            filePath = os.path.join(folderPath,fileName)
        else:
            filePath = folderPath
        
        
        if includeTimestamps:
            try:
                # Получить временные отметки файла
                unixCTime = os.stat(filePath).st_ctime
                unixMTime = os.stat(filePath).st_mtime
                unixATime = os.stat(filePath).st_atime
                createTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixCTime))
                modifyTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixMTime))
                accessTimeStampUTC = TimeConverter.DatetimeToFILETIME(TimeConverter.UnixTimestampToDatetime(unixATime))
            except FileNotFoundError:
                pass
            
        with open(filePath,'rb') as f:
            try:
                content = f.read()
            except FileNotFoundError:
                pass
                 
        return ({'CREATE':createTimeStampUTC,
                   'MODIFY':modifyTimeStampUTC,
                   'ACCESS':accessTimeStampUTC},
                   filePath,
                   content)
   
#----------------------------------------------------------------
class _AbstractDatabaseClass:
    """
    Класс является абстрактным и предоставляет базовую структуру и некоторые общие методы для работы с базами данных в приложении.
    """    
    __metaclass__ = ABCMeta
    def __init__(self,dbPath,log):
        self._log:Any = log
        self._connection:sqlite3.Connection = None
        self._cursor:sqlite3.Cursor = None
        self._dbPath:str = dbPath
        self._cwd:str = os.getcwd()    
    
    @abstractmethod
    def _SetCursor(self) -> None:
        pass
    
    @abstractmethod
    def ExecCommit(self,query:str,params:Any='') -> None:
        pass
    
    @abstractmethod
    def Exec(self,query:str,params:Any='') -> None:
        pass
    
    @abstractmethod    
    def Fetch(self,query:str,params:Any='') -> List:
        pass
    
    @abstractmethod
    def Commit(self) -> None:
        pass
    
    @abstractmethod    
    def CloseConnection(self) -> None:
        pass
    
    def GetDatabasePath(self) -> AnyStr:
        """
        Метод возвращает путь к базе данных SQLite с результатами

        Returns:
            AnyStr: Путь до БД с результатами
        """        
        return self._dbPath

#----------------------------------------------------------------
class _AbstractLocalDatabaseClass(_AbstractDatabaseClass):
    """
    Класс является дочерним классом абстрактного класса _AbstractDatabaseClass.
    Он предоставляет базовую структуру и некоторые общие методы для работы с локальными SQLite базами данных в приложении.
    """    
    __metaclass__ = ABCMeta
    def __init__(self,dbPath:str,log:Any):
        super().__init__(dbPath,log)
     
    @abstractmethod
    def _SwitchOnForeignKeys(self) -> None:
       pass
   
    @abstractmethod
    def _SwitchOnCaseInsensitiveLike(self) -> None:
        pass
    
    def _RegExp(self, expr:str, item:str) -> Optional[re.Match]:
        """
        Метод принимает выражение для регулярного выражения и строку для поиска, и возвращает объект совпадения, если он найден.

        Args:
            expr (str): регулярное выражение
            item (str): объект поиска

        Returns:
            Optional[re.Match]: результат
        """        
        reg = re.compile(expr,flags=re.I)
        return reg.search(str(item)) is not None
    
    def _Lower(self,value:str) -> AnyStr:
        """
        Метод приводит символы строки к нижнему регистру

        Args:
            value (str): исходная строка

        Returns:
            AnyStr: строка в нижнем регистре
        """        
        return str(value).lower()
        
    def _AddLowerFunction(self) -> None:
        """
        Метод создает указатель на функцию приведения строки к нижнему регистру для драйвера SQLite
        """        
        if self._connection is not None:
            # Переопределение функции преобразования к нижнему регистру
            self._connection.create_function("LOWER", 1, self._Lower)
         
    def _AddRegExpSearch(self) -> None:
        """
        Метод создает указатель на функцию поиска совпадений по регулярным выражениям для драйвера SQLite
        """ 
        if self._connection is not None:
            # Прикрутить функцию поиска по регулярному выражению
            self._connection.create_function("REGEXP", 2, self._RegExp)
    
    def ExecCommit(self,query:str,params:Any='') -> None:
        """
        Метод выполняет SQL-запрос и подтверждает транзакцию, обрабатывая возможные ошибки операций с БД.

        Args:
            query (str): запрос
            params (Any, optional): переменные параметры в запросе. Defaults to ''.
        """        
        try:
            self._cursor.execute(query, params)
            self._connection.commit()
        except sqlite3.OperationalError as e:
            message = f'Ошибка запроса к БД: {e}'
            self._log.Warn('_AbstractLocalDatabaseClass',message)
        
    def Fetch(self,query:str,params:Any='') -> List:
        """
        Метод извлекает данные из базы данных с помощью SQL-запроса и подтверждает транзакцию, обрабатывая возможные ошибки операций с БД.

        Args:
            query (str): запрос
            params (Any, optional): переменные параметры в запросе. Defaults to ''.

        Returns:
            List: выборка строк из БД
        """        
        try:
            self._cursor.execute(query, params)
            self._connection.commit()    
            return self._cursor.fetchall()
        except (sqlite3.OperationalError,sqlite3.ProgrammingError) as e:
            message = f'Ошибка запроса к БД: {e}'
            self._log.Warn('_AbstractLocalDatabaseClass',message)
            return []

    def Exec(self,query:str,params:Any='') -> None:
        """
        Метод выполняет SQL-запрос без подтверждения транзакции, а также обрабатывает возможные ошибки операций с БД.

        Args:
            query (str): запрос
            params (Any, optional): переменные параметры в запросе. Defaults to ''.
        """          
        self._cursor.execute(query, params)
        
    def Commit(self) -> None:
        """
        Метод для подтверждения транзакций в БД.
        """        
        self._connection.commit()
        
    def _CheckCreateFolders(self) -> None:
        """
        Метод проверки наличия необходимых каталогов для сохранения БД обработки результатов.
        """        
        
        itemsList = self._dbPath.replace('\\', os.sep).replace('/', os.sep).rsplit(os.sep,2)
        cases = itemsList[0]
        case = itemsList[1]

        # Проверяем последовательно каталоги
        if cases.find(':\\') == -1:
            cwdCases = os.path.join(self._cwd,cases)
            if not os.path.exists(cwdCases):                    
                os.mkdir(cwdCases)
            cwdCasesCase = os.path.join(cwdCases,case)
            if not os.path.exists(cwdCasesCase):                    
                os.mkdir(cwdCasesCase)
            
        else:
            cwdCases = cases
            if not os.path.exists(cwdCases):                    
                os.mkdir(cwdCases)
            cwdCasesCase = os.path.join(cwdCases,case)
            if not os.path.exists(cwdCasesCase):                    
                os.mkdir(cwdCasesCase)

#----------------------------------------------------------------
# Интерфейс для работы с имеющимися SQLite
class SQLiteDatabaseInterfaceReader(_AbstractLocalDatabaseClass):
    """
    Класс представляет собой интерфейс для чтения из локальной базы данных SQLite. 
    
    Он наследуется от абстрактного класса _AbstractLocalDatabaseClass. 
    
    В конструкторе класса устанавливается соединение с базой данных по заданному пути, 
    создается курсор и включаются различные параметры PRAGMA для управления поведением базы данных, 
    такие как внешние ключи, режим только чтения, чувствительность к регистру при использовании оператора LIKE.

    """    
    def __init__(self,dbPath:str,log:Any): 
        super().__init__(dbPath,log)
               
        # Установить соединение из настроек и курсор        
        self._connection = self.__SetConnection()
        
        if self._connection is not None:
            self._cursor = self._SetCursor()
            self._SwitchOnForeignKeys()
            self._AddRegExpSearch()
            self._AddLowerFunction()
            self.__SwitchOnReadOnlyMode()
            self._SwitchOnCaseInsensitiveLike()
     

    def IsConnected(self) -> bool:
        """
        Метод проверки доступности соединения с БД

        Returns:
            bool: результат проверки
        """        
        if self._connection is not None:
            return True
        else: 
            return False
    
    def _SetCursor(self) -> Optional[sqlite3.Cursor]:
        """
        Метод задания курсора в БД
        
        Returns:
            Optional[sqlite3.Cursor]: курсор
        """        
        return self._connection.cursor()
    
    def __SetConnection(self) -> Optional[sqlite3.Connection]:
        """
        Метод установки соединения с БД

        Returns:
            Optional[sqlite3.Connection]: соединение
        """        
        try:
            if self._dbPath is not None:
                return sqlite3.connect(self._dbPath)
            else:
                return None
        except sqlite3.OperationalError:
                return None
   
    def _SwitchOnForeignKeys(self) -> None:
        """
        Метод для включения режима работы с внешними ключами в БД
        """        
        self.ExecCommit('PRAGMA foreign_keys=on;','')
        
    def __SwitchOnReadOnlyMode(self) -> None:
        """
        Метод включения режима "только чтение" с БД
        """        
        self.ExecCommit('PRAGMA query_only=ON;','')
        
    def _SwitchOnCaseInsensitiveLike(self) -> None:
        """
        Метод включения регистронезависимого оператора LIKE
        """        
        self.ExecCommit('PRAGMA case_sensitive_like=off;','')
        
    def _SwitchOnJournalModeMemory(self) -> None:
        """
        Метод для выбора места размещения журнала транзакций БД
        """        
        self.ExecCommit('PRAGMA journal_mode=MEMORY;','')    
                
    def GetInfo(self) -> Dict:
        """
        Метод считывания данных из таблицы Info БД

        Returns:
            Dict: параметры из Info
        """        
        info = {}
        query = 'SELECT Key,Value FROM Info;'
        result = self.Fetch(query)
        for item in result:
            info.update({item[0]:item[1]})        
        return info
    
    def GetHeaders(self) -> List:
        """
        Метод считывания интерфейсных заголовков из таблицы Headers БД

        Returns:
            List: список интерфейсных заголовков
        """        
        query = 'SELECT Name,Label,Width FROM Headers;'
        return self.Fetch(query)
               
    def GetAmountOfRecords(self) -> int:
        """
        Метод подсчета количества записей в таблице Data

        Returns:
            int: количество записей
        """        
        return self.Fetch('SELECT count(ID) FROM Data;')[0][0]
    
    def GetRecordIdCache(self) -> List:
        """
        Получение списка идентификаторов записей в таблице Data

        Returns:
            List: список идентификаторов записей
        """        
        return self.Fetch('SELECT ID FROM Data ORDER BY ID ASC;') 
        
    def IsRecords(self) -> bool:
        """
        Метод определения наличия записей в БД
        
        Returns:
            bool: результат проверки наличя записей
        """        
        query = 'SELECT count(*) FROM Data;'
        try:
            rows = self.Fetch(query,'')[0][0]
            # Если есть данные
            if rows > 0:
                return True
            else:
                return False
        except IndexError:
            return False
        
    def CloseConnection(self) -> None:
        """
        Метод закрытия соединения с БД
        """        
        if self._connection is not None:
            self._connection.close()
            self._connection = None

#----------------------------------------------------------------
# Интерфейс для работы с новыми SQLite
class SQLiteDatabaseInterface(SQLiteDatabaseInterfaceReader):
    """
    Класс представляет собой интерфейс для взаимодействия с базой данных SQLite, 
    наследуя функциональность от класса SQLiteDatabaseInterfaceReader. 
    
    В конструкторе устанавливается соединение с базой данных по заданному пути или в оперативной памяти 
    в зависимости от параметра moduleRAMProcessing. 
    
    Также инициализируются дополнительные параметры, такие как имя модуля и флаг обработки в памяти.
    """    
    def __init__(self,dbPath:str,log:Any,moduleName:str,moduleRAMProcessing:bool): 
        super().__init__(dbPath,log)
        self.__moduleName:str = moduleName
        
        self._RAMProcessing:bool = moduleRAMProcessing
        # Установить соединение из настроек и курсор        
        self._connection = self.__SetConnection()
        
        if self._connection is not None:
            self._cursor = self._SetCursor()
            self._SwitchOnForeignKeys()
            self._AddRegExpSearch()
            self._AddLowerFunction()
            self.__SwitchOnAutoVacuum()
            self._SwitchOnCaseInsensitiveLike()

        
    def RemoveTempTables(self,tempTables:list) -> None:
        """
        Метод очищает временные таблицы в БД по списку
        """         
        for table in tempTables:
            self.ExecCommit(f'DROP TABLE {table};')
    
    def IsRAMAllocated(self) -> bool:
        """
        Метод проверки размещения БД в RAM

        Returns:
            bool: результат проверки
        """        
        return self._RAMProcessing
    
    def __SetConnection(self) -> Optional[sqlite3.Connection]:
        """
        Метод установки содения с БД на запись.

        Returns:
            Optional[sqlite3.Connection]: соединение с БД
        """        
        # Установить соединение в зависимости от потребностей модуля в RAM
        if self._RAMProcessing:
            return sqlite3.connect(':memory:')
      
        else: 
            if self._dbPath is not None:
                try:
                    conn = sqlite3.connect(self._dbPath)
                except sqlite3.OperationalError: # ошибка подключения, проверить наличие каталога
                    try:
                        self._CheckCreateFolders()
                        conn = sqlite3.connect(database=self._dbPath,
                                                   timeout=3.0)  
                    except sqlite3.OperationalError: # нет БД      
                        conn = None
                    return conn
            else:
                # Ошибка задания параметров подключения к файлу БД
                return None

    def __SwitchOnAutoVacuum(self) -> None:
        """
        Метод для включения самоочистки БД при удалении данных
        """        
        self.ExecCommit('PRAGMA auto_vacuum=1;','')
               
    def IsDatabaseDumpAllowed(self) -> bool:
        # Заглушка 
        return True  
      
    def SaveSQLiteDatabaseFromRamToFile(self) -> None:
        """
        Метод сохранения БД из RAM в файл
        """        
        if not self._RAMProcessing:
            # Если и так не в памяти обрабатывает данные
            return

        if self._connection is not None:
            self._CheckCreateFolders()
            fileDBConnection = sqlite3.connect(self._dbPath)
            self._connection.backup(fileDBConnection)
            fileDBConnection.close()
 
