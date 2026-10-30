"""Run only through the isolated Blender test launcher."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Matrix

SOURCE = Path(os.environ['RBB_SOURCE_DIR'])
WORK = Path(os.environ['RBB_TEST_ROOT'])
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'tests'))
for key in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[key]).resolve().is_relative_to(WORK.resolve())

import xivgate_revit_bridge
from xivgate_revit_bridge import ui, runtime
from xivgate_revit_bridge.blender_import import import_result, OWNER, ROOT_FLAG
from xivgate_revit_bridge.conversion import build_usd
from fixture_cache import write_fixture

xivgate_revit_bridge.register()
props = bpy.context.scene.xivgate_revit_bridge
props.busy = True
for identifier, category, recommendation in (('11', 'windows', 'include'), ('12', 'windows', 'review'), ('13', 'walls', 'exclude')):
    row = props.elements.add()
    row.element_id = identifier
    row.unique_id = 'unique-' + identifier
    row.category_id = category
    row.category_name = category
    row.workset_id = 'facade'
    row.workset_name = 'Фасады'
    row.recommendation = recommendation
    row.selected = recommendation != 'exclude'
for category in ('windows', 'walls'):
    row = props.categories.add()
    row.group_kind = 'category';row.group_id = category;row.name = category
row = props.worksets.add();row.group_kind = 'workset';row.group_id = 'facade';row.name = 'Фасады'
props.busy = False
ui.sync_groups(props)
assert props.selected_count == 2
props.categories[1].selected = True
assert props.selected_count == 3
props.worksets[0].selected = False
assert props.selected_count == 0
props.elements[0].selected = True
assert props.selected_count == 1

run = WORK / ('integration_' + bpy.app.version_string.replace(' ', '_') + '_' + str(os.getpid()))
first = write_fixture(run / 'first')
first_usd = first.with_name('scene.usdc')
build_usd(first, first_usd)
unrelated = bpy.data.objects.get('Cube')
result = import_result(first_usd, source_path='fixture.rvt')
identity = result['import_id']
instances = [obj for obj in bpy.data.objects if obj.get(OWNER) == identity and 'revit:id' in obj]
assert len(instances) == 3
assert len({obj.instance_collection for obj in instances}) == 2
assert sum(obj.matrix_world.determinant() < 0 for obj in instances) == 1
assert any(image.packed_file for image in bpy.data.images if image.source == 'FILE')
assert any(material.name.startswith('Fixture facade material') for material in bpy.data.materials)
assert unrelated in bpy.data.objects.values()
retained_world = next(obj for obj in instances if obj['revit:id'] == '11').matrix_world.copy()

old_collection = bpy.data.collections[result['collection']]
foreign = bpy.data.objects.new('User object inside import', None)
old_collection.objects.link(foreign)
old_root = next(obj for obj in bpy.data.objects if obj.get(OWNER) == identity and obj.type == 'EMPTY' and obj.instance_type == 'NONE')
foreign.parent = old_root
foreign.matrix_world = Matrix.Translation((9, 8, 7))
bpy.context.view_layer.update()
foreign_world = foreign.matrix_world.copy()
second = write_fixture(run / 'second', {'11'})
second_usd = second.with_name('scene.usdc')
build_usd(second, second_usd, source_offset=result['source_offset'])
updated = import_result(second_usd, import_id=identity, source_path='fixture.rvt')
assert updated['placements'] == 1
retained = next(obj for obj in bpy.data.objects if obj.get(OWNER) == identity and obj.get('revit:id') == '11')
assert max(abs(retained.matrix_world[r][c] - retained_world[r][c]) for r in range(4) for c in range(4)) < 0.0001
assert foreign in bpy.data.objects.values()
assert max(abs(foreign.matrix_world[r][c] - foreign_world[r][c]) for r in range(4) for c in range(4)) < 0.0001
assert unrelated in bpy.data.objects.values()
assert sum(bool(collection.get(ROOT_FLAG)) and collection.get(OWNER) == identity for collection in bpy.data.collections) == 1
expanded = import_result(first_usd, import_id=identity, preserve_instances=False, source_path='fixture.rvt')
expanded_objects = [obj for obj in bpy.data.objects if obj.get(OWNER) == identity and 'revit:id' in obj]
assert len(expanded_objects) == 3 and all(obj.type == 'MESH' for obj in expanded_objects)
assert len({obj.data for obj in expanded_objects}) == 3
assert foreign in bpy.data.objects.values() and unrelated in bpy.data.objects.values()
assert all(len(obj.data.uv_layers) == 1 for obj in expanded_objects)

# Material/texture/packing switches must affect the result rather than just the UI.
third = write_fixture(run / 'unpacked')
third_usd = third.with_name('scene.usdc')
build_usd(third, third_usd)
before_images = set(bpy.data.images)
import_result(third_usd, pack_images=False)
new_images = [image for image in bpy.data.images if image not in before_images and image.source == 'FILE']
assert new_images and not any(image.packed_file for image in new_images)
bare = write_fixture(run / 'bare')
bare_usd = bare.with_name('scene.usdc')
build_usd(bare, bare_usd, include_materials=False, include_textures=False)
before_materials = set(bpy.data.materials)
import_result(bare_usd)
assert set(bpy.data.materials) == before_materials

# A different active scene must neither choose a stale worker document nor receive this result.
original_scene = bpy.context.scene
target_scene = bpy.data.scenes.new('Async target')
runtime.STATE.scene = target_scene
runtime.STATE.converted_usd = first_usd
runtime.STATE.pending_options = dict(preserve_instances=True, pack_images=True, center_model=True)
runtime._apply_import()
target_props = target_scene.xivgate_revit_bridge
assert target_props.result_collection in target_scene.collection.children
assert target_props.result_collection not in original_scene.collection.children
props.catalog_source = 'model-a.rvt'; props.source_rvt = 'model-a.rvt'
runtime.STATE.session = SimpleNamespace(source_path='model-b.rvt')
runtime.STATE.worker = SimpleNamespace(poll=lambda: None)
with patch.object(runtime, 'begin_scan') as scan:
    runtime.begin_export(original_scene)
    scan.assert_called_once_with(original_scene, auto_import=True)
runtime.STATE.worker = None
runtime.STATE.session = None

report = {'status': 'passed', 'blender': bpy.app.version_string, 'ui_selection_callbacks': True,
          'shared_prototypes': True, 'mirrored_placements': True, 'selective_reimport': True,
          'unrelated_objects_preserved': True, 'foreign_child_preserved': True,
          'independent_mesh_option': True, 'texture_packing': True}
report.update(stable_reimport_coordinates=True, async_scene_binding=True, stale_scene_worker_rescan=True)
(run / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
xivgate_revit_bridge.unregister()
xivgate_revit_bridge.register()
xivgate_revit_bridge.unregister()
print('XIVGATE_INTEGRATION ' + json.dumps(report, ensure_ascii=False))
