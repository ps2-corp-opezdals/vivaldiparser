# -*- coding: utf-8 -*-
"""
Модуль для анализа журналов Dr.Web (dwservice.log и Doctor Web.evtx)
"""
import os
import re
from typing import Any, Dict
from xml.etree import ElementTree as ET
from Common.Routines import TimeConverter, FixedOffset as tzInfo
from Evtx.Evtx import Evtx
from datetime import datetime, timedelta

# ----------------------------------------------------------------------
class _DrWebLogParser:
    """
    Базовый класс, иницилизирующий пармметры для извлечения и обработки данных журналов АВЗ DrWeb 
    """    
    def __init__(self, parserParameters: dict, recordFields: dict):
        self._storage: str = parserParameters.get('STORAGE')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._record: dict = {field: '' for field in recordFields.keys()}
        self._tc: Any = TimeConverter()
        self._currentTzInfo: tzInfo = tzInfo(300,  # Часовой пояс ЕКБ в минутах
                                             'Asia/Yekaterinburg')

    def _CleanRecord(self) -> None:
        """
        Метод для фрагментарной очистки полей записи обработанных данных

        """ 
        for key in self._record.keys():
            self._record[key] = ''

    async def Start(self) -> None:
        """
        Метод-обертка для вызова логики обработки данных
        """ 
        log_path = os.path.join(self._storage, 'dwservice.log')
        evtx_path = os.path.join(self._storage, 'Doctor Web.evtx')
        await self._GetInfo(log_path, evtx_path)


    async def _GetInfo(self, log_path: str, evtx_path: str) -> None:
        """
        Метод обработки данных из dwservice.log и Doctor Web.evtx.

        Args:
            log_path (str): путь до выгруженного dwservice.log
            evtx_path (str): путь до выгруженного Doctor Web.evtx
        """        
        try:
            if not os.path.exists(log_path):
                print(f"Файл журнала {log_path} не найден.")
                return

            log_pattern = re.compile(
                r"(?P<DateTime>\d{4}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}:\d{2}\.\d+).*Add file (?P<FilePath>.+?) infection: (?P<MalwareType>\S+).*len: (?P<FileSize>\d+)"
            )

            with open(log_path, 'r', encoding='utf-8', errors = 'replace') as log_file:
                for line in log_file:
                    match = log_pattern.search(line)
                    if match:
                        try:
                            DateTime = datetime.strptime(match.group('DateTime'), "%Y-%b-%d %H:%M:%S.%f")
                        except ValueError:
                            DateTime = ''
                        try:
                            self._record['DateTime_Local'] = self._tc.GetTimeInSoftwareFormat(DateTime)
                        except OSError:
                            self._record['DateTime_Local'] = ''
                        try:
                            self._record['Timestamp_UTC'] = self._tc.DatetimeToFILETIME(DateTime)
                        except OSError:
                            self._record['Timestamp_UTC'] = ''
                        try:
                            self._record['DateTime_UTC'] = self._tc.GetTimeInSoftwareFormat(DateTime - timedelta(hours=5))
                        except OSError:
                            self._record['DateTime_UTC'] = ''

                        self._record['DateTime'] = match.group('DateTime')
                        self._record['FilePath'] = match.group('FilePath')
                        self._record['MalwareType'] = match.group('MalwareType')
                        self._record['FileSize'] = match.group('FileSize')
                        self._record['TypeOfRecord'] = 'Neutralization'
                        self._record['DataSource'] = log_path
                        self._record['TimeZoneOffset'] = 300

                        await self._wr.WriteRecord((
                            self._record['DateTime_UTC'],
                            self._record['Timestamp_UTC'],
                            self._record['DateTime_Local'],
                            self._record['TimeZoneOffset'],
                            self._record['FilePath'],
                            self._record['MalwareType'],
                            self._record['FileSize'],
                            self._record['TypeOfRecord'],
                            self._record['DataSource']
                        ))

                        self._CleanRecord()

        except Exception as e:
            print(f"Ошибка при обработке файла {log_path}: {e}")

        """
        Обработка данных из Doctor Web.evtx.
        """
        try:
            if not os.path.exists(evtx_path):
                print(f"Файл журнала {evtx_path} не найден.")
                return

            with Evtx(evtx_path) as log:
                namespaces = {'ns': 'http://schemas.microsoft.com/win/2004/08/events/event'}
                for record in log.records():
                    try:
                        xml = record.xml()
                        root = ET.fromstring(xml)

                        system = root.find(".//ns:System", namespaces)
                        if system is None:
                            continue

                        eventdata = root.find(".//ns:EventData", namespaces)
                        if eventdata is None:
                            continue

                        # Извлечение данных из EventData
                        event_data_text = eventdata.find("ns:Data", namespaces).text if eventdata.find("ns:Data", namespaces) is not None else ''
                        if event_data_text.__contains__('Preventive Protection event:'):
                            time_created = system.find("ns:TimeCreated", namespaces).attrib.get(
                                "SystemTime") if system.find("ns:TimeCreated", namespaces) is not None else ''
                            DateTime = datetime.strptime(time_created, "%Y-%m-%d %H:%M:%S.%f")
                            try:
                                self._record['DateTime_UTC'] = self._tc.GetTimeInSoftwareFormat(DateTime)
                            except OSError:
                                self._record['DateTime_UTC'] = ''
                            try:
                                self._record['Timestamp_UTC'] = self._tc.DatetimeToFILETIME(DateTime)
                            except OSError:
                                self._record['Timestamp_UTC'] = ''
                            try:
                                self._record['DateTime_Local'] = self._tc.GetTimeInSoftwareFormat(
                                    self._tc.FILETIMEToDatetime(self._record['Timestamp_UTC'], self._currentTzInfo))
                            except OSError:
                                self._record['DateTime_Local'] = ''

                            self._record['TimeZoneOffset'] = 300
                            pattern = r'blocked\s*alert\s*path:\s*(?P<FilePath>(?:\\[^\\\s]+)+\\[^\\\s]+(?:\.\w+)?)'
                            file_path_match = re.search(pattern, event_data_text, re.IGNORECASE)
                            malware_type_match = re.search(r'threat: ([A-Za-z0-9:\.]+)', event_data_text)
                            file_size_match = re.search(r'fileinfo: size: (\d+)', event_data_text)

                            self._record['FilePath'] = file_path_match.group(1).strip() if file_path_match else ''
                            self._record['MalwareType'] = malware_type_match.group(1).strip() if malware_type_match else ''
                            self._record['FileSize'] = file_size_match.group(1).strip() if file_size_match else ''
                            self._record['TypeOfRecord'] = 'Preventive Protection'
                            self._record['DataSource'] = evtx_path

                            await self._wr.WriteRecord((
                                self._record['DateTime_UTC'],
                                self._record['Timestamp_UTC'],
                                self._record['DateTime_Local'],
                                self._record['TimeZoneOffset'],
                                self._record['FilePath'],
                                self._record['MalwareType'],
                                self._record['FileSize'],
                                self._record['TypeOfRecord'],
                                self._record['DataSource']
                            ))

                            self._CleanRecord()

                    except ET.ParseError as e:
                        print(f"Ошибка разбора XML: {e}")

        except Exception as e:
            print(f"Ошибка при обработке файла {evtx_path}: {e}")
