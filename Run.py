# -*- coding: utf-8 -*-
"""
Точка входа

"""

import sys,asyncio
from Interfaces.Main import Interface

#--------------------------------------------------------------------------------
def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    appEntryPoint:Interface = Interface()
    # Обеспечиваем асинхронный цикл работы
    try:
        loop.run_until_complete(appEntryPoint.Run()) 
        sys.exit(0)
    except asyncio.exceptions.CancelledError:
        sys.exit(-1)

if __name__ == '__main__': 
    main()               