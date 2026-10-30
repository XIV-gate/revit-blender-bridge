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
from .core.reporting import report_entries, raw_values
from .core.diagnostics import Timeline


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
    root = STATE.file_root or (STATE.session.directory if STATE.session else None)
    if root is None or not value.is_relative_to(root):
        raise ValueError('Ответ обработчика указывает за пределы задания')
    return value


class Runtime:
    def __init__(self):
        self.scene_uid = None
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
        self.file_root = None
        self.cache_index = None
        self.cache_signature = ''
        self.delta_ids = None
        self.force_scan_done = False
        self.timings = Timeline()
        self.timing_write_error = ''

    @property
    def scene(self):
        # RNA wrappers may be invalidated by undo/reallocation. Resolve the ID
        # afresh, never by name: a different scene may reuse a deleted name.
        if self.scene_uid is None or not hasattr(bpy.data, 'scenes'):
            return None
        return next((scene for scene in bpy.data.scenes if scene.session_uid == self.scene_uid), None)

    @scene.setter
    def scene(self, scene):
        self.scene_uid = scene.session_uid if scene is not None else None

    @property
    def props(self):
        scene = self.scene
        if scene is None:
            raise RuntimeError('Сцена задания больше не существует; задание остановлено')
        return scene.xivgate_revit_bridge

    def stop(self):
        if self.scene is not None and self.action is not None:
            _record_stage('stopped', terminal=True)
        if self.converter and self.converter.poll() is None:
            self.converter.terminate()
            try:
                self.converter.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self.worker:
            self.worker.terminate()
        if (self.session and (self.worker is None or self.worker.poll() is not None)
                and (self.converter is None or self.converter.poll() is not None)):
            try:
                self.session.finish()
            except (OSError, ValueError):
                pass
        self.converter = None
        self.worker = None
        self.session = None
        self.action = None
        self.file_root = None


STATE = Runtime()
MAINTENANCE = False


def _timer_start():
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.2)


def _message(state, message, *, busy=False, progress=None, timing_stage=None):
    _record_stage(timing_stage or state.lower(), terminal=not busy)
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
    set_warnings(STATE.props, values)


def set_warnings(props, values):
    props.warnings.clear()
    for entry in report_entries(values):
        row = props.warnings.add()
        for key, value in entry.items():
            setattr(row, key, value)
    props.warning_count = len(props.warnings)


def _process_ids():
    result = {'blender': os.getpid()}
    for name, process in (('revit', STATE.worker), ('converter', STATE.converter)):
        pid = getattr(process, 'pid', None)
        if type(pid) is int and pid > 0:
            result[name] = pid
    return result


def _record_stage(stage, *, terminal=False, scene=None):
    from .core.jobs import atomic_write_json
    from .core.resources import memory_snapshot
    trace = STATE.timings
    request_id = None if stage in ('preparing_scan', 'checking_source_and_cache') else STATE.request_id
    if trace.events and (trace.events[-1]['stage'], trace.events[-1]['request_id']) == (stage, request_id):
        return
    try:
        memory = memory_snapshot(_process_ids())
    except (OSError, ValueError, TypeError) as error:
        memory = {'unavailable': [str(error)]}
    trace.transition(stage, request_id=request_id, terminal=terminal, memory=memory)
    if STATE.session and (scene is None or STATE.scene == scene):
        try:
            atomic_write_json(STATE.session.directory / 'client_timing.json', trace.snapshot())
        except (OSError, ValueError) as error:
            STATE.timing_write_error = str(error)


