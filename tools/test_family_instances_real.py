"""Opt-in native family payload regression, preserving every raw input byte."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import bpy
import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

root = Path(os.environ['RBB_TEST_ROOT']).resolve()
manifest = Path(os.environ['RBB_REAL_MANIFEST']).resolve(strict=True)
assert manifest.is_relative_to(root)
metadata = json.loads(manifest.read_text(encoding='utf-8-sig'))
binary = manifest.with_name('geometry.bin')
original = (hashlib.sha256(manifest.read_bytes()).hexdigest(), hashlib.sha256(binary.read_bytes()).hexdigest())
dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
buffer = binary.read_bytes()
records = {p['id']: np.frombuffer(buffer[p['offset']:p['offset']+p['bytes']], dtype=dtype) for p in metadata['prototypes']}
chair_rows = [row for row in metadata['occurrences'] if row.get('type_id') == '7086106']
assert len(chair_rows) == 35 and all(row['native_instance_safe'] for row in chair_rows)
assert len({row['native_instance_key'] for row in chair_rows}) > 1
identity = None
passes = []
for tolerance in (5.0, .1, 0):
    target = manifest.with_name('family-' + str(os.getpid()) + '-' + str(tolerance) + '.usdc')
    started = time.monotonic()
    converted = build_usd(manifest, target, center_model=False, instance_tolerance_mm=tolerance)
    conversion_seconds = time.monotonic() - started
    result = import_result(target, import_id=identity, preserve_instances=True,
        source_path=metadata['source']['path'], selected_element_ids={row['id'] for row in metadata['occurrences']})
    identity = result['import_id']
    objects = {str(obj['revit:id']): obj for obj in bpy.context.scene.objects
               if obj.get(OWNER) == identity and 'revit:id' in obj}
    assert len(objects) == len(metadata['occurrences']) and all(obj.type == 'MESH' for obj in objects.values())
    chair_meshes = {objects[row['id']].data.as_pointer() for row in chair_rows}
    assert len(chair_meshes) == (1 if tolerance == 5 else 2), (tolerance, len(chair_meshes), converted)
    max_bounds_error = 0
    for row in metadata['occurrences']:
        obj = objects[row['id']]
        local = records[row['prototype']]['vertices'][:, :, :3].reshape(-1, 3).astype(np.float64)
        transform = row['transform']
        expected = local @ np.asarray(transform[:9]).reshape(3, 3) + transform[9:12]
        matrix = np.asarray(obj.matrix_world)
        actual = np.asarray([tuple(vertex.co) for vertex in obj.data.vertices]) @ matrix[:3, :3].T + matrix[:3, 3]
        error = max(np.max(np.abs(expected.min(axis=0)-actual.min(axis=0))),
                    np.max(np.abs(expected.max(axis=0)-actual.max(axis=0))))
        max_bounds_error = max(max_bounds_error, float(error))
        assert obj.data.uv_layers and len(obj.data.uv_layers.active.data) == len(obj.data.loops)
    assert max_bounds_error < tolerance / 1000 * 2 + 1e-5, max_bounds_error
    assert not result['missing_images']
    images = [image for image in bpy.data.images if image.type == 'IMAGE' and image.source == 'FILE']
    assert len(images) >= 3 and all(image.packed_file and image.size[0] > 0 for image in images)
    assert original == (hashlib.sha256(manifest.read_bytes()).hexdigest(), hashlib.sha256(binary.read_bytes()).hexdigest())
    passes.append(dict(tolerance_mm=tolerance, chair_meshes=len(chair_meshes), prototypes=result['prototypes'],
        placements=result['placements'], max_world_bounds_error_metres=max_bounds_error,
        conversion_seconds=conversion_seconds, matching=converted['instance_matching']))
report = dict(status='passed', blender=bpy.app.version_string, passes=passes,
    raw_unchanged=True, native_source=True, all_35_chairs_shared=True, packed_images=True, uv_layers=True)
output = root / 'Final050Verified' / ('real_family_' + str(os.getpid()) + '.json')
output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print('AGR_REAL_FAMILY ' + json.dumps(report), flush=True)
