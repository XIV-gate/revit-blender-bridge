"""Atomic file transport; each job and output revision owns its paths."""
import copy
import hashlib
import json
from pathlib import Path
import os
import secrets
import uuid

JOB_MARKER = '.agr_revit_bridge_job.json'
FINISH_MARKER = '.agr_revit_bridge_finished.json'
JOB_PACKAGE_ID = 'xivgate_revit_bridge'


def job_identity(session_id):
    return {'schema': 1, 'package_id': JOB_PACKAGE_ID, 'session_id': session_id}


def contained_path(root, value):
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Путь выходит за пределы задания')
    return path


def atomic_write_json(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        with Path(path).open(encoding='utf-8-sig') as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def source_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class JobSession:
    def __init__(self, directory, session_id, token):
        self.directory = Path(directory).resolve()
        self.session_id = session_id
        self.token = token
        self.source_path = ''
        self.source_copy = ''
        self.request_id = None
        self.finished = False

    @classmethod
    def create(cls, work_root):
        root = Path(work_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        session_id = str(uuid.uuid4())
        directory = contained_path(root, session_id)
        directory.mkdir()
        contained_path(directory, 'input').mkdir()
        contained_path(directory, 'output').mkdir()
        atomic_write_json(contained_path(directory, JOB_MARKER), job_identity(session_id))
        return cls(directory, session_id, secrets.token_urlsafe(32))

    def request(self, action, source_rvt='', selection_ids=(), options=None):
        if self.finished or contained_path(self.directory, FINISH_MARKER).exists():
            raise ValueError('Сеанс завершён; для новой операции создайте новое задание')
        if action not in ('scan', 'export', 'shutdown'):
            raise ValueError('Неизвестное действие задания')
        source = self.source_path
        if source_rvt:
            source = str(Path(source_rvt).resolve())
        if action == 'scan':
            if not source or not Path(source).is_file():
                raise ValueError('Исходный RVT не найден')
            if Path(source).is_relative_to(self.directory):
                raise ValueError('Исходный RVT должен находиться вне задания')
            if Path(source).suffix.lower() != '.rvt':
                raise ValueError('Требуется файл RVT')
        request_id = str(uuid.uuid4())
        source_copy = (str(contained_path(self.directory, Path('input') / (request_id + '.rvt')))
                       if action == 'scan' else self.source_copy)
        output = contained_path(self.directory, Path('output') / request_id)
        options_copy = copy.deepcopy(options or {})
        if not isinstance(options_copy, dict):
            raise ValueError('Параметры должны быть словарём')
        payload = {'schema': 1, 'request_id': request_id, 'session_id': self.session_id,
                   'token': self.token, 'action': action, 'source_rvt': source,
                   'source_copy': source_copy,
                   'output_dir': str(output),
                   'selection': {'include_ids': sorted({str(value) for value in selection_ids})},
                   'options': options_copy}
        # Validate serialization before creating a revision or publishing anything.
        json.dumps(payload, allow_nan=False)
        output.mkdir()
        atomic_write_json(contained_path(self.directory, 'request.json'), payload)
        self.source_path = source
        self.source_copy = source_copy
        self.request_id = request_id
        return payload

    def _read_matching(self, name, request_id):
        value = read_json(contained_path(self.directory, name))
        if not value or value.get('schema') != 1:
            return None
        if value.get('session_id') != self.session_id or value.get('request_id') != request_id:
            return None
        if 'token' in value and value['token'] != self.token:
            return None
        return value

    def read_status(self):
        return self._read_matching('status.json', self.request_id)

    def read_response(self, request_id):
        response = self._read_matching('response.json', request_id)
        if response is None or not isinstance(response.get('success'), bool):
            return None
        return response

    def cancel(self, request_id):
        if request_id != self.request_id:
            raise ValueError('Можно отменить только текущее задание')
        payload = {'schema': 1, 'session_id': self.session_id, 'request_id': request_id, 'token': self.token}
        atomic_write_json(contained_path(self.directory, 'cancel.json'), payload)
        return payload

    def finish(self):
        """Mark an idle session after its owned worker has exited (caller checks it)."""
        identity = job_identity(self.session_id)
        if (self.directory.name != self.session_id or str(uuid.UUID(self.session_id)) != self.session_id
                or read_json(contained_path(self.directory, JOB_MARKER)) != identity):
            raise ValueError('Нельзя завершить задание без подтверждённого маркера AGR')
        marker = contained_path(self.directory, FINISH_MARKER)
        if marker.exists() and read_json(marker) != identity:
            raise ValueError('Маркер завершения не принадлежит этому заданию')
        if not marker.exists():
            atomic_write_json(marker, identity)
        self.finished = True
        return marker
