"""Preflight and stream family meshes without host calls, welding or mutation."""
from collections import defaultdict
import hashlib
import json

MAX_CHUNK_TRIANGLES = 1_000_000
MAX_EXPANDED_TRIANGLES = 50_000_000
_BATCH_TRIANGLES = 16_384


def _order(value):
    try:
        return 0, int(value), str(value)
    except (TypeError, ValueError):
        return 1, 0, str(value)


def _ids(row, name, fallback):
    values = row.get(name, fallback)
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) or not value for value in values):
        raise ValueError('Повреждены исходные ID семейства')
    return sorted(set(values), key=_order)


def _normalise(values, np):
    magnitude = np.max(np.abs(values), axis=-1, keepdims=True)
    if not np.isfinite(values).all() or np.any(magnitude == 0):
        raise ValueError('Некорректные нормали семейства')
    values = values / magnitude
    return values / np.linalg.norm(values, axis=-1, keepdims=True)


class FamilyGeometryPlan:
    """Reusable descriptor plan; only the currently yielded chunk is expanded.

    USD itself retains its authored geometry. This bounds the Python working
    buffers, not the memory required by the complete expanded USD scene.
    """
    def __init__(self, descriptors, statistics):
        self._descriptors = descriptors
        self.statistics = statistics
        self.occurrences = [descriptor['occurrence'] for descriptor in descriptors]

    def __iter__(self):
        for descriptor in self._descriptors:
            yield self._build_chunk(descriptor)

    @staticmethod
    def _build_chunk(descriptor):
        import numpy as np
        dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
        output = np.empty(descriptor['triangles'], dtype=dtype)
        owners = np.empty(len(output), dtype='<i4')
        offset = 0
        pivot = descriptor['pivot']
        for source_index, (prepared, start, count) in enumerate(descriptor['segments']):
            for first in range(start, start + count, _BATCH_TRIANGLES):
                last = min(first + _BATCH_TRIANGLES, start + count)
                raw = prepared['records'][first:last]
                target = output[offset:offset + len(raw)]
                target[:] = raw
                world = raw['vertices'][:, :, :3].astype(np.float64) @ prepared['basis'].T + prepared['origin']
                normal = raw['vertices'][:, :, 3:6].astype(np.float64) @ prepared['normal_matrix']
                target['vertices'][:, :, :3] = world - pivot
                target['vertices'][:, :, 3:6] = _normalise(normal, np)
                if prepared['mirrored']:
                    target['vertices'][:] = target['vertices'][:, [0, 2, 1]]
                owners[offset:offset + len(raw)] = source_index
                offset += len(raw)
        return dict(records=output, source_index=owners, occurrence=descriptor['occurrence'],
                    prototype=descriptor['occurrence']['prototype'], family_key=descriptor['family_key'],
                    source_table=descriptor['source_table'], source_ranges=descriptor['source_ranges'])


