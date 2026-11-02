"""Opt-in replay: equal surfaces share, different dimensions stay independent."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

import bpy
import numpy as np
from pxr import Usd, UsdGeom, Sdf

sys.dont_write_bytecode=True
sys.path.insert(0,os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER


def subset_stage(source, target, identifiers):
    original=Usd.Stage.Open(str(source)); stage=Usd.Stage.CreateNew(str(target))
    world=UsdGeom.Xform.Define(stage,'/World'); stage.SetDefaultPrim(world.GetPrim())
    world.AddTransformOp().Set(UsdGeom.Xformable(original.GetPrimAtPath('/World')).GetLocalTransformation())
    for attribute in original.GetPrimAtPath('/World').GetAttributes():
        if attribute.GetName().startswith('revit:'):
            world.GetPrim().CreateAttribute(attribute.GetName(),attribute.GetTypeName(),custom=True).Set(attribute.Get())
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.Xform.Define(stage,'/Prototypes').CreateVisibilityAttr('invisible')
    UsdGeom.Xform.Define(stage,'/World/Elements')
    Sdf.CopySpec(original.GetRootLayer(),'/Looks',stage.GetRootLayer(),'/Looks')
    anchors=set()
    for identifier in identifiers:
        path='/World/Elements/e_'+str(identifier)
        prim=original.GetPrimAtPath(path)
        assert prim,identifier
        anchor=str(prim.GetAttribute('revit:prototype').Get()); anchors.add(anchor)
    for anchor in anchors:
        path='/Prototypes/p_'+anchor
        Sdf.CopySpec(original.GetRootLayer(),path,stage.GetRootLayer(),path)
    for identifier in identifiers:
        path='/World/Elements/e_'+str(identifier)
        Sdf.CopySpec(original.GetRootLayer(),path,stage.GetRootLayer(),path)
    stage.GetRootLayer().Save()
    return target


def main():
    root=Path(os.environ['RBB_TEST_ROOT']).resolve()
    manifest=Path(os.environ['RBB_REAL_MANIFEST']).resolve()
    assert manifest.is_relative_to(root)
    ids=json.loads(Path(os.environ['RBB_SELECTION']).read_text())['include_ids']
    target=manifest.parent/('congruent_'+uuid.uuid4().hex+'.usdc')
    start=time.perf_counter()
    converted=build_usd(manifest,target,selected_element_ids=ids,instance_tolerance_mm=.1,
        source_offset=[-4408.612049628991,2087.697506955662,0])
    stage=Usd.Stage.Open(str(target))
    raw=json.loads(manifest.read_text(encoding='utf-8-sig'))
    rows={str(r['id']):r for r in raw['occurrences']}
    max_scale_error=0.
    for prim in stage.GetPrimAtPath('/World/Elements').GetChildren():
        identifier=str(prim.GetAttribute('revit:id').Get())
        raw_basis=np.array(rows[identifier]['transform'][:9]).reshape(3,3)
        new_basis=np.asarray(UsdGeom.Xformable(prim).GetLocalTransformation())[:3,:3]
        error=np.max(abs(np.linalg.svd(raw_basis,compute_uv=False)-np.linalg.svd(new_basis,compute_uv=False)))
        max_scale_error=max(max_scale_error,float(error))
    assert max_scale_error<1e-7,max_scale_error
    probe_ids=['5748745','6497161','5406092','5406134','5406073','11118419','5401169','5602806']
    probe=subset_stage(target,manifest.parent/('probe_'+uuid.uuid4().hex+'.usdc'),probe_ids)
    imported=import_result(probe,preserve_instances=True,selected_element_ids=set(probe_ids))
    objects={str(o.get('revit:id')):o for o in bpy.context.scene.objects if o.get(OWNER)==imported['import_id'] and o.type=='MESH'}
    assert len(objects)==len(probe_ids)
    assert objects['5748745'].data==objects['6497161'].data,'12/20 triangle panels must share'
    assert objects['5406092'].data==objects['5406134'].data,'Equal widths must share'
    assert objects['5406092'].data!=objects['5406073'].data,'22 mm width difference must not share'
    assert objects['11118419'].data!=objects['5401169'].data,'Different widths and heights must not share'
    assert len(objects['5602806'].data.polygons)==1006
    for obj in objects.values():
        assert all(abs(float(x)-1)<1e-6 for x in obj.scale),obj.name
        assert obj.data.uv_layers.active is not None
    report=dict(status='passed',blender=bpy.app.version_string,converted=converted,
        different_sizes_separate=True,subdivision_independent=True,max_placement_scale_error=max_scale_error,
        seconds=time.perf_counter()-start,probe_ids=probe_ids,probe_usd=str(probe))
    path=Path(os.environ['RBB_CONGRUENT_REPORT']);path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('AGR_CONGRUENT_REAL '+json.dumps(report),flush=True)


if __name__=='__main__':main()
