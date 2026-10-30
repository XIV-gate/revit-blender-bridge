"""Real old/new ZIP installation and stale .addin recovery, same Blender process."""
import hashlib
import importlib
import os
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

import addon_utils
import bpy

sys.dont_write_bytecode = True
work = Path(os.environ['RBB_TEST_ROOT'])
old_zip = work / os.environ['RBB_STABLE_PACKAGE']
new_zip = work / os.environ['RBB_EARLY_PACKAGE']
run = work / ('manual_zip_migration_' + str(os.getpid()))
run.mkdir()

assert bpy.ops.preferences.addon_install(filepath=str(old_zip), overwrite=True) == {'FINISHED'}
addon_utils.modules_refresh()
module = addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False)
assert module.bl_info['version'] == (0, 3, 0)
old_ui = module.ui
addon_root = Path(module.__file__).resolve().parent
registration_folder = run / 'RevitAddins'
registration_folder.mkdir()
stale_dll = addon_root / 'runtime/revit/2027/0.2.0/XIVgate.RevitBridge.Worker.dll'
assert not stale_dll.exists()
from xivgate_revit_bridge.core.installer import manifest_plan
stale = manifest_plan('', stale_dll, addin_folder=registration_folder)
registration = Path(stale['path'])
registration.write_text(stale['content'], encoding='utf-8')
scene = bpy.context.scene
scene.xivgate_revit_bridge.addin_folder = str(registration_folder)
scene.xivgate_revit_bridge.import_id = 'keep-scene-settings'
try:
    module.updater.connect_revit(module.runtime.setup_plan(scene.xivgate_revit_bridge))
except module.updater.updates.UpdateError:
    pass
else:
    raise AssertionError('The original 0.3.0 failure was not reproduced')

addon_utils.disable('xivgate_revit_bridge', default_set=False)
assert bpy.ops.preferences.addon_install(filepath=str(new_zip), overwrite=True) == {'FINISHED'}
addon_utils.modules_refresh()
module = addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False)
assert module.bl_info['version'] == (0, 4, 0)
assert module.ui is not old_ui, 'Manual ZIP install retained the old panel'
assert scene.xivgate_revit_bridge.import_id == 'keep-scene-settings'
scene.xivgate_revit_bridge.addin_folder = str(registration_folder)
assert bpy.ops.xivgate_rbb.setup_revit() == {'FINISHED'}
assert module.updater.updates.manifest_matches(module.runtime.setup_plan(scene.xivgate_revit_bridge))
assert ET.parse(registration).findtext('./AddIn/Assembly') == str(module.runtime.worker_path())
assert not stale_dll.exists(), 'Recovery must not recreate obsolete DLLs'

# The new updater must also recover a missing old DLL when switching releases.
registration.write_text(stale['content'], encoding='utf-8')
info = dict(version='0.3.0', channel='stable', available=False, installable=True,
            asset_name=old_zip.name, sha256=hashlib.sha256(old_zip.read_bytes()).hexdigest(),
            size=old_zip.stat().st_size, url='injected-local-download', release_url='injected-release')
module.updater.start_check(fetcher=lambda _: [info])
deadline = time.monotonic() + 30
def current():
    return importlib.import_module('xivgate_revit_bridge.updater')
def finish():
    while current().STATE.busy and time.monotonic() < deadline:
        current().tick(); time.sleep(.02)
    assert not current().STATE.busy
finish()
current().start_install(downloader=lambda _, directory: old_zip)
finish()
assert current().STATE.phase == 'DONE', current().STATE.message
module = sys.modules['xivgate_revit_bridge']
assert module.bl_info['version'] == (0, 3, 0)
assert current().updates.manifest_matches(module.runtime.setup_plan(scene.xivgate_revit_bridge))
assert scene.xivgate_revit_bridge.import_id == 'keep-scene-settings'
addon_utils.disable('xivgate_revit_bridge', default_set=False)
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_MANUAL_ZIP_MIGRATION passed: old failure reproduced, same-process UI reload, '
      'setup and updater stale-pointer recovery, preferences untouched;', bpy.app.version_string)
