"""Isolated real Blender regression for verified incremental additions."""
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
from mathutils import Matrix
from pxr import Sdf, Usd, UsdGeom

SOURCE = Path(os.environ['RBB_SOURCE_DIR']).resolve()
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
for variable in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
                 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[variable]).resolve().is_relative_to(WORK)

from fixture_cache import write_fixture
from xivgate_revit_bridge import blender_import as adapter
from xivgate_revit_bridge.conversion import build_usd

run = WORK / ('delta_import_' + bpy.app.version_string.replace(' ', '_') + '_' + str(os.getpid()))

def fixture(label, ids, offset=None, signatures=True):
    manifest = write_fixture(run / label, ids)
    usd = manifest.with_name('scene.usdc')
    build_usd(manifest, usd, source_offset=offset)
    if signatures:
        metadata = json.loads(manifest.read_text())
        stage = Usd.Stage.Open(str(usd))
        for prototype in metadata['prototypes']:
            mesh = stage.GetPrimAtPath('/Prototypes/p_' + str(prototype['id']) + '/mesh')
            mesh.CreateAttribute('revit:geometry_signature', Sdf.ValueTypeNames.String, custom=True).Set(prototype['sha256'])
        material = stage.GetPrimAtPath('/Looks/m_7')
        material.CreateAttribute('revit:material_signature', Sdf.ValueTypeNames.String, custom=True).Set(hashlib.sha256(b'fixture material semantic').hexdigest())
        stage.GetRootLayer().Save()
    return usd


first = fixture('first', {'11'})
base = adapter.import_result(first, source_path='fixture.rvt', selected_element_ids={'11', 'nogeom'})
identity = base['import_id']
root = next(item for item in bpy.data.collections if item.get(adapter.OWNER) == identity and item.get(adapter.ROOT_FLAG))
root_pointer = root.as_pointer()
world = next(obj for obj in root.all_objects if 'revit:source_offset_metres' in obj)
world.matrix_world = Matrix.Translation((12, -4, 2)) @ Matrix.Rotation(0.3, 4, 'Z')
bpy.context.view_layer.update()
old = next(obj for obj in root.all_objects if obj.get('revit:id') == '11')
old_pointer, old_name, old_data = old.as_pointer(), old.name, old.data
old_world = old.matrix_world.copy()
old_material = old.data.materials[0]
foreign = bpy.data.objects.get('Cube')
for obj in bpy.context.selected_objects:
    obj.select_set(False)
foreign.select_set(True)
bpy.context.view_layer.objects.active = foreign

second = fixture('second', {'12'}, base['source_offset'])
merged = adapter.merge_result(second, import_id=identity, selected_element_ids={'12', 'new-nogeom'},
                              final_selection_ids={'11', '12', 'nogeom', 'new-nogeom'}, source_path='fixture.rvt')
assert merged is not None and merged['placements'] == 2 and merged['added'] == 1
assert root.as_pointer() == root_pointer and old.as_pointer() == old_pointer and old.name == old_name and old.data == old_data
assert max(abs(old.matrix_world[r][c] - old_world[r][c]) for r in range(4) for c in range(4)) < 1e-6
added = next(obj for obj in root.all_objects if obj.get('revit:id') == '12')
assert added.data == old_data and added.data.materials[0] == old_material
assert added.parent == world and added.matrix_world.determinant() < 0
stage = Usd.Stage.Open(str(second))
target = Matrix(tuple(tuple(row) for row in UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath('/World/Elements/e_12')))).transposed()
expected = world.matrix_world @ target
assert max(abs(added.matrix_world[r][c] - expected[r][c]) for r in range(4) for c in range(4)) < 0.0005
assert bpy.context.view_layer.objects.active == foreign and foreign.select_get()
assert sum(bool(item.get(adapter.ROOT_FLAG)) and item.get(adapter.OWNER) == identity for item in bpy.data.collections) == 1

# Different geometry still reuses only an exactly matching material semantic hash.
third = fixture('third', {'13'}, base['source_offset'])
merged = adapter.merge_result(third, import_id=identity, selected_element_ids={'13'},
                              final_selection_ids={'11', '12', '13', 'nogeom', 'new-nogeom'}, source_path='fixture.rvt')
assert merged is not None and merged['placements'] == 3
wall = next(obj for obj in root.all_objects if obj.get('revit:id') == '13')
assert wall.data != old_data and wall.data.materials[0] == old_material