def export_logs(scene, target):
    import platform
    from . import bl_info
    from .core.diagnostics import export_bundle, timestamp
    from .core.resources import gpu_memory, memory_snapshot
    props = scene.xivgate_revit_bridge
    directories = [props.job_directory]
    if STATE.scene == scene and STATE.session:
        directories.append(STATE.session.directory)
    if STATE.scene == scene and STATE.file_root:
        # A cache hit can reference a different, verified job's output revision.
        directories.append(STATE.file_root.parent.parent)
    options = {name: getattr(props, name) for name in ('detail_level', 'preserve_instances',
        'import_materials', 'import_textures', 'pack_images', 'center_model', 'reuse_export_cache')}
    try:
        revit = inspect_revit(bpy.path.abspath(props.revit_exe))
    except (OSError, ValueError, KeyError) as error:
        revit = {'unavailable': str(error), 'path': props.revit_exe}
    snapshot = dict(captured=timestamp(), addon_version='.'.join(map(str, bl_info['version'])),
        blender_version=bpy.app.version_string, python_version=sys.version, os=platform.platform(),
        cpu=platform.processor(), logical_cpus=os.cpu_count(), revit=revit,
        status=props.status, message=props.message, busy=props.busy,
        selected_elements=props.selected_count, catalog_elements=len(props.elements),
        placements=props.result_placements, prototypes=props.result_prototypes,
        options=options, warnings=[dict(message=row.message, severity=row.severity, count=row.count,
            details=row.raw_json[:16384], details_truncated=len(row.raw_json) > 16384) for row in list(props.warnings)[:1000]],
        warnings_total=props.warning_count,
        timings=STATE.timings.snapshot() if STATE.scene == scene else None,
        timing_write_error=STATE.timing_write_error,
        memory=memory_snapshot(_process_ids()), gpu_memory=gpu_memory())
    return export_bundle(target, directories, snapshot)


