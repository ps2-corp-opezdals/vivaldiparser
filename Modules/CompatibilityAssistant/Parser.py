# -*- coding: utf-8 -*-
"""
Модуль обработки записей реестра веток Persisted и Store 
ветки Compatibility Assistant

"""
import re,regipy,itertools,os
from abc import ABCMeta
from typing import Optional,Callable,Any,List,AnyStr,AsyncGenerator,Dict

from Common.Routines import TimeConverter
from Common.Routines import FixedOffset as tzInfo

from Modules.CompatibilityAssistant.SACPStructure import SACPStructure

#----------------------------------------------------------------------
class _CompatibilityAssistantParser:
    """
    Базовый абстрактный класс, иницилизирующий пармметры для извлечения и обработки данных из веток Compatibility Assistant
    реестра различных версий Windows 
    """  
    __metaclass__ = ABCMeta
    def __init__(self,parserParameters:dict,recordFields:dict):
        # Входные параметры
        self._redrawUI:Callable = parserParameters.get('UIREDRAW')
        self._rfh:Any = parserParameters.get('REGISTRYFILEHANDLER')
        self._standaloneFiles:bool = True # ! оставлять как True
        self._storage:str = parserParameters.get('STORAGE')
 
         # Запись в БД
        self._record:dict = {}
        for k,v in recordFields.items():
            if v == 'TEXT':
                self._record[k] = ''
            elif v == 'INTEGER' or v == 'INTEGER UNSIGNED':
                self._record[k] = 0
            else:
                self._record[k] = ''
        
        self._profileList:list = None
        self._tc:Any = TimeConverter()
        if not self._standaloneFiles:
            self._tzInfoHandler = parserParameters.get('TIMEZONEINFOHANDLER')
            self._tzInfoStruct =  self._tzInfoHandler.Start()
            self._currentTzInfo:tzInfo = tzInfo(self._tzInfoStruct.activeTimeBias,
                                        self._tzInfoStruct.timeZoneKeyName)

            self._record['TimeZoneOffset'] = self._tzInfoStruct.activeTimeBias
        else:
            self._currentTzInfo:tzInfo = tzInfo(300, # Часовой пояс ЕКБ в минутах
                                        'Asia/Yekaterinburg')

            self._record['TimeZoneOffset'] = 300
        
        
    def SetUserProfilesList(self,userProfilesList:list) -> None:
        """
        Метод установки перечня профилей пользователей. Может быть пустым

        Args:
            userProfilesList (list): список пользователей ОС
        """ 
        self._profileList = userProfilesList
    
    async def _GetInfo(self, data:dict) -> Optional[List]:
        """
        Метод, реализующий логику обработки данных из веток реестра Compatibility Assistant

        Args:
            data (None): поле None в рамках каркаса
        """ 
        r"""
        В 7 :  
            HKU\SID\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Persisted            
        
        В 8.1 и далее:  
            HKU\SID\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store           
        """
        ntUserDatPath:str = None
        ntUserDatReg:Any = None
        value:Any = None
        persistedRegKey:Any = None
        storeRegKey:any = None
        persistedResult:list = []
        storeResult:list = []
        result:list = []
        
        if data is None:
            self._record['UserName'] == ''
            ntUserDatPath = os.path.join(self._storage,'ntuser.dat')    
            
        if ntUserDatPath is not None:
            self._rfh.SetStorageRegistryFileFullPath(ntUserDatPath)
            ntUserDatReg = self._rfh.GetRegistryHandle()
        
            # Получение данных и запись в БД
            if ntUserDatReg is not None:
                # Попытаться добыть имя пользователя из файла реестра
                if self._record['UserName'] == '':
                    try:
                        self._record['UserName'] = ntUserDatReg.get_key('Volatile Environment').get_value('USERNAME')
                    except (regipy.NoRegistrySubkeysException,
                        regipy.RegistryKeyNotFoundException,
                        regipy.NoRegistryValuesException,
                        regipy.RegistryValueNotFoundException):
                        pass
                
                await self._redrawUI(f'Пользователи Windows: CompatibilityAssistant пользователя {self._record["UserName"]}',1)
                # Обработать ключи реестра
                try:
                    reg_key = ntUserDatReg.get_key('\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Compatibility Assistant')
                    for item in reg_key.iter_subkeys():
                        if item.name.lower() == 'persisted':
                            persistedRegKey = ntUserDatReg.get_key('\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Compatibility Assistant\\Persisted')
                        elif item.name.lower() == 'store':
                            storeRegKey = ntUserDatReg.get_key('\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Compatibility Assistant\\Store')
                            
                except (regipy.NoRegistrySubkeysException,
                        regipy.RegistryKeyNotFoundException,
                        regipy.NoRegistryValuesException,
                        regipy.RegistryValueNotFoundException):
                    reg_key = None
 
                if persistedRegKey is not None:
                    # Заполнить источник информации
                    self._record['DataSource'] = ntUserDatPath
                    persistedResult = self._ParsePersisted(persistedRegKey)
                
                if storeRegKey is not None:
                    # Заполнить источник информации
                    self._record['DataSource'] = ntUserDatPath
                    storeResult = self._ParseStore(storeRegKey)

                        
        result = list(itertools.chain.from_iterable([persistedResult,storeResult]))                
                    
        await self._redrawUI(f'Пользователи Windows: CompatibilityAssistant пользователя {self._record["UserName"]}',100)
        return result
    
    def _ClearRecord(self) -> None:
        """
        Метод для фрагментарной очистки полей записи обработанных данных

        """ 
        self._record['FullPath'] = ''
        self._record['DateTime_UTC'] = ''
        self._record['Timestamp_UTC'] = 0
        self._record['DateTime_Local'] = ''
        
    def _CheckUTF16LEEncoding(self,record:str) -> AnyStr:
        """
        Метод проверки и преобразования кодировки строковых данных

        Args:
            record (str): проверяемая строка

        Returns:
            AnyStr: преобразованный результат
        """        
        if re.match(r'[A-Z]\x00\:.*',record,flags = re.IGNORECASE) is not None:
            try:
                recordByteArray = record.encode('utf-8',errors='strict')
            except UnicodeError:
                return record    
            record = recordByteArray.decode('utf-16le',errors='ignore')
            return record
        else:
            return record
        
    def _ParsePersisted(self,reg:regipy.registry.RegistryHive) -> Optional[List]:
        """
        Метод обработки данных ветки Persisted

        Args:
            reg (_type_): хендл на улей реестра

        Returns:
            Optional[List]: список обработанных записей для сохранения в БД
        """        
        result:list = []
        # Обработать ключи реестра
        for item in reg.get_values():
            self._record['FullPath'] = item.name
            # Определить нет ли utf-16le вместо utf-8
            self._record['FullPath'] = self._CheckUTF16LEEncoding(self._record['FullPath'])
            # Убрать @ и " и обернуть слэши для проверки через re
            self._record['FullPath'] = self._record['FullPath'].replace('@','').replace('"','')
               
            if self._record['FullPath'] is not None:
                
                info = (self._record['UserName'],
                        self._record['FullPath'],
                        self._record['DateTime_UTC'],
                        self._record['Timestamp_UTC'],
                        self._record['DateTime_Local'],
                        self._record['DataSource'],
                        0)
                
                result.append(info)
            
            # Сбросить значения
            self._ClearRecord()
            
        return result
    
    def _ParseStore(self,reg:regipy.registry.RegistryHive) -> Optional[List]:
        """
        Метод обработки данных ветки Store

        Args:
            reg (_type_): хендл на улей реестра

        Returns:
            Optional[List]: список обработанных записей для сохранения в БД
        """ 
        result:list = []
        value:bytearray = None
        for item in reg.get_values():
            self._record['FullPath'] = item.name
            # Определить нет ли utf-16le вместо utf-8
            self._record['FullPath'] = self._CheckUTF16LEEncoding(self._record['FullPath'])
            # Убрать @ и " и обернуть слэши для проверки через re
            self._record['FullPath'] = self._record['FullPath'].replace('@','').replace('"','')

            
            value = bytearray(item.value)
            # Получить значение из структуры с сигнатурой SACP. 
            sacpStruct = SACPStructure.from_buffer_copy(value)
            
            self._record['Timestamp_UTC'] = sacpStruct.timestamp 

            try:
                self._record['DateTime_UTC'] = self._tc.GetTimeInSoftwareFormat(self._tc.FILETIMEToDatetime(self._record['Timestamp_UTC']))
            except OSError:
                # Структура не описана до конца, бывают кривые значения
                self._record['DateTime_UTC'] = ''
            try:
                self._record['DateTime_Local'] = self._tc.GetTimeInSoftwareFormat(self._tc.FILETIMEToDatetime(self._record['Timestamp_UTC'],self._currentTzInfo))                    
            except OSError:
                # Структура не описана до конца, бывают кривые значения
                self._record['DateTime_Local'] = ''
                
            if self._record['FullPath'] is not None and value is not None:
                storeInfo = (self._record['UserName'],
                            self._record['FullPath'],
                            self._record['DateTime_UTC'],
                            self._record['Timestamp_UTC'],
                            self._record['DateTime_Local'],
                            self._record['DataSource'],
                            self._record['TimeZoneOffset'])

                result.append(storeInfo)
                
            # Сбросить значения
            self._ClearRecord()
            value = None
            
        return result
          
    async def Start(self) -> AsyncGenerator:
        """
        Метод-обертка для вызова логики обработки данных,
        реализован в виде асинхронного генератора
        """        
        if not self._standaloneFiles:
            if self._profileList is not None:
                for sid,userInfo in self._profileList.items():
                    yield await self._GetInfo(userInfo)
        else:
            yield await self._GetInfo(None)
            

