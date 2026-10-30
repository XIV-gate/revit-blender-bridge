"""Verified GitHub packages and reversible local updates, without host imports."""
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
import zipfile

from .installer import MANIFEST_NAME, _owned_manifest, install_manifest, manifest_matches

REPOSITORY = 'XIV-gate/revit-blender-bridge'
PACKAGE_ID = 'xivgate_revit_bridge'
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_FILES = 2048
TIMEOUT = 20
_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_CONTROL = 'PACKAGE_MANIFEST.json'
_CDN_HOSTS = frozenset(('github.com', 'release-assets.githubusercontent.com',
                        'objects.githubusercontent.com', 'github-releases.githubusercontent.com'))
_NET48_DEPENDENCIES = frozenset((
    'System.Text.Json.dll', 'System.Text.Encodings.Web.dll', 'Microsoft.Bcl.AsyncInterfaces.dll',
    'System.Memory.dll', 'System.Buffers.dll', 'System.Runtime.CompilerServices.Unsafe.dll',
    'System.Threading.Tasks.Extensions.dll', 'System.Numerics.Vectors.dll',
))


class UpdateError(RuntimeError):
    """A recoverable update failure suitable for the add-on's status message."""


def _version(value):
    if isinstance(value, (tuple, list)) and len(value) == 3:
        value = '.'.join(str(item) for item in value)
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise UpdateError('Требуется стабильная версия X.Y.Z')
    return tuple(int(item) for item in value.split('.'))


def _asset_name(version):
    _version(version)
    return 'agr_revit_bridge-' + version + '.zip'


def _asset_url(version):
    return f'https://github.com/{REPOSITORY}/releases/download/v{version}/{_asset_name(version)}'


def _release_info(payload, current_version, allow_early=False):
    """Validate the same repository, asset and digest rules for either channel."""
    try:
        if (not isinstance(payload, dict) or payload.get('draft') is not False
                or type(payload.get('prerelease')) is not bool
                or (payload['prerelease'] and not allow_early)):
            raise UpdateError('Релиз не опубликован в разрешённом канале')
        tag = payload['tag_name']
        if (not isinstance(tag, str) or not tag.startswith('v')
                or not isinstance(payload.get('published_at'), str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', payload['published_at'])):
            raise UpdateError('Отсутствует стабильный опубликованный тег')
        version = tag[1:]
        newer = _version(version) > _version(current_version)
        release_url = f'https://github.com/{REPOSITORY}/releases/tag/{tag}'
        if payload.get('html_url') != release_url:
            raise UpdateError('Релиз принадлежит другому репозиторию')
        assets = [item for item in payload['assets'] if isinstance(item, dict) and item.get('name') == _asset_name(version)]
        if len(assets) != 1:
            raise UpdateError('В релизе нет единственного установочного ZIP AGR')
        asset = assets[0]
        digest = asset.get('digest', '')
        size = asset.get('size')
        if not isinstance(digest, str) or not digest.startswith('sha256:') or not _SHA256.fullmatch(digest[7:]):
            raise UpdateError('GitHub не предоставил проверяемый SHA256 архива')
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES:
            raise UpdateError('Недопустимый размер архива обновления')
        if asset.get('state') != 'uploaded' or asset.get('browser_download_url') != _asset_url(version):
            raise UpdateError('Недопустимый адрес или состояние архива обновления')
        return dict(version=version, available=newer, asset_name=asset['name'], url=asset['browser_download_url'],
                    sha256=digest[7:], size=size, release_url=release_url)
    except (KeyError, TypeError, ValueError) as error:
        raise UpdateError('GitHub вернул неверное описание релиза') from error


def release_info(payload, current_version):
    """Legacy stable-only latest-release API; never offers an automatic downgrade."""
    return _release_info(payload, current_version)


def release_catalog(payload, current_version):
    """Sorted verified stable/early choices; a downgrade requires explicit selection.

    Invalid entries are omitted, including drafts, nonnumeric tags and assets
    lacking a trusted SHA256. Repeated versions from overlapping pages collapse
    to the first response entry. available retains its legacy 'newer' meaning;
    installable also permits older versions but never the running version.
    """
    current = _version(current_version)
    if not isinstance(payload, list):
        raise UpdateError('GitHub вернул неверный список релизов')
    versions = {}
    for item in payload:
        try:
            info = _release_info(item, current_version, allow_early=True)
        except UpdateError:
            continue
        info['channel'] = 'early' if item['prerelease'] else 'stable'
        info['installable'] = _version(info['version']) != current
        versions.setdefault(info['version'], info)
    return sorted(versions.values(), key=lambda info: _version(info['version']), reverse=True)


def _trusted_url(url, asset):
    parsed = urllib.parse.urlsplit(url)
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        valid_port = False
    hosts = _CDN_HOSTS if asset else frozenset(('api.github.com',))
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username is not None
            or parsed.password is not None or not valid_port or parsed.fragment):
        raise UpdateError('Заблокировано перенаправление за пределы доверенных HTTPS-адресов GitHub')


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, asset=False):
        super().__init__()
        self.asset = asset

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_url(newurl, self.asset)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(request, asset=False):
    _trusted_url(request.full_url, asset)
    response = urllib.request.build_opener(_SafeRedirect(asset)).open(request, timeout=TIMEOUT)
    try:
        _trusted_url(response.geturl(), asset)
    except Exception:
        response.close()
        raise
    return response


