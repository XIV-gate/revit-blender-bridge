"""Read-only OS/driver memory probes. Unknown values are never guessed."""
import csv
import ctypes
import io
import os
from pathlib import Path
import shutil
import subprocess


def parse_nvidia_memory(output):
    result = []
    for row in csv.reader(io.StringIO(output)):
        if len(row) != 5:
            continue
        values = []
        for raw in row[2:]:
            try:
                value = float(raw.strip())
                values.append(int(value*1024**2) if value >= 0 else None)
            except (ValueError, OverflowError):
                values.append(None)
        result.append(dict(id=row[0].strip(), name=row[1].strip(), source='nvidia-smi',
                           total_bytes=values[0], used_bytes=values[1], free_bytes=values[2]))
    return result


def gpu_memory():
    executable = shutil.which('nvidia-smi')
    if executable is None:
        candidate = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'nvidia-smi.exe'
        executable = str(candidate) if candidate.is_file() else None
    if executable:
        try:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run([executable, '--query-gpu=uuid,name,memory.total,memory.used,memory.free',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                    timeout=3, check=False, **options)
            if result.returncode == 0:
                rows = parse_nvidia_memory(result.stdout)
                if rows:
                    return dict(adapters=rows, reason='')
        except (OSError, subprocess.SubprocessError):
            pass
    return dict(adapters=[], reason='Свободная VRAM недоступна: драйвер не предоставил данные nvidia-smi. Не является нулём.')


def memory_snapshot(processes=None):
    result = dict(ram=None, processes=[], unavailable=[])
    if os.name != 'nt':
        result['unavailable'].append('Windows memory API unavailable')
        return result
    from ctypes import wintypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [(name, ctypes.c_ulonglong) for name in (
            'total_physical', 'available_physical', 'total_commit', 'available_commit', 'total_virtual', 'available_virtual', 'extended')]
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('page_faults', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
            'peak_working_set', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        result['ram'] = dict(total_bytes=status.total_physical, available_bytes=status.available_physical,
                             load_percent=status.load, commit_limit_bytes=status.total_commit,
                             commit_available_bytes=status.available_commit)
    else:
        result['unavailable'].append('GlobalMemoryStatusEx failed')
    for name, pid in (processes or {'blender': os.getpid()}).items():
        if not pid:
            continue
        handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if handle and psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                result['processes'].append(dict(name=name, pid=pid, working_set_bytes=counters.working_set,
                    peak_working_set_bytes=counters.peak_working_set, private_bytes=counters.private))
            else:
                result['unavailable'].append(name + ': process memory unavailable')
        finally:
            if handle:
                kernel.CloseHandle(handle)
    return result
