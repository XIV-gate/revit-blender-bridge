"""Opt-in real-payload instance regression, using an explicitly owned replay copy."""
from collections import Counter
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

root = Path(os.environ['RBB_TEST_ROOT']).resolve()
manifest = Path(os.environ['RBB_REAL_MANIFEST']).resolve(strict=True)
assert manifest.is_relative_to(root), 'Use only the approved immutable replay copy'
metadata = json.loads(manifest.read_text(encoding='utf-8-sig'))
assert metadata['summary']['occurrences'] == 1562
assert metadata['summary']['prototypes'] == 669
target = manifest.with_name('instances-' + str(os.getpid()) + '.usdc')
build_usd(manifest, target, instance_tolerance_mm=0, use_revit_instances=False)
result = import_result(target, preserve_instances=True, source_path=metadata['source']['path'],
                       selected_element_ids={str(row['id']) for row in metadata['occurrences']})
owned = {str(obj['revit:id']): obj for obj in bpy.data.objects
         if obj.get(OWNER) == result['import_id'] and 'revit:id' in obj}
assert len(owned) == 1562
assert all(obj.type == 'MESH' and obj.instance_type != 'COLLECTION' for obj in owned.values())
assert len({obj.data.as_pointer() for obj in owned.values()}) == 669
assert owned['7387673'].data == owned['7380274'].data
beam_mesh = owned['7387673'].data
beam_users = [obj for obj in owned.values() if obj.data == beam_mesh]
assert len(beam_users) == 53
chairs = [owned[str(row['id'])] for row in metadata['occurrences'] if row.get('type_id') == '7086106']
assert len(chairs) == 35
chair_groups = sorted(Counter(obj.data.as_pointer() for obj in chairs).values())
assert chair_groups == [6, 29]
assert result['max_transform_error'] <= .0005
assert not result['missing_images']
assert all(obj.data.uv_layers for obj in owned.values())
for mesh in {obj.data for obj in owned.values()}:
    uv = np.array([tuple(loop.uv) for loop in mesh.uv_layers.active.data])
    assert len(uv) == len(mesh.loops) and np.isfinite(uv).all()
packed_images = [img for img in bpy.data.images if img.packed_file is not None]
assert len(packed_images) >= 3
assert all(image.size[0] > 0 and image.size[1] > 0 and len(image.pixels) > 0 for image in packed_images)
before = beam_mesh.vertices[0].co.copy()
beam_mesh.vertices[0].co.x += .001
assert all(abs(obj.data.vertices[0].co.x - (before.x + .001)) < 1e-6 for obj in beam_users)
beam_mesh.vertices[0].co = before
assert bpy.data.objects.get('Cube') is not None, 'Unrelated scene data must remain'
report = dict(status='passed', blender=bpy.app.version_string, placements=1562, unique_meshes=669,
              direct_mesh_objects=True, screenshot_beam_users=53, chair_groups=chair_groups,
              uv_layers_preserved=True, packed_images=len(packed_images),
              tolerance_mm=0, use_revit_instances=False, decoded_images=True, finite_loop_uv=True,
              shared_edit_propagated=True, max_transform_error=result['max_transform_error'])
output = root / ('instance_noise_replay_' + str(os.getpid()) + '.json')
with output.open('x', encoding='utf-8') as stream:
    json.dump(report, stream, indent=2)
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_INSTANCE_NOISE_REPLAY ' + json.dumps(report), flush=True)