def _network_error(error):
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return UpdateError('GitHub: репозиторий закрыт или опубликованных релизов пока нет')
        if error.code in (403, 429):
            return UpdateError('GitHub ограничил частоту запросов; повторите проверку позже')
        return UpdateError('Ошибка GitHub HTTP ' + str(error.code))
    return UpdateError('Не удалось связаться с GitHub; проверьте подключение и повторите позже')


def fetch_release(current_version):
    _version(current_version)
    request = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/releases/latest', headers={
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'AGR-Revit-Bridge'})
    try:
        with _open_url(request) as response:
            data = response.read(MAX_METADATA_BYTES + 1)
        if len(data) > MAX_METADATA_BYTES:
            raise UpdateError('Описание релиза превышает допустимый размер')
        return release_info(json.loads(data.decode('utf-8')), current_version)
    except (urllib.error.URLError, OSError) as error:
        raise _network_error(error) from error
    except (ValueError, UnicodeError) as error:
        raise UpdateError('GitHub вернул повреждённое описание релиза') from error


def fetch_releases(current_version):
    """Fetch at most three 100-release pages and 2 MiB of metadata in total."""
    _version(current_version)
    payload = []
    consumed = 0
    try:
        for page in range(1, 4):
            request = urllib.request.Request(
                f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100&page={page}', headers={
                    'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                    'User-Agent': 'AGR-Revit-Bridge'})
            with _open_url(request) as response:
                data = response.read(MAX_METADATA_BYTES - consumed + 1)
            consumed += len(data)
            if consumed > MAX_METADATA_BYTES:
                raise UpdateError('Список релизов превышает допустимый размер')
            items = json.loads(data.decode('utf-8'))
            if not isinstance(items, list) or len(items) > 100:
                raise UpdateError('GitHub вернул неверную страницу релизов')
            payload.extend(items)
            if len(items) < 100:
                break
        return release_catalog(payload, current_version)
    except (urllib.error.URLError, OSError) as error:
        raise _network_error(error) from error
    except (ValueError, UnicodeError) as error:
        raise UpdateError('GitHub вернул повреждённый список релизов') from error


def download_release(info, existing_directory):
    """Download exclusively, enforce the advertised size and digest, remove partials."""
    try:
        version = info['version']
        if info['asset_name'] != _asset_name(version) or info['url'] != _asset_url(version):
            raise UpdateError('Адрес архива не соответствует релизу AGR')
        if not _SHA256.fullmatch(info['sha256']) or type(info['size']) is not int or not 0 < info['size'] <= MAX_ARCHIVE_BYTES:
            raise UpdateError('Неверная контрольная сумма или размер архива')
        if info.get('installable', info.get('available')) is not True:
            raise UpdateError('Другая версия для установки не выбрана')
        directory = _safe_directory(existing_directory)
        target = directory / info['asset_name']
        request = urllib.request.Request(info['url'], headers={'User-Agent': 'AGR-Revit-Bridge', 'Accept': 'application/octet-stream'})
        created = False
        try:
            with target.open('xb') as output:
                created = True
                digest = hashlib.sha256()
                count = 0
                with _open_url(request, asset=True) as response:
                    while True:
                        block = response.read(min(65536, info['size'] - count + 1))
                        if not block:
                            break
                        count += len(block)
                        if count > info['size'] or count > MAX_ARCHIVE_BYTES:
                            raise UpdateError('Скачанный архив превышает заявленный размер')
                        digest.update(block)
                        output.write(block)
                if count != info['size'] or digest.hexdigest() != info['sha256']:
                    raise UpdateError('Размер или SHA256 скачанного архива не совпадает с GitHub')
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        return target
    except (KeyError, TypeError, ValueError) as error:
        raise UpdateError('Неверное описание загрузки') from error
    except (urllib.error.URLError, OSError) as error:
        raise _network_error(error) from error


def _relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or any(char in name for char in ':*?"<>|')):
        raise UpdateError('Недопустимый путь в пакете')
    parts = name.split('/')
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'{prefix}{i}' for prefix in ('com', 'lpt') for i in range(1, 10)}
    if any(part in ('', '.', '..') or part.casefold() in ('.git', '__pycache__')
           or part.endswith((' ', '.')) or part.split('.')[0].casefold() in reserved for part in parts):
        raise UpdateError('Неканонический путь в пакете: ' + name)
    return PurePosixPath(name)


