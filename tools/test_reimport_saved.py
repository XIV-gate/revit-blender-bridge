"""Large-scene reimport regression from an immutable saved smoke-test result."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import time

import bpy

WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.blender_import import import_result, OWNER, ROOT_FLAG

full = Path(os.environ['RBB_FULL_BLEND']).resolve()
subset = Path(os.environ['RBB_SUBSET_USD']).resolve()
assert full.is_relative_to(WORK) and subset.is_relative_to(WORK)
output = WORK / ('reimport_benchmark_' + str(os.getpid()))
output.mkdir()
trace = (output / 'stack.log').open('w', encoding='utf-8')
bpy.ops.wm.open_mainfile(filepath=str(full))
root = next(collection for collection in bpy.data.collections if collection.get(ROOT_FLAG))
identity = root[OWNER]
before = {obj['revit:id']: obj.matrix_world.copy() for obj in bpy.data.objects
          if obj.get(OWNER) == identity and 'revit:id' in obj}
print('REIMPORT_BEGIN ' + json.dumps(dict(pid=os.getpid(), placements=len(before))), flush=True)
faulthandler.dump_traceback_later(8, repeat=True, file=trace)
started = time.monotonic()
result = import_result(subset, import_id=identity)
elapsed = time.monotonic() - started
faulthandler.cancel_dump_traceback_later()
assert result['placements'] == 4
for obj in bpy.data.objects:
    if obj.get(OWNER) == identity and 'revit:id' in obj:
        expected = before[obj['revit:id']]
        assert max(abs(obj.matrix_world[r][c] - expected[r][c]) for r in range(4) for c in range(4)) < 0.0005
assert bpy.data.objects.get('Cube') is not None
assert sum(bool(collection.get(ROOT_FLAG)) for collection in bpy.data.collections) == 1
assert not any(collection.name.startswith('Сохранённые пользовательские') for collection in bpy.data.collections)
target = output / 'RevitSubsetReimport.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(target), check_existing=False)
report = dict(status='passed', blender=bpy.app.version_string, before=len(before), after=result['placements'],
              reimport_seconds=elapsed, stable_coordinates=True, unrelated_preserved=True, blend=str(target))
(output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
trace.close()
print('XIVGATE_LARGE_REIMPORT ' + json.dumps(report, ensure_ascii=False), flush=True)