def plan_family_geometry(occurrences, records_by_id, *, max_triangles=MAX_CHUNK_TRIANGLES,
                         max_expanded_triangles=MAX_EXPANDED_TRIANGLES):
    """Validate everything before returning an iterable of rebased family chunks.

    Source IDs remain strings. Per-face int32 values index a chunk-local table
    of original occurrence owners; compound source IDs are reported as a union,
    never as invented per-child ownership. Oversized occurrences may span chunks.
    """
    import numpy as np
    if (type(max_triangles) is not int or not 1 <= max_triangles <= MAX_CHUNK_TRIANGLES
            or type(max_expanded_triangles) is not int or max_expanded_triangles < 1):
        raise ValueError('Некорректное ограничение размера семейства')
    lookup = {str(key): key for key in records_by_id}
    groups = defaultdict(list)
    seen = set()
    validated = set()
    expanded = 0
    mirrored = 0
    for row in occurrences:
        identifier = str(row['id'])
        if identifier in seen:
            raise ValueError('Повторяющийся ID размещения: ' + identifier)
        seen.add(identifier)
        try:
            prototype = lookup[str(row['prototype'])]
        except KeyError as error:
            raise ValueError('Не найден прототип семейства') from error
        records = records_by_id[prototype]
        if prototype not in validated:
            if (records.ndim != 1 or records.dtype.names is None
                    or not {'vertices', 'material'}.issubset(records.dtype.names)
                    or records['vertices'].shape != (len(records), 3, 8)
                    or records['vertices'].dtype != np.dtype('<f4')
                    or records['material'].dtype != np.dtype('<i8')
                    or not np.isfinite(records['vertices']).all()):
                raise ValueError('Некорректная геометрия семейства')
            validated.add(prototype)
        expanded += len(records)
        if expanded > max_expanded_triangles:
            raise ValueError('Семейства требуют разворачивания слишком большого числа треугольников: '
                             + str(expanded) + '; используйте режим инстансов')
        try:
            transform = np.asarray(row['transform'], dtype=np.float64)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError('Некорректная трансформация семейства') from error
        if transform.shape != (12,) or not np.isfinite(transform).all():
            raise ValueError('Некорректная трансформация семейства')
        basis = transform[:9].reshape(3, 3).T
        sign, _ = np.linalg.slogdet(basis)
        if sign == 0:
            raise ValueError('Вырожденная трансформация семейства')
        inverse = np.linalg.inv(basis)
        if not np.isfinite(inverse).all():
            raise ValueError('Некорректная обратная трансформация семейства')
        source_ids = _ids(row, 'source_element_ids', [identifier])
        parent = row.get('parent_id')
        parents = _ids(row, 'selection_parent_ids', [str(parent)] if parent else [])
        category = str(row.get('category_id') or row.get('category') or row.get('category_name') or 'Прочее')
        family = str(row.get('family_name') or '')
        family = family if family.strip() else ''
        type_name = str(row.get('type_name') or '')
        label = family or (type_name if type_name.strip() else 'Без семейства')
        type_id = row.get('type_id')
        valid_type = type_id is not None and not isinstance(type_id, bool) and str(type_id).strip() not in ('', '-1')
        key = ((category, 'family', family) if family else
               (category, 'type', str(type_id).strip()) if valid_type else
               (category, 'occurrence', identifier))
        table = dict(id=identifier, source_element_ids=source_ids, selection_parent_ids=parents)
        prepared = dict(row=row, records=records, prototype=prototype, basis=basis, group_label=label,
                        origin=transform[9:].copy(), normal_matrix=inverse, mirrored=sign < 0, table=table)
        groups[key].append(prepared)
        mirrored += int(sign < 0)
    if not expanded:
        raise ValueError('В выбранных семействах нет треугольников')
    descriptors = []
    split_counts = defaultdict(int)
    tokens = {}
    for key in sorted(groups):
        sources = sorted(groups[key], key=lambda prepared: _order(prepared['table']['id']))
        family_key = json.dumps(key, ensure_ascii=False, separators=(',', ':'))
        token = hashlib.sha256(family_key.encode('utf-8')).hexdigest()[:16]
        if token in tokens and tokens[token] != key:
            raise ValueError('Совпадение идентификаторов семейств')
        tokens[token] = key
        chunks, current, size = [], [], 0
        for prepared in sources:
            first = 0
            while first < len(prepared['records']):
                count = min(max_triangles - size, len(prepared['records']) - first)
                current.append((prepared, first, count))
                split_counts[prepared['table']['id']] += 1
                first += count
                size += count
                if size == max_triangles:
                    chunks.append(current)
                    current, size = [], 0
        if current:
            chunks.append(current)
        for part, segments in enumerate(chunks, 1):
            low, high = np.full(3, np.inf), np.full(3, -np.inf)
            table, ranges = [], []
            offset = 0
            for source_index, (prepared, first, count) in enumerate(segments):
                table.append(prepared['table'])
                ranges.append(dict(source_index=source_index, start=offset, count=count))
                offset += count
                for start in range(first, first + count, _BATCH_TRIANGLES):
                    raw = prepared['records'][start:min(start + _BATCH_TRIANGLES, first + count)]['vertices']
                    with np.errstate(over='ignore', invalid='ignore'):
                        world = raw[:, :, :3].astype(np.float64) @ prepared['basis'].T + prepared['origin']
                        normal = raw[:, :, 3:6].astype(np.float64) @ prepared['normal_matrix']
                    if not np.isfinite(world).all():
                        raise ValueError('Координаты семейства выходят за числовой диапазон')
                    _normalise(normal, np)
                    low = np.minimum(low, world.min(axis=(0, 1)))
                    high = np.maximum(high, world.max(axis=(0, 1)))
            pivot = low / 2 + high / 2
            if not np.isfinite(pivot).all() or np.max(np.maximum(np.abs(low - pivot), np.abs(high - pivot))) > np.finfo('f4').max:
                raise ValueError('Размер семейства выходит за диапазон координат Mesh')
            identifier = 'family_' + token + '_p' + str(part).zfill(4)
            if identifier in seen:
                raise ValueError('ID семейства совпадает с исходным элементом')
            first = segments[0][0]['row']
            label = sources[0]['group_label']
            worksets = {str(prepared['row'].get('workset') or '') for prepared, _, _ in segments}
            workset_ids = {str(prepared['row'].get('workset_id') or '') for prepared, _, _ in segments}
            occurrence = dict(id=identifier, unique_id=identifier, prototype=len(descriptors),
                category_id=key[0], category=str(first.get('category') or first.get('category_name') or 'Прочее'),
                family_name=label if key[1] == 'family' else '', type_name=label if key[1] != 'family' else '',
                name=label + (' · часть ' + str(part) if len(chunks) > 1 else ''),
                workset=next(iter(worksets)) if len(worksets) == 1 else 'Несколько рабочих наборов',
                workset_id=next(iter(workset_ids)) if len(workset_ids) == 1 else '', decision='include',
                transform=[1., 0., 0., 0., 1., 0., 0., 0., 1., *pivot.tolist()],
                import_mode='FAMILY', family_key=family_key,
                source_element_ids=sorted({value for source in table for value in source['source_element_ids']}, key=_order),
                selection_parent_ids=sorted({value for source in table for value in source['selection_parent_ids']}, key=_order),
                source_table=table, source_ranges=ranges)
            descriptors.append(dict(segments=segments, triangles=offset, pivot=pivot, family_key=family_key,
                                    occurrence=occurrence, source_table=table, source_ranges=ranges))
    statistics = dict(source_placements=len(seen), source_prototypes=len(validated),
                      source_families=len(groups), families=len({item['family_key'] for item in descriptors}),
                      chunks=len(descriptors), expanded_triangles=expanded, expanded_record_bytes=expanded * 104,
                      max_chunk_triangles=max_triangles,
                      actual_max_chunk_triangles=max(item['triangles'] for item in descriptors), mirrored_placements=mirrored,
                      split_placements=sum(count > 1 for count in split_counts.values()))
    return FamilyGeometryPlan(descriptors, statistics)
