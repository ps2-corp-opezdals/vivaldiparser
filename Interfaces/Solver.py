# -*- coding: utf-8 -*-
"""
Модуль запуска логики обработки данных

"""
import importlib,os
import importlib.util as util


from typing import Optional,Any,Dict,Awaitable

from Common.Routines import FileContentReader,SQLiteDatabaseInterface,RegistryFileHandler
from Interfaces.OutputInterface import SQLiteDBOutputWriter

#----------------------------------------------------------------
class Solver:   
    """
    Класс используется для управления задачами, связанных с обработкой данных. 
    Конструктор класса принимает различные параметры настройки (settings), дату и время запуска приложения (appStartDateTime), 
    интерфейсы (interfaces), путь к источнику данных (dataSourcePath) и имя выходного файла (outputFileName). 
    
    Класс также имеет метод RedrawUI, который используется для обновления пользовательского интерфейса приложения - унифицированно выводит результат.
    """    
    def __init__(self,settings:dict,appStartDateTime:str,interfaces:dict,dataSourcePath:str,outputFileName:str,dataModelsFolder:str):  
        self._cwd:str = os.getcwd()
        
        # Настройки
        self._settings:dict = settings
        
        # Метод обновления интерфейса приложения
        self._redrawUIMethod:Awaitable = self.RedrawUI
        
        # Логирование       
        self._log:Any = interfaces.get('LOGGER')
        
        # Читатель содержимого файлов
        self._fcr:FileContentReader = FileContentReader()
        
        # Каталог временных файлов 
        self._tempFolder:str = self._settings.get('TemporaryFilesFolder')
        
        # Обработчик реестра Windows
        self._rfh:Any = RegistryFileHandler(self._tempFolder,self._log)

        # Каталог с делами
        self._caseName:str = appStartDateTime
        self._caseFolder:str = self._settings.get('CaseFolder')
        # Каталог с моделями
        self._modelFolder:str = dataModelsFolder
        # Имя файла с результатами
        self._outputFileNameBasePart:str = outputFileName
        
        
        # Параметры для передачи в модули
        self._moduleParameters:dict = {
            'TEMP':self._tempFolder,
            'LOG':self._log,
            'CASEFOLDER':self._caseFolder,
            'CASENAME':self._caseName,
            'UIREDRAW':self._redrawUIMethod,
            'STORAGE':dataSourcePath,
            'REGISTRYFILEHANDLER':self._rfh
        }
        
    async def RedrawUI(self,message:str,percent:int) -> None:
        """
        Метод используется для обновления пользовательского интерфейса приложения - унифицированно выводит результат.

        Args:
            message (str): выводимое сообщение
            percent (int): процент выполенния задачи модуля
        """        
        print(message,percent)
        
    async def _ProcessTask(self,modulePath:str,moduleName:str) -> Optional[Dict]:
        """
        Метод динамически загружает модули из каталога Modules и выполняет их функцию Start()

        Args:
            modulePath (str): путь до модуля
            moduleName (str): название модуля

        Returns:
            Optional[Dict]: возвращаемый результат в виде словаря {"название модуля":"название сохраненной БД"}, либо None
        """        
        # Загрузить модуль
        try:
            spec = util.spec_from_file_location(name='Parser', location=os.path.join(modulePath,moduleName))
            if spec is not None: # если модуль найден
                module = util.module_from_spec(spec)
                execResult = spec.loader.exec_module(module)
                moduleInstance = module.Parser(self._moduleParameters)
                output = await moduleInstance.Start()
                return output
            else:
                return None
                
        except ImportError as ie:
            message = f'Импорт внутри динамического модуля завершился ошибкой: {ie}!'
            self._log.Error(f'Solver.ProcessTask: {os.path.join(modulePath,moduleName)}',message)
            return None
        
        except ModuleNotFoundError as mnfe:
            message = f'Ошибка импорта - модуль не найден: {mnfe} !'
            self._log.Error(f'Solver.ProcessTask: {os.path.join(modulePath,moduleName)}',message)
            return None
            

    async def Start(self) -> None:
        """
        Метод определяет модули в каталоге Modules, для каждого подготавливает параметры в нужном виде и вызывает метод _ProcessTask
        """        
        modulesPath:str = os.path.join(self._cwd,self._modelFolder)
        
        modules:list = self._fcr.ListDir(modulesPath)
        print(modules)
        for item in modules:
            moduleFullPath = os.path.join(self._cwd,self._modelFolder,item)
        
            # Параметры модуля   
            self._moduleParameters.update({'MODULENAME':item})
            self._moduleParameters.update({'FILECONTENTREADER':self._fcr})
            self._moduleParameters.update({'OUTPUTFILENAME':f'{item}_{self._outputFileNameBasePart}'})
            self._moduleParameters.update({'RAMPROCESSING':True})
        
            # Соединение с БД и интерфейс вывода информации
            dbConnection = SQLiteDatabaseInterface(
                dbPath=os.path.join(self._caseFolder,self._caseName,self._moduleParameters['OUTPUTFILENAME']),
                log=self._log,
                moduleName=item,
                ramProcessing=self._moduleParameters['RAMPROCESSING']
                )
            
            outputWriter = SQLiteDBOutputWriter(
                {
                    'DBNAME':self._moduleParameters['OUTPUTFILENAME'],
                    'CASENAME':self._moduleParameters.get('CASENAME'),
                    'CASEFOLDER':self._moduleParameters.get('CASEFOLDER'),
                    'MODULENAME':item
                }
            )
            
            outputWriter.SetDBConnection(dbConnection)

            self._moduleParameters.update({'DBCONNECTION':dbConnection})
            self._moduleParameters.update({'OUTPUTWRITER':outputWriter})
            
            moduleResult = await self._ProcessTask(modulePath=moduleFullPath, fileName='Parser.py')
            
            await self._redrawUIMethod(f'Завершена работа модуля: {moduleResult}',100)
        
        