@dataclass(frozen=True)
class Package:
    files: dict
    manifest: dict


def _json_object(data):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise UpdateError('Повторяющийся ключ в манифесте пакета')
            result[key] = value
        return result
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)


def _worker_paths(manifest, version):
    legacy = f'runtime/revit/2027/{version}/XIVgate.RevitBridge.Worker.dll'
    if manifest.get('worker') != legacy:
        raise UpdateError('Сборка Revit 2027 должна находиться в каталоге своей версии')
    workers = manifest.get('workers', {'2027': legacy})
    allowed = frozenset(('2024', '2025', '2025-net10', '2026', '2026-net10', '2027'))
    if not isinstance(workers, dict) or not workers or workers.get('2027') != legacy or set(workers) - allowed:
        raise UpdateError('Неверный перечень сборок Revit')
    for key, path in workers.items():
        if path != f'runtime/revit/{key}/{version}/XIVgate.RevitBridge.Worker.dll':
            raise UpdateError('Каждая сборка Revit должна находиться в каталоге своей версии')
    return frozenset(workers.values())


def _runtime_paths(manifest, version):
    """Declared workers plus narrowly allowed version-owned support payloads."""
    workers = _worker_paths(manifest, version)
    extra = manifest.get('runtime_files', [])
    if not isinstance(extra, list) or any(not isinstance(path, str) for path in extra) or len(extra) != len(set(extra)):
        raise UpdateError('Неверный перечень файлов runtime')
    paths = set(workers)
    worker_keys = manifest.get('workers', {'2027': manifest['worker']})
    for path in extra:
        _relative(path)
        if path in workers:
            paths.add(path)
            continue
        parts = path.split('/')
        if (len(parts) != 5 or parts[:2] != ['runtime', 'revit'] or parts[2] not in worker_keys
                or parts[3] != version):
            raise UpdateError('Вспомогательный файл должен находиться в каталоге своей сборки Revit')
        filename = parts[4]
        permitted = (parts[2] == '2024' and filename in _NET48_DEPENDENCIES)
        permitted |= filename in ('XIVgate.RevitBridge.Worker.deps.json', 'XIVgate.RevitBridge.Worker.runtimeconfig.json')
        if not permitted:
            raise UpdateError('Неожиданный вспомогательный файл runtime: ' + path)
        paths.add(path)
    if '2024' in worker_keys:
        required = {f'runtime/revit/2024/{version}/{name}' for name in _NET48_DEPENDENCIES}
        if not required.issubset(paths):
            raise UpdateError('В пакете отсутствуют обязательные зависимости Revit 2024')
    return frozenset(paths)


