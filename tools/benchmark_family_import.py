"""Opt-in isolated family/instance benchmark. Never writes original input files.

RBB_FAMILY_BENCHMARK_MODE: FAMILY (default), INSTANCES or OBJECTS.
RBB_FAMILY_BENCHMARK_SOURCE: synthetic (default) or real.
Real inputs: RBB_REAL_MANIFEST and optional RBB_SELECTION_JSON ({include_ids:[str]}).
Use run_blender_test.py with an external timeout for the large instance baseline.
"""
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import bpy
import numpy as np
from pxr import Usd, UsdGeom

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER
from xivgate_revit_bridge.core.jobs import atomic_write_json
from xivgate_revit_bridge.geometry_cache import safe_asset_path

root = Path(os.environ['RBB_TEST_ROOT']).resolve(strict=True)
assert root.drive.casefold() == 'd:'
for variable in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
                 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[variable]).resolve().is_relative_to(root), variable
mode = os.environ.get('RBB_FAMILY_BENCHMARK_MODE', 'FAMILY').upper()
kind = os.environ.get('RBB_FAMILY_BENCHMARK_SOURCE', 'synthetic').lower()
assert mode in ('FAMILY', 'INSTANCES', 'OBJECTS') and kind in ('synthetic', 'real')
run = root / ('family_benchmark_' + mode + '_' + uuid.uuid4().hex)
run.mkdir()
report_path = run / 'result.json'
report = dict(status='running', phase='preparing_input', mode=mode, source=kind,
              blender=bpy.app.version_string, process_id=os.getpid(), report=str(report_path))


def publish(phase, **values):
    report.update(phase=phase, **values)
    atomic_write_json(report_path, report)
    print('AGR_FAMILY_BENCHMARK_PHASE ' + json.dumps(report, ensure_ascii=False), flush=True)


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def family_identity(row):
    category = str(row.get('category_id') or row.get('category') or row.get('category_name') or 'Прочее')
    family = str(row.get('family_name') or '')
    if family.strip():
        return category, 'family', family
    type_id = row.get('type_id')
    if type_id is not None and not isinstance(type_id, bool) and str(type_id).strip() not in ('', '-1'):
        return category, 'type', str(type_id).strip()
    return category, 'occurrence', str(row['id'])


