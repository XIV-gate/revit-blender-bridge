"""Authored UV/height fixture; run through the isolated Blender launcher only."""
import json
import os
from pathlib import Path
import sys

import bpy
from pxr import Usd

SOURCE = Path(os.environ['RBB_SOURCE_DIR']).resolve()
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.dont_write_bytecode = True
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
for variable in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[variable]).resolve().is_relative_to(WORK)
from fixture_cache import write_fixture
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER, REVISION
from xivgate_revit_bridge.blender_materials import attach_height_map

root = WORK / ('height_material_' + bpy.app.version_string.replace(' ', '_') + '_' + str(os.getpid()))
manifest = write_fixture(root)
value = json.loads(manifest.read_text())
material = value['materials']['7']
material['textures'] = {'concrete_bump_map/0/unifiedbitmap_Bitmap': 'pixel.png'}
material['properties'] = {'concrete_bump_amount': 0.7}
manifest.write_text(json.dumps(value))
usd = root / 'scene.usdc'
build_usd(manifest, usd)
result = import_result(usd, pack_images=True)
mat = next(item for item in bpy.data.materials if item.get(OWNER) == result['import_id'])
nodes = mat.node_tree.nodes
base_image = next(node.image for node in nodes if node.type == 'TEX_IMAGE' and node.image.colorspace_settings.name != 'Non-Color')
height_node = next(node for node in nodes if node.type == 'TEX_IMAGE' and node.image.colorspace_settings.name == 'Non-Color')
bump = next(node for node in nodes if node.type == 'BUMP')
bsdf = next(node for node in nodes if node.type == 'BSDF_PRINCIPLED')
assert abs(bump.inputs['Strength'].default_value - 0.7) < 1e-6
assert height_node.image != base_image and base_image.colorspace_settings.name != 'Non-Color'
assert bump.inputs['Height'].links[0].from_node == height_node
assert bsdf.inputs['Normal'].links[0].from_node == bump
assert bsdf.inputs['Base Color'].links[0].from_node.type == 'TEX_IMAGE'
assert height_node.inputs['Vector'].links[0].from_node.type == 'UVMAP'
assert height_node.inputs['Vector'].links[0].from_node.uv_map == 'st'
assert height_node.image.packed_file and height_node.image.get(OWNER) == result['import_id']
assert height_node.image.get(REVISION) == result['revision'] and result['packed_images'] == 2
before = set(nodes)
attached = attach_height_map(mat, {'path': 'pixel.png', 'amount': 1, 'invert': False}, root)
assert not attached['images'] and set(nodes) == before
# Existing unrelated normal links are never replaced.
fresh = bpy.data.materials.new('User normal')
fresh.use_nodes = True
shader = next(node for node in fresh.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
normal = fresh.node_tree.nodes.new('ShaderNodeNormalMap')
fresh.node_tree.links.new(normal.outputs['Normal'], shader.inputs['Normal'])
attached = attach_height_map(fresh, {'path': 'pixel.png', 'amount': 1, 'invert': False}, root)
assert not attached['images'] and shader.inputs['Normal'].links[0].from_node == normal
corrupt = root / 'invalid.png'
corrupt.write_bytes(b'not an image')
broken = bpy.data.materials.new('Decode failure remains recoverable')
broken.use_nodes = True
attached = attach_height_map(broken, {'path': 'invalid.png', 'amount': 1, 'invert': False}, root)
assert attached['reason'] == 'height_image_decode_failed' and attached['images'] == []
print('AGR_HEIGHT_MATERIAL ' + json.dumps({'status': 'passed', 'blender': bpy.app.version_string,
      'height_noncolor_packed_owned': True, 'basecolor_unchanged': True, 'existing_normals_preserved': True,
      'explicit_source_strength': True, 'uv_st_mapping': True}), flush=True)
