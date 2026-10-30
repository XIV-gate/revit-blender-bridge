"""Read-only instance grouping in canonical producer coordinates.

Revit identity and sampled surface matching are limited to proven simple,
untextured family geometry. Fallback matches retain triangle order, materials,
normals and UVs. Returned axis adjustments never mutate source placements.
"""
from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
import hashlib
import math

MAX_FALLBACK_COMPARISONS = 64
MAX_SURFACE_COMPARISONS = 4


def _order(identifier):
    try:
        return 0, int(identifier), str(identifier)
    except (ValueError, TypeError):
        return 1, 0, str(identifier)


def _type_id(occurrence):
    value = occurrence.get('type_id')
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value if value and value != '-1' else None


def _uv_sensitive(material):
    if material is None:
        return True  # Missing material metadata cannot establish UV independence.
    flag = material.get('uv_sensitive')
    if isinstance(flag, bool):
        return flag
    return bool(material.get('base_texture') or material.get('textures'))


class _BoundsIndex:
    """Per semantic group, prune anchors by the first bound before full checks."""
    def __init__(self):
        self.entries = []
        self.seen = set()

    def add(self, identifier, bounds):
        if identifier not in self.seen:
            self.seen.add(identifier)
            insort(self.entries, (bounds[0], _order(identifier), identifier))

    def candidates(self, bounds, tolerance, limit=None):
        start = bisect_left(self.entries, (bounds[0] - tolerance,))
        stop = bisect_right(self.entries, (bounds[0] + tolerance, (2,)))
        if limit is not None:
            stop = min(stop, start + limit)
        return (self.entries[index][2] for index in range(start, stop))


