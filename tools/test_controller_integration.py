"""Exercise real UI/controller/converter/importer with an explicit test-only worker double."""
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

import bpy

SOURCE = Path(os.environ['RBB_SOURCE_DIR'])
WORK = Path(os.environ['RBB_TEST_ROOT']) / ('controller_' + str(os.getpid()))
WORK.mkdir()
sys.dont_write_bytecode = True
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
from fixture_cache import write_fixture
import xivgate_revit_bridge
from xivgate_revit_bridge import runtime
from xivgate_revit_bridge.core.jobs import atomic_write_json, read_json


class TestWorker:
    """Only Revit I/O is simulated; conversion is a real bundled Python subprocess."""
    pid = None

    def start(self, exe, directory, token, extra_env=None):
        self.directory = Path(directory)
        self.pid = 123456
        self.last = None
        self.poll()

    def poll(self):
        request = read_json(self.directory / 'request.json') if self.pid else None
        if request and request['request_id'] != self.last:
            self.last = request['request_id']
            output = Path(request['output_dir'])
            response = {key: request[key] for key in ('schema', 'session_id', 'request_id')}
            response.update(success=True, action=request['action'])
            if request['action'] == 'scan':
                elements = [dict(id=key, unique_id='unique-' + key, name='Fixture ' + key,
                    category_id='windows' if key != '13' else 'walls', category_name='Окна' if key != '13' else 'Стены',
                    workset_id='facade', workset_name='Фасады', recommendation='include') for key in ('11', '12', '13')]
                catalog = output / 'catalog.json'
                atomic_write_json(catalog, dict(schema=1, elements=elements, warnings=[]))
                response['catalog_path'] = str(catalog)
            elif request['action'] == 'export':
                manifest = write_fixture(output, set(request['selection']['include_ids']))
                response['manifest_path'] = str(manifest)
            atomic_write_json(self.directory / 'response.json', response)
        return None

    def terminate(self):
        self.pid = None


def wait_ready():
    deadline = time.monotonic() + 45
    while runtime.STATE.action and time.monotonic() < deadline:
        runtime.tick()
        time.sleep(0.05)
    assert not runtime.STATE.action, 'Controller timeout'
    assert props.status != 'ERROR', props.message


xivgate_revit_bridge.register()
scene = bpy.context.scene
props = scene.xivgate_revit_bridge
source = WORK / 'test-only-source.rvt'
source.write_bytes(b'explicit test double, not a Revit document')
exe = WORK / 'Revit.exe'; exe.write_bytes(b'test double')
dll = WORK / 'test-worker.dll'; dll.write_bytes(b'test double')
pointer = WORK / 'test.addin'; pointer.write_text('test-only')
props.source_rvt = str(source); props.revit_exe = str(exe); props.cache_directory = str(WORK / 'jobs')
with patch.object(runtime, 'WorkerProcess', TestWorker), patch.object(runtime, 'worker_path', return_value=dll), \
        patch.object(runtime, 'setup_plan', return_value=dict(path=str(pointer), content='test-only')), \
        patch.object(runtime, 'manifest_matches', return_value=True):
    runtime.begin_scan(scene)
    wait_ready()
    assert props.selected_count == 3 and props.status == 'READY'
    runtime.begin_export(scene)
    # Switch active scene while conversion finishes. The result must still belong to its initiating scene.
    bpy.context.window.scene = bpy.data.scenes.new('Other active scene')
    wait_ready()
    assert props.status == 'DONE' and props.result_placements == 3, props.message
    assert props.result_collection in scene.collection.children
    assert props.result_collection not in bpy.context.scene.collection.children
    props.elements[1].selected = False
    props.elements[2].selected = False
    runtime.begin_export(scene)
    wait_ready()
    assert props.result_placements == 1 and props.origin_valid
assert source.read_bytes() == b'explicit test double, not a Revit document'
xivgate_revit_bridge.unregister()
report = dict(status='passed', blender=bpy.app.version_string, real_converter_subprocess=True,
              catalog_selection=True, selective_reimport=True, scene_binding=True, revit_io='test double')
(WORK / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('XIVGATE_CONTROLLER ' + json.dumps(report))
