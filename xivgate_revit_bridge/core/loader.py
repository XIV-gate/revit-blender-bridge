"""Materialize a frozen entry point; never grant trust or replace a loaded DLL."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

KEYS = frozenset(('2024', '2025', '2025-net10', '2026', '2026-net10', '2027'))
PAYLOAD_FILE = 'core/loader_payload.py'
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')


def payload_record(raw, key, expected_hashes=None):
    """Read a generated literal, without executing its Python module."""
    if key not in KEYS:
        raise ValueError('Неизвестный обработчик Revit')
    try:
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Слишком большой комплект загрузчиков')
        tree = ast.parse(raw.decode('utf-8'))
        if (len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign)
                or len(tree.body[0].targets) != 1
                or not isinstance(tree.body[0].targets[0], ast.Name)
                or tree.body[0].targets[0].id != 'PAYLOAD'):
            raise ValueError('Ожидались только данные загрузчика')
        payload = ast.literal_eval(tree.body[0].value)
        if (not isinstance(payload, dict) or type(payload.get('schema')) is not int or payload['schema'] != 1
                or type(payload.get('abi')) is not int or payload['abi'] != 1
                or not isinstance(payload.get('loaders'), dict)
                or set(payload['loaders']) - KEYS):
            raise ValueError('Неизвестный формат загрузчика')
        if expected_hashes is not None and set(payload['loaders']) != set(expected_hashes):
            raise ValueError('Перечень загрузчиков отличается от манифеста комплекта')
        record = payload['loaders'][key]
        if not isinstance(record, dict) or set(record) != {'data', 'sha256'}:
            raise ValueError('Неполные данные загрузчика')
        digest = record['sha256']
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError('Неверная контрольная сумма загрузчика')
        if expected_hashes is not None and digest != expected_hashes[key]:
            raise ValueError('Загрузчик отличается от манифеста комплекта')
        data = base64.b64decode(record['data'], validate=True)
        if (not data.startswith(b'MZ') or len(data) > 1024 * 1024
                or hashlib.sha256(data).hexdigest() != digest):
            raise ValueError('Загрузчик повреждён')
        return f'runtime/revit/{key}/loader/1/XIVgate.RevitBridge.Loader.dll', data, digest
    except (KeyError, TypeError, SyntaxError, UnicodeError) as error:
        raise ValueError('Не удалось прочитать комплект загрузчиков') from error


def _payload(root):
    from .updates import _reject_links
    path = _reject_links(root / PAYLOAD_FILE)
    if not path.is_file():
        if path.exists():
            raise ValueError('Ресурс загрузчиков должен быть файлом')
        control_path = _reject_links(root / 'PACKAGE_MANIFEST.json')
        if control_path.is_file():
            try:
                control = json.loads(control_path.read_text(encoding='utf-8-sig'))
                if (not isinstance(control, dict) or control.get('schema') != 1
                        or control.get('id') != 'xivgate_revit_bridge' or not isinstance(control.get('files'), dict)):
                    raise ValueError('Неизвестный манифест установленного дополнения')
                if 'loader_payloads' in control or PAYLOAD_FILE in control['files']:
                    raise ValueError('В установленном комплекте отсутствует обязательный ресурс загрузчиков; переустановите ZIP')
            except (OSError, TypeError, json.JSONDecodeError) as error:
                raise ValueError('Не удалось проверить обязательные файлы комплекта') from error
        return None
    raw = path.read_bytes()
    try:
        control = json.loads(_reject_links(root / 'PACKAGE_MANIFEST.json').read_text(encoding='utf-8-sig'))
        if (control.get('schema') != 1 or control.get('id') != 'xivgate_revit_bridge'
                or control['files'][PAYLOAD_FILE] != hashlib.sha256(raw).hexdigest()):
            raise ValueError('Ресурс загрузчиков отличается от установленного комплекта')
        metadata = control['loader_payloads']
        if (not isinstance(metadata, dict) or type(metadata.get('abi')) is not int or metadata['abi'] != 1
                or not isinstance(metadata.get('sha256'), dict) or not metadata['sha256']):
            raise ValueError('Неизвестный комплект постоянных загрузчиков')
        for key in metadata['sha256']:
            payload_record(raw, key, metadata['sha256'])
        return raw
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('Не удалось проверить ресурс загрузчиков') from error


def ensure_loader(addon_root, key):
    """Exclusive atomic extraction; existing loader bytes are immutable."""
    from .updates import _reject_links
    root = _reject_links(addon_root)
    if not root.is_dir():
        raise ValueError('Каталог дополнения не найден')
    raw = _payload(root)
    if raw is None:
        return None  # Old packages and unbundled source retain their old entry.
    name, data, digest = payload_record(raw, key)
    target = _reject_links(root / name)

    def matches():
        _reject_links(target)
        return target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest

    if target.exists():
        if not matches():
            raise ValueError('Постоянный загрузчик отличается от комплекта; перезапись запрещена')
        return target
    for parent in reversed(target.parents):
        if parent == root or not parent.is_relative_to(root):
            continue
        _reject_links(parent)
        parent.mkdir(exist_ok=True)
        _reject_links(parent)
    temporary = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.pending')
    try:
        _reject_links(temporary)
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_links(target)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not matches():
                raise ValueError('Путь постоянного загрузчика занят другим файлом') from None
        if not matches():
            raise ValueError('Не удалось проверить постоянный загрузчик')
        return target
    finally:
        temporary.unlink(missing_ok=True)


def launch_environment(addon_root, key, version):
    """Pin the worker to this verified package, not to an arbitrary caller path."""
    from .updates import _reject_links
    root = _reject_links(addon_root)
    raw = _payload(root)
    if raw is None:
        return {}
    payload_record(raw, key)
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError('Неверная версия обработчика')
    try:
        control = json.loads(_reject_links(root / 'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
        name = f'runtime/revit/{key}/{version}/XIVgate.RevitBridge.Worker.dll'
        if (control.get('schema') != 1 or control.get('id') != 'xivgate_revit_bridge'
                or control.get('version') != version or control['workers'].get(key) != name):
            raise ValueError('Обработчик не соответствует установленному комплекту')
        expected = control['files'][name]
        worker = _reject_links(root / name)
        if (not isinstance(expected, str) or not _SHA256.fullmatch(expected)
                or hashlib.sha256(worker.read_bytes()).hexdigest() != expected):
            raise ValueError('Контрольная сумма обработчика не соответствует комплекту')
        return {'XIVGATE_RBB_WORKER_VERSION': version, 'XIVGATE_RBB_WORKER_SHA256': expected}
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('Не удалось проверить обработчик из установленного комплекта') from error
