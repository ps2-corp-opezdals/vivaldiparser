# -*- coding: utf-8 -*-
"""
Структура SACP в значении параметров ветки Store
  Win8.1, Server2012R2 - в других версиях ОС тоже могут встречаться

"""
import ctypes

# _pack_ = 1 убирает выравнивание полей структуры
# _fields_ = [] поля структуры
#----------------------------------------------------------------------
class SACPStructure(ctypes.Structure):  
    _pack_ = 1  
    _fields_ = [("signature", ctypes.c_uint), # сигнатура SACP 53 41 43 40
                ("smth1", ctypes.c_ubyte * 40), # не изучено
                ("timestamp",ctypes.c_ulonglong)] # ВО 

