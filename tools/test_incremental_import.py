"""Subset removal and stable-name regressions; use run_blender_test.py only."""
import json
import os
from pathlib import Path
import sys
import time

import bpy
from mathutils import Matrix

SOURCE = Path(os.environ['RBB_SOURCE_DIR']).resolve()
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
for name in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
             'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[name]).resolve().is_relative_to(WORK)

from fixture_cache import write_fixture
from xivgate_revit_bridge import blender_import as adapter
from xivgate_revit_bridge.conversion import build_usd

run = WORK / ('incremental_import_' + bpy.app.version_string.replace(' ', '_') + '_' + str(os.getpid()))
manifest = write_fixture(run / 'export')
usd = manifest.with_name('scene.usdc')
build_usd(manifest, usd)

# A foreign collection deliberately owns the ideal category name.
foreign_collection = bpy.data.collections.new('Окна')
bpy.context.scene.collection.children.link(foreign_collection)
foreign_object = bpy.data.objects.get('Cube')

def names(identity):
    return {kind: sorted(data.name for data in getattr(bpy.data, kind) if data.get(adapter.OWNER) == identity)
            for kind in ('objects', 'collections', 'meshes', 'materials', 'images')}

initial = adapter.import_result(usd, source_path='fixture.rvt', selected_element_ids={'11', '12', '13', 'nogeom'})
identity = initial['import_id']
expected_names = names(identity)
placement_names = {str(obj['revit:id']): obj.name for obj in bpy.data.objects
                   if obj.get(adapter.OWNER) == identity and 'revit:id' in obj}
for _ in range(3):
    repeated = adapter.import_result(usd, import_id=identity, source_path='fixture.rvt',
                                     selected_element_ids={'11', '12', '13', 'nogeom'})
    assert names(identity) == expected_names, (names(identity), expected_names)
assert foreign_collection.name == 'Окна'
assert foreign_object in bpy.data.objects.values()

# Repair the generated suffixes left behind by an older importer once names free.
legacy_placement = next(obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == identity and obj.get('revit:id') == '11')
legacy_placement.name = 'e_11.001'
legacy_root = next(collection for collection in bpy.data.collections if collection.get(adapter.ROOT_FLAG) and collection.get(adapter.OWNER) == identity)
legacy_root.name = 'Revit — fixture.001'
repaired = adapter.import_result(usd, import_id=identity, source_path='fixture.rvt', selected_element_ids={'11', '12', '13', 'nogeom'})
assert repaired['collection'] == 'Revit — fixture'
assert next(obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == identity and obj.get('revit:id') == '11').name == 'e_11'

root = next(collection for collection in bpy.data.collections if collection.get(adapter.ROOT_FLAG) and collection.get(adapter.OWNER) == identity)
objects = {str(obj['revit:id']): obj for obj in root.all_objects if 'revit:id' in obj and obj.get(adapter.OWNER) == identity}
retained = objects['11']
retained_pointer = retained.as_pointer()
retained_world = retained.matrix_world.copy()
assert all(obj.type == 'MESH' and obj.instance_type == 'NONE' for obj in objects.values())
assert objects['11'].data == objects['12'].data
assert objects['11'].data != objects['13'].data
assert sum(obj.matrix_world.determinant() < 0 for obj in objects.values()) == 1
before_vertex = objects['12'].data.vertices[0].co.copy()
objects['11'].data.vertices[0].co.x += 0.25
assert abs(objects['12'].data.vertices[0].co.x - before_vertex.x - 0.25) < 1e-6
objects['11'].data.vertices[0].co = before_vertex
assert sum(obj.get(adapter.OWNER) == identity and obj.type == 'EMPTY' for obj in bpy.data.objects) <= 1
assert not any(obj.get(adapter.OWNER) == identity and obj.instance_type == 'COLLECTION' for obj in bpy.data.objects)
prototype_pointers = {obj.data.as_pointer() for obj in objects.values()}
guest = bpy.data.objects.new('Foreign child of dropped placement', None)
next(iter(objects['13'].users_collection)).objects.link(guest)
guest.parent = objects['13']
guest.matrix_world = Matrix.Translation((9, 8, 7))
bpy.context.view_layer.update()
guest_world = guest.matrix_world.copy()

