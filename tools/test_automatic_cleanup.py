"""Real converter/import plus graceful fake-native close and crash preservation."""
import os
import runpy
from pathlib import Path
import time
from unittest.mock import patch
import bpy
base=runpy.run_path(str(Path(os.environ['RBB_SOURCE_DIR'])/'tools/test_controller_integration.py'))
addon,Worker,scene,work=base['xivgate_revit_bridge'],base['TestWorker'],base['scene'],base['WORK']
addon.register()
from xivgate_revit_bridge import runtime,workflow
from xivgate_revit_bridge.core.jobs import read_json
bpy.context.window.scene=scene
props=scene.xivgate_revit_bridge
props.revit_exe=str(base['exe']);props.cache_directory=str(work/'cleanup')
props.keep_temporary_data=False

def wait():
    deadline=time.monotonic()+45
    while runtime.STATE.action and time.monotonic()<deadline:
        runtime.tick();time.sleep(.02)
    assert runtime.STATE.action is None
    assert props.status!='ERROR',props.message

with patch.object(runtime,'WorkerProcess',Worker),patch.object(runtime,'worker_path',return_value=base['dll']),patch.object(runtime,'inspect_revit',return_value=dict(key='2027',year=2027)),patch.object(runtime,'setup_plan',return_value=dict(path=str(base['pointer']),content='test-only')),patch.object(runtime,'manifest_matches',return_value=True):
    for pack in (True,False):
        source=work/('cleanup_'+str(pack)+'.rvt');source.write_bytes(b'original fixture')
        props.source_rvt=str(source);props.pack_images=pack
        runtime.begin_scan(scene);wait()
        request=read_json(runtime.STATE.session.directory/'request.json')
        assert request['options']['keep_temporary_data'] is False
        runtime.begin_export(scene);wait()
        job=Path(props.job_directory)
        assert not job.exists(),(pack,props.message,job)
        assert runtime.STATE.worker is None
        assert props.result_placements==3
        assert not props.import_manifest_path
        if not pack:
            root=workflow.cache_root(props)
            images=[im for im in bpy.data.images if not im.packed_file and im.users and Path(bpy.path.abspath(im.filepath)).is_relative_to(root)]
            assert images
            for image in images:
                assert Path(image.filepath).is_file()
                image.reload()
        assert source.read_bytes()==b'original fixture'
    Worker.crash_after_export=False
    source=work/'crash.rvt';source.write_bytes(b'original fixture')
    props.source_rvt=str(source)
    runtime.begin_scan(scene);wait()
    runtime.begin_export(scene)
    while runtime.STATE.action != 'convert':
        runtime.tick();time.sleep(.02)
    runtime.STATE.worker.pid=None
    runtime.STATE.worker.exit_code=17
    wait()
    assert Path(props.job_directory).exists()
    assert 'штатное завершение' in '\n'.join(__import__('xivgate_revit_bridge.core.reporting',fromlist=['raw_values']).raw_values(props.warnings)).lower()
    Worker.crash_after_export=False
addon.unregister()
print('AUTOMATIC_CLEANUP_PASS',bpy.app.version_string)
