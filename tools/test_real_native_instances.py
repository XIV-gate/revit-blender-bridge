"""Opt-in benchmark on a supplied immutable real USD; writes only task outputs."""
import json
import os
from pathlib import Path
import sys
import time

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.blender_import import import_result, apply_selection, OWNER

source = Path(os.environ['RBB_REAL_USD']).resolve(strict=True)
manifest = json.loads(source.with_name('manifest.json').read_text(encoding='utf-8-sig'))
ids = {str(item['id']) for item in manifest['occurrences']}
drop = {str(item['id']) for item in manifest['occurrences'] if item.get('category') in ('Формы', 'Формообразующее перекрытие')}
run = Path(os.environ['RBB_TEST_ROOT']) / ('real_native_' + str(os.getpid()))
run.mkdir()
started = time.monotonic()
result = import_result(source, selected_element_ids=ids, source_path='facade.rvt')
full_seconds = time.monotonic() - started
owned = [obj for obj in bpy.data.objects if obj.get(OWNER) == result['import_id'] and 'revit:id' in obj]
assert len(owned) == len(ids)
assert all(obj.type == 'MESH' for obj in owned)
assert len({obj.data for obj in owned}) == result['prototypes']
assert not any(obj.instance_type == 'COLLECTION' for obj in owned)
retained = next(obj for obj in owned if str(obj['revit:id']) not in drop)
pointer, name = retained.as_pointer(), retained.name
started = time.monotonic()
updated = apply_selection(result['import_id'], ids - drop)
subset_seconds = time.monotonic() - started
assert updated is not None and updated['removed'] == len(drop)
assert updated['placements'] == len(ids - drop)
assert retained.as_pointer() == pointer and retained.name == name
report = dict(status='passed', blender=bpy.app.version_string, placements=result['placements'],
              unique_meshes=result['prototypes'], direct_mesh_objects=True, full_seconds=full_seconds,
              removed=len(drop), subset_seconds=subset_seconds, retained_identity_preserved=True)
(run / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('AGR_REAL_NATIVE ' + json.dumps(report), flush=True)
