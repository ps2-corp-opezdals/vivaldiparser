import asyncio 


class VivaldiDownloads():
    def __init__(self, parameters):
        self._storage = parameters.get('STORAGE')
        self._outputWriter = parameters.get('OUTPUTWRITER')
        self._log = parameters.get('LOG')
        self._redrawUI = parameters.get('UIREDRAW')

    async def Run(self) -> None:
        print("VivaldiDownloads.Run() DINASTIA ELESEI")
        print(self._storage)        
        # 1. Читаешь данные из self._storage
        # 2. Обрабатываешь их
        # 3. Записываешь результат через self._outputWriter.WriteRecord((...))
        pass


class Parser():
    def __init__(self, parameters):
        self.parameters = parameters
        self.logic = VivaldiDownloads(parameters)

    async def Start(self):
        return await self.logic.Run()




