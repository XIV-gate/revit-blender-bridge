"""Switch actual stable/EA ZIPs in an isolated profile; no live Revit is launched."""
import hashlib
import importlib
import os
from pathlib import Path
import sys
import time
import zipfile

import addon_utils
import bpy

sys.dont_write_bytecode = True
root = Path(os.environ['RBB_TEST_ROOT'])
stable = root / os.environ['RBB_STABLE_PACKAGE']
early = root / os.environ['RBB_EARLY_PACKAGE']
with zipfile.ZipFile(early) as archive:
    early_version = archive.read('xivgate_revit_bridge/VERSION').decode().strip()
assert bpy.ops.preferences.addon_install(filepath=str(stable), overwrite=True) == {'FINISHED'}
addon_utils.modules_refresh()
module = addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False)
assert module is not None
scene = bpy.context.scene
run = root / ('release_switch_' + str(os.getpid()))
run.mkdir()
scene.xivgate_revit_bridge.addin_folder = str(run / 'RevitAddins')
module.updater.connect_revit(module.runtime.setup_plan(scene.xivgate_revit_bridge))
scene.xivgate_revit_bridge.import_id = 'persistent-import-identity'
scene.xivgate_revit_bridge.source_rvt = 'test-only-model.rvt'
scene.xivgate_revit_bridge.preserve_instances = True
cube = bpy.data.objects.get('Cube')


def controller():
    return importlib.import_module('xivgate_revit_bridge.updater')


def wait():
    deadline = time.monotonic() + 30
    while controller().STATE.busy and time.monotonic() < deadline:
        controller().tick(); time.sleep(.02)
    assert not controller().STATE.busy


for version, path, channel in [(early_version, early, 'early'), ('0.3.0', stable, 'stable')]:
    info = dict(version=version, available=version == early_version, installable=True, channel=channel,
                asset_name=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                size=path.stat().st_size, url='injected-test-only', release_url='injected-test-only')
    # The UI controls the channel and selected version; download is the only I/O double.
    scene.xivgate_revit_bridge.release_channel = channel
    controller().start_check(fetcher=lambda _: [info])
    wait()
    assert scene.xivgate_revit_bridge.release_version == version
    assert controller().STATE.info['channel'] == channel
    controller().start_install(downloader=lambda _, directory: path)
    wait()
    assert controller().STATE.phase == 'DONE', controller().STATE.message
    assert controller().current_version() == version
    props = scene.xivgate_revit_bridge
    assert props.import_id == 'persistent-import-identity'
    assert props.source_rvt == 'test-only-model.rvt'
    assert props.preserve_instances
    assert cube in bpy.data.objects.values()
    assert hasattr(props, 'reuse_export_cache') == (version == early_version)

addon_utils.disable('xivgate_revit_bridge', default_set=False)
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_REAL_RELEASE_SWITCH passed', bpy.app.version_string)
