"""Bounded, sampled surface verification for proven simple Revit families.

This is not an exact Hausdorff certificate: vertices, centroids and edge
midpoints are checked in both directions. Callers must enforce provenance,
material and family guards before considering a match.
"""
import math

MAX_TRIANGLES = 2048


def surface_matches(anchor, candidate, signs, tolerance):
    import numpy as np
    if tolerance <= 0 or max(len(anchor), len(candidate)) > MAX_TRIANGLES:
        return False
    first = np.asarray(anchor, dtype=np.float64).copy()
    second = np.asarray(candidate, dtype=np.float64)
    if not len(first) or not len(second) or not np.isfinite(first).all() or not np.isfinite(second).all():
        return False
    first[:, :, :3] *= signs
    first[:, :, 3:6] *= signs
    return _directed(first, second, tolerance) and _directed(second, first, tolerance)


def _directed(source, target, tolerance):
    import numpy as np
    points, normals = source[:, :, :3], source[:, :, 3:6]
    samples = [points.reshape(-1, 3), points.mean(axis=1)]
    directions = [normals.reshape(-1, 3), normals.mean(axis=1)]
    for left, right in ((0, 1), (1, 2), (2, 0)):
        samples.append((points[:, left] + points[:, right]) * .5)
        directions.append((normals[:, left] + normals[:, right]) * .5)
    points, normals = np.concatenate(samples), np.concatenate(directions)
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths <= 1e-15):
        return False
    normals = normals / lengths[:, None]
    triangles, vertex_normals = target[:, :, :3], target[:, :, 3:6]
    minimum, maximum = triangles.min(axis=1), triangles.max(axis=1)
    tolerance_squared = tolerance * tolerance
    angle = math.cos(math.radians(5))
    for start in range(0, len(points), 32):
        chunk = points[start:start + 32]
        mask = np.all((chunk[:, None, :] >= minimum - tolerance)
                      & (chunk[:, None, :] <= maximum + tolerance), axis=2)
        point_ids, triangle_ids = np.nonzero(mask)
        if len(set(point_ids.tolist())) != len(chunk):
            return False
        p, tri = chunk[point_ids], triangles[triangle_ids]
        ns = vertex_normals[triangle_ids]
        ab, ac, ap = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0], p - tri[:, 0]
        dot = lambda a, b: np.einsum('ij,ij->i', a, b)
        aa, bb, cc = dot(ab, ab), dot(ab, ac), dot(ac, ac)
        denominator = aa * cc - bb * bb
        valid = denominator > 1e-30
        divisor = np.where(valid, denominator, 1)
        v = (cc * dot(ap, ab) - bb * dot(ap, ac)) / divisor
        w = (aa * dot(ap, ac) - bb * dot(ap, ab)) / divisor
        u = 1 - v - w
        inside = valid & (u >= -1e-12) & (v >= -1e-12) & (w >= -1e-12)
        projected = tri[:, 0] + v[:, None] * ab + w[:, None] * ac
        distance = np.where(inside, dot(p - projected, p - projected), np.inf)
        near_normal = u[:, None] * ns[:, 0] + v[:, None] * ns[:, 1] + w[:, None] * ns[:, 2]
        for left, right in ((0, 1), (1, 2), (2, 0)):
            edge = tri[:, right] - tri[:, left]
            squared = dot(edge, edge)
            fraction = np.clip(dot(p - tri[:, left], edge) / np.where(squared > 0, squared, 1), 0, 1)
            residual = p - tri[:, left] - fraction[:, None] * edge
            current = dot(residual, residual)
            closer = current < distance
            distance = np.minimum(distance, current)
            interpolated = (1 - fraction[:, None]) * ns[:, left] + fraction[:, None] * ns[:, right]
            near_normal[closer] = interpolated[closer]
        nearest = np.full(len(chunk), np.inf)
        np.minimum.at(nearest, point_ids, distance)
        if np.any(nearest > tolerance_squared + 1e-24):
            return False
        lengths = np.linalg.norm(near_normal, axis=1)
        dots = dot(near_normal / np.maximum(lengths, 1e-30)[:, None], normals[start:start + 32][point_ids])
        # A closer perpendicular face at a hard edge must not mask the
        # corresponding oriented surface within the same distance tolerance.
        acceptable = ((distance <= tolerance_squared + 1e-24)
                      & (lengths > 1e-15) & (dots >= angle))
        matched = np.zeros(len(chunk), dtype=bool)
        np.logical_or.at(matched, point_ids, acceptable)
        if not matched.all():
            return False
    return True
