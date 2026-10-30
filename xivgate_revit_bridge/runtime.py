"""Main-thread Blender controller; external processes do all long-running work."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import bpy

from .core.catalog import annotate_catalog, initial_selection, selection_by_unique_id
from .core.installer import manifest_matches
from .core.jobs import JobSession, read_json, source_sha256
from .core.launcher import WorkerProcess
from .core.revit_versions import inspect_revit


def worker_path(props=None):
    from . import bl_info
    version = '.'.join(map(str, bl_info['version']))
    override = os.environ.get('XIVGATE_RBB_WORKER_DLL')
    key = inspect_revit(bpy.path.abspath(props.revit_exe))['key'] if props is not None else '2027'
    return Path(override) if override else Path(__file__).parent / 'runtime' / 'revit' / key / version / 'XIVgate.RevitBridge.Worker.dll'


def setup_plan(props):
    from .core.installer import manifest_plan
    kwargs = {'addin_folder': props.addin_folder} if props.addin_folder else {}
    info = inspect_revit(bpy.path.abspath(props.revit_exe))
    return manifest_plan(info['path'], worker_path(props), year=info['year'], **kwargs)


def selected_ids(props):
    return {item.element_id for item in props.elements if item.selected}


def catalog_from_props(props):
    return {'elements': [{'id': item.element_id, 'unique_id': item.unique_id, 'name': item.name,
                         'category_id': item.category_id, 'category_name': item.category_name,
                         'workset_id': item.workset_id, 'workset_name': item.workset_name,
                         'recommendation': item.recommendation, 'reason': item.reason}
                        for item in props.elements]}


def _within_job(path):
    value = Path(path).resolve()
    if not STATE.session or not value.is_relative_to(STATE.session.directory):
        raise ValueError('Ответ обработчика указывает за пределы задания')
    return value


class Runtime:
    def __init__(self):
        self.scene = None
        self.session = None
        self.worker = None
        self.request_id = None
        self.action = None
        self.previous_selection = {}
        self.auto_import = False
        self.started = 0
        self.converter = None
        self.conversion_response = None
        self.converted_usd = None
        self.pending_options = {}
        self.worker_exe = ''
        self.pending_context = ''
        self.pending_selected_ids = None
        self.scanned_source_hash = ''
        self.force_refresh = False

    @property
    def props(self):
        return self.scene.xivgate_revit_bridge

    def stop(self):
        if self.converter and self.converter.poll() is None:
            self.converter.terminate()
        if self.worker:
            self.worker.terminate()
        self.converter = None
        self.worker = None
        self.session = None
        self.action = None


STATE = Runtime()
MAINTENANCE = False


def _timer_start():
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.2)


def _message(state, message, *, busy=False, progress=None):
    props = STATE.props
    props.status = state
    props.message = message
    props.busy = busy
    if progress is not None:
        props.progress = max(0, min(1, float(progress)))
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _warnings(values):
    props = STATE.props
    props.warnings.clear()
    for value in dict.fromkeys(map(str, values)):
        props.warnings.add().message = value
    props.warning_count = len(props.warnings)


def begin_scan(scene, *, auto_import=False):
    props = scene.xivgate_revit_bridge
    if MAINTENANCE or props.busy or STATE.action is not None:
        raise RuntimeError('Дождитесь завершения текущего задания')
    if os.name != 'nt':
        raise RuntimeError('Локальный Revit требует Windows')
    exe = Path(bpy.path.abspath(props.revit_exe))
    source = Path(bpy.path.abspath(props.source_rvt)).resolve()
    info = inspect_revit(exe)
    if not source.is_file() or source.suffix.casefold() != '.rvt':
        raise ValueError('Укажите существующий файл RVT')
    if not worker_path(props).is_file():
        raise FileNotFoundError('В комплекте отсутствует обработчик Revit ' + info['key'])
    if not props.cache_directory:
        raise ValueError('Укажите рабочую папку для копии RVT и кэша')
    plan = setup_plan(props)
    if not Path(plan['path']).is_file():
        raise RuntimeError('Сначала нажмите «Подключить Revit» в этой панели')
    if not manifest_matches(plan):
        raise RuntimeError('Регистрация Revit отличается от этого комплекта; подключите правильный обработчик')
    if STATE.worker_exe and os.path.normcase(str(exe.resolve())) != STATE.worker_exe:
        STATE.stop()
    previous = selection_by_unique_id(catalog_from_props(props), selected_ids(props)) if props.catalog_source == str(source) else {}
    if props.import_source and props.import_source != str(source):
        props.import_id = ''
        props.origin_valid = False
    STATE.scene = scene
    STATE.scanned_source_hash = source_sha256(source)
    STATE.previous_selection = previous
    STATE.auto_import = auto_import
    props.elements.clear()
    props.categories.clear()
    props.worksets.clear()
    props.catalog_source = ''
    if STATE.worker is None or STATE.worker.poll() is not None:
        root = Path(bpy.path.abspath(props.cache_directory))
        STATE.session = JobSession.create(root)
        STATE.worker = WorkerProcess()
    request = STATE.session.request('scan', source_rvt=str(source), options={'load_links': props.load_links})
    STATE.request_id = request['request_id']
    STATE.action = 'scan'
    STATE.started = time.monotonic()
    props.job_directory = str(STATE.session.directory)
    _message('STARTING', 'Запуск локального Revit и чтение рабочей копии…', busy=True, progress=0)
    if STATE.worker.pid is None or STATE.worker.poll() is not None:
        STATE.worker.start(str(exe), STATE.session.directory, STATE.session.token,
                           extra_env={'XIVGATE_RBB_SESSION_ID': STATE.session.session_id})
        STATE.worker_exe = os.path.normcase(str(exe.resolve()))
    _timer_start()


def _load_catalog(path):
    from .ui import sync_groups
    catalog = read_json(_within_job(path))
    if not catalog or catalog.get('schema') != 1:
        raise ValueError('Не удалось прочитать каталог модели')
    catalog = annotate_catalog(catalog)
    props = STATE.props
    choices = initial_selection(catalog, STATE.previous_selection)
    for item in catalog.get('elements', []):
        row = props.elements.add()
        row.element_id = str(item['id'])
        row.unique_id = item.get('unique_id', '')
        row.name = item.get('name', '')
        row.category_id = str(item.get('category_id', ''))
        row.category_name = item.get('category_name', '')
        row.workset_id = str(item.get('workset_id', ''))
        row.workset_name = item.get('workset_name', '')
        row.recommendation = item.get('recommendation', 'review')
        row.reason = item.get('reason', '')
        row.selected = choices[row.element_id]
    for kind, field in (('category', 'categories'), ('workset', 'worksets')):
        unique = {str(item.get(kind + '_id', '')): item.get(kind + '_name', '') for item in catalog.get('elements', [])}
        collection = getattr(props, field)
        for identifier, name in sorted(unique.items(), key=lambda pair: pair[1].casefold()):
            row = collection.add()
            row.group_id = identifier
            row.group_kind = kind
            row.name = name or 'Без рабочего набора'
    props.catalog_source = STATE.session.source_path
    _warnings(catalog.get('warnings', []))
    sync_groups(props)
    _message('READY', f'Модель прочитана: {len(props.elements)} элементов. Отметьте нужное.', progress=1)
    if STATE.auto_import:
        STATE.auto_import = False
        begin_export(STATE.scene, force_refresh=STATE.force_refresh)


def begin_export(scene, *, force_refresh=False):
    props = scene.xivgate_revit_bridge
    if MAINTENANCE or props.busy or STATE.action is not None:
        raise RuntimeError('Текущее задание ещё не завершено')
    if not props.elements:
        raise RuntimeError('Сначала получите список элементов модели')
    source = Path(bpy.path.abspath(props.source_rvt)).resolve()
    if source != Path(props.catalog_source):
        raise ValueError('Файл RVT изменён в панели; сначала получите новый список')
    ids = selected_ids(props)
    if not ids:
        raise ValueError('Не выбран ни один элемент')
    STATE.force_refresh = force_refresh
    source_hash = source_sha256(source)
    STATE.pending_context = json.dumps(dict(format=2, source=str(source), sha256=source_hash,
        exe=os.path.normcase(str(Path(bpy.path.abspath(props.revit_exe)).resolve())),
        detail=props.detail_level, instances=props.preserve_instances, materials=props.import_materials,
        textures=props.import_textures, pack=props.pack_images, centered=props.center_model), sort_keys=True)
    STATE.pending_selected_ids = ids
    if not force_refresh and props.import_id and props.import_context_json == STATE.pending_context:
        from .blender_import import apply_selection
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            result = apply_selection(props.import_id, ids)
        if result is not None:
            STATE.scene = scene
            props.result_collection = result['collection']
            props.result_placements, props.result_prototypes = result['placements'], result['prototypes']
            _message('DONE', f"Выбор применён без Revit: {result['placements']} размещений, исключено {result['removed']}.", progress=1)
            return
    if (STATE.session is None or STATE.worker is None or STATE.worker.poll() is not None
            or STATE.scene != scene or STATE.session.source_path != props.catalog_source
            or STATE.worker_exe != os.path.normcase(str(Path(bpy.path.abspath(props.revit_exe)).resolve()))
            or STATE.scanned_source_hash != source_hash):
        begin_scan(scene, auto_import=True)
        return
    ids = selected_ids(props)
    if not ids:
        raise ValueError('Не выбран ни один элемент')
    if Path(bpy.path.abspath(props.source_rvt)).resolve() != Path(props.catalog_source):
        raise ValueError('Файл RVT изменён в панели; сначала получите новый список')
    STATE.scene = scene
    STATE.pending_options = {'preserve_instances': props.preserve_instances, 'materials': props.import_materials,
                             'textures': props.import_textures, 'pack_images': props.pack_images,
                             'center_model': props.center_model}
    request = STATE.session.request('export', selection_ids=ids, options={'detail_level': props.detail_level})
    STATE.request_id = request['request_id']
    STATE.action = 'export'
    STATE.started = time.monotonic()
    _message('EXPORTING', f'Выгрузка выбранных элементов: {len(ids)}…', busy=True, progress=0)
    _timer_start()


def _start_conversion(manifest):
    manifest = _within_job(manifest)
    target = manifest.with_name('scene.usdc')
    response = manifest.with_name('conversion.json')
    python_exe = Path(sys.prefix) / 'bin' / ('python.exe' if os.name == 'nt' else 'python3')
    if not python_exe.is_file():
        raise FileNotFoundError('Не найден встроенный Python Blender: ' + str(python_exe))
    command = [str(python_exe), '-m', 'xivgate_revit_bridge.convert_cli', '--manifest', str(manifest),
               '--target', str(target), '--response', str(response)]
    options = STATE.pending_options
    if not options['materials']:
        command.append('--no-materials')
    if not options['textures']:
        command.append('--no-textures')
    if not options['center_model']:
        command.append('--source-origin')
    elif STATE.props.import_id and STATE.props.origin_valid and STATE.props.origin_centered:
        command.extend(['--offset', *map(str, json.loads(STATE.props.source_offset_json))])
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    temporary = STATE.session.directory / 'tmp'
    temporary.mkdir(exist_ok=True)
    env['TEMP'] = env['TMP'] = str(temporary)
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with manifest.with_name('conversion.log').open('wb') as stream:
        STATE.converter = subprocess.Popen(command, cwd=str(manifest.parent), env=env,
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
    STATE.conversion_response = response
    STATE.converted_usd = target
    STATE.action = 'convert'
    _message('CONVERTING', 'Подготовка геометрии и материалов для Blender…', busy=True, progress=0.5)


def _apply_import():
    from .blender_import import import_result
    props = STATE.props
    _message('IMPORTING', 'Создание проверенного результата Blender…', busy=True, progress=0.9)
    with bpy.context.temp_override(scene=STATE.scene, view_layer=STATE.scene.view_layers[0]):
        result = import_result(STATE.converted_usd, import_id=props.import_id or None,
            preserve_instances=STATE.pending_options['preserve_instances'], pack_images=STATE.pending_options['pack_images'],
            source_path=props.catalog_source, selected_element_ids=STATE.pending_selected_ids)
    props.import_id = result['import_id']
    props.import_source = props.catalog_source
    props.import_context_json = STATE.pending_context
    props.source_offset_json = json.dumps(result['source_offset'])
    props.origin_valid = True
    props.origin_centered = STATE.pending_options['center_model']
    props.result_collection = result['collection']
    _warnings([item.message for item in props.warnings] + ['Недоступно изображение: ' + path for path in result['missing_images']])
    props.result_placements = result['placements']
    props.result_prototypes = result['prototypes']
    STATE.action = None
    STATE.converter = None
    STATE.force_refresh = False
    _message('DONE', f"Импортировано: {result['placements']} размещений, {result['prototypes']} прототипов.", progress=1)


def tick():
    try:
        if STATE.scene is None or STATE.scene.name not in bpy.data.scenes or STATE.action is None:
            return None
        if STATE.action == 'convert':
            code = STATE.converter.poll()
            if code is None:
                return 0.25
            response = read_json(STATE.conversion_response)
            if code != 0 or not response or not response.get('success'):
                raise RuntimeError((response or {}).get('error', 'Не удалось подготовить геометрию; см. conversion.log'))
            _warnings([item.message for item in STATE.props.warnings] + ['Не найдена текстура: ' + path for path in response.get('missing_textures', [])])
            _apply_import()
            return None
        response = STATE.session.read_response(STATE.request_id)
        if response is not None:
            _warnings(response.get('warnings', []))
            if not response['success']:
                raise RuntimeError(str(response.get('error', 'Revit не завершил задание')))
            if STATE.action == 'scan':
                STATE.action = None
                _load_catalog(response['catalog_path'])
                return 0.25 if STATE.action else None
            if STATE.action == 'export':
                _start_conversion(response['manifest_path'])
                return 0.25
        status = STATE.session.read_status()
        if status:
            name = str(status.get('state', '')).upper()
            allowed = {'OPENING': 'SCANNING', 'SCANNING': 'SCANNING', 'EXPORTING': 'EXPORTING',
                       'REQUIRES_ACTION': 'REQUIRES_ACTION', 'STARTING': 'STARTING'}
            _message(allowed.get(name, STATE.props.status), str(status.get('message', 'Обработка модели…')),
                     busy=True, progress=status.get('progress', STATE.props.progress))
        elif time.monotonic() - STATE.started > 45:
            _message('STARTING', 'Revit запускается. Если появилось его окно с вопросом, используйте «Показать Revit».', busy=True)
        if STATE.worker.poll() is not None:
            raise RuntimeError('Процесс Revit завершился до получения ответа. Журнал: ' + str(STATE.session.directory))
        return 0.25
    except Exception as error:
        if STATE.scene is not None:
            try:
                _message('ERROR', str(error))
            except ReferenceError:
                pass
        STATE.action = None
        return None


def cancel():
    if STATE.session and STATE.request_id:
        STATE.session.cancel(STATE.request_id)
    STATE.stop()
    if STATE.scene is not None:
        _message('IDLE', 'Задание отменено. Предыдущий результат сохранён.')


def show_worker():
    """Only called by an explicit user button; never by the development agent."""
    if os.name != 'nt' or not STATE.worker or not STATE.worker.pid or STATE.worker.poll() is not None:
        raise RuntimeError('Обработчик Revit не запущен')
    import ctypes
    from ctypes import wintypes
    pid = STATE.worker.pid
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            user32.ShowWindow(hwnd, 5)
        return True
    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)


def cleanup(*_):
    STATE.stop()
    STATE.scene = None
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