started = time.monotonic()
removed = adapter.apply_selection(identity, {'11', '12', 'nogeom'})
elapsed = time.monotonic() - started
assert removed is not None and removed['placements'] == 2 and removed['removed'] == 1
assert retained.as_pointer() == retained_pointer and retained.name == placement_names['11']
assert max(abs(retained.matrix_world[r][c] - retained_world[r][c]) for r in range(4) for c in range(4)) < 1e-6
assert guest in bpy.data.objects.values() and guest.parent is None
assert guest.name in bpy.context.scene.objects
assert max(abs(guest.matrix_world[r][c] - guest_world[r][c]) for r in range(4) for c in range(4)) < 1e-6
assert prototype_pointers.issubset({mesh.as_pointer() for mesh in bpy.data.meshes})
assert adapter.apply_selection(identity, {'11', '12', '13', 'nogeom'}) is None

# An identity shared by another scene must never be edited globally.
other = bpy.data.scenes.new('Shared import guard')
other.collection.children.link(root)
assert adapter.apply_selection(identity, {'11'}) is None
other.collection.children.unlink(root)
other.collection.objects.link(retained)
assert adapter.apply_selection(identity, {'12', 'nogeom'}) is None
try:
    adapter.import_result(usd, import_id=identity, selected_element_ids={'11', '12', '13', 'nogeom'})
except RuntimeError:
    pass
else:
    raise AssertionError('Full replacement must preserve objects linked by another scene')
other.collection.objects.unlink(retained)
bpy.data.scenes.remove(other)
empty = adapter.apply_selection(identity, set())
assert empty is not None and empty['placements'] == 0 and empty['removed'] == 2
assert guest.name in bpy.context.scene.objects

# Source element IDs group several occurrence IDs; never guess by splitting names.
from pxr import Sdf, Usd
stage = Usd.Stage.Open(str(usd))
for identifier in ('11', '12'):
    stage.GetPrimAtPath('/World/Elements/e_' + identifier).CreateAttribute(
        'revit:element_id', Sdf.ValueTypeNames.String, custom=True).Set('source-11')
stage.GetPrimAtPath('/World/Elements/e_13').CreateAttribute(
    'revit:element_id', Sdf.ValueTypeNames.String, custom=True).Set('source-13')
stage.GetRootLayer().Save()
grouped = adapter.import_result(usd, selected_element_ids={'source-11', 'source-13'})
subset = adapter.apply_selection(grouped['import_id'], {'source-13'})
assert subset is not None and subset['placements'] == 1 and subset['removed'] == 2

# EA opt-in stores existing objects off-scene and relinks the same editable IDs.
restorable = adapter.import_result(usd, selected_element_ids={'source-11', 'source-13', 'nogeom'})
restorable_id = restorable['import_id']
restorable_objects = [obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == restorable_id and 'revit:id' in obj]
original_pointers = {obj['revit:id']: obj.as_pointer() for obj in restorable_objects}
original_names = {obj['revit:id']: obj.name for obj in restorable_objects}
stock_parent = next(obj for obj in restorable_objects if obj['revit:id'] == '11')
stock_guest = bpy.data.objects.new('Foreign child during EA toggle', None)
stock_parent.users_collection[0].objects.link(stock_guest)
stock_guest.parent = stock_parent
stock_guest.matrix_world = Matrix.Translation((3, 4, 5))
bpy.context.view_layer.update()
stock_guest_world = stock_guest.matrix_world.copy()
hidden = adapter.apply_selection(restorable_id, {'source-13', 'nogeom'}, stash_deselected=True)
assert hidden is not None and hidden['placements'] == 1 and hidden['removed'] == 2
assert sum(obj.name in bpy.context.scene.objects for obj in restorable_objects) == 1
assert stock_guest.name in bpy.context.scene.objects and stock_guest.parent is None
assert max(abs(stock_guest.matrix_world[r][c] - stock_guest_world[r][c]) for r in range(4) for c in range(4)) < 1e-6
restored = adapter.apply_selection(restorable_id, {'source-11', 'source-13', 'nogeom'}, stash_deselected=True)
assert restored is not None and restored['placements'] == 3 and restored['restored'] == 2
assert all(obj.as_pointer() == original_pointers[obj['revit:id']] and obj.name == original_names[obj['revit:id']]
           and obj.name in bpy.context.scene.objects for obj in restorable_objects)
