"""Immutable, per-user bridge runtime independent of Blender and model caches."""
import hashlib
import json
import os
from pathlib import Path
import uuid
from .loader import _payload, payload_record, launch_environment


def store_root():
    value = os.environ.get('LOCALAPPDATA')
    if not value or not Path(value).is_absolute():
        raise ValueError('Не найден локальный каталог пользователя для загрузчика Revit')
    return Path(value) / 'XIVgate' / 'RevitBridge'


def _publish(path, data):
    from .updates import _reject_links
    path = _reject_links(path)
    expected = hashlib.sha256(data).hexdigest()
    def matches():
        _reject_links(path)
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
    if path.exists():
        if not matches():
            raise ValueError('Постоянный модуль Revit отличается от комплекта; перезапись запрещена: ' + str(path))
        return
    # Re-check every ancestor: no user-controlled junctions or symlinks.
    for parent in reversed(path.parents):
        _reject_links(parent)
        if not parent.exists(): parent.mkdir()
        _reject_links(parent)
    pending = path.with_name('.' + uuid.uuid4().hex + '.tmp')
    try:
        with pending.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try: os.link(pending, path)
        except FileExistsError:
            if not matches(): raise ValueError('Путь постоянного модуля занят другим файлом') from None
        if not matches(): raise ValueError('Не удалось проверить постоянный модуль Revit')
    finally:
        pending.unlink(missing_ok=True)


def shared_entry(addon_root, key):
    raw = _payload(Path(addon_root))
    if raw is None: return None
    relative, _, _ = payload_record(raw, key)
    return store_root() / relative


def verified_shared_entry(assembly, addon_root):
    """Read-only registration guard; arbitrary external DLLs never qualify."""
    from .updates import _reject_links
    try:
        root = _reject_links(store_root())
        path = _reject_links(assembly)
        relative = path.relative_to(root).as_posix()
        parts = relative.split('/')
        if len(parts) != 6 or parts[:2] != ['runtime', 'revit']: return False
        raw = _payload(Path(addon_root))
        if raw is None: return False
        expected, _, digest = payload_record(raw, parts[2])
        return relative == expected and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except (OSError, ValueError, TypeError):
        return False


def ensure_shared_runtime(addon_root, key):
    """Copy only package-verified worker dependencies, then publish frozen entry."""
    from .updates import _reject_links, _relative
    root = _reject_links(addon_root)
    raw = _payload(root)
    if raw is None: return None
    relative, data, _ = payload_record(raw, key)
    control = json.loads(_reject_links(root / 'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
    version = control['version']
    # This validates canonical version, selected worker path and worker SHA.
    launch_environment(root, key, version)
    worker = control['workers'][key]
    prefix = f'runtime/revit/{key}/{version}/'
    names = [worker] + [name for name in control.get('runtime_files', []) if name.startswith(prefix)]
    destination = _reject_links(store_root())
    for name in names:
        _relative(name)
        if not name.startswith(prefix): raise ValueError('Неверный путь рабочего модуля')
        source = _reject_links(root / name)
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != control['files'][name]:
            raise ValueError('Рабочий модуль Revit повреждён: ' + name)
        _publish(destination / name, content)
    entry = destination / relative
    _publish(entry, data)
    return entry


def capture_shared_registration(content, addon_root):
    """Freeze verified old identity before a package downgrade removes its payload."""
    from .installer import _owned_manifest
    path = _owned_manifest(content)
    if path is None or not verified_shared_entry(path, addon_root): return None
    return (str(path), hashlib.sha256(path.read_bytes()).hexdigest())
