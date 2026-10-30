"""Verified reuse of job-owned export files; never modify original RVT inputs.

The caller supplies an existing cache root through ``indexpath.parent``. Only
the latest explicit signature is indexed. Materials are inline in manifest.json;
the actual export schema references binary_file, base_texture and textures maps.
No files are copied, extracted, removed, or discovered by guessed path fields.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .jobs import atomic_write_json, JOB_MARKER, job_identity, read_json
from ..geometry_cache import matrix_rows

_HASH = re.compile(r'[0-9a-f]{64}\Z')
_MAX_INDEX_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024 * 1024
_KIND = 'xivgate_export_cache'


class CacheError(RuntimeError):
    """The export cannot be recorded or safely reused."""


def _safe_path(value):
    path = Path(os.path.abspath(value))
    for item in (path, *path.parents):
        try:
            attributes = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & 0x400:
            raise CacheError('Ссылки и junction в пути кэша запрещены')
    return path


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or any(character in value for character in ':*?"<>|\x00')):
        raise CacheError('Путь файла кэша должен быть относительным и каноническим')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in value.split('/')):
        raise CacheError('Путь файла выходит за пределы кэша или неканоничен')
    return value


def _contained(root, relative):
    _relative(relative)
    target = _safe_path(root.joinpath(*relative.split('/')))
    if not target.is_relative_to(root):
        raise CacheError('Файл находится вне каталога кэша')
    return target


def _context(indexpath):
    index = _safe_path(indexpath)
    if index.suffix.casefold() != '.json' or not index.parent.is_dir():
        raise CacheError('Индекс JSON должен находиться в существующем каталоге кэша')
    return index, index.parent


def _signature(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise CacheError('Не передана явная контрольная подпись экспорта')
    return value


def _fingerprint(path):
    """Hash in bounded chunks and reject a file changing during the read."""
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise CacheError('Ожидался обычный файл')
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
    after = path.stat()
    if (size != before.st_size or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ino != after.st_ino):
        raise CacheError('Файл изменился во время проверки кэша')
    return {'sha256': digest.hexdigest(), 'size': size}


def export_signature(source_path, selection_ids, detail_level, worker_key, worker_version):
    """Content identity plus the complete selection and geometry-affecting options."""
    try:
        if detail_level not in ('coarse', 'medium', 'fine') or not isinstance(worker_key, str) or not worker_key:
            raise CacheError('Не указаны точные параметры экспорта')
        if not isinstance(worker_version, str) or not worker_version.strip():
            raise CacheError('Не указана версия Revit worker')
        identity = {'schema': 1, 'source_path': os.path.normcase(os.path.abspath(source_path)),
                    'source_sha256': _fingerprint(Path(source_path))['sha256'],
                    'selection_ids': sorted({str(value) for value in selection_ids}),
                    'detail_level': detail_level, 'worker_key': worker_key, 'worker_version': worker_version}
        data = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(data).hexdigest()
    except (OSError, TypeError, ValueError) as error:
        raise CacheError('Не удалось определить подпись исходной модели: ' + str(error)) from error


def _json(path, limit):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise CacheError('Повторяющийся ключ в JSON кэша')
            result[key] = value
        return result
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CacheError('JSON кэша превышает допустимый размер')
    value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise CacheError('Неверный JSON кэша')
    return value, hashlib.sha256(data).hexdigest()


def _verify_dependencies(value):
    """Verify declared original bitmaps read-only, even outside the cache root."""
    dependencies = value.get('cache_dependencies')
    if value.get('cache_reusable') is not True or not isinstance(dependencies, list):
        raise CacheError('Worker не подтвердил возможность повторного использования экспорта')
    paths = set()
    for dependency in dependencies:
        if (not isinstance(dependency, dict) or not isinstance(dependency.get('path'), str)
                or not Path(dependency['path']).is_absolute()
                or not isinstance(dependency.get('sha256'), str) or not _HASH.fullmatch(dependency['sha256'])
                or type(dependency.get('size')) is not int or dependency['size'] < 0):
            raise CacheError('Неверное описание внешней зависимости экспорта')
        path = _safe_path(dependency['path'])
        if path in paths:
            raise CacheError('Повторяющаяся внешняя зависимость экспорта')
        paths.add(path)
        expected = {'sha256': dependency['sha256'], 'size': dependency['size']}
        if _fingerprint(path) != expected:
            raise CacheError('Исходная текстура изменилась после экспорта')
    return paths


def _payloads(root, manifest, *, verify_dependencies=True):
    manifest = _safe_path(manifest)
    if not manifest.is_relative_to(root) or not manifest.is_file():
        raise CacheError('Манифест должен находиться внутри каталога кэша')
    value, manifest_hash = _json(manifest, _MAX_MANIFEST_BYTES)
    if type(value.get('schema')) is not int or value['schema'] != 1 or value.get('units') != 'metres' or value.get('up', 'Z') != 'Z':
        raise CacheError('Неподдерживаемый манифест геометрии')
    dependencies = _verify_dependencies(value) if verify_dependencies else set()
    prototypes = value.get('prototypes')
    occurrences = value.get('occurrences')
    materials = value.get('materials')
    if (not isinstance(prototypes, list) or not prototypes or not isinstance(occurrences, list) or not occurrences
            or not isinstance(materials, dict) or any(not isinstance(item, dict) for item in materials.values())):
        raise CacheError('Манифест не содержит проверяемых геометрии и материалов')
    binary = _contained(manifest.parent, value.get('binary_file', 'geometry.bin'))
    paths = {manifest, binary}
    for material in materials.values():
        textures = material.get('textures', {})
        if not isinstance(textures, dict):
            raise CacheError('Неверный перечень текстур материала')
        for texture_path in textures.values():
            paths.add(_contained(manifest.parent, texture_path))
        texture = material.get('base_texture')
        if texture is not None and texture != '':
            paths.add(_contained(manifest.parent, texture))
    if any(not path.is_file() for path in paths):
        raise CacheError('Не найдены файлы геометрии или текстур')
    prototype_ids = set()
    with binary.open('rb') as stream:
        binary_size = binary.stat().st_size
        for prototype in prototypes:
            if not isinstance(prototype, dict):
                raise CacheError('Неверное описание прототипа')
            identifier = prototype.get('id')
            offset, length, triangles = (prototype.get(key) for key in ('offset', 'bytes', 'triangles'))
            if (type(identifier) is not int or identifier in prototype_ids
                    or any(type(number) is not int or number < 0 for number in (offset, length, triangles))
                    or length != triangles * 104 or offset + length > binary_size):
                raise CacheError('Повреждён буфер прототипа')
            prototype_ids.add(identifier)
            expected = prototype.get('sha256')
            if not isinstance(expected, str) or not _HASH.fullmatch(expected.lower()):
                raise CacheError('Отсутствует контрольная сумма прототипа')
            digest = hashlib.sha256()
            stream.seek(offset)
            remaining = length
            while remaining:
                block = stream.read(min(remaining, 1024 * 1024))
                if not block:
                    raise CacheError('Буфер геометрии обрезан')
                digest.update(block)
                remaining -= len(block)
            if digest.hexdigest() != expected.lower():
                raise CacheError('Контрольная сумма прототипа не совпадает')
    identifiers = set()
    for occurrence in occurrences:
        if (not isinstance(occurrence, dict) or not isinstance(occurrence.get('id'), str)
                or occurrence['id'] in identifiers or occurrence.get('prototype') not in prototype_ids):
            raise CacheError('Повреждены размещения модели')
        identifiers.add(occurrence['id'])
        matrix_rows(occurrence['transform'])
    return paths, manifest_hash, dependencies


def _owned_index(value):
    if type(value.get('schema')) is not int or value['schema'] != 1 or value.get('kind') != _KIND:
        raise CacheError('Индекс не принадлежит кэшу экспорта AGR')
    _signature(value.get('signature'))
    _relative(value.get('manifest'))
    if not isinstance(value.get('files'), dict) or not value['files']:
        raise CacheError('Индекс кэша не содержит файлов')


def record_export(indexpath, signature, manifestpath):
    """Verify an export and atomically replace only the owned latest-entry index."""
    try:
        _signature(signature)
        index, root = _context(indexpath)
        if index.exists():
            prior, _ = _json(index, _MAX_INDEX_BYTES)
            _owned_index(prior)
        manifest = _safe_path(manifestpath)
        paths, manifest_hash, dependencies = _payloads(root, manifest)
        if index in paths or index in dependencies:
            raise CacheError('Индекс не должен заменять файл экспорта')
        files = {path.relative_to(root).as_posix(): _fingerprint(path) for path in sorted(paths)}
        manifest_relative = manifest.relative_to(root).as_posix()
        if files[manifest_relative]['sha256'] != manifest_hash:
            raise CacheError('Манифест изменился во время проверки')
        value = {'schema': 1, 'kind': _KIND, 'signature': signature, 'manifest': manifest_relative, 'files': files}
        if len(json.dumps(value).encode('utf-8')) > _MAX_INDEX_BYTES:
            raise CacheError('Индекс кэша слишком велик')
        atomic_write_json(index, value)
        return index
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        raise CacheError('Не удалось сохранить кэш экспорта: ' + str(error)) from error


def lookup_export(indexpath, signature):
    """A miss is harmless: malformed, escaped, stale or changed entries return None."""
    try:
        _signature(signature)
        index, root = _context(indexpath)
        entry, _ = _json(index, _MAX_INDEX_BYTES)
        _owned_index(entry)
        if entry['signature'] != signature:
            return None
        manifest = _contained(root, entry['manifest'])
        paths, manifest_hash, dependencies = _payloads(root, manifest)
        expected_paths = {path.relative_to(root).as_posix() for path in paths}
        if set(entry['files']) != expected_paths or index in paths or index in dependencies:
            return None
        for relative, expected in entry['files'].items():
            if (not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] < 0
                    or not isinstance(expected.get('sha256'), str) or not _HASH.fullmatch(expected['sha256'])):
                return None
            if _fingerprint(_contained(root, relative)) != expected:
                return None
        if entry['files'][entry['manifest']]['sha256'] != manifest_hash:
            return None
        return manifest
    except (CacheError, OSError, ValueError, TypeError, KeyError, UnicodeError):
        return None


def lookup_snapshot(cache_root, manifest_path, source_sha256):
    """Validate the current import's owned snapshot, without reading original assets.

    The caller must match its source/options/selection to the successful import.
    This does not make an export eligible for the general reusable-cache index.
    """
    try:
        _signature(source_sha256)
        if not Path(cache_root).is_absolute() or not Path(manifest_path).is_absolute():
            return None
        root = _safe_path(cache_root)
        candidate = Path(os.path.abspath(manifest_path))
        if not root.is_dir() or not candidate.is_relative_to(root):
            return None
        parts = candidate.relative_to(root).parts
        if (len(parts) != 4 or parts[1] != 'output' or parts[3] != 'manifest.json'
                or any(str(uuid.UUID(value)) != value for value in (parts[0], parts[2]))):
            return None
        manifest = _safe_path(candidate)
        marker = _safe_path(root / parts[0] / JOB_MARKER)
        if marker.stat().st_size > 4096:
            return None
        identity = read_json(marker)
        if identity != job_identity(parts[0]) or type(identity.get('schema')) is not int:
            return None
        value, manifest_hash = _json(manifest, _MAX_MANIFEST_BYTES)
        source = value.get('source')
        if not isinstance(source, dict) or source.get('sha256') != source_sha256:
            return None
        paths, validated_hash, _ = _payloads(root, manifest, verify_dependencies=False)
        if validated_hash != manifest_hash:
            return None
        # Worker binaries are a complete contiguous sequence of prototypes.
        # Reject appended/unused bytes too, rather than checking referenced slices only.
        position = 0
        for prototype in sorted(value['prototypes'], key=lambda item: item['offset']):
            if prototype['offset'] != position:
                return None
            position += prototype['bytes']
        binary = _contained(manifest.parent, value.get('binary_file', 'geometry.bin'))
        if binary.stat().st_size != position:
            return None
        for path in paths:
            fingerprint = _fingerprint(path)
            if path == manifest and fingerprint['sha256'] != manifest_hash:
                return None
            # Production copied textures use their SHA256 as the filename.
            if _HASH.fullmatch(path.stem) and fingerprint['sha256'] != path.stem:
                return None
        return manifest
    except (CacheError, OSError, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        return None
