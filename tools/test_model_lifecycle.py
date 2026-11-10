"""16 lifecycle combinations in an isolated host; never edits user RVTs."""
import itertools
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import bpy
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
from xivgate_revit_bridge import workflow
from xivgate_revit_bridge.core.jobs import JobSession
from xivgate_revit_bridge.core.model_lifecycle import preparation_options
from xivgate_revit_bridge.core.revit_versions import discover
addon.register()
root=Path(os.environ['RBB_TEST_ROOT']) / ('lifecycle_'+str(os.getpid()))
root.mkdir()
scene=bpy.context.scene
props=scene.xivgate_revit_bridge
assert not bpy.data.filepath
completed=[]
for index,(overwrite,reuse,keep,pack) in enumerate(itertools.product((False,True),repeat=4)):
    source=root/('model_'+str(index)+'.rvt')
    source.write_bytes(b'disposable native-source placeholder')
    props.source_rvt=str(source)
    props.overwrite_source,props.reuse_prepared_model=overwrite,reuse
    props.keep_temporary_data,props.pack_images=keep,pack
    cache=workflow.cache_root(props)
    assert cache==source.with_suffix('.Data')
    job=JobSession.create(cache)
    texture=job.directory/'output'/'pixel.png'
    generated=bpy.data.images.new('pixel',1,1)
    generated.pixels[:]=[.25,.5,.75,1]
    generated.file_format='PNG';generated.filepath_raw=str(texture);generated.save()
    bpy.data.images.remove(generated)
    image=bpy.data.images.load(str(texture),check_existing=False)
    material=bpy.data.materials.new('image holder');material.use_nodes=True
    material.node_tree.nodes.new('ShaderNodeTexImage').image=image
    if pack:image.pack()
    props.import_manifest_path=str(job.directory/'output'/'manifest.json')
    if not keep:
        workflow.prepare_automatic_cleanup(scene,cache)
        job.finish()
        result=workflow.finish_automatic_cleanup(scene,cache)
        assert not result['errors'],result
        assert not job.directory.exists()
        if pack:
            assert image.packed_file
        else:
            assert Path(image.filepath).parent==cache/'textures'
            assert Path(image.filepath).is_file()
            image.reload()
        assert len(image.pixels)==4
        assert not props.import_manifest_path
    else:
        assert texture.is_file()
        assert props.import_manifest_path
    assert source.read_bytes()==b'disposable native-source placeholder'
    bpy.data.materials.remove(material);bpy.data.images.remove(image)
    completed.append([overwrite,reuse,keep,pack])
for field in ('overwrite_source','reuse_prepared_model','keep_temporary_data','pack_images'):
    assert props.bl_rna.properties[field].description
found=discover()
assert found and all(Path(item['path']).is_file() for item in found)
assert addon.ui.RBB_OT_choose_revit.bl_description
popups=[]
selector=SimpleNamespace(report=lambda *args:None)
context=SimpleNamespace(scene=scene,window_manager=SimpleNamespace(invoke_search_popup=lambda op:popups.append(op)))
assert addon.ui.RBB_OT_choose_revit.invoke(selector,context,None)=={'RUNNING_MODAL'}
assert popups==[selector]
assert addon.ui.RBB_OT_choose_revit.execute(selector,context)=={'FINISHED'}
assert props.revit_exe==found[0]['path']

addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
print('MODEL_LIFECYCLE_PASS '+json.dumps(dict(blender=bpy.app.version_string,combinations=len(completed),unsaved_scene=True,installed_revit=[x['year'] for x in found])))
