"""Scene-owned lightweight RVT bounds preview; geometry math imports without Blender."""
from collections.abc import Mapping
import math


PREVIEW_TAG = 'xivgate_rbb_preview'
SCENE_TAG = 'xivgate_rbb_preview_scene'
MESH_TAG = 'xivgate_rbb_preview_mesh'
ORANGE = (1.0, 0.28, 0.025, 1.0)
BOX_EDGES = ((0, 1), (0, 2), (1, 3), (2, 3), (4, 5), (4, 6),
             (5, 7), (6, 7), (0, 4), (1, 5), (2, 6), (3, 7))


def _vector(value):
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError('Ожидаются три числовые координаты')
    try:
        parts = tuple(value)
    except TypeError as exc:
        raise ValueError('Ожидаются три числовые координаты') from exc
    if len(parts) != 3:
        raise ValueError('Ожидаются три числовые координаты')
    if any(isinstance(part, (bool, str, bytes)) for part in parts):
        raise ValueError('Координаты должны быть числами')
    result = tuple(float(part) for part in parts)
    if not all(math.isfinite(part) for part in result):
        raise ValueError('Координаты должны быть конечными')
    return result


def bbox_wire_data(rows, source_offset=(0, 0, 0), limit=1000):
    """Return (vertices, edges, counts) in metres, capped by valid displayed boxes."""
    offset = _vector(source_offset)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('Лимит предпросмотра должен быть неотрицательным целым числом')
    vertices, edges = [], []
    total = shown = 0
    for row in rows:
        total += 1
        if shown >= limit or not isinstance(row, Mapping):
            continue
        try:
            low, high = _vector(row.get('bbox_min')), _vector(row.get('bbox_max'))
            if any(low[axis] > high[axis] for axis in range(3)):
                continue
            corners = [tuple((high[axis] if corner & (1 << axis) else low[axis]) - offset[axis]
                             for axis in range(3)) for corner in range(8)]
            # Blender stores mesh coordinates in float32, unlike Python's float64.
            if not all(math.isfinite(value) and abs(value) <= 3.402823466e38 for point in corners for value in point):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        start = len(vertices)
        vertices.extend(corners)
        edges.extend((start + a, start + b) for a, b in BOX_EDGES)
        shown += 1
    return vertices, edges, {'shown': shown, 'total': total, 'skipped': total - shown}


def _collections(scene):
    result, pending = set(), [scene.collection]
    while pending:
        collection = pending.pop()
        if collection in result:
            continue
        result.add(collection)
        pending.extend(collection.children)
    return result


def _clear_preview(scene, keep=None):
    import bpy
    own_collections = _collections(scene)
    other_collections = set()
    for other in bpy.data.scenes:
        if other != scene:
            other_collections.update(_collections(other))
    result = {'removed': 0, 'unlinked': 0, 'retained_shared': 0}
    for obj in tuple(scene.objects):
        if obj == keep or obj.get(PREVIEW_TAG) is not True or obj.get(SCENE_TAG) != scene:
            continue
        mesh = obj.data if obj.type == 'MESH' else None
        for collection in tuple(obj.users_collection):
            if collection in own_collections and collection not in other_collections:
                collection.objects.unlink(obj)
                result['unlinked'] += 1
        if obj.users == 0:
            bpy.data.objects.remove(obj)
            result['removed'] += 1
            if mesh is not None and mesh.users == 0 and mesh.get(MESH_TAG) is True:
                bpy.data.meshes.remove(mesh)
        else:
            # Shared collections/objects belong to another scene too; leave those links intact.
            result['retained_shared'] += 1
    return result


def clear_preview(scene):
    """Unlink only this scene's previews and delete only their unused owned meshes."""
    return _clear_preview(scene)


def preview_bounds(scene, rows, source_offset=(0, 0, 0), limit=1000):
    """Create one orange edge mesh without selection, camera, viewport or import changes."""
    import bpy
    vertices, edges, result = bbox_wire_data(rows, source_offset, limit)
    result['object_name'] = ''
    if not vertices:
        result.update(clear_preview(scene))
        return result
    mesh = bpy.data.meshes.new('AGR_Revit_Границы')
    obj = None
    try:
        mesh.from_pydata(vertices, edges, [])
        mesh.update()
        mesh[MESH_TAG] = True
        obj = bpy.data.objects.new('AGR_Revit_Предпросмотр', mesh)
        obj[PREVIEW_TAG] = True
        obj[SCENE_TAG] = scene
        obj.color = ORANGE
        obj.display_type = 'WIRE'
        obj.show_wire = True
        obj.show_in_front = True
        obj.hide_render = True
        scene.collection.objects.link(obj)
        result.update(_clear_preview(scene, keep=obj))
        result['object_name'] = obj.name
        return result
    except Exception:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        raise