def plan_instances(occurrences, prototypes, materials, *, tolerance_mm=0.1, use_revit=True):
    """Return occurrence→prototype redirects without changing any input arrays.

    Prototype keys are retained, occurrence keys become strings. Repeated raw
    prototypes share one result even across types. Representatives are visited
    in ascending numeric prototype/occurrence order, and comparisons always use
    the representative's original data, preventing transitive tolerance drift.

    ``native_merges`` and ``tolerant_merges`` count distinct merged prototypes;
    ``exact_reuses`` counts occurrences already sharing a raw prototype. A zero
    tolerance makes fallback fully exact, while safe Revit identity still works.
    Exact normal/UV buckets are tried first. Ambiguous dense bounds buckets have
    a bounded fallback budget; unmatched geometry stays independent on exhaustion.
    """
    try:
        tolerance = float(tolerance_mm)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Допуск должен быть числом от 0 до 1000 мм') from error
    if isinstance(tolerance_mm, bool) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1000:
        raise ValueError('Допуск должен быть числом от 0 до 1000 мм')
    tolerance /= 1000
    import numpy as np  # Blender supplies NumPy; importing this module does not require it.

    stats = dict(occurrences=0, input_prototypes=len(prototypes), used_prototypes=0,
                 representatives=0, exact_reuses=0, native_merges=0, tolerant_merges=0,
                 native_comparisons=0, fallback_comparisons=0, comparison_limit_hits=0,
                 surface_comparisons=0, surface_merges=0, surface_limit_hits=0)
    lookup = {str(key): key for key in prototypes}
    grouped = defaultdict(list)
    ids = set()
    for occurrence in occurrences:
        identifier = str(occurrence['id'])
        if identifier in ids:
            raise ValueError('Повторяющийся ID размещения: ' + identifier)
        ids.add(identifier)
        try:
            key = lookup[str(occurrence['prototype'])]
        except KeyError as error:
            raise ValueError('Не найден прототип размещения: ' + identifier) from error
        grouped[key].append(occurrence)
    stats['occurrences'] = len(ids)
    stats['used_prototypes'] = len(grouped)
    stats['exact_reuses'] = len(ids) - len(grouped)
    if isinstance(materials, dict):
        material_metadata = {str(key): value for key, value in materials.items()}
    else:
        material_metadata = {str(item['id']): item for item in materials}

    info = {}
    for key in grouped:
        records = prototypes[key]
        vertices = records['vertices']
        coordinates = vertices[:, :, :3].reshape(-1, 3)
        if len(coordinates):
            minimum, maximum = coordinates.min(axis=0), coordinates.max(axis=0)
            bounds = tuple(float(value) for value in (*minimum, *maximum))
        else:
            bounds = (0.0,) * 6
        sequence = np.ascontiguousarray(records['material'])
        unique_materials = np.unique(sequence)
        single = int(unique_materials[0]) if len(unique_materials) == 1 else None
        native_material = single if single is not None and not _uv_sensitive(material_metadata.get(str(single))) else None
        info[key] = dict(vertices=vertices, material_sequence=sequence, bounds=bounds, native_material=native_material,
                         fallback=(len(records), hashlib.sha256(sequence.tobytes()).digest()),
                         attributes=hashlib.sha256(np.ascontiguousarray(vertices[:, :, 3:8]).tobytes()).digest())

    native_index = defaultdict(_BoundsIndex)
    fallback_index = defaultdict(_BoundsIndex)
    attribute_index = defaultdict(_BoundsIndex)
    surface_index = defaultdict(_BoundsIndex)
    redirects = {}
    adjustments = {}
    representatives = set()

    def bounds_match(first, second):
        return all(abs(a - b) <= tolerance for a, b in zip(first, second))

    def native_keys(rows, data):
        if not use_revit or data['native_material'] is None:
            return set()
        return {(row['native_instance_key'], _type_id(row), data['native_material']) for row in rows
                if row.get('native_instance_safe') is True and _type_id(row) is not None
                and isinstance(row.get('native_instance_key'), str) and row['native_instance_key'].strip()}

    def fallback_keys(rows, data):
        return {(_type_id(row), *data['fallback']) for row in rows if _type_id(row) is not None}

    def attribute_keys(rows, data):
        return {(*key, data['attributes']) for key in fallback_keys(rows, data)}

    def surface_keys(rows, data):
        if (not use_revit or tolerance <= 0 or data['native_material'] is None
                or any(row.get('native_instance_safe') is not True or _type_id(row) is None
                       or not isinstance(row.get('family_name'), str) or not row['family_name'].strip() for row in rows)):
            return set()
        return {(_type_id(row), row['family_name'].strip(), data['native_material']) for row in rows}

    def matching_candidates(index, keys, data, limit=None):
        candidates = set()
        for key in keys:
            candidates.update(index[key].candidates(data['bounds'], tolerance, limit))
        return sorted(candidates, key=_order)

    for identifier in sorted(grouped, key=_order):
        rows = sorted(grouped[identifier], key=lambda row: _order(row['id']))
        data = info[identifier]
        representative = identifier
        adjustment = [1, 1, 1]
        for candidate in matching_candidates(native_index, native_keys(rows, data), data):
            stats['native_comparisons'] += 1
            if bounds_match(data['bounds'], info[candidate]['bounds']):
                representative = candidate
                stats['native_merges'] += 1
                break
        if representative == identifier:
            limit = MAX_FALLBACK_COMPARISONS + 1
            candidates = matching_candidates(attribute_index, attribute_keys(rows, data), data, limit)
            prioritized = set(candidates)
            candidates.extend(candidate for candidate in matching_candidates(
                fallback_index, fallback_keys(rows, data), data, limit) if candidate not in prioritized)
            comparisons = 0
            for candidate in candidates:
                anchor = info[candidate]
                if not bounds_match(data['bounds'], anchor['bounds']):
                    continue
                if comparisons >= MAX_FALLBACK_COMPARISONS:
                    stats['comparison_limit_hits'] += 1
                    break
                comparisons += 1
                stats['fallback_comparisons'] += 1
                if not np.array_equal(data['material_sequence'], anchor['material_sequence']):
                    continue
                first, second = data['vertices'], anchor['vertices']
                if tolerance == 0:
                    equal = np.array_equal(first, second)
                else:
                    difference = np.abs(first.astype(np.float64) - second.astype(np.float64))
                    equal = (np.all(difference[:, :, :3] <= tolerance)
                             and np.all(difference[:, :, 3:6] <= 1e-6)
                             and np.all(difference[:, :, 6:8] <= 1e-8))
                if equal:
                    representative = candidate
                    stats['tolerant_merges'] += 1
                    break
        if representative == identifier and len(data['vertices']) <= 2048:
            from .instance_surface import surface_matches
            checks = 0
            for signs in ((1, 1, 1), (-1, -1, 1)):
                low, high = data['bounds'][:3], data['bounds'][3:]
                rotated_bounds = tuple(low[axis] if sign > 0 else -high[axis] for axis, sign in enumerate(signs)) + tuple(
                    high[axis] if sign > 0 else -low[axis] for axis, sign in enumerate(signs))
                rotated = dict(data, bounds=rotated_bounds)
                for candidate in matching_candidates(surface_index, surface_keys(rows, data), rotated, MAX_SURFACE_COMPARISONS + 1):
                    anchor = info[candidate]
                    if len(anchor['vertices']) > 2048 or not bounds_match(rotated_bounds, anchor['bounds']):
                        continue
                    if checks >= MAX_SURFACE_COMPARISONS:
                        stats['surface_limit_hits'] += 1
                        break
                    checks += 1
                    stats['surface_comparisons'] += 1
                    if surface_matches(anchor['vertices'], data['vertices'], signs, tolerance):
                        representative = candidate
                        adjustment = list(signs)
                        stats['surface_merges'] += 1
                        break
                if representative != identifier or checks >= MAX_SURFACE_COMPARISONS:
                    break
        representatives.add(representative)
        for row in rows:
            redirects[str(row['id'])] = representative
            if adjustment != [1, 1, 1]:
                adjustments[str(row['id'])] = adjustment.copy()
        # Always index the output anchor's original bounds and geometry, never
        # the just-matched candidate: A≈B and B≈C must not imply A≈C.
        anchor = info[representative]
        if adjustment != [1, 1, 1]:
            continue  # Its native key is expressed in a rotated frame, not the anchor's.
        for key in native_keys(rows, anchor):
            native_index[key].add(representative, anchor['bounds'])
        for key in fallback_keys(rows, anchor):
            fallback_index[key].add(representative, anchor['bounds'])
        for key in attribute_keys(rows, anchor):
            attribute_index[key].add(representative, anchor['bounds'])
        for key in surface_keys(rows, anchor):
            surface_index[key].add(representative, anchor['bounds'])
    stats['representatives'] = len(representatives)
    return dict(redirects=redirects, representatives=representatives, adjustments=adjustments, statistics=stats)
