"""Operation errors remain inspectable after both immediate and asynchronous failure."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
from xivgate_revit_bridge import runtime
from xivgate_revit_bridge.core.jobs import JobSession, atomic_write_json
from xivgate_revit_bridge.core.reporting import raw_values

addon.register()
scene = bpy.context.scene
props = scene.xivgate_revit_bridge
try:
    bpy.ops.xivgate_rbb.import_selected()
except RuntimeError:
    pass  # Blender also returns the operator report as a Python exception.
assert props.status == 'ERROR', props.status
assert props.warning_count == 1
first = raw_values(props.warnings)[0]
assert 'Ошибка операции' in first and props.message
props.busy = True
props.status = 'EXPORTING'
runtime.STATE.action = 'export'
try:
    bpy.ops.xivgate_rbb.import_selected()
except RuntimeError:
    pass
assert props.busy and props.status == 'EXPORTING' and props.warning_count == 1
runtime.STATE.action = None
props.busy = False

session = JobSession.create(Path(os.environ['RBB_TEST_ROOT']) / 'runtime_failure_jobs')
runtime.STATE.scene = scene
runtime.STATE.session = session
runtime.STATE.request_id = 'failure-fixture'
runtime.STATE.action = 'convert'
runtime.STATE.converter = SimpleNamespace(poll=lambda: 1)
runtime.STATE.conversion_response = session.directory / 'conversion_response.json'
atomic_write_json(runtime.STATE.conversion_response, dict(success=False, error='fixture converter failed'))
assert runtime.tick() is None
assert not props.busy and runtime.STATE.action is None
assert props.status == 'ERROR' and 'fixture converter failed' in props.message
values = raw_values(props.warnings)
assert len(values) == 2 and 'fixture converter failed' in values[-1]
assert 'Traceback' in values[-1] and str(session.directory) in values[-1]
logs = list(session.directory.glob('blender_error_*.json'))
assert len(logs) == 1
diagnostic = json.loads(logs[0].read_text(encoding='utf-8'))
assert diagnostic['action'] == 'convert' and diagnostic['request_id'] == 'failure-fixture'
assert diagnostic['error'] == 'fixture converter failed'
assert 'session_token' not in diagnostic
runtime.STATE.converter = None
runtime.STATE.action = 'export'
runtime.STATE.request_id = 'revit-export-failure'
atomic_write_json(session.directory / 'response.json', dict(schema=1, session_id=session.session_id,
    request_id=runtime.STATE.request_id, success=False, error='fixture Revit export failed', warnings=['fixture warning']))
assert runtime.tick() is None
assert props.status == 'ERROR' and not props.busy and runtime.STATE.action is None
assert props.warning_count == 2
assert raw_values(props.warnings)[0] == 'fixture warning'
assert 'fixture Revit export failed' in raw_values(props.warnings)[-1]
assert props.warnings[-1].severity == 'ERROR'
assert props.warning_index == len(props.warnings)-1
assert len(list(session.directory.glob('blender_error_*.json'))) == 2
terminated = []
runtime.STATE.worker = SimpleNamespace(pid=123456, poll=lambda: 0 if terminated else None,
    terminate=lambda: terminated.append(True))
runtime.STATE.request_id = 'loader-startup-failure'
runtime.STATE.action = 'scan'
runtime.STATE.started = runtime.time.monotonic()
(session.directory / 'loader_error.txt').write_text('Fixture loader hash mismatch\nDetailed test traceback', encoding='utf-8')
assert runtime.tick() is None
assert props.status == 'ERROR' and not props.busy and runtime.STATE.action is None
assert 'Fixture loader hash mismatch' in props.message
assert terminated, 'A rejected loader must not leave a hidden Revit waiting forever'
assert len(list(session.directory.glob('blender_error_*.json'))) == 3
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_RUNTIME_FAILURES passed', bpy.app.version_string)
