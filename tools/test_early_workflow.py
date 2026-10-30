"""EA controller: delta export, local toggles, spatial search and cache protection."""
import json
import os
from pathlib import Path
import runpy
import time
from unittest.mock import patch

import bpy

source_root = Path(os.environ['RBB_SOURCE_DIR'])
baseline = runpy.run_path(str(source_root / 'tools' / 'test_controller_integration.py'))
addon, runtime = baseline['xivgate_revit_bridge'], baseline['runtime']
TestWorker, scene, work = baseline['TestWorker'], baseline['scene'], baseline['WORK']
addon.register()
from xivgate_revit_bridge import runtime  # Manual enable reloads disabled cached modules.
bpy.context.window.scene = scene
props = scene.xivgate_revit_bridge


def wait():
    deadline = time.monotonic() + 45
    while runtime.STATE.action and time.monotonic() < deadline:
        runtime.tick(); time.sleep(.02)
    assert not runtime.STATE.action, 'Controller timed out'
    assert props.status != 'ERROR', props.message


from xivgate_revit_bridge.blender_import import OWNER
from xivgate_revit_bridge import workflow
from xivgate_revit_bridge.core.jobs import JobSession

with patch.object(runtime, 'WorkerProcess', TestWorker), patch.object(runtime, 'worker_path', return_value=baseline['dll']), \
        patch.object(runtime, 'inspect_revit', return_value=dict(key='2027', year=2027)), \
        patch.object(runtime, 'setup_plan', return_value=dict(path=str(baseline['pointer']), content='test-only')), \
        patch.object(runtime, 'manifest_matches', return_value=True):
    existing = next(obj for obj in bpy.data.objects if obj.get(OWNER) == props.import_id and obj.get('revit:id') == '11')
    pointer, name, mesh = existing.as_pointer(), existing.name, existing.data
    before = len(TestWorker.export_requests)
    props.elements[1].selected = True
    runtime.begin_export(scene)
    wait()
    assert len(TestWorker.export_requests) == before + 1
    assert TestWorker.export_requests[-1] == ('12',), TestWorker.export_requests
    assert props.result_placements == 2
    added = next(obj for obj in bpy.data.objects if obj.get(OWNER) == props.import_id and obj.get('revit:id') == '12')
    assert existing.as_pointer() == pointer and existing.name == name and existing.data is mesh
    assert added.data is mesh, 'Delta must join the existing editable mesh instance'
    before = len(TestWorker.export_requests)
    added_pointer = added.as_pointer()
    props.elements[1].selected = False
    runtime.begin_export(scene); wait()
    assert props.result_placements == 1
    props.elements[1].selected = True
    runtime.begin_export(scene); wait()
    assert props.result_placements == 2 and added.as_pointer() == added_pointer
    assert len(TestWorker.export_requests) == before, 'Toggles must not re-export cached placements'
    # An actual source overlap declines delta; controller must transition out of busy into full export.
    TestWorker.composite_delta = True
    props.elements[2].selected = True
    runtime.begin_export(scene); wait()
    assert TestWorker.export_requests[-2:] == [('13',), ('11', '12', '13')]
    assert props.result_placements == 3
    TestWorker.composite_delta = False
    props.elements[2].selected = False
    runtime.begin_export(scene); wait()
    runtime.begin_export(scene, force_refresh=True); wait()
    TestWorker.empty_delta = True
    props.elements[2].selected = True
    runtime.begin_export(scene); wait()
    assert TestWorker.export_requests[-2:] == [('13',), ('11', '12', '13')]
    assert props.result_placements == 2, 'A geometry-free addition must preserve existing geometry'
    TestWorker.empty_delta = False
    props.elements[2].selected = False
    runtime.begin_export(scene); wait()
    # Exact full export can be reused after shutdown, without worker launch.
    runtime.begin_export(scene, force_refresh=True); wait()
    starts, before = len(TestWorker.started_executables), len(TestWorker.export_requests)
    props.pack_images = False  # Conversion-only change forces use of the verified export.
    runtime.cancel()
    runtime.begin_export(scene); wait()
    assert len(TestWorker.started_executables) == starts and len(TestWorker.export_requests) == before
    assert props.result_placements == 2 and runtime.STATE.file_root is not None
    # A corrupt cached binary forces a fresh scan/export rather than stale import.
    binary = runtime.STATE.converted_usd.parent / 'geometry.bin'
    binary.write_bytes(b'explicit corrupted fixture')
    props.pack_images = True
    runtime.begin_export(scene); wait()
    assert len(TestWorker.export_requests) == before + 1
    assert props.result_placements == 2

assert len(props.types) == 2
props.list_mode = 'types'
props.type_index = next(index for index, group in enumerate(props.types) if 'Window type' in group.name)
props.elements[1].selected = False
props.preview_only_unselected = True
assert bpy.ops.xivgate_rbb.preview_bounds() == {'FINISHED'}
from xivgate_revit_bridge.spatial_preview import PREVIEW_TAG
preview = [obj for obj in scene.objects if obj.get(PREVIEW_TAG)]
assert len(preview) == 1 and len(preview[0].data.vertices) == 8
assert bpy.ops.xivgate_rbb.clear_preview() == {'FINISHED'}
assert not any(obj.get(PREVIEW_TAG) for obj in scene.objects)

# Finish two artificial jobs. The unpacked image protects one; the other is disposable.
runtime.cancel()
cache = workflow.cache_root(props)
protected = JobSession.create(cache)
from fixture_cache import write_fixture
fixture = write_fixture(protected.directory / 'output' / 'image-fixture')
image_path = fixture.parent / 'pixel.png'
image = bpy.data.images.load(str(image_path), check_existing=False)
assert not image.packed_file
protected.finish()
disposable = JobSession.create(cache)
disposable.finish()
assert protected.directory in workflow.referenced_jobs(cache)
result = workflow.clean_cache(props)
assert protected.directory.is_dir() and image_path.is_file()
assert not disposable.directory.exists() and result['deleted_jobs'] >= 1
assert image in bpy.data.images.values()
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
report = dict(status='passed', delta_exports_only_new_ids=True, linked_mesh_reused=True,
              ambiguous_delta_falls_back=True, empty_delta_falls_back=True,
              local_remove_restore=True, export_cache_after_shutdown=True,
              grouped_type_bounds_preview=True, unpacked_image_cache_protected=True)
(work / 'early_result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('AGR_EARLY_WORKFLOW ' + json.dumps(report), flush=True)