# ----------------------------------------------------------------------
class Parser:
    """
    Класс представляет собой интерфейс для подготовки данных и инициализации классов логики обработки данных, а также записи их в базу данных SQLite. 
    В конструкторе класса принимается словарь параметров, который используется для настройки работы парсеров. 
    Также определяются поля записей таблицы Data БД SQLite.
    """   
    def __init__(self, parameters: dict):
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'DateTime_UTC': 'TEXT',
            'Timestamp_UTC': 'TEXT',
            'DateTime_Local': 'TEXT',
            'TimeZoneOffset': 'INTEGER',
            'FilePath': 'TEXT',
            'MalwareType': 'TEXT',
            'FileSize': 'INTEGER',
            'TypeOfRecord': 'TEXT',
            'DataSource': 'TEXT',
        }

        self.__drWebParser: Any = _DrWebLogParser(parameters, self.__recordFields)

    async def Start(self) -> Dict:
        """
        Метод начинает процесс обработки данных, вызывая инициализированный парсер. 

        Returns:
            Dict: результат обработки данных
        """   
        storage: str = self.__parameters.get('STORAGE')
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')

        if not self.__parameters.get('DBCONNECTION').IsConnected():
            return

        fields: dict = {
            'DateTime_UTC': ('Дата и время события UTC', 50, 'datetime', 'Дата и время события UTC'),
            'Timestamp_UTC': ('Врем. метка события UTC', 50, 'datetime', 'Врем. метка события UTC'),
            'DateTime_Local': ('Дата и время события', 50, 'datetime', 'Дата и время события'),
            'TimeZoneOffset': ('Смещение часового пояса', 50, 'datetime', 'Часовой пояс'),
            'FilePath': ('Путь до файла', 250, 'string', 'Путь к зараженному файлу'),
            'MalwareType': ('Тип угрозы', 150, 'string', 'Тип угрозы'),
            'FileSize': ('Размер файла', 100, 'integer', 'Размер файла в байтах'),
            'TypeOfRecord': ('Тип записи', 100, 'string', 'Тип записи о событии в журналах'),
            'DataSource': ('Источник данных', 100, 'string', 'Источник данных'),
        }

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """:
        Анализ журнала Dr.Web (dwservice.log). Извлекаются следующие данные:
        - Дата и время (DateTime)
        - Путь к файлу (FilePath)
        - Тип вредоносного ПО (MalwareType)
        - Размер файла (FileSize)
        """

        infoTableData: dict = {
            'Name': self.__parameters.get('MODULENAME'),
            'Help': HELP_TEXT,
            'Timestamp': self.__parameters.get('CASENAME'),
            'Vendor': 'LabFramework'
        }

        outputWriter.SetFields(fields, self.__recordFields)
        outputWriter.CreateDatabaseTables()

        if self.__drWebParser is not None:
            await self.__drWebParser.Start()

            outputWriter.RemoveTempTables()
            await outputWriter.CreateDatabaseIndexes(self.__parameters.get('MODULENAME'))
            outputWriter.SetInfo(infoTableData)
            outputWriter.WriteMeta()
            await outputWriter.CloseOutput()

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
