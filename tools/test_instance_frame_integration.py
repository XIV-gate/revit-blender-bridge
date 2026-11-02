"""Translate mesh-local origins without changing any world surface or textured UV."""
import hashlib
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

sys.dont_write_bytecode = True
sys.path[:0] = [os.environ['RBB_SOURCE_DIR'], str(Path(os.environ['RBB_SOURCE_DIR']) / 'tests')]
from fixture_cache import write_fixture
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

work = Path(os.environ['RBB_TEST_ROOT']) / ('frame_integration_' + str(os.getpid()))
manifest = write_fixture(work)
data = json.loads(manifest.read_text())
dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
base = np.frombuffer((work / 'geometry.bin').read_bytes()[:104], dtype=dtype).copy()
records = {}
shifts = ((0, 0, 0), (0, 0, 3.9), (0, 0, 0), (0, 0, 0), (0, .2, 0))
for index, shift in enumerate(shifts):
    record = base.copy()
    record['vertices'][:, :, :3] += shift
    record['material'] = 8 if index < 3 else 7
    if index == 2:
        record['vertices'][:, :, 6:8] += .25
    records[index] = record
prototypes = []
with (work / 'geometry.bin').open('wb') as stream:
    for index, record in records.items():
        payload = record.tobytes()
        prototypes.append(dict(id=index, offset=stream.tell(), bytes=len(payload), triangles=len(record),
                               sha256=hashlib.sha256(payload).hexdigest()))
        stream.write(payload)
rows = [dict(data['occurrences'][0], id=str(100 + index), prototype=index, unique_id='frame-' + str(index),
             type_id='frame', native_instance_safe=False,
             transform=([0, 2, 0, -1, 0, 0, 0, 0, -1, 100 + index * 10, 200, 10]))
        for index in records]
data.update(prototypes=prototypes, occurrences=rows)
data['materials']['7']['uv_sensitive'] = True
data['materials']['8'] = dict(id=8, name='Untextured', color=[.3, .4, .5], uv_sensitive=False)
manifest.write_text(json.dumps(data), encoding='utf-8')
original = (manifest.read_bytes(), (work / 'geometry.bin').read_bytes())
identity = None
for enabled, expected in ((True, 2), (False, 5)):
    target = work / ('frames-' + str(enabled) + '.usdc')
    converted = build_usd(manifest, target, center_model=False, use_revit_instances=enabled)
    assert converted['prototypes'] == expected, converted
    result = import_result(target, import_id=identity, preserve_instances=True,
        selected_element_ids={row['id'] for row in rows}, source_path='fixture.rvt')
    identity = result['import_id']
    objects = {str(obj['revit:id']): obj for obj in bpy.context.scene.objects
               if obj.get(OWNER) == identity and 'revit:id' in obj}
    assert len(objects) == 5
    if enabled:
        assert objects['100'].data == objects['101'].data == objects['102'].data
        assert objects['103'].data == objects['104'].data
    for index, row in enumerate(rows):
        obj = objects[row['id']]
        local = records[index]['vertices'][:, :, :3].reshape(-1, 3).astype(np.float64)
        t = row['transform']
        expected_world = local @ np.asarray(t[:9]).reshape(3, 3) + t[9:12]
        actual_local = np.asarray([tuple(obj.data.vertices[loop.vertex_index].co) for loop in obj.data.loops])
        matrix = np.asarray(obj.matrix_world)
        actual_world = actual_local @ matrix[:3, :3].T + matrix[:3, 3]
        np.testing.assert_allclose(actual_world, expected_world, atol=3e-5, rtol=0)
        if index >= 3:
            uv = np.asarray([tuple(loop.uv) for loop in obj.data.uv_layers.active.data])
            np.testing.assert_array_equal(uv, records[index]['vertices'][:, :, 6:8].reshape(-1, 2))
    assert (manifest.read_bytes(), (work / 'geometry.bin').read_bytes()) == original
assert bpy.data.objects.get('Cube') is not None
print('AGR_INSTANCE_FRAMES_PASS', bpy.app.version_string)
