# -*- coding: utf-8 -*-
"""
Модуль журналирования сообщений ПО

"""

import os
import logging
import traceback
#------------------------------------------------------------------------------
class LogInterface:
    """
    Класс журналирования сообщений и ошибок приложения
    """    
    def __init__(self,appStartDateTime:str):
        os.makedirs(os.path.join(os.getcwd(),'Logs'), exist_ok=True)
        self.__logPath = os.path.join(os.getcwd(),'Logs', f'{appStartDateTime}.log')  
        logging.basicConfig(level=logging.INFO,
                                filename=self.__logPath,
                                filemode='w',
                                format='%(asctime)s___%(levelname)s___%(message)s')

    def Error(self,sourceName:str,message:str) -> None:
        """
        Метод для журналирования сообщений типа "Ошибка"

        Args:
            sourceName (str): источник сообщения
            message (str): сообщение о событии
        """        
        logging.error(f'{sourceName}: {message}')
        
    def Warn(self,sourceName:str,message:str) -> None:
        """
        Метод для журналирования сообщений типа "Предупреждение"

        Args:
            sourceName (str): источник сообщения
            message (str): сообщение о событии
        """
        logging.warning(f'{sourceName}: {message}')
        
    def Info(self,sourceName:str,message:str) -> None:
        """
        Метод для журналирования информационных сообщений

        Args:
            sourceName (str): источник сообщения
            message (str): сообщение о событии
        """
        logging.info(f'{sourceName}: {message}')
            
    @staticmethod
    def DeathRattle(exc_type,exc_value,exc_traceback):
        """
        Метод для журналирования сообщений при аварийном завершении работы приложения

        Args:
            exc_type : тип исключения
            exc_value : значение исключения
            exc_traceback : стек сообщений об ошибках
        """
        logging.error('UNCAUGHT EXCEPTION IN MODULE!',
                        exc_info = (exc_type,exc_value,exc_traceback))
        
        print('UNCAUGHT EXCEPTION IN MODULE!')
        print((exc_type,exc_value))
        print(''.join(traceback.format_tb(exc_traceback)))
        