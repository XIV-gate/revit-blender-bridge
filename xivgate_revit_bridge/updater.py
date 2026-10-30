"""Main-thread update controller; network threads never call Blender/Revit APIs."""
from dataclasses import dataclass, field
import hashlib
import importlib
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

import bpy

from . import runtime
from .core import updates
from .core.installer import manifest_plan


@dataclass
class UpdateState:
    phase: str = 'IDLE'
    message: str = ''
    info: dict | None = None
    generation: int = 0
    results: queue.Queue = field(default_factory=queue.Queue)
    lock: object = field(default_factory=threading.Lock)
    scene: object = None
    package: object = None
    work: Path | None = None
    shutdown_id: str | None = None
    shutdown_deadline: float = 0

    @property
    def busy(self):
        return self.phase in {'CHECKING', 'DOWNLOADING', 'CLOSING', 'INSTALLING', 'RELOADING'}


STATE = UpdateState()


def current_version():
    from . import bl_info
    return '.'.join(map(str, bl_info['version']))


def online_allowed():
    return bool(bpy.app.online_access)


def connect_revit(plan):
    if runtime.MAINTENANCE or runtime.STATE.action is not None:
        raise RuntimeError('Дождитесь завершения текущей операции')
    root = Path(__file__).resolve().parent
    storage = root.parent / '.agr_revit_bridge_updates'
    storage.mkdir(exist_ok=True)
    work = storage / uuid.uuid4().hex
    work.mkdir()
    try:
        return updates.connect_registration(plan, root, work)
    finally:
        try:
            work.rmdir()
        except OSError:
            pass


def _redraw():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type in {'VIEW_3D', 'PREFERENCES'}:
                area.tag_redraw()


def _set(phase, message):
    STATE.phase, STATE.message = phase, message
    _redraw()


def _spawn(kind, function, discard=None):
    state = STATE
    generation, results = state.generation, state.results
    def run():
        try:
            success, value = True, function()
        except Exception as error:
            success, value = False, str(error)
        with state.lock:
            cancelled = generation != state.generation
            if not cancelled:
                results.put((generation, kind, success, value, discard))
        if cancelled and discard is not None:
            discard()
    threading.Thread(target=run, name='AGR Revit update', daemon=True).start()
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=.1)


def start_check(*, fetcher=None):
    if STATE.busy:
        raise RuntimeError('Проверка или установка обновления уже выполняется')
    if fetcher is None and not online_allowed():
        raise RuntimeError('В Blender отключён доступ в интернет. Разрешите его в настройках сети.')
    STATE.generation += 1
    STATE.info = None
    version = current_version()
    _set('CHECKING', 'Проверка последнего релиза на GitHub…')
    _spawn('release', lambda: (fetcher or updates.fetch_release)(version))


def start_install(*, downloader=None):
    if STATE.busy or runtime.MAINTENANCE or runtime.STATE.action is not None:
        raise RuntimeError('Дождитесь завершения текущей операции')
    if not STATE.info or not STATE.info.get('available'):
        raise RuntimeError('Сначала проверьте наличие новой версии')
    if downloader is None and not online_allowed():
        raise RuntimeError('В Blender отключён доступ в интернет')
    root = Path(__file__).resolve().parent
    if (root / '.git').exists() or (root.parent / '.git').exists():
        raise RuntimeError('Для обновлений установите ZIP плагина; исходный Git checkout обновляется через Git')
    storage = root.parent / '.agr_revit_bridge_updates'
    storage.mkdir(exist_ok=True)
    STATE.work = storage / uuid.uuid4().hex
    STATE.work.mkdir()
    STATE.scene = bpy.context.scene
    STATE.generation += 1
    runtime.MAINTENANCE = True
    info, work, blender_version = dict(STATE.info), STATE.work, tuple(bpy.app.version)
    _set('DOWNLOADING', 'Загрузка и проверка AGR Revit Bridge ' + info['version'] + '…')
    def prepare():
        path = Path((downloader or updates.download_release)(info, work))
        if hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
            raise updates.UpdateError('Контрольная сумма скачанного архива не совпадает')
        return updates.validate_archive(path, info['version'], blender_version)
    _spawn('package', prepare, lambda: _discard_work(work, info['asset_name']))


