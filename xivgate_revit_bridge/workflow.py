"""Blender project settings for the Early Access workflow; no profile writes."""
from pathlib import Path

import bpy

from .core.cache_management import project_cache, cache_summary, clear_completed
from .core.revit_versions import discover, inspect_revit


def cache_root(props):
    override = bpy.path.abspath(props.cache_directory) if props.cache_directory else ''
    return project_cache(bpy.data.filepath, override)


def prepare_settings(scene):
    props = scene.xivgate_revit_bridge
    if not props.revit_exe:
        found = discover()
        if not found:
            raise ValueError('Не найден Revit 2024–2027. Укажите Revit.exe в дополнительных настройках.')
        props.revit_exe = found[0]['path']
    source = Path(bpy.path.abspath(props.source_rvt))
    if not source.is_file() or source.suffix.casefold() != '.rvt':
        raise ValueError('Выберите существующий файл RVT')
    return cache_root(props)


def active_job():
    from . import runtime
    return runtime.STATE.session.directory if runtime.STATE.session else None


def cache_info(props):
    root = cache_root(props)
    active = active_job()
    active = active if active is not None and active.is_relative_to(root) else None
    return cache_summary(root, active_directory=active, protected_directories=referenced_jobs(root))


def referenced_jobs(root):
    """Preserve jobs supplying external images/libraries/cache files to any scene."""
    result = set()
    for value in bpy.utils.blend_paths(absolute=True, packed=True, local=False):
        path = Path(value).resolve()
        if path.is_relative_to(root):
            parts = path.relative_to(root).parts
            if parts:
                result.add(root / parts[0])
    # A packed image no longer exposes the raw export as an external blend path,
    # but that snapshot is still needed for fast, reversible instance regrouping.
    from .blender_import import OWNER, ROOT_FLAG
    for scene in bpy.data.scenes:
        props = getattr(scene, 'xivgate_revit_bridge', None)
        if not props or not props.import_id or not props.import_manifest_path:
            continue
        if not any(c.get(OWNER) == props.import_id and c.get(ROOT_FLAG) for c in scene.collection.children_recursive):
            continue
        path = Path(props.import_manifest_path).resolve()
        if path.is_relative_to(root):
            parts = path.relative_to(root).parts
            if parts:
                result.add(root / parts[0])
    return result


def clean_cache(props):
    from . import runtime
    if runtime.MAINTENANCE or runtime.STATE.action is not None:
        raise RuntimeError('Дождитесь завершения задания')
    root = cache_root(props)
    active = active_job()
    active = active if active is not None and active.is_relative_to(root) else None
    return clear_completed(root, active_directory=active, protected_directories=referenced_jobs(root))
