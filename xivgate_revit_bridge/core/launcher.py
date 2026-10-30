"""Own one child process, never attach to an existing Revit process."""
import os
from pathlib import Path
import subprocess
from .jobs import contained_path


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