def _discard_work(work, asset_name):
    if work is None:
        return
    try:
        (work / asset_name).unlink(missing_ok=True)
        work.rmdir()
    except OSError:
        pass  # Never remove recovery snapshots or unrelated files to force cleanup.


def _begin_close():
    worker = runtime.STATE.worker
    if worker is not None and worker.poll() is None:
        if runtime.STATE.session is None:
            raise RuntimeError('Нельзя безопасно закрыть обработчик: отсутствует его сеанс')
        request = runtime.STATE.session.request('shutdown')
        STATE.shutdown_id = request['request_id']
        STATE.shutdown_deadline = time.monotonic() + 30
        _set('CLOSING', 'Закрытие рабочей копии Revit перед обновлением…')
    else:
        runtime.STATE.stop()
        _perform_install()


def _purge_children(package_name, root):
    """Remove package attributes too: importlib.reload alone retains old submodules."""
    names = sorted((name for name in sys.modules if name.startswith(package_name + '.')),
                   key=lambda name: name.count('.'), reverse=True)
    for name in names:
        module = sys.modules.pop(name)
        cached = getattr(module, '__cached__', None)
        if cached:
            path = Path(cached).resolve()
            if path.is_relative_to(root):
                path.unlink(missing_ok=True)
        parent_name, _, child = name.rpartition('.')
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, child, None) is module:
            delattr(parent, child)
    # The root module itself can also have a cached bl_info/register() definition.
    cached = getattr(sys.modules[package_name], '__cached__', None)
    if cached and Path(cached).resolve().is_relative_to(root):
        Path(cached).unlink(missing_ok=True)
    importlib.invalidate_caches()


def _safe_unregister(package):
    try:
        package.unregister()
        return
    except Exception:
        pass
    # Recover registration that failed halfway through its class list.
    ui = getattr(package, 'ui', None)
    if ui is None:
        return
    cleanup()
    try:
        getattr(package, 'runtime', runtime).cleanup()
    except Exception:
        pass
    handler = getattr(ui, '_load_pre', None)
    if handler in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(handler)
    if hasattr(bpy.types.Scene, 'xivgate_revit_bridge'):
        del bpy.types.Scene.xivgate_revit_bridge
    for cls in reversed(getattr(ui, 'CLASSES', ())):
        if getattr(cls, 'is_registered', False):
            bpy.utils.unregister_class(cls)


def _publish(package, phase, message):
    target = importlib.import_module(package.__name__ + '.updater')
    target.STATE.phase, target.STATE.message = phase, message
    target.runtime.MAINTENANCE = False
    target._redraw()
    print('AGR Revit Bridge: ' + message)


