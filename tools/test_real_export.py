"""Opt-in smoke test of real worker output, with an explicit isolated output blend."""
import json
import os
from pathlib import Path
import sys

import bpy

SOURCE = Path(os.environ['RBB_SOURCE_DIR'])
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

manifest = Path(os.environ['RBB_REAL_MANIFEST']).resolve()
assert manifest.is_relative_to(WORK)
metadata = json.loads(manifest.read_text(encoding='utf-8-sig'))
target = manifest.with_name('smoke.usdc')
conversion = build_usd(manifest, target)
unrelated = bpy.data.objects.get('Cube')
result = import_result(target, source_path=metadata.get('source', {}).get('path', ''))
assert result['placements'] == len(metadata['occurrences'])
assert result['max_transform_error'] <= 0.0005
assert unrelated in bpy.data.objects.values()
source_id = result['import_id']
before = {obj['revit:id']: obj.matrix_world.copy() for obj in bpy.data.objects
          if obj.get(OWNER) == source_id and 'revit:id' in obj}
second = os.environ.get('RBB_REAL_SECOND_MANIFEST')
if second:
    second = Path(second).resolve()
    assert second.is_relative_to(WORK)
    second_target = second.with_name('smoke.usdc')
    build_usd(second, second_target, source_offset=result['source_offset'])
    result = import_result(second_target, import_id=source_id)
    for obj in bpy.data.objects:
        if obj.get(OWNER) == source_id and obj.get('revit:id') in before:
            expected = before[obj['revit:id']]
            assert max(abs(obj.matrix_world[r][c] - expected[r][c]) for r in range(4) for c in range(4)) <= 0.0005
assert unrelated in bpy.data.objects.values()
output = WORK / ('real_smoke_' + str(os.getpid()))
output.mkdir()
blend = output / 'RevitBridgeSmoke.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
report = dict(status='passed', blender=bpy.app.version_string, result=result,
              real_export=True, reimport=bool(second), blend=str(blend),
              initial_placements=len(metadata['occurrences']), initial_prototypes=len(metadata['prototypes']))
(output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('XIVGATE_REAL_EXPORT ' + json.dumps(report, ensure_ascii=False))
