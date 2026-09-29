# -*- coding: utf-8 -*-
"""
Модуль-заглушка. Ничего не анализирует: только ждёт заданное число секунд.

Нужен для проверки двух вещей:
  1. Модули выполняются строго последовательно, а не параллельно.
  2. Общий словарь _moduleParameters остаётся целым между итерациями цикла.
"""
import asyncio
from datetime import datetime
from typing import Any,Dict

from Common.Routines import TimeConverter

# Длительность ожидания в секундах. Поменяй для своих проверок.
SLEEP_SECONDS: int = 50

# ----------------------------------------------------------------------
class _SleepStubParser:
    """
    Класс реализует логику ожидания и подготовки итоговой записи модуля
    """
    def __init__(self, parserParameters: dict, recordFields: dict):
        self._storage: str = parserParameters.get('STORAGE')
        self._wr: Any = parserParameters.get('OUTPUTWRITER')
        self._db: Any = parserParameters.get('DBCONNECTION')
        self._log: Any = parserParameters.get('LOG')
        self._redrawUI: Any = parserParameters.get('UIREDRAW')
        self._record: dict = {field: '' for field in recordFields.keys()}
        self._tc: Any = TimeConverter()

    def _CleanRecord(self) -> None:
        """
        Метод очистки полей записи
        """
        for key in self._record.keys():
            self._record[key] = ''

    async def Start(self) -> None:
        """
        Метод ждёт SLEEP_SECONDS секунд, отчитываясь о прогрессе раз в 10 секунд
        """
        await self._redrawUI(f'SleepStub: старт ожидания {SLEEP_SECONDS} сек.', 0)

        waited: int = 0
        while waited < SLEEP_SECONDS:
            step: int = min(10, SLEEP_SECONDS - waited)
            await asyncio.sleep(step)
            waited += step
            await self._redrawUI(f'SleepStub: прошло {waited} из {SLEEP_SECONDS} сек.', int(waited / SLEEP_SECONDS * 100))

        self._record['DateTime_UTC'] = self._tc.GetTimeInSoftwareFormat(datetime.now())
        self._record['FilePath'] = f'SleepStub/{SLEEP_SECONDS}s'
        self._record['DataSource'] = self._storage

        await self._wr.WriteRecord((
            self._record['DateTime_UTC'],
            self._record['FilePath'],
            self._record['DataSource']
        ))

        self._CleanRecord()
        await self._redrawUI('SleepStub: ожидание завершено', 100)

# ----------------------------------------------------------------------
class Parser:
    """
    Класс представляет собой интерфейс для подготовки данных и инициализации классов логики обработки данных,
    а также записи их в базу данных SQLite.
    """
    def __init__(self, parameters: dict):
        self.__parameters: dict = parameters

        self.__recordFields: dict = {
            'DateTime_UTC': 'TEXT',
            'FilePath': 'TEXT',
            'DataSource': 'TEXT'
        }

        self.__sleepStubParser: Any = _SleepStubParser(parameters, self.__recordFields)

    async def Start(self) -> Dict:
        """
        Метод начинает процесс ожидания, затем закрывает базу данных

        Returns:
            Dict: результат обработки данных
        """
        outputWriter: Any = self.__parameters.get('OUTPUTWRITER')

        if not self.__parameters.get('DBCONNECTION').IsConnected():
            return

        fields: dict = {
            'DateTime_UTC': ('Дата и время события UTC', 50, 'datetime', 'Момент завершения ожидания'),
            'FilePath': ('Метка', 250, 'string', 'Идентификатор заглушки'),
            'DataSource': ('Источник данных', 100, 'string', 'Источник данных')
        }

        HELP_TEXT: str = self.__parameters.get('MODULENAME') + """: 
        Модуль-заглушка. Не анализирует улики, только ждёт заданное число секунд.
        Предназначен для проверки последовательности выполнения модулей каркасом.
        """

        infoTableData: dict = {
            'Name': self.__parameters.get('MODULENAME'),
            'Help': HELP_TEXT,
            'Timestamp': self.__parameters.get('CASENAME'),
            'Vendor': 'LabFramework'
        }

        outputWriter.SetFields(fields, self.__recordFields)
        outputWriter.CreateDatabaseTables()

        if self.__sleepStubParser is not None:
            try:
                await self.__sleepStubParser.Start()
            except Exception as e:
                self.__parameters.get('LOG').Error(self.__parameters.get('MODULENAME'), f'Ошибка ожидания: {e}')

            outputWriter.RemoveTempTables()
            await outputWriter.CreateDatabaseIndexes(self.__parameters.get('MODULENAME'))
            outputWriter.SetInfo(infoTableData)
            outputWriter.WriteMeta()
            await outputWriter.CloseOutput()

        return {self.__parameters.get('MODULENAME'): outputWriter.GetDBName()}