def _perform_install():
    package_name = __package__
    module = sys.modules[package_name]
    root = Path(__file__).resolve().parent
    state, old_version = STATE, current_version()
    package_transaction = registration_transaction = None
    try:
        _set('INSTALLING', 'Установка проверенного обновления…')
        props = state.scene.xivgate_revit_bridge
        kwargs = {'addin_folder': props.addin_folder} if props.addin_folder else {}
        plan = manifest_plan(bpy.path.abspath(props.revit_exe), root / state.package.manifest['worker'], **kwargs)
        registration_path = Path(plan['path'])
        prior_registration = registration_path.read_text(encoding='utf-8-sig') if registration_path.exists() else None
        package_transaction = updates.install_package(state.package, root, state.work)
        if prior_registration is not None:
            registration_transaction = updates.repoint_registration(plan, prior_registration, state.work, root)
        _set('RELOADING', 'Перезагрузка AGR Revit Bridge…')
        _safe_unregister(module)
        _purge_children(package_name, root)
        module = importlib.reload(module)
        module.register()
        version = '.'.join(map(str, module.bl_info['version']))
        if version != state.info['version']:
            raise RuntimeError('Загруженная версия не соответствует проверенному релизу')
        # Commit only after code registration succeeded; until here both changes can roll back.
        # Cleanup failures after activation must not trigger a half-rollback.
        cleanup_messages = []
        for transaction in (registration_transaction, package_transaction):
            if transaction is not None:
                try:
                    transaction.commit()
                except OSError as error:
                    cleanup_messages.append(str(error))
        state.phase = 'DONE'
        message = 'AGR Revit Bridge обновлён до ' + version
        if cleanup_messages:
            message += '. Резервные файлы оставлены в ' + str(state.work)
        _publish(module, 'DONE', message)
    except Exception as error:
        rollback_error = None
        try:
            _safe_unregister(module)
            if registration_transaction is not None:
                registration_transaction.rollback()
            if package_transaction is not None:
                package_transaction.rollback()
            _purge_children(package_name, root)
            module = importlib.reload(sys.modules[package_name])
            module.register()
            state.phase = 'ERROR'
            _publish(module, 'ERROR', 'Обновление отменено; восстановлена версия ' + old_version + ': ' + str(error))
        except Exception as failure:
            rollback_error = failure
        if rollback_error is not None:
            runtime.MAINTENANCE = False
            state.phase, state.message = 'ERROR', 'Откат требует проверки. Копии: ' + str(state.work) + ': ' + str(rollback_error)
            print('AGR Revit Bridge: ' + state.message)
    finally:
        # Registration rollback may fail before package.rollback() is reached.
        # Never keep the cross-process installation lock in a traceback cycle.
        if package_transaction is not None:
            package_transaction.release_lock()
        # Successful transactions remove their snapshots. Keep failed recovery data.
        if state.phase in {'DONE', 'ERROR'} and state.work is not None:
            archive = state.work / (state.info or {}).get('asset_name', 'unused.zip')
            archive.unlink(missing_ok=True)
            try:
                state.work.rmdir()
            except OSError:
                pass


def tick():
    try:
        while True:
            try:
                generation, kind, success, result, discard = STATE.results.get_nowait()
            except queue.Empty:
                break
            if generation != STATE.generation:
                if discard is not None:
                    discard()
                continue
            if not success:
                raise RuntimeError(result)
            if kind == 'release':
                STATE.info = result
                if result['available']:
                    _set('AVAILABLE', 'Доступна версия ' + result['version'])
                else:
                    _set('CURRENT', 'Установлена актуальная версия ' + current_version())
            elif kind == 'package':
                STATE.package = result
                _begin_close()
                # Module replacement may have registered a new controller.
                if STATE.phase in {'DONE', 'ERROR'}:
                    return None
        if STATE.phase == 'CLOSING':
            response = runtime.STATE.session.read_response(STATE.shutdown_id)
            if response is not None and not response['success']:
                raise RuntimeError(response.get('error', 'Revit не разрешил закрытие рабочей копии'))
            if response is not None and runtime.STATE.worker.poll() is not None:
                runtime.STATE.stop()
                _perform_install()
                return None
            if time.monotonic() > STATE.shutdown_deadline:
                raise RuntimeError('Revit не закрыл рабочую копию. Обновление не установлено; проверьте его диалог.')
        return .1 if STATE.busy else None
    except Exception as error:
        runtime.MAINTENANCE = False
        _discard_work(STATE.work, (STATE.info or {}).get('asset_name', 'unused.zip'))
        STATE.package = None
        _set('ERROR', str(error))
        return None


def cleanup():
    with STATE.lock:
        STATE.generation += 1
        pending = []
        while not STATE.results.empty():
            pending.append(STATE.results.get_nowait())
    for _, _, _, _, discard in pending:
        if discard is not None:
            discard()
    if STATE.phase not in {'INSTALLING', 'RELOADING'}:
        _discard_work(STATE.work, (STATE.info or {}).get('asset_name', 'unused.zip'))
        STATE.phase = 'IDLE'
        STATE.scene = STATE.package = STATE.work = STATE.info = STATE.shutdown_id = None
        STATE.message = ''
        runtime.MAINTENANCE = False
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
