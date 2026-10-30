"""A far survey point must not displace preview; real conversion uses its frame."""
import json
import os
from pathlib import Path
import sys
import time

import bpy
from mathutils import Vector

source = Path(os.environ['RBB_SOURCE_DIR'])
sys.path[:0] = [str(source), str(source/'tests')]
import xivgate_revit_bridge as addon
addon.register()
from xivgate_revit_bridge import ui, runtime
from xivgate_revit_bridge import spatial_preview as preview
from fixture_cache import write_fixture

root = Path(os.environ['RBB_TEST_ROOT']) / ('preview_origin_' + str(os.getpid()))
manifest = write_fixture(root, {'11'})
value = json.loads(manifest.read_text(encoding='utf-8'))
value['occurrences'][0]['transform'][9:] = [1000000, 2000000, 50]
manifest.write_text(json.dumps(value), encoding='utf-8')
scene = bpy.context.scene
props = scene.xivgate_revit_bridge
props.catalog_source = str(root/'fixture.rvt')
ui._SYNCING = True
for identifier, category, low, high in [
        ('11', 'Окна', [1000000, 2000000, 50], [1000002, 2000000, 53]),
        ('survey', 'Точка съёмки', [0, 0, 0], [0, 0, 0])]:
    row = props.elements.add()
    row.element_id, row.category_id, row.category_name = identifier, identifier, category
    row.name = category
    row.bbox_json = json.dumps((low, high))
    row.selected = True
    row.recommendation = 'include' if identifier == '11' else 'exclude'
ui._SYNCING = False
ui.sync_groups(props)
assert bpy.ops.xivgate_rbb.toggle_preview(kind='element', group_id='11', group_name='Окно') == {'FINISHED'}
frame = next(obj for obj in scene.objects if obj.get(preview.PREVIEW_TAG))
assert json.loads(props.preview_offset_json) == [1000001, 2000000, 0]

def bounds(obj):
    scene.view_layers[0].update()
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    return tuple(tuple(function(point[axis] for point in points) for axis in range(3)) for function in (min, max))

assert bounds(frame) == ((-1, 0, 50), (1, 0, 53))
runtime.STATE.scene = scene
runtime.STATE.file_root = root
runtime.STATE.pending_selected_ids = {'11'}
runtime.STATE.pending_context = '{}'
runtime.STATE.pending_options = dict(preserve_instances=True, pack_images=False, materials=False,
                                     textures=False, center_model=True)

def convert():
    runtime.STATE.pending_options['center_model'] = props.center_model
    runtime._start_conversion(manifest)
    deadline = time.monotonic()+45
    while runtime.STATE.action and time.monotonic() < deadline:
        runtime.tick()
        time.sleep(.02)
    assert runtime.STATE.action is None and props.status != 'ERROR', props.message
    from xivgate_revit_bridge.blender_import import OWNER
    obj = next(item for item in scene.objects if item.type == 'MESH' and item.get(OWNER) == props.import_id)
    for left, right in zip(bounds(frame), bounds(obj)):
        assert (Vector(left)-Vector(right)).length < 1e-4, (bounds(frame), bounds(obj))

convert()
assert json.loads(props.source_offset_json) == [1000001, 2000000, 0]
props.center_model = False
assert bounds(frame) == ((1000000, 2000000, 50), (1000002, 2000000, 53))
convert()
assert not props.origin_centered
props.center_model = True
assert bounds(frame) == ((-1, 0, 50), (1, 0, 53)), 'Current checkbox must override previous uncentered import'
convert()

# Migration repairs an existing old preview even if its previous offset was bad.
frame.matrix_basis.translation = Vector((9000, 8000, -100))
ui._load_post()
assert bounds(frame) == ((-1, 0, 50), (1, 0, 53))
legacy = preview.preview_bounds(scene, [dict(bbox_min=[1000000, 2000000, 50],
    bbox_max=[1000002, 2000000, 53])], source_offset=(500001, 1000000, 51.5), group_key='legacy-origin')
legacy_object = bpy.data.objects[legacy['object_name']]
ui._load_post()
for left, right in zip(bounds(legacy_object), bounds(frame)):
    assert (Vector(left)-Vector(right)).length < 1e-4
runtime.cleanup()
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
print('AGR_PREVIEW_ORIGIN passed', bpy.app.version_string)
