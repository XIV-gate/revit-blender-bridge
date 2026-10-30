"""Run directly for pure geometry tests, or with the isolated Blender test runner."""
import importlib
import math
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
source = Path(os.environ.get('RBB_SOURCE_DIR', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(source))
preview = importlib.import_module('xivgate_revit_bridge.spatial_preview')
row = {'id': '123', 'name': 'Фасадный блок', 'bbox_min': [10, 20, 30], 'bbox_max': [12, 23, 34]}
invalid = [None, {}, {'bbox_min': None, 'bbox_max': None},
           {'bbox_min': [1, 0, 0], 'bbox_max': [0, 1, 1]},
           {'bbox_min': [math.nan, 0, 0], 'bbox_max': [1, 1, 1]},
           {'bbox_min': [0, 0, 0], 'bbox_max': [1, math.inf, 1]},
           {'bbox_min': ['0', 0, 0], 'bbox_max': [1, 1, 1]}]
vertices, edges, result = preview.bbox_wire_data([*invalid, row, row], (10, 20, 30), limit=1)
assert result == {'shown': 1, 'total': 9, 'skipped': 8}, result
assert len(vertices) == 8 and len(edges) == 12
assert vertices[0] == (0, 0, 0) and vertices[-1] == (2, 3, 4)
assert len(set(tuple(sorted(edge)) for edge in edges)) == 12
assert all(sum(vertices[a][axis] != vertices[b][axis] for axis in range(3)) == 1 for a, b in edges)
assert preview.bbox_wire_data([row], limit=0)[2] == {'shown': 0, 'total': 1, 'skipped': 1}
assert preview.bbox_wire_data([row, row], limit=2)[1][-1] == (11, 15)
for offset, limit in [((math.inf, 0, 0), 1), ((0, 0), 1), ((0, 0, 0), -1), ((0, 0, 0), True)]:
    try:
        preview.bbox_wire_data([row], offset, limit)
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid preview configuration was accepted')

try:
    import bpy
except ImportError:
    print('AGR_SPATIAL_PREVIEW pure geometry passed')
    raise SystemExit(0)

root = Path(os.environ['RBB_TEST_ROOT']).resolve()
for key in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[key]).resolve().is_relative_to(root)
scene = bpy.context.scene
from mathutils import Vector
assert preview.bbox_wire_data([row], Vector((10, 20, 30)))[0][0] == (0, 0, 0)
other_scene = bpy.data.scenes.new('UnrelatedScene')
foreign_mesh = bpy.data.meshes.new('UserMesh')
foreign_mesh.from_pydata([(0, 0, 0)], [], [])
foreign = bpy.data.objects.new('UserObject', foreign_mesh)
foreign[preview.PREVIEW_TAG] = True  # A matching flag alone is not scene ownership.
scene.collection.objects.link(foreign)
foreign.select_set(True)
bpy.context.view_layer.objects.active = foreign
selection = {obj.name for obj in bpy.context.selected_objects}
active = bpy.context.view_layer.objects.active
camera = scene.camera

other_result = preview.preview_bounds(other_scene, [row])
other_obj = bpy.data.objects[other_result['object_name']]
first = preview.preview_bounds(scene, [row, *invalid], (10, 20, 30))
obj = bpy.data.objects[first['object_name']]
old_mesh = obj.data
old_mesh_name = old_mesh.name
assert first['shown'] == 1 and first['skipped'] == len(invalid)
assert len(obj.data.vertices) == 8 and len(obj.data.edges) == 12 and not obj.data.polygons
assert tuple(obj.data.vertices[0].co) == (0, 0, 0)
assert tuple(obj.data.vertices[-1].co) == (2, 3, 4)
assert obj.display_type == 'WIRE' and obj.show_wire and obj.show_in_front and obj.hide_render
assert all(abs(obj.color[i] - preview.ORANGE[i]) < 1e-5 for i in range(4))
assert 'xivgate_rbb_import_id' not in obj
assert {item.name for item in bpy.context.selected_objects} == selection
assert bpy.context.view_layer.objects.active == active and scene.camera == camera

second = preview.preview_bounds(scene, [row, row], limit=1)
assert old_mesh_name not in bpy.data.meshes
assert len([item for item in scene.objects if item.get(preview.PREVIEW_TAG) and item.get(preview.SCENE_TAG) == scene]) == 1
assert other_obj.name in other_scene.objects and foreign.name in scene.objects
shared_obj = bpy.data.objects[second['object_name']]
shared_mesh = shared_obj.data
other_scene.collection.objects.link(shared_obj)
preview.clear_preview(scene)
assert shared_obj.name not in scene.objects and shared_obj.name in other_scene.objects
assert shared_obj.data == shared_mesh and shared_mesh.users == 1
assert foreign.name in scene.objects and foreign.data == foreign_mesh
assert other_obj.name in other_scene.objects
preview.clear_preview(other_scene)
assert shared_obj.name in other_scene.objects  # Owner is the first scene, not this other scene.
assert not any(item.get(preview.SCENE_TAG) == other_scene for item in other_scene.objects)

last = preview.preview_bounds(scene, [row])
assert last['object_name']
empty = preview.preview_bounds(scene, invalid)
assert empty['shown'] == 0 and empty['object_name'] == ''
assert not any(item.get(preview.PREVIEW_TAG) and item.get(preview.SCENE_TAG) == scene for item in scene.objects)
assert {item.name for item in bpy.context.selected_objects} == selection
assert bpy.context.view_layer.objects.active == active
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_SPATIAL_PREVIEW host passed', bpy.app.version_string)
