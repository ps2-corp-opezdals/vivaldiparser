# -*- coding: utf-8 -*-
"""
Точка входа

"""

import sys,asyncio
from Interfaces.Main import Interface
import time
#--------------------------------------------------------------------------------
async def main() -> None:
    # Синхронная подготовка: журнал, настройки, каталоги
    appEntryPoint:Interface = Interface()
    # Асинхронная работа: разбор аргументов командной строки и запуск Solver
    await appEntryPoint.Run()
    print('Работа завершена успешно')

#--------------------------------------------------------------------------------
if __name__ == '__main__':
   # asyncio.run() сам создаёт цикл событий, выполняет корутину и закрывает цикл
    start = time.time()
    try:
        asyncio.run(main())
    except asyncio.exceptions.CancelledError, Exception:
        sys.exit(-1)
    print('Время выполнения: ', time.time() - start)
    sys.exit(0)