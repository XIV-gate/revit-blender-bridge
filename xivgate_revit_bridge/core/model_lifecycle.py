"""Independent model preparation flags and durable external texture retention."""
import json
from pathlib import Path
import shutil
from .cache_management import _guard, _owned
from .jobs import source_sha256, atomic_write_json


def preparation_options(props):
    return {name: bool(getattr(props, name, default)) for name, default in
            (("overwrite_source", False), ("reuse_prepared_model", True), ("keep_temporary_data", True))}


def preparation_key(props):
    return json.dumps({**preparation_options(props), "load_links": bool(getattr(props, "load_links", False))}, sort_keys=True)


def retain_texture(root, source):
    root, source = _guard(root), _guard(source)
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError("Текстура не принадлежит выбранной рабочей папке")
    parts = source.relative_to(root).parts
    if parts[0] == 'textures':
        return source
    if not _owned(root, root / parts[0]):
        raise ValueError("Не подтверждён владелец временной текстуры")
    digest = source_sha256(source)
    textures = _guard(root / 'textures')
    marker = textures / '.agr_revit_bridge_textures.json'
    identity = {'schema': 1, 'package_id': 'xivgate_revit_bridge', 'kind': 'retained_textures'}
    if textures.exists():
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')) != identity:
            raise ValueError("Папка textures не принадлежит аддону; очистка отменена")
    else:
        textures.mkdir()
        atomic_write_json(marker, identity)
    extension = source.suffix if source.suffix and len(source.suffix) <= 12 else '.img'
    target = _guard(textures / (digest + extension.lower()))
    if not target.exists():
        with source.open('rb') as reader, target.open('xb') as writer:
            shutil.copyfileobj(reader, writer)
    if source_sha256(target) != digest:
        raise ValueError("Сохранённая текстура не прошла проверку целостности")
    return target
