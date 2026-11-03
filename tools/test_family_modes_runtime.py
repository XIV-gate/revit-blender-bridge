"""Real CLI/cache/mode/face-selection cycle; never launches Revit."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch

import bpy
import bmesh

sys.dont_write_bytecode=True
sys.path[:0]=[os.environ['RBB_SOURCE_DIR'],str(Path(os.environ['RBB_SOURCE_DIR'])/'tests')]
import xivgate_revit_bridge
from xivgate_revit_bridge import runtime
from xivgate_revit_bridge.core.jobs import JobSession,atomic_write_json,read_json
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result,apply_selection,merge_result,OWNER,ROOT_FLAG
from fixture_cache import write_fixture

root=Path(os.environ['RBB_TEST_ROOT']).resolve()
work=root/('family_modes_'+str(os.getpid())+'_'+uuid.uuid4().hex[:8]);work.mkdir()
xivgate_revit_bridge.register()
# Registration may refresh cached children after a manual ZIP update.
from xivgate_revit_bridge import ui, runtime
assert ui.RBB_Settings.is_registered
scene=bpy.context.scene;props=scene.xivgate_revit_bridge
props.preserve_instances=False
assert props.import_mode=='OBJECTS'
props.import_mode='FAMILY'
assert props.preserve_instances and props.import_mode=='FAMILY'
props.import_mode='INSTANCES'
assert props.preserve_instances
source=work/'source.rvt';source.write_bytes(b'test-only source; no Autodesk document')
exe=work/'Revit.exe';exe.write_bytes(b'never executed')
cache=work/'cache'
session=JobSession.create(cache)
request=session.request('export',selection_ids={'11','12','13'})
manifest=write_fixture(Path(request['output_dir']))
data=read_json(manifest)
sha=hashlib.sha256(source.read_bytes()).hexdigest()
data['source']={'path':str(source),'sha256':sha}
for row in data['occurrences']:
    row['family_name']='Windows' if row['id']!='13' else 'Walls'
atomic_write_json(manifest,data)
original=(manifest.read_bytes(),(manifest.parent/'geometry.bin').read_bytes())
converted=build_usd(manifest,manifest.with_name('family.usdc'),import_mode='FAMILY')
assert converted['placements']==2
result=import_result(converted['usd_path'],selected_element_ids={'11','12','13'},source_path=str(source))
identity=result['import_id']
assert result['placements']==2 and result['source_placements']==3


def owned():
    return [obj for obj in scene.objects if obj.type=='MESH' and obj.get(OWNER)==identity]


def window():
    return next(obj for obj in owned()if obj.get('revit:category')=='Окна')


group=window()
assert len(group.data.vertices)==6 and len(group.data.polygons)==2
assert group.data.attributes['revit_source_index'].domain=='FACE'
assert {row['id']for row in json.loads(group.data['revit:source_table'])}=={'11','12'}
assert apply_selection(identity,{'11','12','13'},stash_deselected=True)['placements']==2
assert apply_selection(identity,{'11','13'},stash_deselected=True)is None
assert len(window().data.polygons)==2,'Declined partial selection must not mutate old geometry'
assert merge_result(converted['usd_path'],import_id=identity,selected_element_ids={'13'},final_selection_ids={'11','12','13'})is None
for obj in bpy.context.selected_objects:obj.select_set(False)
group.select_set(True);bpy.context.view_layer.objects.active=group
bpy.ops.object.mode_set(mode='EDIT')
bm=bmesh.from_edit_mesh(group.data);bm.faces.ensure_lookup_table();bm.faces.active=bm.faces[0]
assert bpy.ops.xivgate_rbb.select_source_faces()=={'FINISHED'}
assert sum(face.select for face in bm.faces)==1
bpy.ops.object.mode_set(mode='OBJECT')

props.source_rvt=str(source);props.catalog_source=str(source);props.revit_exe=str(exe)
props.cache_directory=str(cache);props.import_mode='FAMILY';props.import_id=identity
props.import_manifest_path=str(manifest)
props.import_snapshot_context_json=json.dumps(dict(format='native_family_v1',source=str(source),sha256=sha,
    exe=os.path.normcase(str(exe.resolve())),detail=props.detail_level),sort_keys=True)
props.import_snapshot_selection_json=json.dumps(['11','12','13'])
props.result_placements=2;props.result_source_placements=3
for identifier in ('11','12','13'):
    row=props.elements.add();row.element_id=identifier;row.unique_id='u'+identifier;row.selected=True


def wait_ready():
    deadline=time.monotonic()+45
    while runtime.STATE.action and time.monotonic()<deadline:
        runtime.tick();time.sleep(.025)
    assert runtime.STATE.action is None and props.status!='ERROR',props.message


passes=[]
with patch.object(runtime,'begin_scan',side_effect=AssertionError('Snapshot must avoid Revit')), \
     patch.object(runtime,'inspect_revit',side_effect=AssertionError('Snapshot must avoid worker selection')):
    for mode,selected,expected_objects,expected_meshes,window_faces in (
            ('FAMILY',{'11','12','13'},2,2,2),('FAMILY',{'11','13'},2,2,1),
            ('FAMILY',{'11','12','13'},2,2,2),('OBJECTS',{'11','12','13'},3,3,None),
            ('INSTANCES',{'11','12','13'},3,2,None)):
        props.import_mode=mode
        for row in props.elements:row.selected=row.element_id in selected
        runtime.begin_export(scene)
        assert runtime.STATE.action=='convert'
        wait_ready()
        objects=owned()
        assert len(objects)==expected_objects
        assert len({obj.data.as_pointer()for obj in objects})==expected_meshes
        if window_faces is not None:assert len(window().data.polygons)==window_faces
        assert json.loads(props.import_snapshot_selection_json)==['11','12','13']
        assert (manifest.read_bytes(),(manifest.parent/'geometry.bin').read_bytes())==original
        assert not any(obj.name.endswith('.001')for obj in objects)
        passes.append(dict(mode=mode,selected=sorted(selected),objects=len(objects),meshes=expected_meshes))
assert bpy.data.objects.get('Cube')is not None
xivgate_revit_bridge.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
report=dict(status='passed',blender=bpy.app.version_string,passes=passes,
    provenance_preserved=True,source_face_selection=True,raw_unchanged=True,no_revit=True)
(work/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('AGR_FAMILY_MODES '+json.dumps(report),flush=True)
