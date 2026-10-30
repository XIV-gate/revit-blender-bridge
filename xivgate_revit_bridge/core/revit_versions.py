"""Read installed executable metadata without launching Revit or loading its API."""
import json
import os
from pathlib import Path

SUPPORTED_YEARS = (2024, 2025, 2026, 2027)


def worker_key(version, runtime):
    try:
        parts = tuple(int(part) for part in version.split('.'))
        year = 2000 + parts[0]
    except (ValueError, AttributeError, IndexError):
        raise ValueError('Не удалось определить версию Revit') from None
    expected = {2024: (4,), 2025: (8, 10), 2026: (8, 10), 2027: (10,)}
    if year not in expected or runtime not in expected[year]:
        raise ValueError(f'Revit {year}, .NET {runtime} не поддерживается этой сборкой (2024–2027)')
    return str(year) + ('-net10' if year in (2025, 2026) and runtime == 10 else '')


def file_version(path):
    if os.name != 'nt':
        raise ValueError('Определение установленного Revit требует Windows')
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('version', use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ValueError('В Revit.exe нет сведений о версии')
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise OSError('Не удалось прочитать версию Revit.exe')
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not api.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise ValueError('Повреждены сведения о версии Revit.exe')
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if words[0] != 0xFEEF04BD:
        raise ValueError('Неверная сигнатура версии Revit.exe')
    high, low = words[2], words[3]
    return '.'.join(map(str, (high >> 16, high & 65535, low >> 16, low & 65535)))


def inspect_revit(path, *, version_reader=file_version):
    path = Path(path).resolve()
    if not path.is_file() or path.name.casefold() != 'revit.exe':
        raise ValueError('Укажите установленный Revit.exe версии 2024–2027')
    version = version_reader(path)
    parts = tuple(int(part) for part in version.split('.'))
    year = 2000 + parts[0]
    runtime = 4 if year == 2024 else 10 if year >= 2027 or (len(parts) > 1 and parts[1] >= 5) else 8
    config = path.with_suffix('.runtimeconfig.json')
    if config.is_file():
        value = json.loads(config.read_text(encoding='utf-8-sig'))['runtimeOptions']
        frameworks = value.get('frameworks', []) + ([value['framework']] if 'framework' in value else [])
        majors = [int(item['version'].split('.')[0]) for item in frameworks
                  if item.get('name') in ('Microsoft.NETCore.App', 'Microsoft.WindowsDesktop.App')]
        if majors:
            runtime = max(majors)
    key = worker_key(version, runtime)
    return dict(path=str(path), version=version, year=year, runtime=runtime, key=key)


def discover(roots=None, *, inspect=inspect_revit):
    if roots is None:
        roots = list(dict.fromkeys(value for value in (os.environ.get('ProgramW6432'), os.environ.get('PROGRAMFILES')) if value))
    found = {}
    for root in roots:
        for year in SUPPORTED_YEARS:
            path = Path(root) / 'Autodesk' / f'Revit {year}' / 'Revit.exe'
            if path.is_file():
                try:
                    info = inspect(path)
                    found[os.path.normcase(info['path'])] = info
                except (OSError, ValueError, KeyError):
                    continue
    return sorted(found.values(), key=lambda info: tuple(map(int, info['version'].split('.'))), reverse=True)
