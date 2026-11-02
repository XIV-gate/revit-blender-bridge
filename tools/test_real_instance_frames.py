"""Opt-in full payload USD audit plus small real mesh import; never edit live scenes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import bpy
import numpy as np
from pxr import Usd, UsdGeom

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
from xivgate_revit_bridge.conversion import build_usd
from xivgate_revit_bridge.blender_import import import_result, OWNER

root = Path(os.environ['RBB_TEST_ROOT']).resolve()
source = Path(os.environ['RBB_REAL_MANIFEST']).resolve(strict=True)
assert source.is_relative_to(root), 'Use a copied, approved test payload'
manifest_bytes = source.read_bytes()
manifest = json.loads(manifest_bytes)
binary = source.with_name('geometry.bin')
before = hashlib.sha256(binary.read_bytes()).hexdigest()
raw = binary.read_bytes()
dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
records = {p['id']: np.frombuffer(raw, dtype=dtype, offset=p['offset'], count=p['triangles']) for p in manifest['prototypes']}
target = source.with_name('frames-audit-' + str(os.getpid()) + '.usdc')
started = time.monotonic()
converted = build_usd(source, target, instance_tolerance_mm=.1, center_model=False)
elapsed = time.monotonic() - started
stage = Usd.Stage.Open(str(target))
cache = UsdGeom.XformCache()
maximum = 0
redirects = {}
for row in manifest['occurrences']:
    prim = stage.GetPrimAtPath('/World/Elements/e_' + row['id'])
    anchor = int(prim.GetAttribute('revit:prototype').Get())
    redirects[row['id']] = anchor
    matrix = np.asarray(cache.GetLocalToWorldTransform(prim))
    # The protocol and Gf both store the row-vector representation here.
    t = row['transform']
    original = records[row['prototype']]['vertices'][:, :, :3].reshape(-1, 3).astype(np.float64)
    expected = original @ np.asarray(t[:9]).reshape(3, 3) + t[9:12]
    shared = records[anchor]['vertices'][:, :, :3].reshape(-1, 3).astype(np.float64)
    actual = shared @ matrix[:3, :3] + matrix[3, :3]
    error = max(np.max(np.abs(actual.min(0) - expected.min(0))), np.max(np.abs(actual.max(0) - expected.max(0))))
    maximum = max(maximum, float(error))
assert maximum < .000201, maximum  # configured 0.1 mm per-coordinate matching, two bounds plus arithmetic
assert converted['placements'] == len(manifest['occurrences'])
assert converted['prototypes'] == 4322, converted
for first, second in (('10127461', '11119046'), ('16', '173')):
    # Element IDs are not prototype IDs; the first pair is the real glass regression.
    if first in redirects and second in redirects:
        assert redirects[first] == redirects[second]

# Import a small representative subset of the real data to verify host mesh sharing.
groups = {}
for row in manifest['occurrences']:
    groups.setdefault(redirects[row['id']], []).append(row)
selected = []
for rows in groups.values():
    candidates = []
    seen = set()
    for row in rows:
        if row['prototype'] not in seen:
            seen.add(row['prototype']); candidates.append(row)
    if len(candidates) > 1:
        selected.extend(candidates[:2])
    if len(selected) >= 12:
        break
assert len(selected) >= 6
stage = None; cache = None
work = source.parent / ('sample-' + str(os.getpid()))
work.mkdir()
subset = dict(manifest, occurrences=selected)
needed = {row['prototype'] for row in selected}
subset['prototypes'] = [dict(p) for p in manifest['prototypes'] if p['id'] in needed]
with (work / 'geometry.bin').open('wb') as stream:
    for p in subset['prototypes']:
        data = raw[p['offset']:p['offset']+p['bytes']]
        p['offset'] = stream.tell(); stream.write(data)
subset_path = work / 'manifest.json'
subset_path.write_text(json.dumps(subset), encoding='utf-8')
small_target = work / 'subset.usdc'
build_usd(subset_path, small_target, include_materials=False, include_textures=False, center_model=False)
result = import_result(small_target, selected_element_ids={r['id'] for r in selected}, source_path='real-payload.rvt')
owned = {str(o['revit:id']): o for o in bpy.context.scene.objects if o.get(OWNER) == result['import_id'] and 'revit:id' in o}
assert len(owned) == len(selected) and all(o.type == 'MESH' and o.data.uv_layers for o in owned.values())
for index in range(0, len(selected), 2):
    assert owned[selected[index]['id']].data == owned[selected[index+1]['id']].data
assert source.read_bytes() == manifest_bytes and hashlib.sha256(binary.read_bytes()).hexdigest() == before
report = dict(status='passed', blender=bpy.app.version_string, placements=converted['placements'],
    prototypes=converted['prototypes'], full_usd_seconds=elapsed, max_world_bounds_error_metres=maximum,
    sample_placements=len(owned), original_unchanged=True, statistics=converted['instance_matching'])
output = root / 'RecommendationInstanceVerified_20261102' / ('real_frames_' + str(os.getpid()) + '.json')
output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print('AGR_REAL_INSTANCE_FRAMES ' + json.dumps(report), flush=True)