def _validate_files(files, manifest, version, blender_version=None):
    _version(version)
    if (not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or manifest['schema'] != 1
            or manifest.get('id') != PACKAGE_ID or manifest.get('version') != version):
        raise UpdateError('Пакет принадлежит другому дополнению или версии')
    minimum = manifest.get('blender_min')
    if not isinstance(minimum, list) or len(minimum) != 3 or any(type(item) is not int or item < 0 for item in minimum):
        raise UpdateError('Неверная минимальная версия Blender')
    if blender_version is not None and _version(blender_version) < tuple(minimum):
        raise UpdateError('Для обновления требуется более новая версия Blender')
    runtime = _runtime_paths(manifest, version)
    if 'core/loader_payload.py' in files or 'loader_payloads' in manifest:
        try:
            from .loader import payload_record
            metadata = manifest['loader_payloads']
            if (not isinstance(metadata, dict) or type(metadata.get('abi')) is not int or metadata['abi'] != 1
                    or not isinstance(metadata.get('sha256'), dict)
                    or set(metadata['sha256']) != set(manifest['workers'])):
                raise ValueError('Перечень загрузчиков не соответствует обработчикам Revit')
            for key in metadata['sha256']:
                payload_record(files['core/loader_payload.py'], key, metadata['sha256'])
        except (KeyError, TypeError, ValueError) as error:
            raise UpdateError('Неверный комплект постоянных загрузчиков: ' + str(error)) from error
    declared = manifest.get('files')
    if not isinstance(declared, dict) or set(files) != set(declared) or not runtime.issubset(files):
        raise UpdateError('Состав ZIP не совпадает с манифестом пакета')
    seen = set()
    total = 0
    for name, data in files.items():
        _relative(name)
        key = name.casefold()
        if key in seen or key == _CONTROL.casefold():
            raise UpdateError('Повторяющийся путь в пакете')
        seen.add(key)
        if not isinstance(data, bytes) or not _SHA256.fullmatch(str(declared[name])) or hashlib.sha256(data).hexdigest() != declared[name]:
            raise UpdateError('Нарушена контрольная сумма файла: ' + name)
        total += len(data)
        if len(data) > MAX_ARCHIVE_BYTES or total > MAX_UNPACKED_BYTES:
            raise UpdateError('Распакованный пакет слишком велик')
        if name.lower().endswith('.dll') and name not in runtime:
            raise UpdateError('В пакете обнаружена посторонняя DLL')
        if name.casefold().startswith('runtime/') and name not in runtime:
            raise UpdateError('В runtime разрешены только объявленные файлы данной версии')
        if name.lower().endswith(('.exe', '.pyd', '.so', '.bat', '.cmd', '.ps1')):
            raise UpdateError('В пакете обнаружен неожиданный исполняемый файл')
        if name.endswith('.py'):
            try:
                compile(data, name, 'exec', dont_inherit=True)
            except (SyntaxError, ValueError) as error:
                raise UpdateError('Ошибка Python в файле ' + name) from error
    try:
        if files['VERSION'].decode('utf-8').strip() != version:
            raise UpdateError('VERSION не совпадает с релизом')
        tree = ast.parse(files['__init__.py'])
        values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets)]
        if len(values) != 1 or not isinstance(values[0], dict) or tuple(values[0].get('version', ())) != _version(version):
            raise UpdateError('bl_info.version не совпадает с релизом')
    except (KeyError, ValueError, TypeError, UnicodeError, SyntaxError) as error:
        raise UpdateError('В пакете отсутствуют согласованные VERSION и bl_info') from error


def validate_archive(path, version, blender_version=None):
    """Read a bounded ZIP into memory; never extract or execute untrusted code."""
    try:
        if Path(path).stat().st_size > MAX_ARCHIVE_BYTES:
            raise UpdateError('ZIP превышает допустимый размер')
        files = {}
        seen = set()
        total = 0
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_FILES:
                raise UpdateError('Недопустимое количество файлов в ZIP')
            for entry in entries:
                _relative(entry.filename.rstrip('/') if entry.is_dir() else entry.filename)
                parts = entry.filename.rstrip('/').split('/')
                if parts == [PACKAGE_ID] and entry.is_dir():
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise UpdateError('Ссылки в ZIP запрещены')
                    continue
                if len(parts) < 2 or parts[0] != PACKAGE_ID:
                    raise UpdateError('ZIP должен содержать один каталог дополнения')
                relative = '/'.join(parts[1:])
                key = relative.casefold()
                if key in seen:
                    raise UpdateError('ZIP содержит повторяющиеся пути')
                seen.add(key)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise UpdateError('Ссылки и специальные файлы в ZIP запрещены')
                if entry.is_dir():
                    continue
                if entry.flag_bits & 1 or entry.file_size > MAX_ARCHIVE_BYTES:
                    raise UpdateError('Зашифрованный или слишком большой файл в ZIP')
                total += entry.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise UpdateError('ZIP превышает предел распакованного размера')
                with archive.open(entry) as stream:
                    data = stream.read(min(entry.file_size, MAX_ARCHIVE_BYTES) + 1)
                if len(data) != entry.file_size:
                    raise UpdateError('Фактический размер файла ZIP не совпадает с заголовком')
                files[relative] = data
        control = files.pop(_CONTROL, None)
        if control is None or len(control) > MAX_METADATA_BYTES:
            raise UpdateError('Отсутствует манифест пакета')
        manifest = _json_object(control)
        _validate_files(files, manifest, version, blender_version)
        return Package(files, manifest)
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, UnicodeError) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError('Не удалось проверить ZIP обновления: ' + str(error)) from error


