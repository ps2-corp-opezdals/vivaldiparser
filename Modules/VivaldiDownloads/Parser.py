import asyncio 


class VivaldiDownloads():
    def __init__(self, parameters):
        self._storage = parameters.get('STORAGE')
        self._outputWriter = parameters.get('OUTPUTWRITER')
        self._log = parameters.get('LOG')
        self._redrawUI = parameters.get('UIREDRAW')
        


class Parser():
    def __init__(self, parameters):
        self.parameters = parameters
        self.logig = VivaldiDownloads(parameters)

    async def Start(self):
        pass




