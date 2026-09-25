# -*- coding: utf-8 -*-
"""
Модуль обработки записей реестра веток MuiCache

"""
import regipy,os
from abc import ABCMeta, abstractmethod
from typing import Callable,Any,Dict

#----------------------------------------------------------------------
class _MuiCacheParser:
    """
    Базовый абстрактный класс, иницилизирующий пармметры для извлечения и обработки данных из веток MuiCache 
    реестра различных версий Windows 
    """    
    __metaclass__ = ABCMeta
    def __init__(self,parserParameters:dict,recordFields:dict):
        # Входные параметры
        self._redrawUI:Callable = parserParameters.get('UIREDRAW')
        self._rfh:regipy.registry.RegistryHive = parserParameters.get('REGISTRYFILEHANDLER')
        self._wr:Any = parserParameters.get('OUTPUTWRITER')
        self._db:Any = parserParameters.get('DBCONNECTION')
        self._standaloneFiles:bool = True # ! оставлять как True
        self._storage:str = parserParameters.get('STORAGE')
        
        self._profileList:list = None
        
        # Запись в БД
        self._record:dict = {}
        for k,v in recordFields.items():
            if v == 'TEXT':
                self._record[k] = ''
            elif v == 'INTEGER' or v == 'INTEGER UNSIGNED':
                self._record[k] = 0
            else:
                self._record[k] = ''
                
    def SetUserProfilesList(self,userProfilesList:list) -> None:
        """
        Метод установки перечня профилей пользователей. Может быть пустым

        Args:
            userProfilesList (list): список пользователей ОС
        """        
        self._profileList = userProfilesList
    
    @abstractmethod
    async def _GetInfo(self,data) -> None:
        pass

    def _CleanRecord(self) -> None:
        """
        Метод для фрагментарной очистки полей записи обработанных данных

        """        
        self._record['Name'] = ''
        self._record['Company'] = ''
        self._record['Parameter'] = ''
        self._record['Value'] = ''
            
    async def Start(self) -> None:
        """
        Метод-обертка для вызова логики обработки данных 
        """        
        if not self._standaloneFiles:
            if self._profileList is not None:
                for sid,userInfo in self._profileList.items():
                    await self._GetInfo(userInfo)

        else:
            await self._GetInfo(None)
            
#----------------------------------------------------------------------
class _MuiCacheParser_V1(_MuiCacheParser):
    # XP и Server2003
    def __init__(self,parserParameters:dict,recordFields:dict):
        super().__init__(parserParameters,recordFields)
        
    async def _GetInfo(self, data:None) -> None:
        """
        Метод, реализующий логику обработки данных из веток реестра MuiCache Windows XP и Server2003

        Args:
            data (None): поле None в рамках каркаса
        """        
        r"""
        В XP:  
            Свой софт и системные DLL и exe с параметрами
            HKU\SID\Software\Microsoft\Windows\ShellNoRoam\MUICache            
        """
        ntUserDatPath:str = None
        
        if data is None:
            self._record['UserName'] == ''
            ntUserDatPath = os.path.join(self._storage,'ntuser.dat')    
        
        await self._redrawUI('Пользователи Windows: MUICache пользователя ' + self._record['UserName'],1)
        
        if ntUserDatPath is not None:
            self._rfh.SetStorageRegistryFileFullPath(ntUserDatPath)
            ntUserDatReg = self._rfh.GetRegistryHandle()
        
            # Получение данных и запись в БД
            if ntUserDatReg is not None:
                # Заполнить источник информации
                self._record['DataSource'] = ntUserDatPath
                
                # Обработать ключи реестра
                try:
                    for item in ntUserDatReg.get_key('\\Software\\Microsoft\\Windows\\ShellNoRoam\\MUICache').get_values():
                        
                        rawName = item.name
                        if rawName == 'LangID':
                            continue
                        # Определить нет ли utf-16le вместо utf-8
                        # Убрать @ и " и обернуть слэши для проверки через re
                        rawName = rawName.replace('@','').replace('"','').replace(';','')
                       
                        try:
                            self._record['Parameter'] = rawName.rsplit(',-',1)[1]
                        except IndexError:
                            self._record['Parameter'] = ''
                    
                        try:
                            self._record['Name'] = rawName.rsplit(',-',1)[0]
                        except IndexError:
                            self._record['Name'] = rawName
                            
                        self._record['Value'] = item.value
                    
                        if rawName is not None and self._record['Value'] is not None:
                            info = (self._record['UserName'],
                                self._record['Name'],
                                self._record['Company'],
                                self._record['Parameter'],
                                self._record['Value'],
                                self._record['DataSource'])
        
                            await self._wr.WriteRecord(info)
                            
                        # Сбросить значения
                        self._CleanRecord()
                
                except regipy.RegistryKeyNotFoundException:
                    pass # нет ключей
                except regipy.RegistryKeyNotFoundException:
                    pass # нет такой ветки

        await self._redrawUI('Пользователи Windows: MUICache пользователя ' + self._record['UserName'],100)        