def record_failure(scene, error, action=None):
    """Keep failures in the scene report and an owned job, never just a popup."""
    import traceback
    from . import bl_info
    from .core.jobs import atomic_write_json
    props = scene.xivgate_revit_bridge
    owns_state = STATE.scene == scene
    if owns_state:
        _record_stage('error', terminal=True)
    phase = action or (STATE.action if owns_state else None) or 'operation'
    details = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    entry = f'Ошибка операции ({phase}): {error}\n{details}'
    if owns_state and STATE.session is not None:
        directory = STATE.session.directory
        entry += '\nПапка задания: ' + str(directory)
        path = directory / ('blender_error_' + uuid.uuid4().hex + '.json')
        try:
            atomic_write_json(path, dict(schema=1, action=phase, error=str(error), traceback=details,
                request_id=STATE.request_id, addon_version='.'.join(map(str, bl_info['version'])),
                blender_version=bpy.app.version_string))
            entry += '\nДиагностика: ' + str(path)
        except OSError as log_error:
            entry += '\nНе удалось записать диагностику: ' + str(log_error)
    set_warnings(props, raw_values(props.warnings) + [entry])
    props.warning_index = len(props.warnings) - 1
    props.status = 'ERROR'
    props.message = f'{error} — подробности в отчёте.'
    props.busy = False
    if owns_state:
        STATE.action = None
        STATE.force_refresh = False
        STATE.force_scan_done = False
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def begin_scan(scene, *, auto_import=False):
    props = scene.xivgate_revit_bridge
    if MAINTENANCE or props.busy or STATE.action is not None:
        raise RuntimeError('Дождитесь завершения текущего задания')
    if os.name != 'nt':
        raise RuntimeError('Локальный Revit требует Windows')
    _record_stage('preparing_scan', scene=scene)
    from .workflow import prepare_settings
    cache = prepare_settings(scene)
    exe = Path(bpy.path.abspath(props.revit_exe))
    source = Path(bpy.path.abspath(props.source_rvt)).resolve()
    info = inspect_revit(exe)
    if not source.is_file() or source.suffix.casefold() != '.rvt':
        raise ValueError('Укажите существующий файл RVT')
    if not worker_path(props).is_file():
        raise FileNotFoundError('В комплекте отсутствует обработчик Revit ' + info['key'])
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
    STATE.file_root = None
    STATE.scanned_source_hash = source_sha256(source)
    STATE.previous_selection = previous
    STATE.auto_import = auto_import
    if props.preview_source != str(source):
        props.preview_source = ''
        props.preview_offset_json = ''
    from .spatial_preview import clear_preview
    clear_preview(scene)
    props.tree_rows.clear()
    props.elements.clear()
    props.categories.clear()
    props.worksets.clear()
    props.types.clear()
    props.scope_kind = props.scope_id = props.scope_label = ''
    props.selection_filter = 'all'
    props.catalog_source = ''
    if STATE.worker is None or STATE.worker.poll() is not None:
        STATE.session = JobSession.create(cache)
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
        row.type_id = row.category_id + ':' + str(item.get('type_id', '-1'))
        row.family_name = item.get('family_name', '')
        row.type_name = row.category_name + ' · ' + ' / '.join(value for value in (row.family_name, item.get('type_name') or row.name) if value)
        minimum, maximum = item.get('bbox_min'), item.get('bbox_max')
        row.bbox_json = json.dumps((minimum, maximum)) if minimum is not None and maximum is not None else ''
        row.recommendation = item.get('recommendation', 'review')
        row.reason = item.get('reason', '')
        row.selected = choices[row.element_id]
    for kind, field in (('category', 'categories'), ('workset', 'worksets'), ('type', 'types')):
        unique = {getattr(item, kind + '_id'): getattr(item, kind + '_name') for item in props.elements}
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
    _record_stage('checking_source_and_cache', scene=scene)
    STATE.force_refresh = force_refresh
    if force_refresh and not STATE.force_scan_done:
        STATE.force_scan_done = True
        begin_scan(scene, auto_import=True)
        return
    if not force_refresh:
        STATE.force_scan_done = False
    STATE.delta_ids = None
    STATE.file_root = None
    source_hash = source_sha256(source)
    STATE.pending_context = json.dumps(dict(format=2, source=str(source), sha256=source_hash,
        exe=os.path.normcase(str(Path(bpy.path.abspath(props.revit_exe)).resolve())),
        detail=props.detail_level, instances=props.preserve_instances, materials=props.import_materials,
        textures=props.import_textures, pack=props.pack_images, centered=props.center_model), sort_keys=True)
    STATE.pending_selected_ids = ids
    STATE.pending_options = {'preserve_instances': props.preserve_instances, 'materials': props.import_materials,
                            'textures': props.import_textures, 'pack_images': props.pack_images,
                            'center_model': props.center_model}
    same_context = props.import_id and props.import_context_json == STATE.pending_context
    if not force_refresh and same_context:
        from .blender_import import apply_selection, OWNER, ROOT_FLAG, AVAILABLE_IDS, SELECTION
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            result = apply_selection(props.import_id, ids, stash_deselected=True)
        if result is not None:
            STATE.scene = scene
            props.result_collection = result['collection']
            props.result_placements, props.result_prototypes = result['placements'], result['prototypes']
            _message('DONE', f"Выбор применён без Revit: {result['placements']} размещений; скрыто {result['removed']}, восстановлено {result.get('restored', 0)}.", progress=1)
            return
        roots = [root for root in scene.collection.children_recursive if root.get(OWNER) == props.import_id and root.get(ROOT_FLAG)]
        if len(roots) == 1:
            try:
                available = set(json.loads(roots[0].get(AVAILABLE_IDS, roots[0].get(SELECTION, '[]'))))
                added = ids - available
                if added and props.origin_valid:
                    STATE.delta_ids = added
            except (ValueError, TypeError):
                pass  # Unknown metadata requires a complete source refresh.
    from .workflow import cache_root
    from .core.export_cache import export_signature, lookup_export
    from . import bl_info
    host = inspect_revit(bpy.path.abspath(props.revit_exe))
    key = '|'.join((host['key'], host.get('version', ''), os.path.normcase(str(Path(bpy.path.abspath(props.revit_exe)).resolve()))))
    STATE.cache_index = cache_root(props) / 'last_export.json'
    STATE.cache_signature = export_signature(source, ids, props.detail_level, key, '.'.join(map(str, bl_info['version'])))
    if not force_refresh and STATE.delta_ids is None and props.reuse_export_cache:
        manifest = lookup_export(STATE.cache_index, STATE.cache_signature)
        if manifest is not None:
            STATE.scene = scene
            STATE.file_root = manifest.parent
            _start_conversion(manifest)
            _timer_start()
            return
    if (STATE.session is None or STATE.worker is None or STATE.worker.poll() is not None
            or STATE.scene != scene or STATE.session.source_path != props.catalog_source
            or STATE.worker_exe != os.path.normcase(str(Path(bpy.path.abspath(props.revit_exe)).resolve()))
            or STATE.session.directory.parent != cache_root(props)
            or STATE.scanned_source_hash != source_hash):
        begin_scan(scene, auto_import=True)
        return
    STATE.scene = scene
    export_ids = STATE.delta_ids if STATE.delta_ids is not None else ids
    request = STATE.session.request('export', selection_ids=export_ids, options={'detail_level': props.detail_level})
    STATE.request_id = request['request_id']
    STATE.action = 'export'
    STATE.started = time.monotonic()
    _message('EXPORTING', ('Дозагрузка новых элементов: ' if STATE.delta_ids is not None else 'Выгрузка выбранных элементов: ') + str(len(export_ids)), busy=True, progress=0)
    _timer_start()


