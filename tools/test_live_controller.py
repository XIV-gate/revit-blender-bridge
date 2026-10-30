"""Opt-in real reimport against a worker launched and recorded by the test owner.

The harness alone adopts that explicitly supplied session. Production never
attaches to an existing Revit process. Root remains responsible for shutdown.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time

import bpy

SOURCE = Path(os.environ['RBB_SOURCE_DIR'])
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
import xivgate_revit_bridge
from xivgate_revit_bridge import runtime, ui
from xivgate_revit_bridge.blender_import import OWNER
from xivgate_revit_bridge.core.jobs import JobSession, read_json

record_path = Path(os.environ['RBB_LIVE_RECORD']).resolve()
assert record_path.is_relative_to(WORK)
record = read_json(record_path)
job = Path(record['directory']).resolve()
assert job.is_relative_to(WORK) and job.name == record['session_id']
response = read_json(job / 'response.json')
assert response['success']
manifest_path = (Path(record['first_export_output']) / 'manifest.json').resolve()
assert manifest_path.is_relative_to(job)
manifest = read_json(manifest_path)


class OwnedProbe:
    """Query only the explicitly recorded test child; never close it from this harness."""
    def __init__(self, pid):
        self.pid = int(pid)
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.OpenProcess(0x1000, False, self.pid)
        assert self.handle

    def poll(self):
        code = wintypes.DWORD()
        assert self.api.GetExitCodeProcess(self.handle, ctypes.byref(code))
        return None if code.value == 259 else code.value

    def terminate(self):
        pass  # Test owner performs its authenticated shutdown after this process exits.

    def close(self):
        self.api.CloseHandle(self.handle)


def wait_done(seconds=180):
    deadline = time.monotonic() + seconds
    while runtime.STATE.action and time.monotonic() < deadline:
        runtime.tick()
        time.sleep(0.1)
    assert runtime.STATE.action is None, 'Real controller timeout'
    assert props.status == 'DONE', props.message


xivgate_revit_bridge.register()
scene = bpy.context.scene
props = scene.xivgate_revit_bridge
props.source_rvt = record['source_path']
props.cache_directory = str(WORK)
props.busy = True
session = JobSession(job, record['session_id'], record['token'])
session.source_path = record['source_path']; session.source_copy = record['source_copy']
session.request_id = record['request_id']
probe = OwnedProbe(record['pid'])
runtime.STATE.scene = scene; runtime.STATE.session = session; runtime.STATE.worker = probe
runtime._load_catalog(job / 'catalog.json')
assert len(props.elements) == len(read_json(job / 'catalog.json')['elements'])
runtime.STATE.pending_options = dict(preserve_instances=True, materials=True, textures=True, pack_images=True, center_model=True)
cached = manifest_path.with_name('scene.usdc')
if cached.exists():
    runtime.STATE.converted_usd = cached
    runtime._apply_import()
else:
    runtime._start_conversion(manifest_path)
    wait_done()
assert props.result_placements == len(manifest['occurrences'])
identity = props.import_id
positions = {obj['revit:id']: obj.matrix_world.copy() for obj in bpy.data.objects
             if obj.get(OWNER) == identity and 'revit:id' in obj}
initial_packed = sum(bool(image.packed_file) for image in bpy.data.images if image.get(OWNER) == identity)
assert initial_packed >= 1, 'Available real source texture was not packed'
output = WORK / ('live_controller_' + str(os.getpid()))
output.mkdir()
full_blend = output / 'RevitFacadeFull.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(full_blend), check_existing=False)

windows = {}
for item in manifest['occurrences']:
    if item['category'] == 'Окна':
        windows.setdefault(item['prototype'], []).append(item['id'])
selected = set(next(ids[:2] for ids in windows.values() if len(ids) >= 2))
for category in ('Стены', 'Ограждение'):
    selected.add(next(item['id'] for item in manifest['occurrences'] if item['category'] == category))
props.busy = True
for item in props.elements:
    item.selected = item.element_id in selected
props.busy = False
ui.sync_groups(props)
assert props.selected_count == len(selected) == 4
assert bpy.ops.xivgate_rbb.import_selected() == {'FINISHED'}
wait_done()
objects = [obj for obj in bpy.data.objects if obj.get(OWNER) == identity and 'revit:id' in obj]
assert {obj['revit:id'] for obj in objects} == selected
for obj in objects:
    expected = positions[obj['revit:id']]
    assert max(abs(obj.matrix_world[r][c] - expected[r][c]) for r in range(4) for c in range(4)) < 0.0005
assert bpy.data.objects.get('Cube') is not None
subset_blend = output / 'RevitSubsetReimport.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(subset_blend), check_existing=False)
report = dict(status='passed', blender=bpy.app.version_string, real_owned_revit=True,
    catalog_elements=len(props.elements), initial_placements=len(manifest['occurrences']),
    prototypes=len(manifest['prototypes']), packed_images=initial_packed,
    reimport_placements=props.result_placements, exact_selection=True, stable_coordinates=True,
    full_blend=str(full_blend), subset_blend=str(subset_blend), latest_request_id=session.request_id)
(output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
xivgate_revit_bridge.unregister()
probe.close()
print('XIVGATE_LIVE_CONTROLLER ' + json.dumps(report, ensure_ascii=False))
