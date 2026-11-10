"""Read actual source metadata without Revit; exercise automatic selection."""
import ctypes
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import bpy
sys.dont_write_bytecode=True
sys.path.insert(0,os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
from xivgate_revit_bridge.core.rvt_metadata import file_info,_cached
addon.register()
source=Path(os.environ['RBB_RVT_METADATA_FIXTURE'])
before=source.stat()
info=file_info(source)
assert info['year']==2019,info
results=[]
def mta():
    ole=ctypes.WinDLL('ole32');hr=ole.CoInitializeEx(None,0)
    try:
        _cached.cache_clear();results.append(file_info(source))
    finally:
        if hr in (0,1):ole.CoUninitialize()
worker=threading.Thread(target=mta);worker.start();worker.join()
assert results[0]['year']==2019,results
props=bpy.context.scene.xivgate_revit_bridge
props.source_rvt=str(source)
messages=[]
operator=SimpleNamespace(report=lambda severity,message:messages.append(message))
assert addon.ui.RBB_OT_auto_revit.execute(operator,bpy.context)=={'FINISHED'},messages
assert props.revit_exe and messages
assert (source.stat().st_mtime_ns,source.stat().st_size)==(before.st_mtime_ns,before.st_size)
addon.unregister()
print('RVT_METADATA_PASS',bpy.app.version_string,info['year'],results[0]['year'])
