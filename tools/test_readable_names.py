"""Isolated Blender 4.2/5.2 checks for readable owned object and mesh names."""
import json
import os
from pathlib import Path
import sys

import bpy

SOURCE = Path(os.environ['RBB_SOURCE_DIR']).resolve()
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
for key in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
            'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[key]).resolve().is_relative_to(WORK)

from fixture_cache import write_fixture
from xivgate_revit_bridge import blender_import as adapter
from xivgate_revit_bridge.conversion import build_usd

run = WORK / ('readable_names_' + bpy.app.version_string.replace(' ', '_') + '_' + str(os.getpid()))

def fixture(label, ids=None, offset=None):
    manifest = write_fixture(run / label, ids)
    value = json.loads(manifest.read_text())
    for item in value['occurrences']:
        item.update(family_name='Оконный блок' if item['id'] != '13' else 'Стена',
                    type_name='1800' if item['id'] != '13' else 'Наружная', name='1800' if item['id'] != '13' else 'Наружная')
    manifest.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    usd = manifest.with_name('scene.usdc')
    build_usd(manifest, usd, source_offset=offset)
    return usd

def placements(identity):
    return {str(obj['revit:id']): obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == identity and 'revit:id' in obj}

usd = fixture('all')
result = adapter.import_result(usd, source_path='fixture.rvt', selected_element_ids={'11', '12', '13'})
identity = result['import_id']
objects = placements(identity)
assert objects['11'].name.startswith('Окна') and '1800' in objects['11'].name
assert objects['11'].name.endswith('[11]')
assert objects['11'].data == objects['12'].data
assert objects['11'].data.name.startswith('Окна') and '[g:' in objects['11'].data.name
for obj in objects.values():
    assert len(obj.name.encode('utf-8')) <= 63 and len(obj.data.name.encode('utf-8')) <= 63
    assert obj.get(adapter.GENERATED_NAME) == obj.name
    assert obj.data.get(adapter.GENERATED_NAME) == obj.data.name
expected = {identifier: (obj.name, obj.data.name) for identifier, obj in objects.items()}
for _ in range(2):
    adapter.import_result(usd, import_id=identity, source_path='fixture.rvt', selected_element_ids={'11', '12', '13'})
    assert {identifier: (obj.name, obj.data.name) for identifier, obj in placements(identity).items()} == expected

# Genuine user edits survive explicit naming and full reimport, including shared data.
objects = placements(identity)
objects['11'].name = 'Моё окно на углу'
objects['11'].data.name = 'Моя геометрия окна'
before_objects = {identifier: obj.as_pointer() for identifier, obj in objects.items()}
before_data = objects['11'].data.as_pointer()
renamed = adapter.rename_owned_import(identity)
assert renamed['skipped_custom'] >= 2
assert objects['11'].name == 'Моё окно на углу' and objects['11'].data.name == 'Моя геометрия окна'
assert {identifier: obj.as_pointer() for identifier, obj in objects.items()} == before_objects
assert objects['11'].data.as_pointer() == before_data
adapter.import_result(usd, import_id=identity, source_path='fixture.rvt', selected_element_ids={'11', '12', '13'})
objects = placements(identity)
assert objects['11'].name == 'Моё окно на углу'
assert objects['11'].data.name == 'Моя геометрия окна' and objects['11'].data == objects['12'].data
objects['11'].name, objects['11'].data.name = 'e_11', 'mesh'
adapter.import_result(usd, import_id=identity, source_path='fixture.rvt', selected_element_ids={'11', '12', '13'})
objects = placements(identity)
assert objects['11'].name == 'e_11' and objects['11'].data.name == 'mesh'
adapter.rename_owned_import(identity)
assert objects['11'].name == 'e_11' and objects['11'].data.name == 'mesh'

# Repair legacy-owned transport names without geometry rebuild or touching guests.
legacy = adapter.import_result(usd, source_path='legacy.rvt', selected_element_ids={'11', '12', '13'})
legacy_objects = placements(legacy['import_id'])
for identifier, obj in legacy_objects.items():
    obj.name = 'e_' + identifier + '.001'
    obj.pop(adapter.GENERATED_NAME, None)
    obj.pop(adapter.USER_NAME, None)
for mesh in {obj.data for obj in legacy_objects.values()}:
    mesh.name = 'mesh.001'
    mesh.pop(adapter.GENERATED_NAME, None)
    mesh.pop(adapter.USER_NAME, None)
