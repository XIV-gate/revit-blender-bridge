"""Bounded, local diagnostics only: no models, geometry, textures or credentials."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid
import zipfile

from .cache_management import _guard
from .jobs import JOB_MARKER, atomic_write_json, job_identity, read_json


def timestamp():
    now = datetime.now(timezone.utc)
    return dict(utc=now.isoformat(), local_time=now.astimezone().isoformat())


class Timeline:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.events = []
        self.truncated = False

    def transition(self, stage, *, request_id=None, terminal=False, memory=None):
        if self.events and (self.events[-1]['stage'], self.events[-1]['request_id']) == (stage, request_id):
            return False
        event = dict(stage=stage, request_id=request_id, terminal=terminal,
                     elapsed_seconds=self.clock()-self.started, **timestamp())
        if memory is not None:
            event['memory'] = memory
        self.events.append(event)
        if len(self.events) > 1000:
            self.events.pop(0)
            self.truncated = True
        return True

    def snapshot(self):
        elapsed = self.clock()-self.started
        stages = []
        for index, event in enumerate(self.events):
            if event['terminal']:
                continue
            end = self.events[index+1]['elapsed_seconds'] if index+1 < len(self.events) else elapsed
            stages.append(dict(stage=event['stage'], request_id=event['request_id'],
                               seconds=max(0, end-event['elapsed_seconds']), running=index+1 == len(self.events)))
        return dict(schema=1, captured=timestamp(), events=list(self.events), stages=stages,
                    history_truncated=self.truncated)


_ROOT_FILES = {'worker.log', 'worker.stdout.log', 'worker.stderr.log', 'status.json',
               'response.json', 'startup_error.json', 'warnings.json', 'client_timing.json'}


def readable_summary(snapshot):
    def gib(value):
        return f'{value/1024**3:.2f} GiB' if isinstance(value, (int, float)) else 'нет данных'
    lines = ['AGR Revit Bridge — диагностика', 'Время: ' + str(snapshot.get('captured', {}).get('local_time', '')),
             'Аддон: ' + str(snapshot.get('addon_version', '')), 'Blender: ' + str(snapshot.get('blender_version', '')),
             'Revit: ' + str(snapshot.get('revit', {}).get('version', 'нет данных')),
             'Состояние: ' + str(snapshot.get('status', '')), str(snapshot.get('message', '')), '', 'Время этапов:']
    for row in (snapshot.get('timings') or {}).get('stages', []):
        lines.append(f"  {row['stage']}: {row['seconds']:.2f} с" + (' (продолжается)' if row['running'] else ''))
    memory = snapshot.get('memory') or {}
    ram = memory.get('ram') or {}
    lines.extend(['', 'Память в момент экспорта:', 'RAM доступно / всего: ' + gib(ram.get('available_bytes')) + ' / ' + gib(ram.get('total_bytes'))])
    for process in memory.get('processes', []):
        lines.append(process['name'] + ': RAM ' + gib(process.get('working_set_bytes')) + ', пик ' + gib(process.get('peak_working_set_bytes')))
    gpu = snapshot.get('gpu_memory') or {}
    for adapter in gpu.get('adapters', []):
        lines.append(adapter['name'] + ': VRAM свободно / всего: ' + gib(adapter.get('free_bytes')) + ' / ' + gib(adapter.get('total_bytes')))
    if gpu.get('reason'):
        lines.append(gpu['reason'])
    lines.extend(['', 'Замеры RAM на границах этапов: diagnostics.json → snapshot → timings → events.',
                  'Время наблюдается клиентом Blender; быстрые промежуточные стадии Revit могут не попасть между опросами.',
                  'Паузы после READY/DONE не включены во время обработки. Это не непрерывный профайлер.'])
    return '\n'.join(lines)


def export_bundle(target, directories, snapshot, *, per_file_limit=2*1024**2, total_limit=32*1024**2):
    snapshot = dict(snapshot)
    target = _guard(target)
    if target.suffix.lower() != '.zip' or not target.parent.is_dir():
        raise ValueError('Выберите ZIP в существующей папке')
    if target.exists():
        raise FileExistsError('Архив уже существует; выберите другое имя')
    files, skipped, truncated, tokens = {}, [], [], []
    for directory in dict.fromkeys(str(value) for value in directories if value):
        try:
            root = _guard(directory)
            if (str(uuid.UUID(root.name)) != root.name
                    or read_json(_guard(root / JOB_MARKER)) != job_identity(root.name)):
                raise ValueError('Нет маркера задания AGR')
            request = read_json(_guard(root / 'request.json')) or {}
            if isinstance(request.get('token'), str) and request['token']:
                tokens.append(request['token'])
            candidates = [path for path in root.iterdir() if path.name in _ROOT_FILES
                          or (path.name.startswith('blender_error_') and path.suffix == '.json')]
            output_root = _guard(root / 'output')
            if output_root.is_dir():
                for output in sorted(output_root.iterdir())[-128:]:
                    output = _guard(output)
                    if not output.is_dir():
                        continue
                    candidates.extend(path for path in output.iterdir()
                        if path.name == 'conversion.log' or path.name == 'conversion.json'
                        or path.name.endswith('.conversion.json') or path.name == 'manifest.json')
            for path in sorted(candidates)[:256]:
                try:
                    path = _guard(path)
                    if not path.is_file() or not path.is_relative_to(root):
                        continue
                    name = 'jobs/' + root.name + '/' + path.relative_to(root).as_posix()
                    if path.name == 'manifest.json':
                        if path.stat().st_size > 32*1024**2:
                            skipped.append(name + ': manifest too large')
                            continue
                        value = read_json(path) or {}
                        payload = json.dumps({key: value[key] for key in ('source', 'summary', 'units', 'schema') if key in value}, ensure_ascii=False).encode()
                        name += '.summary.json'
                    else:
                        with path.open('rb') as stream:
                            size = path.stat().st_size
                            stream.seek(max(0, size-per_file_limit))
                            payload = stream.read(per_file_limit)
                        if size > per_file_limit:
                            truncated.append(name)
                    if sum(len(value.encode('utf-8')) for value in files.values()) + len(payload) > total_limit:
                        skipped.append(name + ': archive size limit')
                        continue
                    files[name] = payload.decode('utf-8-sig', errors='replace')
                except (OSError, ValueError) as error:
                    skipped.append(path.name + ': ' + str(error))
        except (OSError, ValueError) as error:
            skipped.append(str(directory) + ': ' + str(error))
    if not snapshot.get('timings'):
        for name, content in files.items():
            if name.endswith('/client_timing.json') and name not in truncated:
                try:
                    saved = json.loads(content)
                    stages = saved.get('stages')
                    if (saved.get('schema') == 1 and isinstance(stages, list)
                            and all(isinstance(row, dict) and isinstance(row.get('stage'), str)
                                and type(row.get('seconds')) in (int, float) and math.isfinite(row['seconds'])
                                and isinstance(row.get('running'), bool) for row in stages)):
                        snapshot['timings'] = saved
                        snapshot['timings_saved_snapshot'] = True
                        break
                except (ValueError, AttributeError):
                    pass
    report = dict(schema=1, captured=timestamp(), snapshot=snapshot, skipped=skipped,
                  truncated=truncated, limits=dict(per_file_bytes=per_file_limit, total_bytes=total_limit))
    files['diagnostics.json'] = json.dumps(report, ensure_ascii=False, indent=2)
    files['summary.txt'] = readable_summary(snapshot)
    files['README.txt'] = ('AGR Revit Bridge diagnostic export\n'
        'Times: UTC and local offset; durations in seconds. RAM/VRAM values in bytes.\n'
        'No RVT, mesh, textures, catalog, selection or request file included.\n'
        'Logs may contain local paths and element names. Send privately.\n'
        'Truncated files contain their last bytes; skipped files are listed in diagnostics.json.\n')
    with target.open('xb') as stream:
        try:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in files.items():
                    for token in tokens:
                        content = content.replace(token, '[REDACTED]')
                    archive.writestr(name, content)
        except Exception:
            stream.close()
            target.unlink(missing_ok=True)
            raise
    return dict(path=str(target), files=len(files), skipped=skipped, truncated=truncated)
