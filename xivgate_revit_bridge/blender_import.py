"""Blender-only adapter; source cache conversion and job management live elsewhere."""
import math
from pathlib import Path
import uuid

import bpy
from mathutils import Matrix


OWNER = 'xivgate_rbb_import_id'
REVISION = 'xivgate_rbb_revision'
ROOT_FLAG = 'xivgate_rbb_root'


def _snapshot():
    return {kind: set(getattr(bpy.data, kind)) for kind in ('objects', 'collections', 'meshes', 'materials', 'images')}


def _remove_revision(revision):
    owned = {obj for obj in bpy.data.objects if obj.get(REVISION) == revision}
    # Preserve unrelated user objects even if parented to our previous result.
    for obj in bpy.data.objects:
        if obj not in owned and obj.parent in owned:
            world = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world
    for obj in owned:
        bpy.data.objects.remove(obj, do_unlink=True)
    collections = [item for item in bpy.data.collections if item.get(REVISION) == revision]
    for collection in reversed(collections):
        if not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
        else:
            collection.name = 'Сохранённые пользовательские объекты'
            collection.pop(ROOT_FLAG, None)
    for kind in ('meshes', 'materials', 'images'):
        for data in list(getattr(bpy.data, kind)):
            if data.get(REVISION) == revision and data.users == 0:
                getattr(bpy.data, kind).remove(data)