mesh = legacy_objects['11'].data
foreign = bpy.data.objects.new('Foreign mesh user', mesh)
bpy.context.scene.collection.objects.link(foreign)
foreign_name, shared_name = foreign.name, mesh.name
mesh_pointer = mesh.as_pointer()
object_pointers = {identifier: obj.as_pointer() for identifier, obj in legacy_objects.items()}
stats = adapter.rename_owned_import(legacy['import_id'])
assert stats['objects'] == 3 and stats['skipped_foreign'] >= 1
assert legacy_objects['11'].name.startswith('Окна') and '[11]' in legacy_objects['11'].name
assert foreign.name == foreign_name and mesh.name == shared_name and mesh.as_pointer() == mesh_pointer
assert {identifier: obj.as_pointer() for identifier, obj in legacy_objects.items()} == object_pointers
again = adapter.rename_owned_import(legacy['import_id'])
assert again['objects'] == 0 and again['meshes'] == 0

expanded = adapter.import_result(usd, preserve_instances=False, source_path='independent.rvt')
for identifier, obj in placements(expanded['import_id']).items():
    assert obj.data.name.endswith('[' + identifier + ']')

# Delta addition reuses the custom named existing mesh and retains the old object.
single_usd = fixture('single', {'11'})
single = adapter.import_result(single_usd, source_path='delta.rvt', selected_element_ids={'11'})
old = placements(single['import_id'])['11']
old.name, old.data.name = 'Пользовательское окно', 'Пользовательский меш'
old_pointer, data_pointer = old.as_pointer(), old.data.as_pointer()
addition = fixture('addition', {'12'}, single['source_offset'])
merged = adapter.merge_result(addition, import_id=single['import_id'], selected_element_ids={'12'},
                              final_selection_ids={'11', '12'}, source_path='delta.rvt')
assert merged is not None
combined = placements(single['import_id'])
assert old.as_pointer() == old_pointer and old.data.as_pointer() == data_pointer
assert old.name == 'Пользовательское окно' and old.data.name == 'Пользовательский меш'
assert combined['12'].name.startswith('Окна') and '[12]' in combined['12'].name
assert combined['12'].data == old.data

# A real foreign name collision must retain the ID through Blender's byte limit
# and must not allocate another numeric suffix every time the button is pressed.
from pxr import Sdf, Usd
from xivgate_revit_bridge.core.naming import object_name
unicode_usd = fixture('unicode', {'11'})
stage = Usd.Stage.Open(str(unicode_usd))
prim = stage.GetPrimAtPath('/World/Elements/e_11')
long_family = 'Очень длинное название оконной фасадной системы ' * 8
prim.GetAttribute('revit:family_name').Set(long_family)
stage.GetRootLayer().Save()
canonical = object_name({'id': '11', 'category': 'Окна', 'family_name': long_family, 'type_name': '1800', 'name': '1800'})
collision = bpy.data.objects.new(canonical, None)
bpy.context.scene.collection.objects.link(collision)
long_result = adapter.import_result(unicode_usd, source_path='unicode.rvt')
long_object = placements(long_result['import_id'])['11']
assert '[11]' in long_object.name and len(long_object.name.encode()) <= 63
first_collision_name = long_object.name
adapter.rename_owned_import(long_result['import_id'])
adapter.rename_owned_import(long_result['import_id'])
assert long_object.name == first_collision_name and collision.name == canonical
long_root = next(item for item in bpy.data.collections if item.get(adapter.OWNER) == long_result['import_id'] and item.get(adapter.ROOT_FLAG))
other_scene = bpy.data.scenes.new('Naming shared scene guard')
other_scene.collection.children.link(long_root)
try:
    adapter.rename_owned_import(long_result['import_id'])
except RuntimeError:
    pass
else:
    raise AssertionError('A root shared by another scene must not be renamed')
assert long_object.name == first_collision_name
other_scene.collection.children.unlink(long_root)
bpy.data.scenes.remove(other_scene)

report = dict(status='passed', blender=bpy.app.version_string, readable_objects_and_meshes=True,
              stable_reimports=True, custom_names_preserved=True, rename_without_rebuild=True,
              foreign_shared_data_preserved=True, linked_meshes_preserved=True,
              independent_mesh_ids=True, delta_names_preserved=True, utf8_63_bytes=True)
(run / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('AGR_READABLE_NAMES ' + json.dumps(report, ensure_ascii=False), flush=True)
