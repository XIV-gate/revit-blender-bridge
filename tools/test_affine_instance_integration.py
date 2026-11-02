"""Verify size-preserving shared meshes in real USD/Blender, without changing a live scene."""
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import bpy
import numpy as np
from pxr import Usd, UsdGeom

sys.dont_write_bytecode = True
sys.path[:0] = [os.environ['RBB_SOURCE_DIR'], str(Path(os.environ['RBB_SOURCE_DIR'])/'tests')]
from fixture_cache import write_fixture
from test_affine_instances import box, DTYPE
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

work = Path(os.environ['RBB_TEST_ROOT'])/('affine_integration_'+str(os.getpid())+'_'+uuid.uuid4().hex[:8])
manifest = write_fixture(work)
data = json.loads(manifest.read_text(encoding='utf-8'))
records = {0:box((2,.035,3)), 1:box((2,.035,3),(3.9,.2,-2)),
           2:box((2,.035,3)), 3:box((4,.035,5)), 4:box((2,.035,3))}
for index,record in records.items():
    record['material'] = 8 if index<2 else 7
records[1]['vertices'][:,:,6:8] += .25  # Explicitly untextured: UV variation is allowed.
records[3]['vertices'][:,:,6:8] += .125  # Textured: this must remain a separate mesh.
for index,scale in ((5,np.array([1.,1.,1.])), (6,np.array([1.,1.,1.]))):
    record = np.zeros(1,dtype=DTYPE)
    record['vertices'][0,:,:3] = np.array([[0.,0.,0.],[1.,1.,0.],[0.,0.,1.]])*scale
    normal = np.array([1.,-1.,0.])/scale
    normal /= np.linalg.norm(normal)
    record['vertices'][0,:,3:6] = normal
    record['vertices'][0,:,6:8] = [[0,0],[1,0],[0,1]]
    record['material'] = 8
    records[index] = record
# Reorder triangles and cyclically rotate their corners, preserving winding and
# the association between each point, its custom normal and its UV.
for index in (1,4,6):
    records[index]['vertices'] = records[index]['vertices'][:,[1,2,0]]
    records[index] = records[index][::-1].copy()
prototypes=[]
with (work/'geometry.bin').open('wb') as stream:
    for index,record in records.items():
        payload=record.tobytes()
        prototypes.append(dict(id=index,offset=stream.tell(),bytes=len(payload),triangles=len(record),
                               sha256=hashlib.sha256(payload).hexdigest()))
        stream.write(payload)
rows=[dict(data['occurrences'][0], id=str(100+index),unique_id='affine-'+str(index),prototype=index,
           type_id='slanted' if index>=5 else 'panel', native_instance_safe=False,
           transform=([0,2,0,-1,0,0,0,0,-.5,100+index*10,200,20]))for index in records]