#----------------------------------------------------------------------
class _MuiCacheParser_V2(_MuiCacheParser):
    # Vista, 7, 8, 8.1, 10 Server2008, Server2008R2, Server2012, Server2012R2, Server2016
    def __init__(self,parserParameters:dict,recordFields:dict):
        super().__init__(parserParameters,recordFields)
             
    async def _GetInfo(self, data:None) -> None:
        """
        Метод, реализующий логику обработки данных из веток реестра MuiCache Windows 7+

        Args:
            data (None): поле None в рамках каркаса
        """
        r"""
        В 7:  
            Чаще системные DLL и exe с параметрами
            HKU\SID_Classes\Local Settings\MuiCache\<BYTE>\B1A07F78
            и повтор
            HKU\SID\Software\Classes\Local Settings\MuiCache\<BYTE>\B1A07F78
            Запуск своих приложений со значениями расширений параметров ApplicationCompany и FriendlyName
            HKU\SID_Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache
               
        В 8.1:  
            Не изменяемый MuiCache
            HKU\SID_Classes\Local Settings\ImmutableMuiCache\Strings\B1A07F78
            и повтор
            HKU\SID\Software\Classes\Local Settings\ImmutableMuiCache\Strings\B1A07F78
            
            Чаще системные DLL и exe с параметрами
            HKU\SID_Classes\Local Settings\MuiCache\<BYTE>\B1A07F78
            и повтор
            HKU\SID\Software\Classes\Local Settings\MuiCache\<BYTE>\B1A07F78
            
            Запуск своих приложений со значениями расширений параметров ApplicationCompany и FriendlyAppName
            HKU\SID_Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache
        """
        pathKey:str = ''
        ntUserDatPath:str = None
        usrClassDatPath:str = None
        
        if data is None:
            self._record['UserName'] == ''
            ntUserDatPath = os.path.join(self._storage,'ntuser.dat')
            usrClassDatPath = os.path.join(self._storage,'UsrClass.dat')
            
        
        if usrClassDatPath is not None:
            self._rfh.SetStorageRegistryFileFullPath(usrClassDatPath)    
            usrClassDatReg = self._rfh.GetRegistryHandle()
        
            # Получение данных и запись в БД
            if usrClassDatReg is not None:
                # Заполнить источник информации
                self._record['DataSource'] = usrClassDatPath
                
                # Обработать ключи реестра
                try:
                    # Первая часть
                    for item in usrClassDatReg.get_key('\\Local Settings\\MuiCache').iter_subkeys():
                        pathKey = item.name 
                        break
                
                    for item in usrClassDatReg.get_key('\\Local Settings\\MuiCache\\' + pathKey + '\\B1A07F78').get_values():
                        rawName = item.name
                        if rawName == 'LangID' or rawName == 'LanguageList':
                            continue
                    
                        # Определить нет ли utf-16le вместо utf-8
                        # Убрать @ и " и обернуть слэши для проверки через re
                        rawName = rawName.replace('@','').replace('"','').replace(';','')
                    
                        try:
                            self._record['Parameter'] = rawName.rsplit(',-',1)[1]
                        except IndexError:
                            self._record['Parameter'] = ''
                    
                        try:
                            self._record['Name'] = rawName.rsplit(',-',1)[0]
                        except IndexError:
                            self._record['Name'] = rawName
                    
                        self._record['Value'] = item.value
                    
                        if rawName is not None and self._record['Value'] is not None:
                            info = (self._record['UserName'],
                                    self._record['Name'],
                                    self._record['Company'],
                                    self._record['Parameter'],
                                    self._record['Value'],
                                    self._record['DataSource'])
        
                            await self._wr.WriteRecord(info)
                        
                        # Сбросить значения
                        self._CleanRecord()
                
                except regipy.NoRegistrySubkeysException:
                    pass # нет ключей
                except regipy.RegistryKeyNotFoundException:
                    pass # нет такой ветки
                except regipy.exceptions.RegistryParsingException:
                    pass # кривой реестр
                
                # Вторая часть
                try:
                    for item in usrClassDatReg.get_key('\\Local Settings\\Software\\Microsoft\\Windows\\Shell\\MuiCache').get_values():
                        
                        rawName = item.name
                        if rawName == 'LangID' or rawName == 'LanguageList':
                            continue
                        
                        # Убрать @ и " и обернуть слэши для проверки через re
                        rawName = rawName.replace('@','').replace('"','').replace(';','')
                    
                        try:
                            self._record['Parameter'] = rawName.rsplit(',-',1)[1]
                        except IndexError:
                            self._record['Parameter'] = ''
                    
                        try:
                            self._record['Name'] = rawName.rsplit(',-',1)[0]
                        except IndexError:
                            self._record['Name'] = rawName
                        
                        if self._record['Name'].endswith('.FriendlyAppName'):
                            self._record['Name'] = self._record['Name'].rsplit('.FriendlyAppName',1)[0]
                        elif self._record['Name'].endswith('.ApplicationCompany'):
                            self.__UpdateRecordCompanyValue(self._record['Name'].rsplit('.ApplicationCompany',1)[0],item.value)
                            self._CleanRecord()
                            continue

                        self._record['Value'] = item.value
                    
                        if rawName is not None and self._record['Value'] is not None:
                            info = (self._record['UserName'],
                                   self._record['Name'],
                                   self._record['Company'],
                                   self._record['Parameter'],
                                   self._record['Value'],
                                   self._record['DataSource'])
                           
                            await self._wr.WriteRecord(info)
                        
                        # Сбросить значения
                        self._CleanRecord()
                
                except regipy.RegistryKeyNotFoundException:
                    pass
                
                except regipy.NoRegistrySubkeysException:
                    pass
                except regipy.exceptions.RegistryParsingException:
                    pass # кривой реестр

            
        await self._redrawUI('Пользователи Windows: MUICache пользователя ' + self._record['UserName'],100)
        
    
    def __UpdateRecordCompanyValue(self,softwareName:str|int,value:int|str) -> None:
        """
        Метод обновления поля Company

        Args:
            softwareName (str | int): Название программы
            value (int | str): значение, обновляемое в поле Company
        """        
        query = 'UPDATE Data SET Company = ? WHERE Name = ?;'
        self._db.ExecCommit(query,(str(value),str(softwareName)))
        
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
            'Name':'TEXT',
            'Company':'TEXT',
            'Parameter':'TEXT',
            'Value':'TEXT',
            'DataSource':'TEXT'
         }
              
        osVersion:str = '10'
        if osVersion.find('__') != -1:
            self.__osVersion,self.__osRelease = osVersion.split('__')
        else:
            self.__osVersion = osVersion
            
        if self.__osVersion in ['XP', 'Server2003']:
            self.__muiCacheParser:Any = _MuiCacheParser_V1(parameters,self.__recordFields)
        else:
            self.__muiCacheParser:Any = _MuiCacheParser_V2(parameters,self.__recordFields)   
            
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
            'Name':('Полный путь / Название',430,'string','Полный путь / Название'),
            'Company':('Производитель ПО', 150,'string', 'Производитель ПО'),
            'Parameter':('Параметр',-1,'string', 'Параметр'),
            'Value':('Описание',430,'string', 'Описание'),
            'DataSource':('Источник данных',230,'string','Источник данных')
        }
        
        HELP_TEXT:str = self.__parameters.get('MODULENAME') + """:
        Списки задания имени программы/библиотеки/иного ресурса 
        в соответствии с языком системы
                        
        Извлекается из веток:
        Windows XP:  
        HKU\\<SID>\\Software\\Microsoft\\Windows\\ShellNoRoam\\MUICache

        Windows 7, 8, 8.1, 10:  
        HKU\\<SID>\\Software\\Classes\\Local Settings\\MuiCache\\<BYTE>\\B1A07F78
        HKU\\<SID>_Classes\\Local Settings\\Software\\Microsoft\\Windows\\Shell\\MuiCache

        Переменные среды извлекаются из:
        HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SecEdit\\EnvironmentVariables
                
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
        if self.__muiCacheParser is not None:
            self.__muiCacheParser.SetUserProfilesList(self.__parameters.get('USERPROFILES',{}))
            await self.__muiCacheParser.Start()

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

