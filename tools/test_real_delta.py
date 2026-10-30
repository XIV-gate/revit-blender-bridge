"""Opt-in real-output delta benchmark; all generated data stays in RBB_TEST_ROOT.

Run once through run_blender_test.py with RBB_REAL_MANIFEST set explicitly.
The original manifest/binary/copied textures are read-only inputs; no blend is saved.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import bpy
import numpy as np

sys.dont_write_bytecode = True
SOURCE = Path(os.environ['RBB_SOURCE_DIR']).resolve()
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
sys.path.insert(0, str(SOURCE))
for variable in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
                 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[variable]).resolve().is_relative_to(WORK), variable
assert WORK.drive.casefold() == 'd:'

from xivgate_revit_bridge import blender_import as adapter
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.geometry_cache import safe_asset_path, validate_prototype


def filtered_copy(original_path, metadata, occurrences, destination):
    """Copy only selected prototype chunks and the material table/image payloads they use."""
    destination.mkdir()
    result = copy.deepcopy(metadata)
    result['occurrences'] = occurrences
    wanted = {int(item['prototype']) for item in occurrences}
    prototypes, material_ids = [], set()
    dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
    binary = safe_asset_path(original_path.parent, metadata.get('binary_file', 'geometry.bin'))
    with binary.open('rb') as source, (destination / 'geometry.bin').open('xb') as output:
        for entry in metadata['prototypes']:
            if int(entry['id']) not in wanted:
                continue
            source.seek(int(entry['offset']))
            data = source.read(int(entry['bytes']))
            validate_prototype(entry, data)
            material_ids.update(int(value) for value in np.unique(np.frombuffer(data, dtype=dtype)['material']))
            prototype = dict(entry, offset=output.tell())
            prototypes.append(prototype)
            output.write(data)
        binary_bytes = output.tell()
    result['binary_file'] = 'geometry.bin'
    result['prototypes'] = prototypes
    result['materials'] = {str(key): copy.deepcopy(metadata['materials'][str(key)]) for key in sorted(material_ids)}
    copied = set()
    for material in result['materials'].values():
        paths = set(material.get('textures', {}).values())
        if material.get('base_texture'):
            paths.add(material['base_texture'])
        for relative in paths:
            original = safe_asset_path(original_path.parent, relative)
            target = safe_asset_path(destination, relative)
            assert original.is_file(), 'Missing copied input texture: ' + str(relative)
            if relative not in copied:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                copied.add(relative)
    result['summary'] = dict(occurrences=len(occurrences), prototypes=len(prototypes),
                             triangles=sum(int(item['triangles']) for item in prototypes),
                             materials=len(result['materials']), bytes=binary_bytes)
    path = destination / 'manifest.json'
    path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return path


original = Path(os.environ['RBB_REAL_MANIFEST']).resolve(strict=True)
assert not original.is_relative_to(WORK), 'This opt-in benchmark expects a separate read-only real fixture'
manifest_bytes = original.read_bytes()
manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
metadata = json.loads(manifest_bytes.decode('utf-8-sig'))
run = WORK / ('real_delta_' + str(os.getpid()))
run.mkdir()
report_path = run / 'result.json'
report = dict(status='running', blender=bpy.app.version_string, input_manifest=str(original),
              input_sha256=manifest_hash, run=str(run), timings={})
try:
    occurrences = metadata['occurrences']
    candidates = sorted((item for item in occurrences if int(item['prototype']) == 1434), key=lambda item: int(item['id']))
    assert len(candidates) > 10, 'Prototype 1434 requires retained and ten newly added windows'
    assert all(str(item.get('category_id')) == '-2000014' for item in candidates), 'Target prototype must contain windows'
    additions = candidates[:10]
    added_ids = {str(item['id']) for item in additions}
    assert all({str(value) for value in item.get('source_element_ids', [item['id']])} == {str(item['id'])}
               and not item.get('selection_parent_ids') and not item.get('parent_id') for item in additions)
    retained = [item for item in occurrences if str(item['id']) not in added_ids]
    full_ids = {str(item['id']) for item in occurrences}
    baseline_ids = full_ids - added_ids
    report.update(initial_placements=len(occurrences), initial_prototypes=len(metadata['prototypes']),
                  target_prototype=1434, matching_windows=len(candidates), added_ids=sorted(added_ids))
    started = time.monotonic()
    baseline_manifest = filtered_copy(original, metadata, retained, run / 'baseline')
    delta_manifest = filtered_copy(original, metadata, additions, run / 'delta')
    report['timings']['prepare_subsets_seconds'] = time.monotonic() - started
    print('AGR_REAL_DELTA subsets ready', len(retained), '+', len(additions), flush=True)

    baseline_usd = baseline_manifest.with_name('scene.usdc')
    started = time.monotonic()
    baseline_conversion = build_usd(baseline_manifest, baseline_usd)
    report['timings']['baseline_conversion_seconds'] = time.monotonic() - started
    delta_usd = delta_manifest.with_name('scene.usdc')
    started = time.monotonic()
    delta_conversion = build_usd(delta_manifest, delta_usd, source_offset=baseline_conversion['source_offset'])
    report['timings']['delta_conversion_seconds'] = time.monotonic() - started
    print('AGR_REAL_DELTA conversions ready', report['timings'], flush=True)

    source_path = metadata.get('source', {}).get('path', '')
    started = time.monotonic()
    baseline = adapter.import_result(baseline_usd, preserve_instances=True, source_path=source_path,
                                     selected_element_ids=baseline_ids)
    report['timings']['baseline_import_seconds'] = time.monotonic() - started
    identity = baseline['import_id']
    old = {str(obj['revit:id']): (obj.as_pointer(), obj.name, obj.data.as_pointer()) for obj in bpy.data.objects
           if obj.get(adapter.OWNER) == identity and 'revit:id' in obj}
    assert set(old) == baseline_ids
    existing_window = next(obj for obj in bpy.data.objects if obj.get(adapter.OWNER) == identity
                           and obj.get('revit:id') == str(candidates[10]['id']))
    edited_mesh = existing_window.data
    shared_pointer = edited_mesh.as_pointer()
    before_edit = edited_mesh.vertices[0].co.copy()
    edited_mesh.vertices[0].co.x += 0.01
    edited_mesh.update()
    edited_vertex = edited_mesh.vertices[0].co.copy()
    assert abs(edited_vertex.x - before_edit.x - 0.01) < 1e-5
    print('AGR_REAL_DELTA baseline imported; merging ten windows', report['timings'], flush=True)

    started = time.monotonic()
    merged = adapter.merge_result(delta_usd, import_id=identity, selected_element_ids=added_ids,
                                  final_selection_ids=full_ids, preserve_instances=True, source_path=source_path)
    report['timings']['delta_merge_seconds'] = time.monotonic() - started
    final = {str(obj['revit:id']): obj for obj in bpy.data.objects
             if obj.get(adapter.OWNER) == identity and 'revit:id' in obj}
    assert all(identifier in final and final[identifier].as_pointer() == value[0]
               and final[identifier].name == value[1] and final[identifier].data.as_pointer() == value[2]
               for identifier, value in old.items()), 'Retained placements/names/mesh pointers changed'
    assert edited_mesh.as_pointer() == shared_pointer and (edited_mesh.vertices[0].co - edited_vertex).length < 1e-8
    assert not any(obj.get(adapter.OWNER) == identity and obj.instance_type == 'COLLECTION' for obj in bpy.data.objects)
    if merged is None:
        assert set(final) == baseline_ids, 'Declined merge changed baseline geometry'
        report.update(status='declined', reason='Merge conservatively declined the real model dependency boundary',
                      baseline=baseline, merge=None, retained_unchanged=True)
    else:
        assert set(final) == full_ids
        assert merged['placements'] == len(occurrences) and merged['added'] == 10 and merged['reused_meshes'] == 10, merged
        assert all(final[identifier].data.as_pointer() == shared_pointer for identifier in added_ids)
        report.update(status='passed', baseline=baseline, merge=merged, final_placements=len(final),
                      retained_unchanged=True, shared_user_edit_preserved=True, all_added_reuse_edited_mesh=True,
                      no_collection_instances=True, source_offset=baseline_conversion['source_offset'])
    assert original.read_bytes() == manifest_bytes, 'Original manifest changed'
    assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
except Exception as error:
    report.update(status='failed', error=repr(error))
    raise
finally:
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('AGR_REAL_DELTA ' + json.dumps(report, ensure_ascii=False), flush=True)
