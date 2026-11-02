"""Conservative removal of detached, remote floor parts from export copies."""
from collections import Counter, defaultdict
import math

from .catalog import _FACADE_ANCHORS, _facade_region

MAX_FLOOR_TRIANGLES = 100_000
_FLOORS = frozenset(('перекрытия', 'floors', 'floor'))


def _category(row):
    return str(row.get('category') or row.get('category_name') or '').strip().casefold()


def _placement(row):
    import numpy as np
    transform = np.asarray(row['transform'], dtype=np.float64)
    if transform.shape != (12,) or not np.isfinite(transform).all():
        raise ValueError('Invalid placement')
    return transform[:9].reshape(3, 3), transform[9:]


def _coordinates(records):
    import numpy as np
    coordinates = records['vertices'][:, :, :3].reshape(-1, 3)
    if not len(coordinates) or not np.isfinite(coordinates).all():
        raise ValueError('Invalid coordinates')
    return coordinates


def _intersects(low, high, region):
    anchor_low, anchor_high, margin = region
    return all(high[axis] >= anchor_low[axis] - margin
               and low[axis] <= anchor_high[axis] + margin for axis in range(3))


def _components(records):
    import numpy as np
    # Float64 integer-valued keys avoid overflowing int64 for distant source
    # coordinates. Welding can join nearby parts but never clips a triangle.
    keys = np.rint(_coordinates(records).astype(np.float64) / 1e-6)
    _, vertex_ids = np.unique(keys, axis=0, return_inverse=True)
    triangles = vertex_ids.reshape(-1, 3)
    parents = list(range(len(triangles)))
    owner = {}

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, triangle in enumerate(triangles):
        for vertex in triangle:
            vertex = int(vertex)
            previous = owner.get(vertex)
            if previous is None:
                owner[vertex] = index
            else:
                parents[find(index)] = find(previous)
    groups = defaultdict(list)
    for index in range(len(triangles)):
        groups[find(index)].append(index)
    return list(groups.values())


def filter_remote_components(occurrences, records_by_id):
    """Return filtered copies and details; originals and placements are unchanged.

    Only a uniquely used floor prototype containing BOTH nearby and detached
    remote components can change. A wholly remote floor remains intact. An
    unchanged result returns the original mapping and array references.
    """
    import numpy as np
    rows = list(occurrences)
    lookup = {str(key): key for key in records_by_id}
    uses = Counter(str(row.get('prototype')) for row in rows)
    report = dict(elements=[], filtered_elements=0, removed_components=0,
                  removed_triangles=0, kept_triangles=0, skipped_large=0,
                  skipped_shared=0, skipped_invalid=0)
    result = records_by_id
    anchors, bounds_cache = [], {}
    for row in rows:
        category = _category(row)
        if category not in _FACADE_ANCHORS:
            continue
        try:
            key = lookup[str(row['prototype'])]
            basis, origin = _placement(row)
            cache_key = (key, tuple(basis.reshape(-1)))
            if cache_key not in bounds_cache:
                points = _coordinates(records_by_id[key]) @ basis
                bounds_cache[cache_key] = (points.min(axis=0), points.max(axis=0))
            low, high = bounds_cache[cache_key]
            anchors.append(dict(category_name=category, bbox_min=low + origin, bbox_max=high + origin))
        except (KeyError, ValueError, TypeError, IndexError, OverflowError):
            continue
    region = _facade_region(anchors)
    if region is None:
        return result, report

    for row in rows:
        if _category(row) not in _FLOORS:
            continue
        if uses[str(row.get('prototype'))] != 1:
            report['skipped_shared'] += 1
            continue
        try:
            key = lookup[str(row['prototype'])]
            records = records_by_id[key]
            if len(records) > MAX_FLOOR_TRIANGLES:
                report['skipped_large'] += 1
                continue
            basis, origin = _placement(row)
            parts = _components(records)
            if len(parts) < 2:
                continue
            keep, remove, distances = [], [], []
            for indices in parts:
                points = _coordinates(records[indices]) @ basis + origin
                low, high = points.min(axis=0), points.max(axis=0)
                if not np.isfinite(low).all() or not np.isfinite(high).all():
                    raise ValueError('Invalid world bounds')
                if _intersects(low, high, region):
                    keep.extend(indices)
                else:
                    remove.append(indices)
                    distances.append(math.hypot(*(max(region[0][axis] - high[axis],
                        low[axis] - region[1][axis], 0) for axis in range(3))))
            if not keep or not remove:
                continue
            kept = records[sorted(keep)].copy()
            remaining = _coordinates(kept) @ basis + origin
            removed_triangles = sum(map(len, remove))
            detail = dict(id=str(row['id']), prototype=key, removed_components=len(remove),
                          removed_triangles=removed_triangles, kept_triangles=len(kept),
                          distance=min(distances), bbox_min=remaining.min(axis=0).tolist(),
                          bbox_max=remaining.max(axis=0).tolist())
            if result is records_by_id:
                result = dict(records_by_id)
            result[key] = kept
            report['elements'].append(detail)
            report['filtered_elements'] += 1
            report['removed_components'] += len(remove)
            report['removed_triangles'] += removed_triangles
            report['kept_triangles'] += len(kept)
        except (KeyError, ValueError, TypeError, IndexError, OverflowError):
            report['skipped_invalid'] += 1
    return result, report
