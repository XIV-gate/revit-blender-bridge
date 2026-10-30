"""Deleted/replaced Scene RNA must never prevent cancellation or orphan a worker."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

sys.dont_write_bytecode = True
if os.environ.get('RBB_PACKAGE_NAME'):
    import addon_utils
    package = Path(os.environ['RBB_TEST_ROOT']) / os.environ['RBB_PACKAGE_NAME']
    bpy.ops.preferences.addon_install(filepath=str(package), overwrite=True)
    addon_utils.modules_refresh()
    assert addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False) is not None
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
from xivgate_revit_bridge import runtime
from xivgate_revit_bridge.core.jobs import JobSession

if not os.environ.get('RBB_PACKAGE_NAME'):
    addon.register()
else:
    assert Path(addon.__file__).is_relative_to(Path(os.environ['BLENDER_USER_SCRIPTS']))
root = Path(os.environ['RBB_TEST_ROOT']) / 'scene_lifecycle_jobs'


class Worker:
    pid = None
    stopped = False
    def poll(self): return 0 if self.stopped else None
    def terminate(self): self.stopped = True


def task():
    scene = bpy.data.scenes.new('Transient owner')
    worker = Worker()
    session = JobSession.create(root)
    request = session.request('export', selection_ids=['1'])
    runtime.STATE.scene, runtime.STATE.session, runtime.STATE.worker = scene, session, worker
    runtime.STATE.request_id, runtime.STATE.action = request['request_id'], 'export'
    scene.xivgate_revit_bridge.busy = True
    scene.xivgate_revit_bridge.job_directory = str(session.directory)
    return scene, worker


scene, worker = task()
name = scene.name
bpy.data.scenes.remove(scene)
replacement = bpy.data.scenes.new(name)
runtime.cancel()
assert worker.stopped
assert runtime.STATE.scene is None, 'A new Scene with the same name must not inherit the job'
assert runtime.STATE.action is None
assert replacement.xivgate_revit_bridge.status == 'IDLE'

scene, worker = task()
bpy.data.scenes.remove(scene)
assert runtime.tick() is None
assert worker.stopped, 'Missing scene must stop the process, not just stop polling'

scene, worker = task()
scene.name = 'Renamed owner'
assert runtime.STATE.scene == scene
with patch.object(runtime.STATE.session, 'cancel', side_effect=OSError('fixture cancel signal unavailable')):
    runtime.cancel()
assert worker.stopped and not scene.xivgate_revit_bridge.busy
assert runtime.STATE.action is None

scene, worker = task()
bpy.context.window.scene = scene
assert bpy.ops.xivgate_rbb.cancel() == {'FINISHED'}
assert worker.stopped and not bpy.context.scene.xivgate_revit_bridge.busy
assert bpy.context.scene.xivgate_revit_bridge.status == 'IDLE'

scene, worker = task()
bpy.context.window.scene = scene
uid = scene.session_uid
bpy.ops.ed.undo_push(message='Before scene change')
scene.name = 'Undo renamed owner'
bpy.ops.ed.undo_push(message='After scene change')
assert bpy.ops.ed.undo() == {'FINISHED'}
fresh = runtime.STATE.scene
assert fresh is not None and fresh.session_uid == uid
runtime.cancel(fresh)
assert worker.stopped and not runtime.STATE.scene.xivgate_revit_bridge.busy

if os.environ.get('RBB_PACKAGE_NAME'):
    addon_utils.disable('xivgate_revit_bridge', default_set=False)
else:
    addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_SCENE_LIFECYCLE passed', bpy.app.version_string)