assert adapter.apply_selection(restorable_id, {'source-11', 'source-13', 'unknown'}, stash_deselected=True) is None
adapter.apply_selection(restorable_id, set(), stash_deselected=True)
adapter.import_result(usd, import_id=restorable_id, selected_element_ids={'source-11', 'source-13'})
assert sum(obj.get(adapter.OWNER) == restorable_id and 'revit:id' in obj for obj in bpy.data.objects) == 3
assert not any(collection.get(adapter.STASH_FLAG) and collection.get(adapter.OWNER) == restorable_id for collection in bpy.data.collections)

# Partial composite bundles require a new export; never drop retained child geometry.
stage = Usd.Stage.Open(str(usd))
stage.GetPrimAtPath('/World/Elements/e_11').CreateAttribute(
    'revit:source_element_ids', Sdf.ValueTypeNames.String, custom=True).Set(json.dumps(['source-11', 'child-11']))
stage.GetRootLayer().Save()
bundle = adapter.import_result(usd, selected_element_ids={'source-11', 'child-11', 'source-13'})
assert adapter.apply_selection(bundle['import_id'], {'child-11', 'source-13'}) is None
assert sum(obj.get(adapter.OWNER) == bundle['import_id'] and 'revit:id' in obj for obj in bpy.data.objects) == 3

# A parent exported beside its selected child omitted that child's geometry.
# Removing only the child requires fresh export, not deletion of its only mesh.
parent_case = adapter.import_result(usd, selected_element_ids={'source-11', 'source-13'})
child = next(obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == parent_case['import_id'] and obj.get('revit:id') == '13')
child['revit:selection_parent_ids'] = json.dumps(['source-11'])
before_parent_case = {obj.as_pointer() for obj in bpy.data.objects if obj.get(adapter.OWNER) == parent_case['import_id']}
assert adapter.apply_selection(parent_case['import_id'], {'source-11'}) is None
assert adapter.apply_selection(parent_case['import_id'], {'source-11'}, stash_deselected=True) is None
assert {obj.as_pointer() for obj in bpy.data.objects if obj.get(adapter.OWNER) == parent_case['import_id']} == before_parent_case
child.pop('revit:selection_parent_ids')
child['revit:parent_id'] = 'source-11'
assert adapter.apply_selection(parent_case['import_id'], {'source-11'}) is None

# The independent-mesh option obeys the same removal behavior.
expanded = adapter.import_result(usd, preserve_instances=False, selected_element_ids={'source-11', 'source-13'})
subset = adapter.apply_selection(expanded['import_id'], {'source-11'})
assert subset is not None and subset['placements'] == 2
assert all(obj.type == 'MESH' for obj in bpy.data.objects
           if obj.get(adapter.OWNER) == expanded['import_id'] and 'revit:id' in obj)
expanded_meshes = [obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == expanded['import_id'] and 'revit:id' in obj]
assert len({obj.data for obj in expanded_meshes}) == 2

report = dict(status='passed', blender=bpy.app.version_string, stable_all_datablock_names=True,
              subset_batch_removal=True, unrelated_preserved=True, guest_world_preserved=True,
              empty_selection=True, multiple_occurrences_per_element=True, independent_meshes=True,
              shared_scene_guard=True, linked_editable_meshes=True, no_hidden_prototype_objects=True,
              ea_reversible_local_toggles=True, subset_seconds=elapsed)
(run / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('AGR_INCREMENTAL_IMPORT ' + json.dumps(report, ensure_ascii=False), flush=True)
