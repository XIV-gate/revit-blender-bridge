"""Real converter/importer regression for native and tolerant instance grouping."""
import hashlib
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix
from pxr import Usd, UsdGeom

sys.dont_write_bytecode = True
sys.path[:0] = [os.environ['RBB_SOURCE_DIR'], str(Path(os.environ['RBB_SOURCE_DIR']) / 'tests')]
from fixture_cache import write_fixture
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER
from xivgate_revit_bridge.geometry_cache import matrix_rows

work = Path(os.environ['RBB_TEST_ROOT']) / ('conversion_matching_' + str(os.getpid()))
manifest = write_fixture(work)
data = json.loads(manifest.read_text(encoding='utf-8'))
dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
original = np.frombuffer((work / 'geometry.bin').read_bytes()[:104], dtype=dtype).copy()
records = {}
for index in range(6):
    record = np.repeat(original, 2 if index == 1 else 1)
    record['material'] = 7 if index in (2, 3) else 8
    if index in (1, 5):
        record['vertices'][:, :, 0] += .00015
    if index in (1, 3):
        record['vertices'][:, :, 6:8] += .125
    records[index] = record
prototypes = []
with (work / 'geometry.bin').open('wb') as stream:
    for index, record in records.items():
        payload = record.tobytes()
        prototypes.append(dict(id=index, offset=stream.tell(), bytes=len(payload), triangles=len(record),
                               sha256=hashlib.sha256(payload).hexdigest()))
        stream.write(payload)
template = data['occurrences'][0]
rows = [dict(template, id=str(11 + index), unique_id='match-' + str(index), prototype=index,
             name='Fixture ' + str(index), type_id='fallback' if index >= 4 else 'chair',
             native_instance_key='family:chair', native_instance_safe=index < 4,
             transform=[-1 if index % 2 else 1, 0, 0, 0, 1, 0, 0, 0, 1, 100 + index * 10, 200, index])
        for index in range(6)]
data.update(prototypes=prototypes, occurrences=rows, summary=dict(occurrences=6, prototypes=6))
data['materials']['7']['uv_sensitive'] = True
data['materials']['8'] = dict(id=8, source_material_id='source-8', name='Матовый пластик',
                            color=[.3, .4, .5], transparency=0, smoothness=20, properties={}, uv_sensitive=False)
manifest.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
original_manifest = manifest.read_bytes()
original_binary = (work / 'geometry.bin').read_bytes()
identity = None
reports = []
for tolerance, expected_count in ((.1, 6), (.2, 4), (0, 6)):
    target = work / ('match_' + str(tolerance) + '.usdc')
    converted = build_usd(manifest, target, center_model=False, instance_tolerance_mm=tolerance)
    assert converted['prototypes'] == expected_count, converted
    stage = Usd.Stage.Open(str(target))
    expected_redirects = {index: (0 if index == 1 else 4 if index == 5 else index)
                          if tolerance == .2 else index for index in records}
    for index, row in enumerate(rows):
        placement = stage.GetPrimAtPath('/World/Elements/e_' + row['id'])
        representative = expected_redirects[index]
        assert int(placement.GetAttribute('revit:prototype').Get()) == representative
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath('/Prototypes/p_' + str(representative) + '/mesh'))
        np.testing.assert_array_equal(np.asarray(mesh.GetPointsAttr().Get()),
                                      records[representative]['vertices'][:, :, :3].reshape(-1, 3))
        uv = UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').Get()
        np.testing.assert_array_equal(np.asarray(uv), records[representative]['vertices'][:, :, 6:8].reshape(-1, 2))
    result = import_result(target, import_id=identity, preserve_instances=True, source_path='fixture.rvt',
                           selected_element_ids={row['id'] for row in rows})
    identity = result['import_id']
    objects = {str(obj['revit:id']): obj for obj in bpy.context.scene.objects
               if obj.get(OWNER) == identity and 'revit:id' in obj}
    assert len(objects) == 6
    assert len({obj.data.as_pointer() for obj in objects.values()}) == expected_count
    for index, row in enumerate(rows):
        obj = objects[row['id']]
        expected_matrix = Matrix(matrix_rows(row['transform'])).transposed()
        assert max(abs(obj.matrix_world[r][c] - expected_matrix[r][c]) for r in range(4) for c in range(4)) < 1e-5
        source = records[expected_redirects[index]]['vertices'].reshape(-1, 8)
        points = np.asarray([tuple(obj.data.vertices[loop.vertex_index].co) for loop in obj.data.loops])
        np.testing.assert_allclose(points, source[:, :3], atol=1e-7, rtol=0)
        uv = np.asarray([tuple(loop.uv) for loop in obj.data.uv_layers.active.data])
        np.testing.assert_allclose(uv, source[:, 6:8], atol=1e-7, rtol=0)
        assert len(obj.data.loops) == len(source) and np.isfinite(uv).all()
        expected_material = 'source-7' if index in (2, 3) else 'source-8'
        assert len(obj.data.materials) > 0
        assert all(material.get('revit:source_material_id') == expected_material for material in obj.data.materials)
    assert objects['13'].data != objects['14'].data, 'Textured UV variants must never collapse'
    assert bool(objects['11'].data == objects['12'].data) == (tolerance == .2)
    assert bool(objects['15'].data == objects['16'].data) == (tolerance == .2)
    images = [image for image in bpy.data.images if image.get(OWNER) == identity]
    assert images and all(image.packed_file and image.size[0] > 0 and len(image.pixels) > 0 for image in images)
    assert manifest.read_bytes() == original_manifest and (work / 'geometry.bin').read_bytes() == original_binary
    reports.append(dict(tolerance_mm=tolerance, unique_meshes=expected_count, statistics=converted['instance_matching']))
assert bpy.data.objects.get('Cube') is not None
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
report = dict(status='passed', blender=bpy.app.version_string, passes=reports, transforms_unchanged=True,
              raw_geometry_restored=True, textured_uv_variants_preserved=True, packed_images_decoded=True)
(work / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('AGR_CONVERSION_INSTANCE_MATCHING ' + json.dumps(report), flush=True)
