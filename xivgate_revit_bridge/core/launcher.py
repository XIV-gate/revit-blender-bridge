"""Own one child process, never attach to an existing Revit process."""
import os
from pathlib import Path
import subprocess
from .jobs import contained_path


def choose_main_window(windows, pid):
    """Fail closed: runtime notification windows are never a Revit main window."""
    candidates = []
    for item in windows:
        title = item['title'].strip().casefold()
        class_name = item['class_name'].casefold()
        if (item['pid'] != pid or item['tool'] or item['area'] <= 0
                or not (title == 'revit' or title.startswith('autodesk revit'))
                or any(token in class_name for token in ('notification', 'broadcast', 'notify', 'gdip'))):
            continue
        candidates.append(item)
    return max(candidates, key=lambda item: (item['title'].casefold().startswith('autodesk revit'), item['area']))['hwnd'] if candidates else None


def show_main_window(pid):
    """Only an explicit UI command may reveal the owned main window/dialog."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetLastActivePopup.argtypes = [wintypes.HWND]
    user32.GetLastActivePopup.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    windows = []
    def visit(hwnd, _):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid:
            title, name = ctypes.create_unicode_buffer(2048), ctypes.create_unicode_buffer(256)
            bounds = wintypes.RECT()
            user32.GetWindowTextW(hwnd, title, len(title))
            user32.GetClassNameW(hwnd, name, len(name))
            user32.GetWindowRect(hwnd, ctypes.byref(bounds))
            windows.append(dict(hwnd=hwnd, pid=pid, title=title.value, class_name=name.value,
                                area=max(0, bounds.right-bounds.left)*max(0, bounds.bottom-bounds.top),
                                tool=bool(user32.GetWindowLongW(hwnd, -20) & 0x80)))
        return True
    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    main = choose_main_window(windows, pid)
    if main is None:
        raise RuntimeError('Главное окно Revit пока не найдено. Дождитесь запуска; служебные окна не раскрываются.')
    user32.ShowWindow(main, 9 if user32.IsIconic(main) else 5)
    popup = user32.GetLastActivePopup(main)
    if popup and popup != main and user32.IsWindowVisible(popup):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(popup, ctypes.byref(owner_pid))
        if owner_pid.value == pid:
            user32.ShowWindow(popup, 9 if user32.IsIconic(popup) else 5)


class WorkerProcess:
    def __init__(self):
        self._process = None

    @property
    def pid(self):
        return self._process.pid if self._process is not None else None

    def start(self, revit_exe, session_directory, token, extra_env=None):
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError('Процесс Revit уже запущен этим сеансом')
        directory = Path(session_directory).resolve(strict=True)
        env = os.environ.copy()
        env.update(extra_env or {})
        env['XIVGATE_RBB_JOB_ROOT'] = str(directory)
        env['XIVGATE_RBB_SESSION_TOKEN'] = token
        temporary = contained_path(directory, 'tmp')
        temporary.mkdir(exist_ok=True)
        env['TEMP'] = str(temporary)
        env['TMP'] = str(temporary)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        kwargs = {}
        if os.name == 'nt':
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            kwargs.update(startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
        with (directory / 'worker.stdout.log').open('ab') as stdout, (directory / 'worker.stderr.log').open('ab') as stderr:
            self._process = subprocess.Popen([str(revit_exe)], cwd=str(directory), env=env,
                                             stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, **kwargs)
        return self.pid

    def poll(self):
        return self._process.poll() if self._process is not None else None

    def terminate(self):
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # Keep the unfinished job protected if the process has not exited.