def synthetic_manifest():
    dataset = run / 'dataset'
    dataset.mkdir()
    dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
    prototypes = []
    with (dataset / 'geometry.bin').open('wb') as stream:
        for index, triangles in enumerate((3, 2)):
            records = np.zeros(triangles, dtype=dtype)
            records['vertices'][:, :, :3] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
            for triangle in range(triangles):
                records['vertices'][triangle, :, 2] = triangle * .25
            records['vertices'][:, :, 5] = 1
            records['vertices'][:, :, 6:8] = [[0, 0], [1, 0], [0, 1]]
            records['material'] = 7
            payload = records.tobytes()
            prototypes.append(dict(id=index, offset=stream.tell(), bytes=len(payload), triangles=triangles,
                                   sha256=hashlib.sha256(payload).hexdigest()))
            stream.write(payload)
    rows = []
    for index in range(73133):
        prototype = 0 if index < 36734 else 1
        identifier = str(index + 1)
        rows.append(dict(id=identifier, unique_id='benchmark-' + identifier, prototype=prototype,
                         category_id='benchmark', category='Обобщенные модели',
                         family_name='Семейство ' + str(index % 10), type_name='Тип ' + str(prototype),
                         type_id=str(prototype + 1), name='Элемент ' + identifier,
                         workset='Тест', workset_id='0', source_element_ids=[identifier],
                         selection_parent_ids=[], native_instance_safe=False,
                         transform=[1, 0, 0, 0, 1, 0, 0, 0, 1, (index % 300) * 3, (index // 300) * 3, 0]))
    assert len(rows) == 73133
    assert sum(prototypes[row['prototype']]['triangles'] for row in rows) == 183000
    value = dict(schema=1, units='metres', up='Z', occurrences=rows, prototypes=prototypes,
                 source={'path': 'synthetic_family_benchmark.rvt'},
                 materials={'7': dict(id=7, name='Материал', color=[.4, .5, .6], uv_sensitive=False)})
    path = dataset / 'manifest.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


try:
    manifest = (synthetic_manifest() if kind == 'synthetic'
                else Path(os.environ['RBB_REAL_MANIFEST']).resolve(strict=True))
    assert manifest.is_relative_to(root), 'Real input must be an approved replay copy'
    metadata = json.loads(manifest.read_text(encoding='utf-8-sig'))
    binary = safe_asset_path(manifest.parent, metadata.get('binary_file', 'geometry.bin'))
    assert binary.is_relative_to(root)
    original = digest(manifest), digest(binary)
    selected = None
    if os.environ.get('RBB_SELECTION_JSON'):
        selection_path = Path(os.environ['RBB_SELECTION_JSON']).resolve(strict=True)
        assert selection_path.is_relative_to(root)
        selection = json.loads(selection_path.read_text(encoding='utf-8-sig'))
        selected = selection.get('include_ids') if isinstance(selection, dict) else selection
        assert isinstance(selected, list) and all(isinstance(value, str) for value in selected)
        selected = set(selected)
    rows = [row for row in metadata['occurrences'] if selected is None or row['id'] in selected]
    prototypes = {int(item['id']): item for item in metadata['prototypes']}
    selected_ids = selected if selected is not None else {str(row['id']) for row in rows}
    expanded = sum(prototypes[int(row['prototype'])]['triangles'] for row in rows)
    target = manifest.with_name('family-benchmark-' + mode + '-' + uuid.uuid4().hex + '.usdc')
    publish('before_conversion', source_manifest=str(manifest), target=str(target),
            source_placements=len(rows), source_triangle_occurrences=expanded,
            source_unique_prototypes=len({row['prototype'] for row in rows}),
            source_families=len({family_identity(row) for row in rows}),
            source_geometry_bytes=binary.stat().st_size, converter_seconds=None, import_seconds=None)
    last_progress = [0.]
    def progress(event):
        if time.monotonic() - last_progress[0] >= .5 or event['progress'] >= .95:
            publish('converting', conversion_progress=event)
            last_progress[0] = time.monotonic()
    started = time.monotonic()
    converted = build_usd(manifest, target, import_mode=mode, selected_element_ids=selected,
                          progress_callback=progress)
    conversion_seconds = time.monotonic() - started
    stage = Usd.Stage.Open(str(target))
    triangle_count = sum(len(UsdGeom.Mesh(prim.GetChild('mesh')).GetFaceVertexCountsAttr().Get())
                         for prim in stage.GetPrimAtPath('/Prototypes').GetChildren())
    stage = None
    gc.collect()
    publish('before_import', converter_seconds=conversion_seconds, converted=converted,
            output_prototype_triangles=triangle_count, usd_bytes=target.stat().st_size)
    # This durable boundary is intentionally written before the expensive host
    # operation, so an external timeout preserves converter timing and counts.
    publish('importing', import_started_monotonic=time.monotonic())
    started = time.monotonic()
    imported = import_result(target, preserve_instances=mode != 'OBJECTS',
                             selected_element_ids=selected_ids, source_path=metadata.get('source', {}).get('path', 'benchmark.rvt'))
    import_seconds = time.monotonic() - started
    owned = [obj for obj in bpy.context.scene.objects if obj.get(OWNER) == imported['import_id'] and obj.type == 'MESH']
    assert len(owned) == converted['placements']
    if kind == 'synthetic' and selected is None:
        assert len(rows) == 73133 and expanded == 183000
        assert len(owned) == (10 if mode == 'FAMILY' else 73133)
    assert (digest(manifest), digest(binary)) == original
    assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
    publish('done', status='passed', import_seconds=import_seconds, mesh_objects=len(owned),
            mesh_datablocks=len({obj.data.as_pointer() for obj in owned}), raw_inputs_unchanged=True,
            expanded_output_triangles=triangle_count if mode == 'FAMILY' else None)
except Exception as error:
    publish('failed', status='failed', error=repr(error))
    raise
