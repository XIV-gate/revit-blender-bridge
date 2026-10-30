"""Exercise verified updates/hot reload only inside an isolated installed add-on."""
import ctypes
from ctypes import wintypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch, Mock
import zipfile

import bpy
import addon_utils

WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge import updater  # Red initially: updater does not exist.
for name in list(sys.modules):
    if name == 'xivgate_revit_bridge' or name.startswith('xivgate_revit_bridge.'):
        del sys.modules[name]
sys.path.remove(os.environ['RBB_SOURCE_DIR'])
run = WORK / ('update_integration_' + str(os.getpid()))
run.mkdir()
base = WORK / os.environ['RBB_PACKAGE_NAME']
assert bpy.ops.preferences.addon_install(filepath=str(base), overwrite=True) == {'FINISHED'}
addon_utils.modules_refresh()
module = addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False)
assert module is not None
assert module.bl_info['name'] == 'AGR Revit Bridge'
assert module.ui.RBB_PT_panel.bl_category == 'AGR'
from xivgate_revit_bridge.core.installer import manifest_plan, install_manifest


def future_archive(version, broken=False):
    with zipfile.ZipFile(base) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    prefix = 'xivgate_revit_bridge/'
    manifest = json.loads(files.pop(prefix + 'PACKAGE_MANIFEST.json'))
    old_worker = manifest['worker']
    new_worker = f'runtime/revit/2027/{version}/XIVgate.RevitBridge.Worker.dll'
    files[prefix + new_worker] = files.pop(prefix + old_worker)
    init = files[prefix + '__init__.py'].decode()
    init = re.sub(r'"version": \(\d+, \d+, \d+\)', '"version": ' + str(tuple(map(int, version.split('.')))), init)
    if broken:
        init += '\ndef register():\n    raise RuntimeError("intentional reload fixture")\n'
    files[prefix + '__init__.py'] = init.encode()
    files[prefix + 'runtime.py'] += ('\n_update_test_marker = ' + repr(version) + '\n').encode()
    files[prefix + 'VERSION'] = (version + '\n').encode()
    manifest.update(version=version, worker=new_worker,
                    files={name[len(prefix):]: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    files[prefix + 'PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
    target = run / f'agr_revit_bridge-{version}.zip'
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return target


def info_for(path, version):
    return dict(version=version, available=True, asset_name=path.name,
        url=f'https://github.com/XIV-gate/revit-blender-bridge/releases/download/v{version}/{path.name}',
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size,
        release_url=f'https://github.com/XIV-gate/revit-blender-bridge/releases/tag/v{version}')


def current():
    return importlib.import_module('xivgate_revit_bridge.updater')


def advance():
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = current()
        value.tick()
        value = current()  # tick may replace this module during a successful hot reload.
        if not value.STATE.busy:
            return value
        time.sleep(.02)
    raise AssertionError('Updater timed out')


scene = bpy.context.scene
props = scene.xivgate_revit_bridge
props.source_rvt = 'user-selected-model.rvt'
props.cache_directory = str(run)
props.import_id = 'preserve-import-identity'
props.import_materials = False
props.addin_folder = str(run / 'RevitAddins')
props.detail_level = 'fine'
props.busy = True
row = props.elements.add(); row.element_id = 'kept'; row.unique_id = 'stable-id'; row.selected = False
props.busy = False
old_worker = module.runtime.worker_path()
old_bytes = old_worker.read_bytes()
legacy_worker = old_worker.parent.parent / old_worker.name
legacy_worker.write_bytes(b'old unversioned worker fixture; never executed')
plan = manifest_plan('', legacy_worker, addin_folder=props.addin_folder)
registration = install_manifest(plan)
assert bpy.ops.xivgate_rbb.setup_revit() == {'FINISHED'}
import xml.etree.ElementTree as ET
assert ET.parse(registration).findtext('./AddIn/Assembly') == str(old_worker)
original_cube = bpy.data.objects.get('Cube')

# Match a Windows loader's read lock: no write/delete access to the old DLL.
api = ctypes.WinDLL('kernel32', use_last_error=True)
api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
api.CreateFileW.restype = wintypes.HANDLE
api.CloseHandle.argtypes = [wintypes.HANDLE]
handle = api.CreateFileW(str(old_worker), 0x80000000, 1, None, 3, 0, None)
assert handle not in (None, ctypes.c_void_p(-1).value)

class FakeWorker:
    closed = False
    def poll(self): return 0 if self.closed else None
    def terminate(self): assert self.closed, 'Updater must never force-kill a worker'

class FakeSession:
    requested = None
    def request(self, action):
        self.requested = action; worker.closed = True
        return {'request_id': 'owned-shutdown'}
    def read_response(self, request_id): return {'success': True}

worker, session = FakeWorker(), FakeSession()
module.runtime.STATE.worker = worker; module.runtime.STATE.session = session
try:
    update = current()
    with patch.object(update, 'online_allowed', return_value=False), patch.object(update.updates, 'fetch_release') as network:
        try:
            update.start_check()
        except RuntimeError:
            pass
        else:
            raise AssertionError('Offline setting was ignored')
        network.assert_not_called()
    archive = future_archive('0.2.1')
    update.start_check(fetcher=lambda version: info_for(archive, '0.2.1'))
    update = advance()
    assert update.STATE.info['version'] == '0.2.1'
    class DeniedSession:
        def request(self, action): return {'request_id': 'denied-shutdown'}
        def read_response(self, request_id): return {'success': False, 'error': 'foreign document remains open'}
    module.runtime.STATE.session = DeniedSession()
    update.start_install(downloader=lambda info, directory: archive)
    update = advance()
    assert update.STATE.phase == 'ERROR' and not worker.closed
    assert module.bl_info['version'] == (0, 2, 0) and old_worker.read_bytes() == old_bytes
    assert not module.runtime.MAINTENANCE
    module.runtime.STATE.session = session
    update.start_install(downloader=lambda info, directory: archive)
    try:
        module.runtime.begin_scan(scene)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Scan must be blocked while installing an update')
    update = advance()
    module = sys.modules['xivgate_revit_bridge']
    assert module.bl_info['version'] == (0, 2, 1), update.STATE.message
    assert update.STATE.phase == 'DONE', update.STATE.message
    assert module.runtime._update_test_marker == '0.2.1'
    assert session.requested == 'shutdown' and worker.closed
    assert old_worker.read_bytes() == old_bytes
    assert ET.parse(registration).findtext('./AddIn/Assembly') == str(module.runtime.worker_path())
    props = scene.xivgate_revit_bridge
    assert props.source_rvt == 'user-selected-model.rvt' and props.import_id == 'preserve-import-identity'
    assert not props.import_materials and props.detail_level == 'fine'
    assert props.elements[0].unique_id == 'stable-id' and not props.elements[0].selected
    assert original_cube in bpy.data.objects.values()
    # A syntax-valid but broken new register() must restore code and the Revit pointer.
    archive = future_archive('0.2.2', broken=True)
    update.start_check(fetcher=lambda version: info_for(archive, '0.2.2'))
    update = advance()
    update.start_install(downloader=lambda info, directory: archive)
    update = advance()
    module = sys.modules['xivgate_revit_bridge']
    assert module.bl_info['version'] == (0, 2, 1)
    assert module.runtime._update_test_marker == '0.2.1'
    assert update.STATE.phase == 'ERROR', update.STATE.message
    assert ET.parse(registration).findtext('./AddIn/Assembly') == str(module.runtime.worker_path())
    assert scene.xivgate_revit_bridge.source_rvt == 'user-selected-model.rvt'
    assert original_cube in bpy.data.objects.values()
    # Opening a blend cancels pending work and discards late background results.
    fixture_blend = run / 'load-cancel.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(fixture_blend), check_existing=False)
    for phase in ('check', 'download'):
        ready = threading.Event()
        def delayed(version):
            ready.wait(5)
            return info_for(archive, '0.2.2')
        def delayed_download(info, directory):
            ready.wait(5)
            return archive
        if phase == 'check':
            update.start_check(fetcher=delayed)
        else:
            update.start_check(fetcher=lambda version: info_for(archive, '0.2.2'))
            update = advance()
            update.start_install(downloader=delayed_download)
            assert module.runtime.MAINTENANCE
        assert update.STATE.busy
        bpy.ops.wm.open_mainfile(filepath=str(fixture_blend))
        ready.set()
        time.sleep(.1)
        update = current(); update.tick()
        assert not update.STATE.busy and update.STATE.phase == 'IDLE'
        assert not module.runtime.MAINTENANCE and update.STATE.scene is None
    # Even a failed registration rollback must release the installation OS lock.
    # Mock only this double-failure branch: never damage the real installed test package.
    package_transaction, registration_transaction = Mock(), Mock()
    registration_transaction.rollback.side_effect = RuntimeError('intentional registration rollback failure')
    update.STATE.scene = bpy.context.scene
    update.STATE.work = run / 'double-failure'
    update.STATE.work.mkdir()
    update.STATE.info = dict(version='0.2.2', asset_name='unused.zip')
    update.STATE.package = SimpleNamespace(manifest={'worker': 'runtime/revit/2027/0.2.2/XIVgate.RevitBridge.Worker.dll'})
    with patch.object(update.updates, 'install_package', return_value=package_transaction), \
         patch.object(update.updates, 'repoint_registration', return_value=registration_transaction), \
         patch.object(update, '_safe_unregister'), patch.object(update, '_purge_children'), \
         patch.object(update.importlib, 'reload', side_effect=RuntimeError('intentional reload failure')):
        update._perform_install()
    package_transaction.release_lock.assert_called_once()
finally:
    api.CloseHandle(handle)
addon_utils.disable('xivgate_revit_bridge', default_set=False)
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
report = dict(status='passed', blender=bpy.app.version_string, network='injected test transport',
    hot_reload=True, legacy_registration_migrated=True, graceful_worker_shutdown=True, locked_old_dll_preserved=True,
    registration_repointed=True, broken_release_rolled_back=True, settings_preserved=True,
    scene_preserved=True, file_load_cancels_pending_update=True, denied_shutdown_aborts_update=True,
    new_modules_reloaded=True, rollback_failure_releases_lock=True, preferences_not_saved=True)
(run / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('AGR_UPDATE_INTEGRATION ' + json.dumps(report), flush=True)