def _start_conversion(manifest):
    manifest = _within_job(manifest)
    target = manifest.with_name('scene.usdc')
    response = manifest.with_name('conversion.json')
    if STATE.file_root is not None:
        target = manifest.with_name('scene-' + uuid.uuid4().hex + '.usdc')
        response = target.with_suffix('.conversion.json')
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
    elif STATE.props.preview_source == STATE.props.catalog_source and STATE.props.preview_offset_json:
        command.extend(['--offset', *map(str, json.loads(STATE.props.preview_offset_json))])
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    temporary = (STATE.file_root or STATE.session.directory) / 'tmp'
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
        kwargs = dict(preserve_instances=STATE.pending_options['preserve_instances'],
                      pack_images=STATE.pending_options['pack_images'], source_path=props.catalog_source)
        if STATE.delta_ids is not None:
            from .blender_import import merge_result
            result = merge_result(STATE.converted_usd, import_id=props.import_id,
                selected_element_ids=STATE.delta_ids, final_selection_ids=STATE.pending_selected_ids, **kwargs)
            if result is None:
                _fallback_full('Составные элементы требуют полной выгрузки; предыдущая модель сохранена.')
                return
        else:
            result = import_result(STATE.converted_usd, import_id=props.import_id or None,
                                  selected_element_ids=STATE.pending_selected_ids, **kwargs)
    props.import_id = result['import_id']
    props.import_source = props.catalog_source
    props.import_context_json = STATE.pending_context
    props.source_offset_json = json.dumps(result['source_offset'])
    props.origin_valid = True
    props.origin_centered = STATE.pending_options['center_model']
    props.result_collection = result['collection']
    from .spatial_preview import align_previews
    from .ui import imported_root
    align_previews(STATE.scene, result['source_offset'] if props.origin_centered else (0, 0, 0),
                   imported_root(STATE.scene, props))
    _warnings(raw_values(props.warnings) + ['Недоступно изображение: ' + path for path in result['missing_images']])
    props.result_placements = result['placements']
    props.result_prototypes = result['prototypes']
    STATE.action = None
    STATE.converter = None
    STATE.force_refresh = False
    STATE.force_scan_done = False
    _message('DONE', f"Импортировано: {result['placements']} размещений, {result['prototypes']} прототипов.", progress=1)


def _fallback_full(message):
    STATE.action = None
    STATE.converter = None
    STATE.delta_ids = None
    STATE.force_scan_done = False
    _message('EXPORTING', message, busy=False, progress=0)
    begin_export(STATE.scene, force_refresh=True)