def _reject_links(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise UpdateError('Символические ссылки и junction в пути обновления запрещены')
    return path


def _safe_directory(path):
    path = _reject_links(path)
    if not path.is_dir():
        raise UpdateError('Рабочий каталог обновления должен существовать')
    return path


def _target(root, relative):
    _relative(relative)
    path = _reject_links(root.joinpath(*PurePosixPath(relative).parts))
    if not path.is_relative_to(root):
        raise UpdateError('Путь выходит за пределы дополнения')
    return path


def _atomic_file(path, data, exclusive=False):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.pending')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _InstallLock:
    """Stable inode and OS-owned lock shared by every Blender process."""
    def __init__(self, stream):
        self.stream = stream

    @classmethod
    def acquire(cls, addon_root):
        root = _safe_directory(addon_root)
        identity = hashlib.sha256(os.path.normcase(str(root)).encode('utf-8')).hexdigest()[:24]
        path = _reject_links(root.parent / ('.agr_revit_bridge-update-' + identity + '.lock'))
        stream = None
        try:
            # Never unlink this file: replacing its inode would permit two locks.
            stream = path.open('a+b')
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return cls(stream)
        except OSError as error:
            if stream is not None:
                stream.close()
            raise UpdateError('Дополнение уже обновляется в другом окне Blender или каталог блокировки недоступен') from error

    def release(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()  # Closing also releases the OS lock after an unlock error.


class PackageTransaction:
    def __init__(self, root, backup, lock):
        self.root = root
        self._lock = lock
        self.directory = backup / ('package-' + uuid.uuid4().hex)
        self.directory.mkdir()
        self.snapshots = {}
        self.applied = []
        self.directories = []
        self.finished = False

    def snapshot(self, name):
        path = _target(self.root, name)
        if path.exists() and not path.is_file():
            raise UpdateError('Файл обновления занят каталогом: ' + name)
        original = path.read_bytes() if path.exists() else None
        snapshot = None
        if original is not None:
            snapshot = self.directory / str(len(self.snapshots))
            snapshot.write_bytes(original)
        self.snapshots[name] = snapshot

    def parents(self, path):
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            _reject_links(directory)
            directory.mkdir()
            self.directories.append(directory)

    def _cleanup(self):
        for snapshot in self.snapshots.values():
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)
        self.directory.rmdir()

    def rollback(self):
        try:
            if not self.finished and self._lock is None:
                self._lock = _InstallLock.acquire(self.root)
            self._rollback()
        finally:
            self.release_lock()

    def _rollback(self):
        if self.finished:
            return
        while self.applied:
            name, installed = self.applied[-1]
            path = _target(self.root, name)
            if installed is None:
                if path.exists():
                    raise UpdateError('Откат остановлен: файл изменён извне; копия сохранена в ' + str(self.directory))
            elif not path.is_file() or path.read_bytes() != installed:
                raise UpdateError('Откат остановлен: файл изменён извне; копия сохранена в ' + str(self.directory))
            snapshot = self.snapshots[name]
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_file(path, snapshot.read_bytes())
            self.applied.pop()
        for directory in reversed(self.directories):
            try:
                directory.rmdir()
            except OSError:
                pass  # Retain directories containing files created by someone else.
        self._cleanup()
        self.finished = True

    def commit(self):
        try:
            if not self.finished:
                self._cleanup()
        finally:
            # A cleanup error does not undo an already activated new version.
            self.finished = True
            self.release_lock()

    def release_lock(self):
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.release()


def install_package(package, addon_root, backup_directory):
    """Replace managed files in place, retain all old versioned worker binaries."""
    transaction = None
    lock = None
    try:
        _validate_files(package.files, package.manifest, package.manifest['version'])
        root = _safe_directory(addon_root)
        backup = _safe_directory(backup_directory)
        if backup.is_relative_to(root):
            raise UpdateError('Резервные копии обновления должны находиться вне дополнения')
        if (root / '.git').exists() or (root.parent / '.git').exists():
            raise UpdateError('Исходный Git checkout нельзя обновлять установленным ZIP')
        lock = _InstallLock.acquire(root)
        data = dict(package.files)
        data[_CONTROL] = (json.dumps(package.manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        stale = set()
        old_control = _target(root, _CONTROL)
        if old_control.is_file():
            old = _json_object(old_control.read_bytes())
            if old.get('id') != PACKAGE_ID or old.get('schema') != 1 or not isinstance(old.get('files'), dict):
                raise UpdateError('Неизвестный установленный манифест; требуется ручная проверка')
            for name in old['files']:
                _relative(name)
                if name not in data and not name.casefold().startswith('runtime/'):
                    path = _target(root, name)
                    expected_hash = old['files'][name]
                    if (path.is_file() and _SHA256.fullmatch(str(expected_hash))
                            and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash):
                        stale.add(name)
            for name in tuple(data):
                path = _target(root, name)
                if name != _CONTROL and name not in old['files'] and path.exists():
                    if not path.is_file() or path.read_bytes() != data[name]:
                        raise UpdateError('Обновление затрагивает неуправляемый файл: ' + name)
                    del data[name]
        runtime = _runtime_paths(package.manifest, package.manifest['version'])
        for worker in runtime:
            worker_target = _target(root, worker)
            if worker_target.exists():
                if not worker_target.is_file() or worker_target.read_bytes() != package.files[worker]:
                    raise UpdateError('В каталоге новой версии уже есть другая DLL; перезапись запрещена')
                data.pop(worker, None)
        transaction = PackageTransaction(root, backup, lock)
        for name in sorted(set(data) | stale):
            transaction.snapshot(name)
        # Publish version metadata last; old DLLs are never touched or renamed.
        names = sorted(data, key=lambda name: (name in ('VERSION', _CONTROL), name))
        for name in names:
            path = _target(root, name)
            transaction.parents(path)
            _atomic_file(path, data[name], exclusive=name in runtime)
            transaction.applied.append((name, data[name]))
        for name in sorted(stale):
            path = _target(root, name)
            if path.exists():
                path.unlink()
                transaction.applied.append((name, None))
        return transaction
    except Exception as error:
        if transaction is not None:
            try:
                transaction.rollback()
            except Exception as rollback_error:
                raise UpdateError('Обновление прервано; резервная копия: ' + str(transaction.directory)
                                  + '. Откат требует проверки: ' + str(rollback_error)) from error
        if isinstance(error, UpdateError):
            raise
        raise UpdateError('Не удалось установить обновление: ' + str(error)) from error
    finally:
        if transaction is None and lock is not None:
            lock.release()


class RegistrationTransaction:
    def __init__(self, target, moved, directory, old_bytes, new_bytes):
        self.target, self.moved, self.directory = target, moved, directory
        self.old_bytes, self.new_bytes = old_bytes, new_bytes
        self.finished = False

    def _cleanup(self):
        self.moved.unlink(missing_ok=True)
        (self.directory / 'registration.addin.backup').unlink(missing_ok=True)
        self.directory.rmdir()

    def rollback(self):
        if self.finished:
            return
        _reject_links(self.target)
        if not self.target.is_file() or self.target.read_bytes() != self.new_bytes:
            raise UpdateError('Регистрация изменена извне; резервная копия: ' + str(self.directory))
        displaced = self.target.with_name('.' + self.target.name + '.' + uuid.uuid4().hex + '.rollback')
        os.rename(self.target, displaced)
        try:
            if displaced.read_bytes() != self.new_bytes:
                _restore_no_clobber(displaced, self.target, self.directory)
                raise UpdateError('Регистрация изменилась во время отката; резервная копия: ' + str(self.directory))
            _restore_no_clobber(self.moved, self.target, self.directory)
        except Exception:
            # Keep every displaced file for manual recovery when an external race wins.
            raise
        displaced.unlink(missing_ok=True)
        self._cleanup()
        self.finished = True

    def commit(self):
        if not self.finished:
            self._cleanup()
            self.finished = True


def _restore_no_clobber(source, target, backup_directory):
    try:
        os.link(source, target)
    except OSError as error:
        raise UpdateError('Чужой файл или блокировка препятствует восстановлению регистрации; '
                          'сохранены копии: ' + str(backup_directory) + ' и ' + str(source)) from error


def repoint_registration(plan, expected_content, backup_directory, addon_root):
    """Repoint only the expected owned manifest using rename/check/exclusive publish."""
    root = _safe_directory(addon_root)
    backup = _safe_directory(backup_directory)
    target = _reject_links(plan['path'])
    if target.name != MANIFEST_NAME or not target.is_file():
        raise UpdateError('Регистрация Revit не найдена')
    old_assembly = _owned_manifest(expected_content, allow_legacy=True)
    new_assembly = _owned_manifest(plan['content'])
    runtime = _target(root, 'runtime')
    if old_assembly is None or new_assembly is None:
        raise UpdateError('Регистрация не принадлежит этому дополнению')
    for is_old, assembly in ((True, old_assembly), (False, new_assembly)):
        checked = _reject_links(assembly)
        if not checked.is_relative_to(runtime):
            raise UpdateError('Сборка регистрации должна находиться внутри runtime установленного дополнения')
        if checked.is_file():
            continue
        # Manual ZIP replacement can remove the previous version's DLL while
        # leaving our owned .addin pointer. Recover that stale pointer only;
        # the replacement must exist and foreign/malformed paths remain refused.
        stale_worker = re.fullmatch(
            r'revit/(2024|2025(?:-net10)?|2026(?:-net10)?|2027)/'
            r'(?:(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)/)?'
            r'XIVgate\.RevitBridge\.Worker\.dll', checked.relative_to(runtime).as_posix())
        if not is_old or checked.exists() or stale_worker is None:
            raise UpdateError('Новая DLL должна существовать; старая регистрация может ссылаться только на собственный обработчик')
    if target.read_text(encoding='utf-8-sig') != expected_content:
        raise UpdateError('Регистрация изменилась; автоматическое переподключение остановлено')
    directory = backup / ('registration-' + uuid.uuid4().hex)
    directory.mkdir()
    moved = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.backup')
    staged = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.pending')
    new_bytes = plan['content'].encode('utf-8')
    was_moved = False
    try:
        with staged.open('xb') as stream:
            stream.write(new_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(target, moved)
        was_moved = True
        old_bytes = moved.read_bytes()
        (directory / 'registration.addin.backup').write_bytes(old_bytes)
        if moved.read_text(encoding='utf-8-sig') != expected_content:
            raise UpdateError('Регистрация изменилась во время переподключения')
        os.link(staged, target)
        return RegistrationTransaction(target, moved, directory, old_bytes, new_bytes)
    except Exception as error:
        if was_moved:
            _restore_no_clobber(moved, target, directory)
            moved.unlink(missing_ok=True)
        (directory / 'registration.addin.backup').unlink(missing_ok=True)
        directory.rmdir()
        if isinstance(error, UpdateError):
            raise
        raise UpdateError('Не удалось переподключить регистрацию: ' + str(error)) from error
    finally:
        staged.unlink(missing_ok=True)


def connect_registration(plan, addon_root, backup_directory):
    """Explicit setup: create an owned pointer or migrate this installation's DLL.

    Uses the same OS lock as package installation. An old unversioned DLL below
    this add-on's runtime is eligible; foreign/external registrations are never
    replaced. A successful pointer remains active if only backup cleanup fails.
    """
    root = _safe_directory(addon_root)
    lock = _InstallLock.acquire(root)
    try:
        target = _reject_links(plan['path'])
        assembly = _owned_manifest(plan['content'])
        if assembly is None:
            raise UpdateError('Новый манифест не принадлежит этому дополнению')
        assembly = _reject_links(assembly)
        if not assembly.is_relative_to(_target(root, 'runtime')) or not assembly.is_file():
            raise UpdateError('Новая сборка должна находиться внутри runtime установленного дополнения')
        if not target.exists() or manifest_matches(plan):
            return install_manifest(plan)
        prior = target.read_text(encoding='utf-8-sig')
        transaction = repoint_registration(plan, prior, backup_directory, root)
        try:
            transaction.commit()
        except OSError as error:
            warnings.warn('Регистрация обновлена; не удалось удалить резервную копию '
                          + str(transaction.directory) + ': ' + str(error), RuntimeWarning, stacklevel=2)
        return target
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        raise UpdateError('Не удалось подключить Revit: ' + str(error)) from error
    finally:
        lock.release()
