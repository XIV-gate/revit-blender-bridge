"""Actual Blender operator, live RAM/driver probe and bounded ZIP diagnostics."""
import json
import os
from pathlib import Path
import sys
import zipfile

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
scene = bpy.context.scene
props = scene.xivgate_revit_bridge
session = JobSession.create(Path(os.environ['RBB_TEST_ROOT']) / 'log_export_jobs')
request = session.request('export', selection_ids=['1'])
props.job_directory = str(session.directory)
runtime.STATE.scene, runtime.STATE.session = scene, session
runtime.STATE.request_id = request['request_id']
runtime.STATE.action = 'export'
(session.directory / 'worker.log').write_text('fake job token ' + session.token, encoding='utf-8')
(session.directory / 'input' / 'original.rvt').write_bytes(b'PRIVATE RVT')
runtime._message('EXPORTING', 'Fixture export', busy=True)
runtime._message('CONVERTING', 'Fixture conversion', busy=True)
path = session.directory.parent / ('logs-' + session.session_id + '.zip')
assert bpy.ops.xivgate_rbb.export_logs(filepath=str(path)) == {'FINISHED'}
assert props.busy and runtime.STATE.action == 'export'  # Export never cancels or changes the job.
with zipfile.ZipFile(path) as archive:
    report = json.loads(archive.read('diagnostics.json'))
    snapshot = report['snapshot']
    assert snapshot['busy'] and snapshot['blender_version'] == bpy.app.version_string
    assert snapshot['timings']['stages'][-1]['running']
    assert snapshot['memory']['ram']['available_bytes'] > 0
    assert snapshot['memory']['ram']['total_bytes'] >= snapshot['memory']['ram']['available_bytes']
    assert snapshot['memory']['processes'][0]['name'] == 'blender'
    assert 'gpu_memory' in snapshot
    assert 'RAM доступно' in archive.read('summary.txt').decode()
    payload = b' '.join(archive.read(name) for name in archive.namelist())
    assert session.token.encode() not in payload and b'PRIVATE RVT' not in payload
    assert any(name.endswith('client_timing.json') for name in archive.namelist())
runtime._message('DONE', 'Fixture complete')
runtime.STATE.action = None
if os.environ.get('RBB_PACKAGE_NAME'):
    addon_utils.disable('xivgate_revit_bridge', default_set=False)
else:
    addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_LOG_EXPORT passed', bpy.app.version_string, str(path))
