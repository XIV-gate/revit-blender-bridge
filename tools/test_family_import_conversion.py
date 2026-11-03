"""Real USD/Blender checks for independent objects, instances and family meshes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

import bpy
import numpy as np
from pxr import Usd, UsdGeom

sys.dont_write_bytecode=True
sys.path[:0]=[os.environ['RBB_SOURCE_DIR'],str(Path(os.environ['RBB_SOURCE_DIR'])/'tests')]
from fixture_cache import write_fixture
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

work=Path(os.environ['RBB_TEST_ROOT'])/('family_conversion_'+uuid.uuid4().hex)
manifest=write_fixture(work)
data=json.loads(manifest.read_text(encoding='utf-8'))
dtype=np.dtype([('vertices','<f4',(3,8)),('material','<i8')])
old=np.frombuffer((work/'geometry.bin').read_bytes(),dtype=dtype)
first=np.repeat(old[:1],2)
first['vertices'][1,:,1]+=.2
first['material'][1]=8
records={0:first,1:old[1:].copy()}
prototypes=[]
with (work/'geometry.bin').open('wb')as stream:
    for key,record in records.items():
        payload=record.tobytes()
        prototypes.append(dict(id=key,offset=stream.tell(),bytes=len(payload),triangles=len(record),
                               sha256=hashlib.sha256(payload).hexdigest()))
        stream.write(payload)
for row in data['occurrences']:
    row['family_name']='Оконный блок'if row['prototype']==0 else 'Стена'
    row['source_element_ids']=[row['id']]
    row['selection_parent_ids']=['parent-'+row['id']]
data['prototypes']=prototypes
data['materials']['8']=dict(id=8,name='Второй материал',color=[.2,.4,.6],uv_sensitive=False)
manifest.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
original=(manifest.read_bytes(),(work/'geometry.bin').read_bytes())
source_rows={row['id']:row for row in data['occurrences']}
identity=None
reports=[]
checked_faces=checked_mirrored_faces=0
for mode in ('OBJECTS','INSTANCES','FAMILY'):
    target=work/(mode+'.usdc')
    events=[]
    converted=build_usd(manifest,target,center_model=False,import_mode=mode,progress_callback=events.append)
    assert events[-1]['progress']==1
    assert all(first['progress']<=second['progress'] for first,second in zip(events,events[1:]))
    expected = 2 if mode == 'FAMILY' else 3
    assert converted['import_mode']==mode and converted['source_placements']==3
    assert converted['placements']==expected
    stage=Usd.Stage.Open(str(target))
    world=stage.GetPrimAtPath('/World')
    assert world.GetAttribute('revit:import_mode').Get()==mode
    assert world.GetAttribute('revit:source_placements').Get()==3
    if mode=='FAMILY':
        assert converted['grouping']['expanded_triangles']==5
        total=0
        for placement in stage.GetPrimAtPath('/World/Elements').GetChildren():
            assert placement.IsInstanceable()
            assert str(placement.GetAttribute('revit:id').Get()).startswith('family_')
            table=json.loads(placement.GetAttribute('revit:source_table').Get())
            ranges=json.loads(placement.GetAttribute('revit:source_ranges').Get())
            prototype=placement.GetAttribute('revit:prototype').Get()
            mesh=UsdGeom.Mesh(stage.GetPrimAtPath('/Prototypes/p_'+prototype+'/mesh'))
            owners=np.array(UsdGeom.PrimvarsAPI(mesh).GetPrimvar('revit_source_index').Get())
            assert UsdGeom.PrimvarsAPI(mesh).GetPrimvar('revit_source_index').GetInterpolation()=='uniform'
            points=np.array(mesh.GetPointsAttr().Get()).reshape(-1,3,3)
            normals=np.array(mesh.GetNormalsAttr().Get()).reshape(-1,3,3)
            uv=np.array(UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').Get()).reshape(-1,3,2)
            transform=np.asarray(UsdGeom.Xformable(placement).GetLocalTransformation())
            points=points@transform[:3,:3]+transform[3,:3]
            for part in ranges:
                owner=table[part['source_index']]['id']
                row=source_rows[owner]
                raw=records[row['prototype']]
                basis=np.array(row['transform'][:9],dtype=float).reshape(3,3).T
                order=[0,2,1]if np.linalg.det(basis)<0 else [0,1,2]
                start,stop=part['start'],part['start']+part['count']
                assert np.all(owners[start:stop]==part['source_index'])
                expected_points=raw['vertices'][:,order,:3].astype(float)@basis.T+row['transform'][9:]
                expected_normals=raw['vertices'][:,order,3:6].astype(float)@np.linalg.inv(basis)
                expected_normals/=np.linalg.norm(expected_normals,axis=2,keepdims=True)
                np.testing.assert_allclose(points[start:stop],expected_points,atol=1e-6,rtol=0)
                np.testing.assert_allclose(normals[start:stop],expected_normals,atol=1e-7,rtol=0)
                np.testing.assert_array_equal(uv[start:stop],raw['vertices'][:,order,6:8])
            total+=len(owners)
        assert total==5
    result=import_result(target,import_id=identity,preserve_instances=mode!='OBJECTS',source_path='fixture.rvt',
                         selected_element_ids={'11','12','13'})
    identity=result['import_id']
    objects={str(obj['revit:id']):obj for obj in bpy.context.scene.objects if obj.get(OWNER)==identity and 'revit:id'in obj}
    assert len(objects)==expected
    assert all(obj.type=='MESH'for obj in objects.values())
    if mode!='FAMILY':
        assert (objects['11'].data==objects['12'].data)==(mode=='INSTANCES')
    else:
        assert sum(len(obj.data.polygons)for obj in objects.values())==5
        assert sorted(len(obj.data.materials)for obj in objects.values())==[1,2]
        for obj in objects.values():
            mesh=obj.data
            table=json.loads(mesh['revit:source_table'])
            ranges=json.loads(mesh['revit:source_ranges'])
            owner_attribute=mesh.attributes['revit_source_index']
            assert owner_attribute.domain=='FACE' and owner_attribute.data_type=='INT'
            assert mesh.uv_layers.active is not None
            world_basis=np.asarray(obj.matrix_world,dtype=float)[:3,:3]
            for part in ranges:
                row=source_rows[table[part['source_index']]['id']]
                raw=records[row['prototype']]
                basis=np.asarray(row['transform'][:9],dtype=float).reshape(3,3).T
                mirrored=np.linalg.det(basis)<0
                order=[0,2,1] if mirrored else [0,1,2]
                for source_face,face_index in enumerate(range(part['start'],part['start']+part['count'])):
                    face=mesh.polygons[face_index]
                    loops=list(face.loop_indices)
                    assert len(loops)==3
                    assert owner_attribute.data[face_index].value==part['source_index']
                    expected_uv=raw['vertices'][source_face,order,6:8]
                    actual_uv=np.array([tuple(mesh.uv_layers.active.data[index].uv)for index in loops])
                    np.testing.assert_array_equal(actual_uv,expected_uv)
                    expected_normal=raw['vertices'][source_face,order,3:6].astype(float)@np.linalg.inv(basis)
                    expected_normal/=np.linalg.norm(expected_normal,axis=1,keepdims=True)
                    actual_normal=np.array([tuple(mesh.corner_normals[index].vector)for index in loops])@np.linalg.inv(world_basis)
                    actual_normal/=np.linalg.norm(actual_normal,axis=1,keepdims=True)
                    np.testing.assert_allclose(actual_normal,expected_normal,atol=1e-6,rtol=0)
                    material_id=str(int(raw['material'][source_face]))
                    expected_material=str(data['materials'][material_id].get('source_material_id',material_id))
                    assert 0<=face.material_index<len(mesh.materials)
                    assert mesh.materials[face.material_index].get('revit:source_material_id')==expected_material
                    checked_faces+=1
                    checked_mirrored_faces+=int(mirrored)
        packed=[image for image in bpy.data.images if image.get(OWNER)==identity and image.packed_file]
        assert packed
        expected_image=hashlib.sha256((work/'pixel.png').read_bytes()).hexdigest()
        for image in packed:
            assert tuple(image.size)==(2,2)
            pixels=np.asarray(image.pixels[:],dtype=float)
            assert len(pixels)==16 and np.isfinite(pixels).all() and np.ptp(pixels.reshape(-1,4)[:,:3])>0
            assert hashlib.sha256(image.packed_file.data).hexdigest()==expected_image
        assert checked_faces==5 and checked_mirrored_faces==2
    assert (manifest.read_bytes(),(work/'geometry.bin').read_bytes())==original
    reports.append(dict(mode=mode,placements=len(objects),meshes=len({obj.data.as_pointer()for obj in objects.values()})))
# Invalid family placement must fail before even an empty stage is written.
bad=json.loads(manifest.read_text(encoding='utf-8'))
bad['occurrences'][-1]['transform']=[0]*12
invalid=work/'invalid.json'
invalid.write_text(json.dumps(bad),encoding='utf-8')
target=work/'invalid.usdc'
try:
    build_usd(invalid,target,import_mode='FAMILY')
except ValueError:
    assert not target.exists()
else:
    raise AssertionError('Singular family placement accepted')
target=work/'not-enough-ram.usdc'
with patch('xivgate_revit_bridge.core.resources.memory_snapshot',return_value={'ram':{'available_bytes':1}}):
    try:
        build_usd(manifest,target,import_mode='FAMILY')
    except ValueError as error:
        assert 'памяти'in str(error) and not target.exists()
    else:
        raise AssertionError('Insufficient memory was not detected before stage creation')
with patch('xivgate_revit_bridge.core.resources.memory_snapshot',return_value={'ram':None}):
    unknown=build_usd(manifest,work/'unknown-ram.usdc',import_mode='FAMILY')
    assert unknown['grouping']['available_ram_bytes']is None and unknown['grouping']['estimated_total_bytes']>0
assert bpy.data.objects.get('Cube')is not None
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
report=dict(status='passed',blender=bpy.app.version_string,modes=reports,raw_unchanged=True,
            provenance=True,mirrored_normals_uv=True,preflight_before_stage=True,
            memory_guard_before_stage=True,unknown_memory_estimate=True,progress_callbacks=True)
report.update(blender_loop_uv=True,blender_corner_normals=True,blender_face_materials=True,
              checked_faces=checked_faces,checked_mirrored_faces=checked_mirrored_faces,
              packed_image_pixels_decoded=True,packed_image_bytes_verified=True)
(work/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('AGR_FAMILY_CONVERSION '+json.dumps(report),flush=True)
