"""Conservative positive-axis affine sharing; never modify raw geometry."""
from collections import defaultdict
import hashlib
import math

_MAX_COMPARISONS_PER_PROTOTYPE = 32
_MAX_TOTAL_COMPARISONS = 50_000
_POSITION_CAP_METRES = 1e-5
_NORMAL_TOLERANCE = 1e-6


def _order(value):
    try:
        return 0, int(value), str(value)
    except (ValueError, TypeError):
        return 1, 0, str(value)


def plan_affine_instances(occurrences, records_by_id, materials, tolerance_mm=.1):
    """Find same-type meshes differing only by translation and positive scale.

    Return only groups containing at least two distinct raw prototype IDs. All
    their occurrences, including anchor occurrences, are returned so callers
    can replace a preceding approximate plan safely. Original records stay the
    output mesh data; ``scales`` combine with pivot compensation separately.

    Canonicalization preserves winding: only cyclic corner rotations and whole
    triangle permutations are allowed, carrying materials/normals/UV together.
    Quantized keys only prune candidates. Every merge is subsequently checked
    against original float64 coordinates, inverse-transpose normals and UVs.
    """
    try:
        tolerance = float(tolerance_mm)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Допуск должен быть числом от 0 до 1000 мм') from error
    if isinstance(tolerance_mm, bool) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1000:
        raise ValueError('Допуск должен быть числом от 0 до 1000 мм')
    limit = min(tolerance / 1000, _POSITION_CAP_METRES)
    import numpy as np

    metadata = ({str(key): value for key,value in materials.items()} if isinstance(materials,dict)
                else {str(value['id']):value for value in materials})
    lookup = {str(key):key for key in records_by_id}
    cohorts = defaultdict(list)
    seen_ids = set()
    for row in occurrences:
        identifier = str(row['id'])
        if identifier in seen_ids:
            raise ValueError('Повторяющийся ID размещения: ' + identifier)
        seen_ids.add(identifier)
        type_id = row.get('type_id')
        if type_id is None or isinstance(type_id,bool) or str(type_id).strip() in ('','-1'):
            continue
        try:
            prototype = lookup[str(row['prototype'])]
        except KeyError as error:
            raise ValueError('Не найден прототип размещения: ' + identifier) from error
        cohorts[(prototype,str(type_id).strip())].append(row)
    stats = dict(occurrences=len(seen_ids),input_prototypes=len(records_by_id),eligible_prototypes=0,
                 groups=0,grouped_prototypes=0,grouped_occurrences=0,comparisons=0,
                 comparison_limit_hits=0,empty_skips=0,normal_skips=0,
                 max_position_error_metres=0.,max_normal_error=0.)
    info = {}
    for prototype in sorted({key[0]for key in cohorts},key=_order):
        records = records_by_id[prototype]
        vertices = records['vertices']
        if vertices.shape != (len(records),3,8) or not np.isfinite(vertices).all():
            raise ValueError('Некорректная геометрия прототипа')
        if not len(records):
            stats['empty_skips'] += 1
            continue
        xyz = vertices[:,:,:3].astype(np.float64)
        low, high = xyz.min(axis=(0,1)), xyz.max(axis=(0,1))
        extent = high-low
        center = (low+high)/2
        safe_extent = np.where(extent==0,1.,extent)
        normal = vertices[:,:,3:6].astype(np.float64)*safe_extent
        lengths = np.linalg.norm(normal,axis=2,keepdims=True)
        if np.any(lengths==0):
            stats['normal_skips'] += 1
            continue
        normal /= lengths
        sensitive = any(metadata.get(str(int(material)),{}).get('uv_sensitive') is not False
                        for material in np.unique(records['material']))
        canonical = np.concatenate((np.round((xyz-center)/safe_extent,4), np.round(normal,4),
                                    np.round(vertices[:,:,6:8].astype(np.float64),8) if sensitive
                                    else np.zeros((len(records),3,2))),axis=2)
        canonical[canonical==0] = 0.  # Normalize the insignificant negative zero bit.
        # Choose a lexicographically minimal CYCLIC rotation, preserving winding.
        starts = np.zeros(len(records),dtype=np.intp)
        triangles = np.arange(len(records))[:,None]
        corners = np.arange(3)
        for rotation in (1,2):
            current = canonical[triangles,(starts[:,None]+corners)%3].reshape(len(records),-1)
            other = np.roll(canonical,-rotation,axis=1).reshape(len(records),-1)
            different = current != other
            first = different.argmax(axis=1)
            choose = different.any(axis=1) & (other[np.arange(len(records)),first] < current[np.arange(len(records)),first])
            starts[choose] = rotation
        local_order = (starts[:,None]+corners)%3
        oriented = canonical[triangles,local_order].reshape(len(records),-1)
        triangle_order = np.lexsort(tuple(oriented[:,index]for index in range(oriented.shape[1]-1,-1,-1))
                                    +(records['material'],))
        point_order = (np.arange(len(records))[:,None]*3+local_order)[triangle_order].reshape(-1)
        material_sequence = np.ascontiguousarray(records['material'][triangle_order])
        digest = hashlib.sha256(material_sequence.tobytes())
        digest.update(np.ascontiguousarray(oriented[triangle_order]).tobytes())
        info[prototype] = dict(vertices=vertices,center=center,extent=extent,sensitive=sensitive,
                               point_order=point_order,materials=material_sequence,
                               key=(len(records),tuple(extent==0),digest.digest()))
    stats['eligible_prototypes'] = len(info)
    buckets = defaultdict(list)
    groups = defaultdict(list)
    for (prototype,type_id),rows in sorted(cohorts.items(),key=lambda pair:(_order(pair[0][0]),pair[0][1])):
        if prototype not in info:
            continue
        source = info[prototype]
        source_values = source['vertices'].reshape(-1,8)[source['point_order']].astype(np.float64)
        gain = 1.
        for row in rows:
            if 'transform' in row:
                transform = np.asarray(row['transform'],dtype=np.float64)
                if transform.shape != (12,) or not np.isfinite(transform).all():
                    raise ValueError('Некорректная матрица размещения')
                gain = max(gain,float(np.linalg.norm(transform[:9].reshape(3,3),ord=2)))
        bucket = (type_id,*source['key'])
        representative, scales = prototype, np.ones(3,dtype=np.float64)
        comparisons = 0
        for anchor_id in buckets[bucket]:
            if comparisons >= _MAX_COMPARISONS_PER_PROTOTYPE or stats['comparisons'] >= _MAX_TOTAL_COMPARISONS:
                stats['comparison_limit_hits'] += 1
                break
            comparisons += 1
            stats['comparisons'] += 1
            anchor = info[anchor_id]
            if not np.array_equal(source['materials'],anchor['materials']):
                continue
            with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
                scale = np.divide(source['extent'],anchor['extent'],out=np.ones(3),where=anchor['extent']!=0)
            if not np.isfinite(scale).all() or np.any(scale<=0):
                continue
            values = anchor['vertices'].reshape(-1,8)[anchor['point_order']].astype(np.float64)
            predicted = (values[:,:3]-anchor['center'])*scale+source['center']
            error = float(np.linalg.norm(predicted-source_values[:,:3],axis=1).max()*gain)
            if error > limit+1e-12:
                continue
            normal = values[:,3:6]/scale
            normal /= np.linalg.norm(normal,axis=1,keepdims=True)
            own_normal = source_values[:,3:6]
            own_normal = own_normal/np.linalg.norm(own_normal,axis=1,keepdims=True)
            normal_error = float(np.abs(normal-own_normal).max())
            if not np.isfinite(normal_error) or normal_error>_NORMAL_TOLERANCE:
                continue
            if source['sensitive'] and np.max(np.abs(values[:,6:8]-source_values[:,6:8]))>1e-8:
                continue
            representative,scales = anchor_id,scale
            stats['max_position_error_metres'] = max(stats['max_position_error_metres'],error)
            stats['max_normal_error'] = max(stats['max_normal_error'],normal_error)
            break
        if representative == prototype:
            buckets[bucket].append(prototype)
        groups[(type_id,representative)].append((prototype,rows,scales.tolist()))
    redirects,scales_by_id = {},{}
    grouped_prototypes = set()
    for (_,anchor),members in groups.items():
        if len({prototype for prototype,_,_ in members})<2:
            continue
        stats['groups'] += 1
        for prototype,rows,scale in members:
            grouped_prototypes.add(prototype)
            for row in sorted(rows,key=lambda row:_order(row['id'])):
                redirects[str(row['id'])] = anchor
                scales_by_id[str(row['id'])] = scale.copy()
    stats['grouped_prototypes'] = len(grouped_prototypes)
    stats['grouped_occurrences'] = len(redirects)
    return dict(redirects=redirects,scales=scales_by_id,statistics=stats)
