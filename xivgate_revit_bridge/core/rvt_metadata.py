"""Bounded read-only BasicFileInfo inspection without Autodesk APIs or Revit."""
import functools
import os
from pathlib import Path
import re
import struct


def parse_basic_info(raw):
    if len(raw)>65536:
        raise ValueError('Слишком большой BasicFileInfo')
    years=set()
    if len(raw)>=19:
        length=struct.unpack_from('<I',raw,15)[0]
        if 0<length<=512 and 19+2*length<=len(raw):
            try:
                text=raw[19:19+2*length].decode('utf-16le')
                match=re.search(r'(?:Autodesk\s+)?Revit[^\r\n]*?\b((?:19|20)\d{2})\b',text)
                if match:years.add(int(match[1]))
            except UnicodeError:
                pass
    for offset in (0,1):
        data=raw[offset:len(raw)-(len(raw)-offset)%2]
        text=data.decode('utf-16le',errors='ignore')
        years.update(int(m[1]) for m in re.finditer(r'(?:^|[\r\n])Format:[ \t]*((?:19|20)\d{2})[ \t]*(?:[\r\n]|$)',text))
    if len(years)!=1:
        raise ValueError('Версия RVT не определена однозначно')
    return {'year':years.pop()}


def _read_stream(path):
    if os.name!='nt':raise OSError('Чтение RVT требует Windows')
    import ctypes as c
    ole=c.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[c.c_void_p,c.c_ulong]
    ole.CoInitializeEx.restype=c.c_long
    hr=ole.CoInitializeEx(None,2)
    if hr < 0 and hr != -2147417850:
        raise OSError(f'CoInitializeEx: {hr & 0xffffffff:08X}')
    initialized=hr in (0,1)
    storage,stream=c.c_void_p(),c.c_void_p()
    def method(pointer,index,result,*arguments):
        table=c.cast(pointer,c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result,c.c_void_p,*arguments)(table[index])
    try:
        ole.StgOpenStorage.argtypes=[c.c_wchar_p,c.c_void_p,c.c_ulong,c.c_void_p,c.c_ulong,c.POINTER(c.c_void_p)]
        ole.StgOpenStorage.restype=c.c_long
        result=ole.StgOpenStorage(str(path),None,0x20,None,0,c.byref(storage))
        if result<0 or not storage:raise OSError(f'Не удалось прочитать структуру RVT: {result & 0xffffffff:08X}')
        result=method(storage,4,c.c_long,c.c_wchar_p,c.c_void_p,c.c_ulong,c.c_ulong,c.POINTER(c.c_void_p))(storage,'BasicFileInfo',None,0x10,0,c.byref(stream))
        if result<0 or not stream:raise OSError('В RVT нет BasicFileInfo')
        data=c.create_string_buffer(65537);count=c.c_ulong()
        result=method(stream,3,c.c_long,c.c_void_p,c.c_ulong,c.POINTER(c.c_ulong))(stream,data,len(data),c.byref(count))
        if result<0 or count.value>65536:raise OSError('Не удалось прочитать BasicFileInfo')
        return data.raw[:count.value]
    finally:
        if stream:method(stream,2,c.c_ulong)(stream)
        if storage:method(storage,2,c.c_ulong)(storage)
        if initialized:ole.CoUninitialize()


@functools.lru_cache(maxsize=32)
def _cached(path,size,modified,inode):
    try:
        result=parse_basic_info(_read_stream(path))
        stat=Path(path).stat()
        if (stat.st_size,stat.st_mtime_ns,stat.st_ino)!=(size,modified,inode):
            raise OSError('Файл изменился при чтении сведений')
        return {**result,'bytes':size,'error':''}
    except (OSError,ValueError) as error:
        return {'year':None,'bytes':size,'error':str(error)}


def file_info(path, *, refresh=False):
    if refresh:_cached.cache_clear()
    try:
        source=Path(path)
        if source.suffix.lower()!='.rvt':raise ValueError('Выберите RVT')
        stat=source.stat()
        return _cached(str(source.resolve()),stat.st_size,stat.st_mtime_ns,stat.st_ino)
    except (OSError,ValueError) as error:
        return {'year':None,'bytes':0,'error':str(error)}


def choose_revit(year,installed):
    if not year:return None
    eligible=[row for row in installed if row['year']>=year]
    if not eligible:return None
    nearest=min(row['year'] for row in eligible)
    return max((row for row in eligible if row['year']==nearest),key=lambda row:tuple(map(int,row['version'].split('.'))))
