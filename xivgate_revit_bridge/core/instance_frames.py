"""Read-only local frames used for comparing translated instance geometry."""


def center_prototypes(records_by_id):
    """Return centered XYZ copies and original float64 bounding-box centers.

    The triangle record format is vertices float32[3,8], material int64. Normals,
    UVs and material IDs retain their exact bits. Empty and already centered
    arrays are returned by identity; callers must continue treating them as
    read-only. Original coordinates are never replaced in the input mapping.
    """
    import numpy as np

    centered, centers = {}, {}
    for identifier, records in records_by_id.items():
        if (records.ndim != 1 or records.dtype.names is None
                or not {'vertices', 'material'}.issubset(records.dtype.names)):
            raise ValueError('Некорректный формат треугольников прототипа')
        vertices = records['vertices']
        if (vertices.shape != (len(records), 3, 8) or vertices.dtype != np.dtype('<f4')
                or records['material'].shape != (len(records),)
                or records['material'].dtype != np.dtype('<i8')):
            raise ValueError('Некорректный формат координат или материалов прототипа')
        if not np.isfinite(vertices).all():
            raise ValueError('Геометрия прототипа содержит нечисловые значения')
        coordinates = vertices[:, :, :3]
        if len(records):
            minimum = coordinates.min(axis=(0, 1)).astype(np.float64)
            maximum = coordinates.max(axis=(0, 1)).astype(np.float64)
            center = (minimum + maximum) / 2
        else:
            center = np.zeros(3, dtype=np.float64)
        centers[identifier] = center
        if not np.any(center):
            centered[identifier] = records
            continue
        result = records.copy()
        # One axis at a time avoids a complete float64 copy of the triangle
        # buffer. Assignment rounds only XYZ back to the original float32.
        for axis in range(3):
            result['vertices'][:, :, axis] = coordinates[:, :, axis].astype(np.float64) - center[axis]
        centered[identifier] = result
    return centered, centers


def compensated_transform(original12, sourcecenter, anchorcenter, signs, scales=(1,1,1)):
    """Compose placement with [diag(signs*scales), sourcecenter-R*anchorcenter].

    The 12-number Revit transport stores three basis *columns* followed by the
    origin. Compensation is applied through that original basis, preserving
    rotation, mirrors and nonuniform scales. Return a fresh Python list.
    """
    import numpy as np

    def vector(value, length):
        try:
            result = np.asarray(value, dtype=np.float64)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError('Некорректные данные трансформации экземпляра') from error
        if result.shape != (length,) or not np.isfinite(result).all():
            raise ValueError('Некорректные данные трансформации экземпляра')
        return result

    original = vector(original12, 12)
    source = vector(sourcecenter, 3)
    anchor = vector(anchorcenter, 3)
    axes = vector(signs, 3)
    if not np.all((axes == 1) | (axes == -1)):
        raise ValueError('Знаки осей экземпляра должны быть +1 или −1')
    scale = vector(scales,3)
    if np.any(scale<=0):
        raise ValueError('Масштаб экземпляра должен быть положительным')
    axes = axes*scale
    basis = original[:9].reshape(3, 3).T
    with np.errstate(over='ignore', invalid='ignore'):
        delta = source - axes * anchor
        transformed_basis = basis * axes
        origin = original[9:] + basis @ delta
    if not np.isfinite(transformed_basis).all() or not np.isfinite(origin).all():
        raise ValueError('Компенсация трансформации выходит за числовой диапазон')
    return np.concatenate((transformed_basis.T.reshape(-1), origin)).tolist()
