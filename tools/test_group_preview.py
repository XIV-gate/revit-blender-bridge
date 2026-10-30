"""Per-row previews work before import, independently of checkboxes/active row."""
import json
import os
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
addon.register()
from xivgate_revit_bridge import ui
from xivgate_revit_bridge import spatial_preview as preview

scene = bpy.context.scene
props = scene.xivgate_revit_bridge
props.catalog_source = 'fixture.rvt'
props.center_model = True
ui._SYNCING = True
for index in range(1002):
    row = props.elements.add()
    row.element_id = str(index)
    row.category_id = 'windows' if index < 1001 else 'walls'
    row.name = row.category_name = 'Окна' if index < 1001 else 'Стены'
    row.bbox_json = json.dumps(([1000000+index, 2000000, 0], [1000001+index, 2000001, 3]))
    row.selected = False
for identifier, name in [('windows', 'Окна'), ('walls', 'Стены')]:
    group = props.categories.add()
    group.group_kind, group.group_id, group.name = 'category', identifier, name
ui._SYNCING = False
ui.sync_groups(props)
props.category_index = 1
props.search = 'Стены'
guest = bpy.data.objects.get('Cube')
selected = tuple(bpy.context.selected_objects)
active = bpy.context.view_layer.objects.active

def objects():
    return {obj[preview.GROUP_TAG]: obj for obj in scene.objects if obj.get(preview.PREVIEW_TAG)
            and obj.get(preview.SCENE_TAG) == scene}

window_key = ui.preview_group_key(props, 'category', 'windows')
wall_key = ui.preview_group_key(props, 'category', 'walls')
assert not props.origin_valid and not props.import_id
assert bpy.ops.xivgate_rbb.toggle_preview(kind='category', group_id='windows', group_name='Окна') == {'FINISHED'}
windows = objects()[window_key]
pointer = windows.as_pointer()
assert len(windows.data.vertices) == 1001*8, 'Do not silently truncate the layer to 1000 boxes'
assert max(abs(value) for vertex in windows.data.vertices for value in vertex.co) < 2000
assert props.selected_count == 0 and props.category_index == 1 and props.search == 'Стены'
assert bpy.ops.xivgate_rbb.toggle_preview(kind='category', group_id='walls', group_name='Стены') == {'FINISHED'}
assert len(objects()) == 2 and objects()[window_key].as_pointer() == pointer
wall = objects()[wall_key]
assert bpy.ops.xivgate_rbb.toggle_preview(kind='category', group_id='windows', group_name='Окна') == {'FINISHED'}
assert set(objects()) == {wall_key}
assert tuple(bpy.context.selected_objects) == selected and bpy.context.view_layer.objects.active == active
assert guest in scene.objects.values()

# A subsequent geometry import can choose a different origin and moved root.
parent = bpy.data.objects.new('Fixture imported root', None)
scene.collection.objects.link(parent)
parent.matrix_world = Matrix.Translation((4, 5, 6))
offset = (1000000, 2000000, 0)
preview.align_previews(scene, offset, parent)
scene.view_layers[0].update()
point = wall.matrix_world @ wall.data.vertices[0].co
assert (point-Vector((1005, 5, 6))).length < 1e-4, point
other = bpy.data.scenes.new('Other scene')
other.collection.objects.link(wall)
before = wall.matrix_world.copy()
preview.align_previews(scene, (0, 0, 0), None)
assert wall.matrix_world == before, 'Shared previews must not move another scene'
other.collection.objects.unlink(wall)
bpy.data.scenes.remove(other)

props.elements[-1].bbox_json = ''
assert bpy.ops.xivgate_rbb.toggle_preview(kind='category', group_id='walls', group_name='Стены') == {'FINISHED'}
assert not objects()
assert bpy.ops.xivgate_rbb.toggle_preview(kind='category', group_id='walls', group_name='Стены') == {'CANCELLED'}
assert props.selected_count == 0
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_GROUP_PREVIEW passed', bpy.app.version_string, flush=True)