def import_result(usd_path, *, import_id=None, preserve_instances=True, pack_images=True, source_path=''):
    """Verify the new revision before replacing only the previous owned revision."""
    from pxr import Usd, UsdGeom, UsdShade

    source = Path(usd_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if 'support_scene_instancing' not in bpy.ops.wm.usd_import.get_rna_type().properties:
        raise RuntimeError('Эта сборка Blender не поддерживает инстансы USD')
    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError('Не удалось прочитать результат Revit')
    prims = {str(prim.GetAttribute('revit:id').Get()): prim for prim in stage.Traverse()
             if prim.IsInstance() and prim.HasAttribute('revit:id')}
    if not prims:
        raise ValueError('В результате нет размещений Revit')
    identity = import_id or uuid.uuid4().hex
    revision = uuid.uuid4().hex
    old_roots = [item for item in bpy.data.collections if item.get(OWNER) == identity and item.get(ROOT_FLAG)]
    # Duplicated/linked scenes can copy our identity and revision tags. Refuse a global replacement.
    for scene in bpy.data.scenes:
        if scene != bpy.context.scene and any(root in scene.collection.children_recursive for root in old_roots):
            raise RuntimeError('Этот импорт также используется другой сценой. Выполните новый независимый импорт перед обновлением.')
    old_revisions = {item.get(REVISION) for item in bpy.data.collections
                     if item.get(OWNER) == identity and item.get(ROOT_FLAG)}
    before = _snapshot()
    try:
        result = bpy.ops.wm.usd_import(filepath=str(source), support_scene_instancing=True,
            import_cameras=False, import_lights=False, import_materials=True, import_usd_preview=True,
            import_textures_mode='IMPORT_NONE', set_frame_range=False,
            read_mesh_uvs=True, read_mesh_attributes=True, import_visible_only=True,
            create_collection=True, mtl_name_collision_mode='MAKE_UNIQUE')
        if 'FINISHED' not in result:
            raise RuntimeError('Импорт USD не завершён')
        created = {kind: [data for data in getattr(bpy.data, kind) if data not in previous]
                   for kind, previous in before.items()}
        for values in created.values():
            for data in values:
                data[OWNER] = identity
                data[REVISION] = revision
        placements = [obj for obj in created['objects'] if obj.instance_type == 'COLLECTION' and 'revit:id' in obj]
        if len(placements) != len(prims):
            raise RuntimeError('Число импортированных элементов не совпадает с выгрузкой')
        root_candidates = [obj for obj in created['objects'] if 'revit:source_offset_metres' in obj]
        root = root_candidates[0] if len(root_candidates) == 1 else None
        collection = (root.users_collection[0] if root and root.users_collection
                      else next((item for item in created['collections'] if item.name in bpy.context.scene.collection.children), None))
        if collection is None:
            raise RuntimeError('Не найдена коллекция результата')
        collection.name = 'Revit — ' + (Path(source_path).stem if source_path else source.stem)
        collection[ROOT_FLAG] = True
        collection['xivgate_rbb_source'] = source_path
        if root:
            root.matrix_world = Matrix.Identity(4)
        cache = UsdGeom.XformCache()
        conversion = Matrix.Rotation(math.pi / 2, 4, 'X') if UsdGeom.GetStageUpAxis(stage) == 'Y' else Matrix.Identity(4)
        units = UsdGeom.GetStageMetersPerUnit(stage)
        expected = {}
        groups = {}
        for obj in placements:
            prim = prims[str(obj['revit:id'])]
            target = conversion @ Matrix(tuple(tuple(row) for row in cache.GetLocalToWorldTransform(prim))).transposed()
            target.translation *= units
            if root:
                obj.parent = root
                obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = target
            if not preserve_instances:
                members = [member for member in obj.instance_collection.all_objects if member.type == 'MESH']
                if len(members) != 1:
                    raise RuntimeError('Нельзя раскрыть составной прототип без потери структуры')
                member = members[0]
                replacement = bpy.data.objects.new(obj.name + '_mesh', member.data.copy())
                collection.objects.link(replacement)
                for key, value in obj.items():
                    replacement[key] = value
                replacement.data[OWNER] = identity
                replacement.data[REVISION] = revision
                replacement.parent = root
                replacement.matrix_world = target @ member.matrix_local
                bpy.data.objects.remove(obj, do_unlink=True)
                obj = replacement
            expected[obj] = target
            category = str(obj.get('revit:category', 'Прочее'))
            if category not in groups:
                group = bpy.data.collections.new(category)
                group[OWNER] = identity
                group[REVISION] = revision
                collection.children.link(group)
                groups[category] = group
            for previous in tuple(obj.users_collection):
                previous.objects.unlink(obj)
            groups[category].objects.link(obj)
        bpy.context.view_layer.update()
        error = max(abs(obj.matrix_world[row][column] - target[row][column]) for obj, target in expected.items()
                    for row in range(4) for column in range(4))
        if error > 0.0005:
            raise RuntimeError('Не удалось точно восстановить размещение: ' + str(error))
        missing = []
        packed = 0
        for image in created['images']:
            if image.source != 'FILE':
                continue
            image_path = Path(bpy.path.abspath(image.filepath))
            if image_path.is_file():
                if pack_images:
                    image.pack()
                    packed += 1
            else:
                missing.append(str(image_path))
        # Restore human-readable Revit material names without touching existing materials.
        for material in created['materials']:
            token = material.name
            if token.rsplit('.', 1)[-1].isdigit():
                token = token.rsplit('.', 1)[0]
            prim = stage.GetPrimAtPath('/Looks/' + token)
            if prim:
                material.name = prim.GetDisplayName() or token
                attribute = prim.GetAttribute('revit:source_material_id')
                if attribute:
                    material['revit:source_material_id'] = str(attribute.Get())
        for old in old_revisions:
            if old and old != revision:
                _remove_revision(old)
        return {'import_id': identity, 'revision': revision, 'collection': collection.name,
                'source_offset': list(stage.GetPrimAtPath('/World').GetAttribute('revit:source_offset_metres').Get()),
                'placements': len(expected), 'prototypes': len({str(prim.GetPrototype().GetPath()) for prim in prims.values()}),
                'max_transform_error': error, 'packed_images': packed, 'missing_images': missing}
    except Exception:
        # All our new objects are tagged; also cover failures before tagging completes.
        for kind in ('objects', 'collections', 'meshes', 'materials', 'images'):
            for data in list(getattr(bpy.data, kind)):
                if data not in before[kind] and (kind in ('objects', 'collections') or data.users == 0):
                    getattr(bpy.data, kind).remove(data, do_unlink=True)
        raise