def tick():
    try:
        if STATE.action is None:
            return None
        if STATE.scene is None:
            # Do not orphan a live worker after its receiving scene is removed.
            _record_stage('scene_removed', terminal=True)
            STATE.stop()
            return None
        if STATE.action == 'convert':
            code = STATE.converter.poll()
            if code is None:
                return 0.25
            response = read_json(STATE.conversion_response)
            if code != 0 or not response or not response.get('success'):
                raise RuntimeError((response or {}).get('error', 'Не удалось подготовить геометрию; см. conversion.log'))
            _warnings(raw_values(STATE.props.warnings) + ['Не найдена текстура: ' + path for path in response.get('missing_textures', [])])
            _apply_import()
            return .25 if STATE.action else None
        response = STATE.session.read_response(STATE.request_id)
        if response is not None:
            _warnings(response.get('warnings', []))
            if not response['success']:
                error = str(response.get('error', 'Revit не завершил задание'))
                if STATE.action == 'export' and STATE.delta_ids is not None and error.startswith('Выбранные элементы не дали геометрии'):
                    _fallback_full('Новые элементы не дали сетки; проверка полного набора с сохранением текущей модели.')
                    return .25
                raise RuntimeError(error)
            if STATE.action == 'scan':
                STATE.action = None
                _load_catalog(response['catalog_path'])
                return 0.25 if STATE.action else None
            if STATE.action == 'export':
                if STATE.delta_ids is None:
                    from .core.export_cache import record_export, CacheError
                    try:
                        record_export(STATE.cache_index, STATE.cache_signature, _within_job(response['manifest_path']))
                    except (CacheError, OSError) as error:
                        _warnings(raw_values(STATE.props.warnings) + ['Кэш не сохранён: ' + str(error)])
                _start_conversion(response['manifest_path'])
                return 0.25
        status = STATE.session.read_status()
        if status:
            name = str(status.get('state', '')).upper()
            allowed = {'OPENING': 'SCANNING', 'SCANNING': 'SCANNING', 'EXPORTING': 'EXPORTING',
                       'REQUIRES_ACTION': 'REQUIRES_ACTION', 'STARTING': 'STARTING'}
            _message(allowed.get(name, STATE.props.status), str(status.get('message', 'Обработка модели…')),
                     busy=True, progress=status.get('progress', STATE.props.progress), timing_stage=name.lower() or 'worker')
        elif time.monotonic() - STATE.started > 45:
            _message('STARTING', 'Revit запускается. Если появилось его окно с вопросом, используйте «Показать Revit».', busy=True)
        if STATE.worker.poll() is not None:
            raise RuntimeError('Процесс Revit завершился до получения ответа. Журнал: ' + str(STATE.session.directory))
        return 0.25
    except Exception as error:
        if STATE.scene is not None:
            try:
                record_failure(STATE.scene, error)
            except ReferenceError:
                pass
        STATE.action = None
        STATE.force_refresh = False
        STATE.force_scan_done = False
        return None


def cancel(scene=None):
    owner = STATE.scene
    signal_error = None
    try:
        if STATE.session and STATE.request_id:
            STATE.session.cancel(STATE.request_id)
    except (OSError, ValueError) as error:
        signal_error = str(error)
    finally:
        # A missing scene or an unwritable cancel file must not block stopping.
        STATE.stop()
    STATE.force_refresh = False
    STATE.force_scan_done = False
    if owner is not None:
        _message('IDLE', 'Задание отменено. Предыдущий результат сохранён.')
        if signal_error:
            set_warnings(STATE.props, raw_values(STATE.props.warnings) +
                         ['Файл отмены недоступен; процесс остановлен напрямую: ' + signal_error])
    elif scene is not None and scene.library is None:
        props = scene.xivgate_revit_bridge
        props.busy = False
        props.status = 'IDLE'
        props.message = 'Задание остановлено. Исходная сцена задания больше не существует.'


def show_worker():
    """Only called by an explicit user button; never by the development agent."""
    if os.name != 'nt' or not STATE.worker or not STATE.worker.pid or STATE.worker.poll() is not None:
        raise RuntimeError('Обработчик Revit не запущен')
    from .core.launcher import show_main_window
    show_main_window(STATE.worker.pid)


def cleanup(*_):
    STATE.stop()
    STATE.scene = None
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