# Duplicate occurrence IDs and partial composite requests leave old IDs untouched.
before = {kind: set(getattr(bpy.data, kind)) for kind in ('objects', 'collections', 'meshes', 'materials', 'images')}
assert adapter.merge_result(second, import_id=identity, selected_element_ids={'different-source'},
                            final_selection_ids={'11', '12', '13', 'different-source'}) is None
assert all(set(getattr(bpy.data, kind)) == values for kind, values in before.items())
old['revit:source_element_ids'] = json.dumps(['11', 'child-11'])
root[adapter.AVAILABLE_IDS] = json.dumps(['11', '12', '13', 'child-11', 'nogeom', 'new-nogeom'])
assert adapter.merge_result(second, import_id=identity, selected_element_ids={'another'},
                            final_selection_ids={'child-11', 'another'}) is None
assert old.as_pointer() == old_pointer

# A genuinely new occurrence with no optional hashes is imported without old-mesh reuse.
plain = fixture('plain', {'12'}, base['source_offset'], signatures=False)
stage = Usd.Stage.Open(str(plain))
prim = stage.GetPrimAtPath('/World/Elements/e_12')
prim.GetAttribute('revit:id').Set('14')
prim.GetAttribute('revit:element_id').Set('14')
prim.GetAttribute('revit:source_element_ids').Set(json.dumps(['14']))
for path in ('/Prototypes/p_0/mesh', '/Looks/m_7'):
    item = stage.GetPrimAtPath(path)
    for attribute in ('revit:geometry_signature', 'revit:material_signature'):
        if item.HasAttribute(attribute):
            item.RemoveProperty(attribute)
stage.GetRootLayer().Save()
merged = adapter.merge_result(plain, import_id=identity, selected_element_ids={'14'},
                              final_selection_ids={'11', '12', '13', '14'}, source_path='fixture.rvt')
assert merged is not None and merged['placements'] == 4
new_plain = next(obj for obj in root.all_objects if obj.get('revit:id') == '14')
assert new_plain.data != old_data
assert old.as_pointer() == old_pointer and old.data == old_data

# Force a failure after local final selection already stashed old geometry.
# Restore old metadata, links, foreign parent and every pre-existing ID exactly.
rollback_usd = fixture('rollback', {'12'}, base['source_offset'])
rollback_stage = Usd.Stage.Open(str(rollback_usd))
rollback_prim = rollback_stage.GetPrimAtPath('/World/Elements/e_12')
rollback_prim.GetAttribute('revit:id').Set('15')
rollback_prim.GetAttribute('revit:element_id').Set('15')
rollback_prim.GetAttribute('revit:source_element_ids').Set(json.dumps(['15']))
rollback_stage.GetRootLayer().Save()
guest = bpy.data.objects.new('Foreign delta rollback child', None)
old.users_collection[0].objects.link(guest)
guest.parent = old
guest.matrix_world = Matrix.Translation((7, 8, 9))
bpy.context.view_layer.update()
guest_world = guest.matrix_world.copy()
baseline = {kind: set(getattr(bpy.data, kind)) for kind in ('objects', 'collections', 'meshes', 'materials', 'images')}
metadata_before = {key: root[key] for key in (adapter.SELECTION, adapter.GEOMETRY_IDS, adapter.AVAILABLE_IDS, adapter.AVAILABLE_GEOMETRY_IDS)}
apply = adapter.apply_selection

def decline_after_mutation(*args, **kwargs):
    assert apply(*args, **kwargs) is not None
    return None

with patch.object(adapter, 'apply_selection', side_effect=decline_after_mutation):
    assert adapter.merge_result(rollback_usd, import_id=identity, selected_element_ids={'15'},
                                final_selection_ids={'12', '13', '14', '15'}, source_path='fixture.rvt') is None
assert all(set(getattr(bpy.data, kind)) == values for kind, values in baseline.items())
assert {key: root[key] for key in metadata_before} == metadata_before
assert guest.parent == old and guest.name in bpy.context.scene.objects
assert max(abs(guest.matrix_world[r][c] - guest_world[r][c]) for r in range(4) for c in range(4)) < 1e-6
assert old.as_pointer() == old_pointer and old.name == old_name and old.data == old_data

report = dict(status='passed', blender=bpy.app.version_string, retained_old_ids=True,
              shared_mesh_signature_reuse=True, material_signature_reuse=True,
              preserved_user_root_transform=True, preserved_context=True,
              duplicates_rollback=True, partial_composites_refused=True, missing_signatures_no_reuse=True,
              rollback_after_selection_mutation=True, foreign_parent_rollback=True)
(run / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('AGR_DELTA_IMPORT ' + json.dumps(report, ensure_ascii=False), flush=True)
