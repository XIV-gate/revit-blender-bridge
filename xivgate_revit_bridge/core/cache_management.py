"""Explicit cache locations and conservative cleanup of owned jobs and RVTs."""
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

from .jobs import JOB_MARKER, FINISH_MARKER, job_identity

PREPARED_DIRECTORY = 'prepared_models'
PREPARED_ROOT_MARKER = '.agr_revit_bridge_prepared_root.json'
PREPARED_MARKER = '.agr_revit_bridge_prepared.json'
PREPARED_MODEL_FILE = 'model.rvt'
MAXIMUM_PREPARED_DESCRIPTOR_BYTES = 8 * 1024 * 1024
PREPARED_ROOT_IDENTITY = {'schema': 1, 'package_id': 'xivgate_revit_bridge',
                          'kind': 'prepared_model_cache'}


def _prepared_json(path):
    _guard(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Маркер подготовленной модели должен быть обычным файлом')
    with path.open('rb') as stream:
        content = stream.read(MAXIMUM_PREPARED_DESCRIPTOR_BYTES + 1)
    if len(content) > MAXIMUM_PREPARED_DESCRIPTOR_BYTES:
        raise ValueError('Слишком большой маркер подготовленной модели')
    value = json.loads(content.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Неверный маркер подготовленной модели')
    return value


def _prepared_root(root):
    directory = _guard(root / PREPARED_DIRECTORY)
    if directory.parent != root or not directory.is_dir():
        raise ValueError('Неверный каталог подготовленных моделей')
    value = _prepared_json(directory / PREPARED_ROOT_MARKER)
    if value != PREPARED_ROOT_IDENTITY or type(value.get('schema')) is not int:
        raise ValueError('Не подтверждён владелец кэша подготовленных моделей')
    return directory


def _prepared_size(root, directory):
    """Validate ready ownership and exact contents; never hash multi-GB RVTs here."""
    prepared_root = _prepared_root(root)
    directory = _guard(directory)
    if directory.parent != prepared_root or not directory.is_dir():
        raise ValueError('Подготовленная модель находится вне своего кэша')
    if uuid.UUID(directory.name).hex != directory.name:
        raise ValueError('Неверный идентификатор подготовленной модели')
    value = _prepared_json(directory / PREPARED_MARKER)
    if (type(value.get('schema')) is not int or value.get('schema') != 1
            or value.get('package_id') != 'xivgate_revit_bridge'
            or value.get('kind') != 'prepared_model'
            or value.get('entry_id') != directory.name
            or value.get('model_file') != PREPARED_MODEL_FILE):
        raise ValueError('Не подтверждён владелец подготовленной модели')
    if {path.name for path in directory.iterdir()} != {PREPARED_MODEL_FILE, PREPARED_MARKER}:
        raise ValueError('В подготовленной модели обнаружены неизвестные файлы')
    for name in (PREPARED_MODEL_FILE, PREPARED_MARKER):
        if not stat.S_ISREG(_guard(directory / name).lstat().st_mode):
            raise ValueError('В подготовленной модели обнаружен специальный файл')
    return _checked_size(root, directory)


def _guard(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Каталог кэша должен быть задан абсолютным путём')
    path = Path(os.path.abspath(path))
    for current in (path, *path.parents):
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or getattr(details, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Ссылки и junction в пути очистки кэша запрещены')
    return path


def project_cache(blend_path, override=''):
    """Return a location without creating anything, including for an unsaved scene."""
    if override:
        return _guard(override)
    if not blend_path:
        raise ValueError('Сохраните Blender-сцену или укажите отдельный каталог кэша')
    blend = _guard(blend_path)
    if blend.suffix.casefold() != '.blend' or not blend.is_file():
        raise ValueError('Для проектного кэша требуется сохранённый файл .blend')
    return _guard(blend.parent / 'Data' / 'Cache' / 'RevitBridge')


def _context(root, active_directory):
    root = _guard(root)
    if root.exists() and not root.is_dir():
        raise ValueError('Путь кэша занят файлом')
    active = _guard(active_directory) if active_directory is not None else None
    if active is not None and (active == root or not active.is_relative_to(root)):
        raise ValueError('Активное задание должно находиться внутри выбранного кэша')
    return root, active


def _marker(path, session_id):
    try:
        _guard(path)
        with path.open('rb') as stream:
            content = stream.read(4097)
        if len(content) > 4096:
            return False
        value = json.loads(content.decode('utf-8-sig'))
        return value == job_identity(session_id) and type(value.get('schema')) is int
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def _owned(root, directory, finished=False):
    directory = _guard(directory)
    if directory.parent != root or not directory.is_dir():
        return False
    try:
        if str(uuid.UUID(directory.name)) != directory.name:
            return False
    except ValueError:
        return False
    if not _marker(directory / JOB_MARKER, directory.name):
        return False
    return not finished or _marker(directory / FINISH_MARKER, directory.name)


def _checked_size(root, directory):
    """Fully preflight every descendant before any recursive deletion is allowed."""
    directory = _guard(directory)
    if directory == root or not directory.is_relative_to(root):
        raise ValueError('Удаляемое задание находится вне каталога кэша')
    total = 0
    pending = [directory]
    while pending:
        current = pending.pop()
        checked = _guard(current)
        if not checked.is_relative_to(directory):
            raise ValueError('Содержимое задания выходит за пределы его каталога')
        details = checked.lstat()
        if stat.S_ISDIR(details.st_mode):
            pending.extend(checked.iterdir())
        elif stat.S_ISREG(details.st_mode):
            total += details.st_size
        else:
            raise ValueError('В задании обнаружен специальный файл')
    return total


def _protected(directory, references):
    return any(directory == path or directory.is_relative_to(path) or path.is_relative_to(directory)
               for path in references)


def cache_summary(root, active_directory=None, protected_directories=()):
    """Count owned jobs and prepared RVTs; never traverse unknown trees."""
    root, active = _context(root, active_directory)
    protected = tuple(_guard(path) for path in protected_directories)
    summary = {'root': str(root), 'total_bytes': 0, 'eligible_bytes': 0, 'eligible_jobs': 0,
               'prepared_models': 0, 'prepared_bytes': 0, 'eligible_prepared_models': 0,
               'eligible_prepared_directories': [],
               'eligible_directories': [], 'errors': []}
    if not root.exists():
        return summary
    for directory in sorted(root.iterdir()):
        try:
            if directory.name == PREPARED_DIRECTORY:
                prepared_root = _prepared_root(root)
                for entry in sorted(prepared_root.iterdir()):
                    if entry.name == PREPARED_ROOT_MARKER:
                        continue
                    try:
                        size = _prepared_size(root, entry)
                        summary['prepared_models'] += 1
                        summary['prepared_bytes'] += size
                        summary['total_bytes'] += size
                        if not _protected(entry, protected) and not (active and _protected(entry, (active,))):
                            summary['eligible_bytes'] += size
                            summary['eligible_prepared_models'] += 1
                            summary['eligible_prepared_directories'].append(str(entry))
                    except (OSError, ValueError, TypeError, UnicodeError) as error:
                        summary['errors'].append(str(entry) + ': ' + str(error))
                continue
            if not _owned(root, directory):
                continue
            size = _checked_size(root, directory)
            summary['total_bytes'] += size
            # Preserve an explicitly active descendant too, even for an invalid old caller.
            is_active = active is not None and (active == directory or active.is_relative_to(directory))
            if not is_active and not _protected(directory, protected) and _owned(root, directory, finished=True):
                summary['eligible_bytes'] += size
                summary['eligible_jobs'] += 1
                summary['eligible_directories'].append(str(directory))
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            summary['errors'].append(str(directory) + ': ' + str(error))
    return summary


def clear_completed(root, active_directory=None, protected_directories=()):
    """Delete checked finished jobs and ready prepared RVTs after UI confirmation.

    Never reset/clean the whole root, follow links, delete standalone files, or
    reinterpret an unmarked directory as cache. The caller owns confirmation.
    """
    root, active = _context(root, active_directory)
    protected = tuple(_guard(path) for path in protected_directories)
    summary = cache_summary(root, active, protected)
    result = {'deleted_jobs': 0, 'deleted_models': 0, 'deleted_bytes': 0, 'errors': list(summary['errors'])}
    for raw in summary['eligible_directories']:
        directory = Path(raw)
        try:
            directory = _guard(directory)
            # Recheck after summary: ownership, finish state and contents can change.
            if not _owned(root, directory, finished=True):
                continue
            if active is not None and (active == directory or active.is_relative_to(directory)):
                continue
            if _protected(directory, protected):
                continue
            size = _checked_size(root, directory)
            if directory.parent != root or directory.resolve() != directory:
                raise ValueError('Не удалось доказать безопасный абсолютный путь удаления')
            if not _owned(root, directory, finished=True):
                continue
            shutil.rmtree(directory)
            result['deleted_jobs'] += 1
            result['deleted_bytes'] += size
        except (OSError, ValueError) as error:
            result['errors'].append(str(directory) + ': ' + str(error))
    for raw in summary['eligible_prepared_directories']:
        directory = Path(raw)
        try:
            size = _prepared_size(root, directory)
            if _protected(directory, protected) or (active and _protected(directory, (active,))):
                continue
            if directory.parent != root / PREPARED_DIRECTORY or directory.resolve() != directory:
                raise ValueError('Не удалось доказать безопасный абсолютный путь удаления')
            _prepared_size(root, directory)
            # A concurrent reader may lock the RVT on Windows. Leave its ready
            # marker intact if this first unlink fails so a later cleanup can retry.
            _guard(directory / PREPARED_MODEL_FILE).unlink()
            _guard(directory / PREPARED_MARKER).unlink()
            _guard(directory).rmdir()
            result['deleted_models'] += 1
            result['deleted_bytes'] += size
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            result['errors'].append(str(directory) + ': ' + str(error))
    return result