#----------------------------------------------------------------------
class _CompatibilityAssistantParser_V1(_CompatibilityAssistantParser):
    """
    Класс-реализация абстрактного класса _CompatibilityAssistantParser
    """    
    # 7 и Server2008R2
    def __init__(self,parserParameters:dict,recordFields:dict):
        super().__init__(parserParameters,recordFields)
            
#----------------------------------------------------------------------
class _CompatibilityAssistantParser_V2(_CompatibilityAssistantParser):
    """
    Класс-реализация абстрактного класса _CompatibilityAssistantParser
    """ 
    #  8, 8.1, 10, Server2012, Server2012R2, Server2016
    def __init__(self,parserParameters:dict,recordFields:dict):
        super().__init__(parserParameters,recordFields)
            
#----------------------------------------------------------------------
class Parser:
    """
    Класс представляет собой интерфейс для подготовки данных и инициализации классов логики обработки данных, а также записи их в базу данных SQLite. 
    В конструкторе класса принимается словарь параметров, который используется для настройки работы парсеров. 
    Также определяются поля записей таблицы Data БД SQLite.
    """   
    def __init__(self,parameters:dict):  
        self.__parameters:dict = parameters 
        self.__recordFields:dict = {
            'UserName':'TEXT',
            'FullPath':'TEXT',
            'DateTime_UTC':'TEXT',
            'Timestamp_UTC':'INTEGER',
            'DateTime_Local':'TEXT',
            'DataSource':'TEXT',
            'TimeZoneOffset':'INTEGER'
            }
             
        osVersion:str = '10'
        if osVersion.find('__') != -1:
            self.__osVersion,self.__osRelease = osVersion.split('__')
        else:
            self.__osVersion = osVersion
        
        if self.__osVersion in ['7', 'Server2008R2']:
            self.__compatibilityAssistantParser = _CompatibilityAssistantParser_V1(parameters,self.__recordFields)

        else:
            self.__compatibilityAssistantParser = _CompatibilityAssistantParser_V2(parameters,self.__recordFields)
    
            
    async def Start(self) -> Dict:
        """
        Метод начинает процесс обработки данных, вызывая инициализированный парсер. 

        Returns:
            Dict: результат обработки данных
        """     
        storage:str = self.__parameters.get('STORAGE')
        outputWriter:Any = self.__parameters.get('OUTPUTWRITER')
        
        if not self.__parameters.get('DBCONNECTION').IsConnected():
            return # Модуль не может обрабатывать информацию - нет соединения с БД вывода
        
        fields:dict = {
            'UserName':('Имя пользователя',140,'string','Имя пользователя'),
            'FullPath':('Полный путь / Название',650,'string','Полный путь / Название'),
            'DateTime_UTC':('Дата и время создания записи (UTC)', 230,'string', 'Дата и время создания записи (UTC)'),
            'Timestamp_UTC':('Врем. метка создания записи (UTC)',-1,'integer', 'Врем. метка создания записи (UTC)'),
            'DateTime_Local':('Дата и время создания записи',-1,'string', 'Дата и время создания записи'),
            'DataSource':('Источник данных',230,'string','Источник данных')
        }
        
        HELP_TEXT:str = self.__parameters.get('MODULENAME') + """: 
        Список совместимости приложений с операционной системой 

                            
        Данные извлекаются из ветки:
        Windows 7:
        HKU\\<SID>\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Compatibility Assistant\\Persisted
        Windows 8,8.1,10:
        HKU\\<SID>\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Compatibility Assistant\\Store

        Переменные среды извлекаются из:
        HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SecEdit\\EnvironmentVariables
            
        Сведения о часовом поясе извлекаются из:
        HKLM\\SYSTEM\\ControlSet<NUM>\\Control\\TimeZoneInformation    


        """
        # Таблица Info
        infoTableData:dict = {
            'Name':self.__parameters.get('MODULENAME'),
            'Help':HELP_TEXT,
            'Timestamp':self.__parameters.get('CASENAME'), # Время начала обработки массива - имя case
            'Vendor':'LabFramework'
        }
        
        # Задать параметры вывода и создать таблицы
        outputWriter.SetFields(fields,self.__recordFields)
        outputWriter.CreateDatabaseTables()

        # Обработать профили
        if self.__compatibilityAssistantParser is not None:
            self.__compatibilityAssistantParser.SetUserProfilesList(self.__parameters.get('USERPROFILES',{}))
            async for userRecords in self.__compatibilityAssistantParser.Start():
                # Записать данные в БД
                for record in userRecords:
                    await outputWriter.WriteRecord(record)

            # Удалить временные таблицы
            outputWriter.RemoveTempTables()
            
            # Создать индексы
            await outputWriter.CreateDatabaseIndexes(self.__parameters.get('MODULENAME'))
        
            # Завершить формирование БД
            outputWriter.SetInfo(infoTableData)
            outputWriter.WriteMeta()
            
            # Закрыть соединение БД
            await outputWriter.CloseOutput()
            
        return {self.__parameters.get('MODULENAME'):outputWriter.GetDBName()}
       

