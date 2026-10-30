"""Read-only real-export texture audit; Blender mode uses run_blender_test.py.

RBB_TEXTURE_SOURCE_ROOTS: JSON array of explicitly allowed read-only roots.
RBB_TEXTURE_CATALOG: optional current catalog, for source snapshot comparison.
RBB_TEXTURE_MANIFEST: optional exact manifest for isolated actual import/UV checks.
All generated audit/conversion data stays below RBB_TEST_ROOT.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOTS = [Path(value).resolve() for value in json.loads(os.environ['RBB_TEXTURE_SOURCE_ROOTS'])]
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
assert WORK.drive.lower() == 'd:' and WORK.is_dir()
SOURCE = Path(os.environ.get('RBB_SOURCE_DIR', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(SOURCE))
REPORT = WORK / ('project_texture_audit_' + str(os.getpid()))
REPORT.mkdir()


def allowed(path):
    path = Path(path).resolve()
    assert any(path.is_relative_to(root) for root in ROOTS), path
    return path


def read(path):
    return json.loads(allowed(path).read_text(encoding='utf-8-sig'))


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def raw_bitmaps(material):
    return {key: value for key, value in material.get('properties', {}).items()
            if isinstance(value, str) and re.search(r'\.(png|jpe?g|bmp|tiff?|tga|exr|hdr|gif|dds)(?:$|\|)', value, re.I)}


current = read(os.environ['RBB_TEXTURE_CATALOG']).get('source', {}) if os.environ.get('RBB_TEXTURE_CATALOG') else {}
inventory = []
for root in ROOTS:
    for manifest in root.rglob('*manifest*.json'):
        if any(part.startswith('project_texture_audit_') for part in manifest.parts):
            continue
        try:
            value = read(manifest)
            source = value.get('source')
            if 'materials' not in value or 'prototypes' not in value or not source:
                continue  # Exclude authored fixtures and package manifests.
            source_data = source if isinstance(source, dict) else {'path': source}
            source_path = str(source_data.get('path', ''))
            if not source_path.lower().endswith('.rvt') or 'test-only' in source_path:
                continue
            copies = {}
            material_rows = []
            raw = set()
            for key, material in value['materials'].items():
                textures = material.get('textures', {})
                base = material.get('base_texture')
                paths = set(textures.values()) if isinstance(textures, dict) else set()
                if base:
                    paths.add(base)
                for relative in paths:
                    path = allowed(manifest.parent / relative)
                    copies[relative] = {'path': str(path), 'exists': path.is_file(),
                                        'size': path.stat().st_size if path.is_file() else None,
                                        'sha256': fingerprint(path) if path.is_file() else None}
                properties = raw_bitmaps(material)
                raw.update(properties.values())
                material_rows.append({'key': key, 'name': material.get('name'),
                                      'source_material_id': material.get('source_material_id'),
                                      'base_texture': base, 'textures': textures, 'raw_bitmaps': properties})
            warnings = [str(warning) for warning in value.get('warnings', [])]
            missing = sorted({warning for warning in warnings if 'текстур' in warning.lower() or 'texture' in warning.lower()})
            bitmap_refs = {value for material in material_rows for key, value in material['raw_bitmaps'].items()
                           if key.lower().endswith('unifiedbitmap_bitmap')}
            exact_refs = []
            for reference in sorted(bitmap_refs):
                alternatives = sorted({item.strip() for item in reference.split('|') if item.strip()})
                states = []
                for candidate in alternatives:
                    path = Path(candidate)
                    states.append({'path': candidate, 'absolute': path.is_absolute(),
                                   'exists_at_exact_path': path.is_file() if path.is_absolute() and not candidate.startswith(('\\\\', '//')) else None})
                exact_refs.append({'reference': reference, 'alternatives': states})
            row = {'manifest': str(manifest), 'source': source_data,
                   'same_source_path': os.path.normcase(source_path) == os.path.normcase(str(current.get('path', ''))),
                   'same_source_sha256': source_data.get('sha256') == current.get('sha256') if source_data.get('sha256') else None,
                   'materials': len(material_rows), 'materials_with_base_texture': sum(bool(item['base_texture']) for item in material_rows),
                   'materials_with_copied_maps': sum(bool(item['textures'] or item['base_texture']) for item in material_rows),
                   'unique_copied_maps': len(copies), 'copied_maps': copies,
                   'unique_raw_bitmap_references': len(raw), 'raw_bitmap_references': sorted(raw),
                   'unique_actual_bitmap_references': len(bitmap_refs), 'exact_bitmap_references': exact_refs,
                   'texture_warnings': missing, 'texture_warning_count': len(missing),
                   'material_details': material_rows,
                   'occurrences': len(value.get('occurrences', [])), 'prototypes': len(value.get('prototypes', []))}
            inventory.append(row)
        except (ValueError, OSError, TypeError, AssertionError):
            continue

report = {'current_source': current, 'exports': inventory, 'limits': ['Read-only known export roots only; no broad filesystem search.',
          'Current unsaved live Blender scene is not accessed.', 'Old manifest without source SHA256 proves path identity only.']}

try:
    import bpy
except ImportError:
    bpy = None

if bpy and os.environ.get('RBB_TEXTURE_MANIFEST'):
    for variable in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
        assert Path(os.environ[variable]).resolve().is_relative_to(WORK)
    import numpy as np
    from xivgate_revit_bridge.blender_import import import_result, OWNER
    from xivgate_revit_bridge.conversion import build_usd
    from pxr import Usd, UsdGeom
    manifest = allowed(os.environ['RBB_TEXTURE_MANIFEST'])
    metadata = read(manifest)
    # Copy payloads only to the approved D: audit output. Never emit USD beside originals.
    import shutil
    converted = REPORT / 'payload'
    converted.mkdir()
    references = {metadata.get('binary_file', 'geometry.bin')}
    for material in metadata['materials'].values():
        references.update(material.get('textures', {}).values())
        if material.get('base_texture'):
            references.add(material['base_texture'])
    for relative in references:
        original = allowed(manifest.parent / relative)
        target = (converted / relative).resolve()
        assert target.is_relative_to(converted)
        target.parent.mkdir(parents=True, exist_ok=True)
        if original.is_file():
            shutil.copyfile(original, target)
    copied_manifest = converted / 'manifest.json'
    if os.environ.get('RBB_TEXTURE_SAMPLE_ONLY') == '1':
        wanted = {int(key) for key, material in metadata['materials'].items() if material.get('base_texture')}
        selected_prototypes = set()
        dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
        with (converted / metadata.get('binary_file', 'geometry.bin')).open('rb') as binary:
            for prototype in metadata['prototypes']:
                binary.seek(prototype['offset'])
                records = np.frombuffer(binary.read(prototype['bytes']), dtype=dtype)
                hit = set(map(int, records['material'])) & wanted
                if hit:
                    selected_prototypes.add(prototype['id'])
                    wanted -= hit
                if not wanted:
                    break
        metadata['prototypes'] = [entry for entry in metadata['prototypes'] if entry['id'] in selected_prototypes]
        occurrences = []
        for occurrence in metadata['occurrences']:
            if occurrence['prototype'] in selected_prototypes:
                occurrences.append(occurrence)
                selected_prototypes.remove(occurrence['prototype'])
        metadata['occurrences'] = occurrences
    copied_manifest.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
    usd = converted / 'scene.usdc'
    conversion = build_usd(copied_manifest, usd)
    source_metadata = metadata.get('source', '')
    imported = import_result(usd, preserve_instances=True, pack_images=True,
                             source_path=source_metadata.get('path', '') if isinstance(source_metadata, dict) else source_metadata)
    meshes = {obj.data for obj in bpy.data.objects if obj.get(OWNER) == imported['import_id'] and obj.type == 'MESH'}
    materials = {material for mesh in meshes for material in mesh.materials if material}
    images = {node.image for material in materials if material.use_nodes for node in material.node_tree.nodes
              if node.type == 'TEX_IMAGE' and node.image}
    shader_images = set(images)
    for relative in references:
        if relative != metadata.get('binary_file', 'geometry.bin') and (converted / relative).is_file():
            images.add(bpy.data.images.load(str(converted / relative), check_existing=True))
    image_rows = []
    for image in images:
        length = len(image.pixels)
        samples = [float(image.pixels[index]) for index in sorted({0, max(0, length // 2), max(0, length - 1)})] if length else []
        image_rows.append({'name': image.name, 'path': bpy.path.abspath(image.filepath), 'size': list(image.size),
                           'has_data': image.has_data, 'pixels': length, 'samples': samples,
                           'used_by_imported_shader': image in shader_images,
                           'finite_samples': all(math.isfinite(value) for value in samples),
                           'packed': bool(image.packed_file),
                           'packed_sha256': hashlib.sha256(image.packed_file.data).hexdigest() if image.packed_file else None})
    node_rows = []
    for material in materials:
        if not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type != 'TEX_IMAGE' or not node.image:
                continue
            links = [{'from_node': link.from_node.type, 'from_socket': link.from_socket.name,
                      'to_node': link.to_node.type, 'to_socket': link.to_socket.name}
                     for link in material.node_tree.links if link.from_node == node or link.to_node == node]
            node_rows.append({'material': material.name, 'source_id': material.get('revit:source_material_id'),
                              'image': node.image.name, 'links': links,
                              'implicit_uv': not node.inputs['Vector'].is_linked,
                              'uv_map_nodes': [{'type': item.type, 'uv_map': getattr(item, 'uv_map', '')}
                                               for item in material.node_tree.nodes if item.type in ('UVMAP', 'TEX_COORD')]})
    uv_rows = []
    for mesh in meshes:
        layer = mesh.uv_layers.active
        flat = np.empty(len(layer.data) * 2, dtype=np.float32) if layer else np.empty(0)
        if layer:
            layer.data.foreach_get('uv', flat)
        values = flat.reshape(-1, 2)
        textured = any(material and material.use_nodes and any(node.type == 'TEX_IMAGE' and node.image for node in material.node_tree.nodes)
                       for material in mesh.materials)
        uv_rows.append({'mesh': mesh.name, 'loops': len(mesh.loops), 'uv_layers': len(mesh.uv_layers),
                        'uv_loop_count': len(layer.data) if layer else 0, 'active_uv': layer.name if layer else None,
                        'finite': bool(np.isfinite(flat).all()),
                        'varies': bool(len(values) and np.any(np.ptp(values, axis=0) > 1e-8)), 'textured': textured})
    prototypes = {str(value['id']): value for value in metadata['prototypes']}
    samples = []
    stage = Usd.Stage.Open(str(usd))
    checked = set()
    with (converted / metadata.get('binary_file', 'geometry.bin')).open('rb') as binary:
        candidates = [obj for obj in bpy.data.objects if obj.get(OWNER) == imported['import_id'] and obj.type == 'MESH']
        candidates.sort(key=lambda obj: (not any(material and material.use_nodes and any(node.type == 'TEX_IMAGE' and node.image for node in material.node_tree.nodes)
                                                    for material in obj.data.materials), str(obj.get('revit:prototype', ''))))
        for obj in candidates:
            prototype = str(obj.get('revit:prototype', ''))
            if obj.get(OWNER) != imported['import_id'] or obj.type != 'MESH' or prototype in checked or prototype not in prototypes:
                continue
            if len(samples) >= 12:
                break
            entry = prototypes[prototype]
            binary.seek(int(entry['offset']))
            raw = binary.read(int(entry['bytes']))
            dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
            binary_uv = np.frombuffer(raw, dtype=dtype)['vertices'][:, :, 6:8].reshape(-1, 2)
            prim = stage.GetPrimAtPath('/Prototypes/p_' + prototype + '/mesh')
            usd_uv = np.asarray(UsdGeom.PrimvarsAPI(prim).GetPrimvar('st').Get(), dtype=np.float32)
            uv_layer = obj.data.uv_layers.active
            blender_uv = np.empty(len(uv_layer.data) * 2, dtype=np.float32)
            uv_layer.data.foreach_get('uv', blender_uv)
            blender_uv = blender_uv.reshape(-1, 2)
            samples.append({'prototype': prototype, 'mesh': obj.data.name, 'uv_count': len(binary_uv),
                            'binary_vs_usd_max_error': float(np.max(np.abs(binary_uv - usd_uv))) if binary_uv.shape == usd_uv.shape else None,
                            'binary_vs_blender_max_error': float(np.max(np.abs(binary_uv - blender_uv))) if binary_uv.shape == blender_uv.shape else None})
            checked.add(prototype)
    report['blender'] = {'version': bpy.app.version_string, 'manifest': str(manifest), 'conversion': conversion,
                         'scope': 'real textured prototype subset' if os.environ.get('RBB_TEXTURE_SAMPLE_ONLY') == '1' else 'full real export',
                         'import': imported, 'mesh_count': len(meshes), 'material_count': len(materials),
                         'image_count': len(shader_images), 'decoded_copied_image_count': len(images), 'images': image_rows, 'image_nodes': node_rows,
                         'uv_meshes': uv_rows, 'source_usd_blender_uv_samples': samples}
    if images:
        cell = 256
        atlas = np.ones((cell, cell * len(images), 4), dtype=np.float32)
        labels = []
        for index, image in enumerate(sorted(images, key=lambda item: item.name)):
            width, height = image.size
            data = np.asarray(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
            xs = np.linspace(0, width - 1, cell).astype(int)
            ys = np.linspace(0, height - 1, cell).astype(int)
            atlas[:, index * cell:(index + 1) * cell, :] = data[ys[:, None], xs[None, :], :]
            labels.append({'column': index, 'image': image.name, 'path': bpy.path.abspath(image.filepath)})
        contact = bpy.data.images.new('Audit found maps', width=atlas.shape[1], height=cell, alpha=True, float_buffer=True)
        contact.pixels.foreach_set(atlas.ravel())
        bpy.context.scene.view_settings.view_transform = 'Standard'
        contact.save_render(str(REPORT / 'found_maps.png'), scene=bpy.context.scene)
        report['contact_sheet'] = {'path': str(REPORT / 'found_maps.png'), 'columns': labels}
    if os.environ.get('RBB_TEXTURE_RENDER') == '1':
        from mathutils import Vector
        render = bpy.data.scenes.new('Audit actual material tiles')
        render.render.engine = 'CYCLES'
        render.cycles.device = 'CPU'
        render.cycles.samples = 8
        render.render.threads_mode = 'FIXED'
        render.render.threads = 4
        used = sorted({material for material in materials if material.use_nodes and any(
            node.type == 'TEX_IMAGE' and node.image and node.image in shader_images and node.image.colorspace_settings.name != 'Non-Color'
            for node in material.node_tree.nodes)}, key=lambda item: item.name)
        for column, material in enumerate(used):
            x = (column - (len(used) - 1) / 2) * 2.1
            mesh = bpy.data.meshes.new('Material tile')
            mesh.from_pydata([(x - 1, -1, 0), (x + 1, -1, 0), (x + 1, 1, 0), (x - 1, 1, 0)], [], [(0, 1, 2, 3)])
            layer = mesh.uv_layers.new(name='st')
            for index, uv in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
                layer.data[index].uv = uv
            tile = bpy.data.objects.new('Actual material ' + material.name, mesh)
            render.collection.objects.link(tile)
            tile.data.materials.append(material)
        camera_data = bpy.data.cameras.new('Audit camera')
        camera = bpy.data.objects.new('Audit camera', camera_data)
        render.collection.objects.link(camera)
        camera.location = (0, 0, 8)
        camera_data.type = 'ORTHO'
        camera_data.ortho_scale = len(used) * 2.1
        render.camera = camera
        light_data = bpy.data.lights.new('Audit area', 'AREA')
        light_data.energy = 1000
        light_data.shape = 'DISK'
        light_data.size = 4
        light = bpy.data.objects.new('Audit area', light_data)
        render.collection.objects.link(light)
        light.location = (-3, -2, 5)
        light.rotation_euler = (Vector((0, 0, 0)) - light.location).to_track_quat('-Z', 'Y').to_euler()
        render.world = bpy.data.worlds.new('Audit world')
        render.world.color = (0.15, 0.15, 0.15)
        render.view_settings.view_transform = 'Standard'
        render.render.resolution_x = max(1, len(used)) * 256
        render.render.resolution_y = 256
        render.render.resolution_percentage = 100
        render.render.filepath = str(REPORT / 'shader_materials.png')
        bpy.ops.render.render(write_still=True, scene=render.name)
        report['shader_render'] = {'path': render.render.filepath, 'engine': 'CYCLES', 'device': 'CPU', 'samples': 8,
                                   'material_columns': [material.name for material in used],
                                   'scope': 'actual imported material nodes on authored UV tiles; not a whole-building render'}

(REPORT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
summary = [{'manifest': item['manifest'], 'same_source_sha256': item['same_source_sha256'],
            'materials': item['materials'], 'materials_with_base': item['materials_with_base_texture'],
            'copied_maps': item['unique_copied_maps'], 'raw_references': item['unique_raw_bitmap_references'],
            'texture_warnings': item['texture_warning_count']} for item in inventory]
print('AGR_TEXTURE_AUDIT ' + json.dumps({'report': str(REPORT / 'report.json'), 'exports': summary,
                                      'blender_images': report.get('blender', {}).get('image_count')}, ensure_ascii=True), flush=True)
