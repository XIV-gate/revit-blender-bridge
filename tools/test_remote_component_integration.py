"""Opt-in real-cache subset/filter regression; read only an approved replay copy."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import bpy
from pxr import Usd, UsdGeom

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

root = Path(os.environ['RBB_TEST_ROOT']).resolve()
manifest = Path(os.environ['RBB_REAL_MANIFEST']).resolve(strict=True)
assert manifest.is_relative_to(root), 'Use the approved immutable replay copy'
metadata = json.loads(manifest.read_text(encoding='utf-8-sig'))
binary = manifest.parent/metadata.get('binary_file','geometry.bin')
def digest(path):
    sha=hashlib.sha256()
    with path.open('rb')as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()
original=(digest(manifest),digest(binary))
panels={str(row['id'])for row in metadata['occurrences']if str(row.get('type_id'))=='927216'}
assert len(panels)==1541 and '11118419'in panels
floor='5602806'
selected=panels|{floor}
work=root/('remote_integration_'+str(os.getpid())+'_'+uuid.uuid4().hex[:8])
work.mkdir()
# USD image assets must live beside their manifest. Link read-only inputs into
# this private run directory; the converter never writes them or the original.
os.link(manifest, work/'manifest.json')
os.link(binary, work/metadata.get('binary_file','geometry.bin'))
textures = manifest.parent/'textures'
if textures.is_dir():
    for texture in textures.rglob('*'):
        if texture.is_file():
            linked = work/texture.relative_to(manifest.parent)
            linked.parent.mkdir(parents=True, exist_ok=True)
            os.link(texture, linked)
conversion_manifest = work/'manifest.json'
passes=[]
identity=None
for index,(label,enabled,ids,expected_triangles)in enumerate((
        ('on',True,selected,1006),('off',False,selected,1164),
        ('subset',False,{'11118419',floor},1164),('restore',True,selected,1006))):
    target=work/(str(index)+'-'+label+'.usdc')
    started=time.monotonic()
    converted=build_usd(conversion_manifest,target,center_model=False,filter_remote_geometry=enabled,
                        selected_element_ids=ids,instance_tolerance_mm=.1)
    assert converted['placements']==len(ids),converted
    stage=Usd.Stage.Open(str(target))
    children=stage.GetPrimAtPath('/World/Elements').GetChildren()
    assert {str(prim.GetAttribute('revit:id').Get())for prim in children}==ids
    placement=stage.GetPrimAtPath('/World/Elements/e_'+floor)
    prototype=str(placement.GetAttribute('revit:prototype').Get())
    mesh=UsdGeom.Mesh(stage.GetPrimAtPath('/Prototypes/p_'+prototype+'/mesh'))
    assert len(mesh.GetFaceVertexCountsAttr().Get())==expected_triangles
    details=converted['remote_components']
    if enabled:
        assert details['removed_triangles']==158 and details['filtered_elements']==1,details
        assert details['elements'][0]['id']==floor and details['elements'][0]['kept_triangles']==1006
    else:
        assert details=={}
    result=import_result(target,import_id=identity,preserve_instances=True,source_path=metadata['source']['path'],
                         selected_element_ids=ids)
    identity=result['import_id']
    objects={str(obj['revit:id']):obj for obj in bpy.context.scene.objects
             if obj.get(OWNER)==identity and 'revit:id'in obj}
    assert set(objects)==ids
    assert len(objects[floor].data.polygons)==expected_triangles
    control=objects['11118419']
    users=sum(obj.data==control.data for obj in objects.values())
    assert (users == 1) if label == 'subset' else (users > 1), (label, users)
    if label != 'subset':
        assert objects['11118419'].data != objects['5401169'].data, 'Different sizes must stay independent'
    assert (digest(manifest),digest(binary))==original
    passes.append(dict(case=label,filter_enabled=enabled,placements=len(ids),floor_triangles=expected_triangles,
                       control_mesh_users=users,seconds=time.monotonic()-started,remote_components=details))
assert bpy.data.objects.get('Cube')is not None
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
report=dict(status='passed',blender=bpy.app.version_string,raw_unchanged=True,passes=passes,
            subset_only_selected_ids=True,different_sizes_independent=True,no_revit=True)
(work/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('AGR_REMOTE_INTEGRATION '+json.dumps(report),flush=True)