data.update(prototypes=prototypes,occurrences=rows)
data['materials']['7']['uv_sensitive']=True
data['materials']['8']=dict(id=8,name='Untextured',color=[.3,.4,.5],uv_sensitive=False)
manifest.write_text(json.dumps(data),encoding='utf-8')
original=(manifest.read_bytes(),(work/'geometry.bin').read_bytes())
identity=None
passes=[]
for iteration,enabled in enumerate((True,False,True)):
    target=work/('affine-'+str(iteration)+'.usdc')
    converted=build_usd(manifest,target,center_model=False,use_revit_instances=enabled)
    expected_count=4 if enabled else 7
    assert converted['prototypes']==expected_count,converted
    mapping={0:0,1:0,2:2,3:3,4:2,5:5,6:5} if enabled else {index:index for index in records}
    stage=Usd.Stage.Open(str(target))
    for anchor in set(mapping.values()):
        mesh=UsdGeom.Mesh(stage.GetPrimAtPath('/Prototypes/p_'+str(anchor)+'/mesh'))
        raw=records[anchor]['vertices'].reshape(-1,8)
        np.testing.assert_array_equal(np.asarray(mesh.GetPointsAttr().Get()),raw[:,:3])
        np.testing.assert_array_equal(np.asarray(mesh.GetNormalsAttr().Get()),raw[:,3:6])
        np.testing.assert_array_equal(np.asarray(UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').Get()),raw[:,6:8])
    result=import_result(target,import_id=identity,preserve_instances=True,source_path='fixture.rvt',
                         selected_element_ids={row['id']for row in rows})
    identity=result['import_id']
    objects={str(obj['revit:id']):obj for obj in bpy.context.scene.objects if obj.get(OWNER)==identity and 'revit:id'in obj}
    assert len(objects)==7
    assert len({obj.data.as_pointer()for obj in objects.values()})==expected_count
    assert (objects['100'].data==objects['101'].data)==enabled
    assert (objects['102'].data==objects['104'].data)==enabled
    assert objects['102'].data!=objects['103'].data
    assert (objects['105'].data==objects['106'].data)==enabled
    max_error=max_normal_error=0.
    for index,row in enumerate(rows):
        obj=objects[row['id']]
        matrix=np.asarray(obj.matrix_world,dtype=np.float64)
        basis=np.array(row['transform'][:9],dtype=np.float64).reshape(3,3).T
        raw=records[index]['vertices'].reshape(-1,8).astype(np.float64)
        expected_points=raw[:,:3]@basis.T+row['transform'][9:]
        expected_normals=raw[:,3:6]@np.linalg.inv(basis)
        expected_normals/=np.linalg.norm(expected_normals,axis=1,keepdims=True)
        mesh=obj.data
        local=np.array([tuple(mesh.vertices[loop.vertex_index].co)for loop in mesh.loops])
        actual_points=local@matrix[:3,:3].T+matrix[:3,3]
        actual_normals=np.array([tuple(normal.vector)for normal in mesh.corner_normals])@np.linalg.inv(matrix[:3,:3])
        actual_normals/=np.linalg.norm(actual_normals,axis=1,keepdims=True)
        uv=np.array([tuple(loop.uv)for loop in mesh.uv_layers.active.data])
        # Compare paired world attributes rather than raw point order: an
        # affine representative may legitimately use reordered triangles.
        for points,normals,other_points,other_normals,own_uv,other_uv in (
                (actual_points,actual_normals,expected_points,expected_normals,uv,raw[:,6:8]),
                (expected_points,expected_normals,actual_points,actual_normals,raw[:,6:8],uv)):
            for corner,normal,texture in zip(points,normals,own_uv):
                distances=np.max(np.abs(other_points-corner),axis=1)
                normal_errors=np.max(np.abs(other_normals-normal),axis=1)
                valid=(distances<=3e-5)&(normal_errors<=3e-6)
                if index in (2,3,4):
                    valid &= np.max(np.abs(other_uv-texture),axis=1)<=1e-8
                assert valid.any(),(index,enabled,corner,normal,distances.min(),normal_errors.min())
                choice=np.flatnonzero(valid)[np.argmin(distances[valid])]
                max_error=max(max_error,float(distances[choice]))
                max_normal_error=max(max_normal_error,float(normal_errors[choice]))
        expected_material='source-7' if index in (2,3,4)else '8'
        assert len(mesh.materials)>0
        assert all(material['revit:source_material_id']==expected_material for material in mesh.materials)
    assert (manifest.read_bytes(),(work/'geometry.bin').read_bytes())==original
    images=[image for image in bpy.data.images if image.get(OWNER)==identity]
    assert images and all(image.packed_file and len(image.pixels)>0 for image in images)
    passes.append(dict(enabled=enabled,unique_meshes=expected_count,max_world_corner_error_metres=max_error,
                       max_world_normal_error=max_normal_error,statistics=converted['instance_matching']))
assert bpy.data.objects.get('Cube')is not None
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
report=dict(status='passed',blender=bpy.app.version_string,passes=passes,raw_anchors_unchanged=True,
            textured_uv_preserved=True,custom_normals_preserved=True,raw_restore_when_disabled=True)
(work/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('AGR_AFFINE_INTEGRATION '+json.dumps(report),flush=True)
