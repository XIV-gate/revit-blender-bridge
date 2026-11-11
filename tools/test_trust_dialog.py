"""Read-only detector exercised on owned hidden fixture windows, not Revit."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import bpy
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.core.launcher import pending_trust_dialog
user = ctypes.WinDLL('user32', use_last_error=True)
user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user.CreateWindowExW.restype = wintypes.HWND
user.DestroyWindow.argtypes = [wintypes.HWND]
user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
window = user.CreateWindowExW(0, 'STATIC', 'Security - Unsigned Add-In', 0, 0, 0, 100, 100, None, None, None, None)
assert window
control = user.CreateWindowExW(0, 'STATIC', 'XIVgate Revit Blender Bridge - XIVgate.RevitBridge.Loader.dll',
    0x40000000, 0, 0, 50, 50, window, None, None, None)
try:
    assert control
    assert pending_trust_dialog(os.getpid())
    assert not pending_trust_dialog(0)
    user.SetWindowTextW(control, 'OtherVendor.dll')
    assert not pending_trust_dialog(os.getpid())
    user.SetWindowTextW(control, 'XIVgate.RevitBridge.Loader.dll')
    user.SetWindowTextW(window, 'Безопасность — Надстройка без подписи')
    assert pending_trust_dialog(os.getpid())
finally:
    user.DestroyWindow(window)
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_TRUST_DIALOG passed ' + bpy.app.version_string)
