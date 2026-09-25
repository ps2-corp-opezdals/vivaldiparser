# -*- coding: utf-8 -*-
"""
Модуль интерфейса обработки настроек

"""
import json
from typing import Any,Dict

#------------------------------------------------------------------------------    
class SettingsInterface:
    """
    Класс предназначен для управления настройками приложения. 
    При создании экземпляра класса передается интерфейс журналирования сообщений (logInterface).
    """    
    def __init__(self,logInterface:Any):
        self.__settingsFileName:str = 'Settings.json'
        self.__settings:dict = {}
        self.__log = logInterface
        
        # Инициализировать настройки
        self.__ReadSettings()
        
    def __ReadSettings(self) -> None:
        """
        Метод считывает настройки программы из файла Settings.json
        """        
        with open(self.__settingsFileName,'rb') as f:
            try:
                self.__settings = json.load(f)
            except json.decoder.JSONDecodeError as e:
                message = f'Файл настроек содержит ошибки: {e}'
                self.__log.Error('SettingsInterface',message)
                
                self.__settings['CaseFolder'] = 'Cases'
                self.__settings['TemporaryFilesFolder'] = 'Temp'
                

    def GetSettings(self) -> Dict:
        """
        Метод возвращает считанные настройки

        Returns:
            Dict: словарь настроек
        """        
        return self.__settings
    
    def GetSettingValueByName(self,parameterName:str) -> Any:
        """
        Метод возвращает значение параметра по имени из настроек

        Args:
            parameterName (str): имя параметры

        Returns:
            Any: возвращаемое значение
        """        
        return self.__settings.get(parameterName)
    