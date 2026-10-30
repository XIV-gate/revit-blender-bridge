"""Blender-only adapter; source cache conversion and job management live elsewhere."""
import math
import json
from pathlib import Path
import re
import uuid

import bpy
from mathutils import Matrix


OWNER = 'xivgate_rbb_import_id'
REVISION = 'xivgate_rbb_revision'
ROOT_FLAG = 'xivgate_rbb_root'
SELECTION = 'xivgate_rbb_selected_element_ids'
GEOMETRY_IDS = 'xivgate_rbb_geometry_element_ids'
CATEGORY = 'xivgate_rbb_category'
AVAILABLE_IDS = 'xivgate_rbb_available_element_ids'
AVAILABLE_GEOMETRY_IDS = 'xivgate_rbb_available_geometry_ids'
STASH_FLAG = 'xivgate_rbb_stash'


def _base_name(name):
    return re.sub(r'(?:\.\d+)+$', '', name)


def _preferred_name(previous, canonical):
    # Repair legacy generated .001 suffixes, while retaining explicit user names.
    return canonical if not previous or re.fullmatch(re.escape(canonical) + r'(?:\.\d+)*', previous) else previous


def _stored_ids(root, key, fallback):
    if key not in root:
        return fallback
    values = json.loads(root[key])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Повреждён сохранённый набор ID импорта')
    return set(values)


def _element_id(obj):
    return str(obj.get('revit:element_id', obj.get('revit:id', '')))


def _source_ids(obj):
    raw = obj.get('revit:source_element_ids')
    if raw is None:
        return {_element_id(obj)}
    values = json.loads(str(raw))
    if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
        raise ValueError('Некорректные исходные ID составного размещения')
    return set(values)


def _selection_parent_ids(obj):
    raw = obj.get('revit:selection_parent_ids')
    if raw is None:
        parent = str(obj.get('revit:parent_id', ''))
        return {parent} if parent else set()
    values = json.loads(str(raw))
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Некорректные ID родительских элементов Revit')
    return set(values)


def _detach_guests(owned):
    for obj in bpy.data.objects:
        if obj not in owned and obj.parent in owned:
            world = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world


def _restore_names(created, overrides):
    planned = {}
    for kind, values in created.items():
        count = {}
        for data in sorted(values, key=lambda item: item.name):
            base = _base_name(data.name)
            number = count.get(base, 0)
            count[base] = number + 1
            planned[data] = overrides.get(data, base if not number else base + '.' + str(number).zfill(3))
    # Release only our new names before assigning canonical names. Foreign IDs
    # remain untouched; Blender itself keeps any genuine foreign-name collision.
    token = uuid.uuid4().hex
    for number, data in enumerate(planned):
        data.name = '_agr_' + token + '_' + str(number)
    for data, desired in planned.items():
        data.name = desired


def apply_selection(import_id, selected_element_ids, *, stash_deselected=False):
    """Remove a subset, or opt in to reversible toggles of already loaded objects."""
    selected = {str(value) for value in selected_element_ids}
    roots = [collection for collection in bpy.data.collections
             if collection.get(OWNER) == import_id and collection.get(ROOT_FLAG)]
    if len(roots) != 1:
        return None
    root = roots[0]
    if root not in bpy.context.scene.collection.children_recursive:
        return None
    if any(scene != bpy.context.scene and root in scene.collection.children_recursive for scene in bpy.data.scenes):
        return None
    revision = root.get(REVISION)
    placements = [obj for obj in root.all_objects if obj.get(OWNER) == import_id
                  and obj.get(REVISION) == revision and 'revit:id' in obj]
    placement_set = set(placements)
    represented = {_element_id(obj) for obj in placements}
    storage = None
    stocked = []
    if stash_deselected:
        storages = [item for item in bpy.data.collections
                    if item.get(STASH_FLAG) and item.get(OWNER) == import_id and item.get(REVISION) == revision]
        if len(storages) > 1:
            return None
        storage = storages[0] if storages else None
        if storage is not None:
            if storage.children or any(storage in scene.collection.children_recursive for scene in bpy.data.scenes):
                return None
            stocked = list(storage.objects)
            if any(obj.get(OWNER) != import_id or obj.get(REVISION) != revision or 'revit:id' not in obj
                   or any(obj.name in scene.objects for scene in bpy.data.scenes) for obj in stocked):
                return None
    stocked_set = set(stocked)
    try:
        previous = _stored_ids(root, SELECTION, represented)
        expected = _stored_ids(root, GEOMETRY_IDS, represented)
        available = _stored_ids(root, AVAILABLE_IDS, previous) if stash_deselected else previous
        all_geometry = _stored_ids(root, AVAILABLE_GEOMETRY_IDS, represented) if stash_deselected else represented
        if (not selected.issubset(available) or represented != expected
                or (stash_deselected and represented | {_element_id(obj) for obj in stocked} != all_geometry)):
            return None
        dropped = set()
        restored = []
        if selected != previous:
            for obj in placements + stocked:
                if _element_id(obj) not in selected:
                    if _source_ids(obj) & selected:
                        return None  # Part of a composite remains: a fresh export must split it.
                    if _selection_parent_ids(obj) & selected:
                        return None  # Its selected parent must regain the child's geometry.
                    if obj in placement_set:
                        dropped.add(obj)
                elif obj in stocked_set:
                    restored.append(obj)
    except (ValueError, TypeError, KeyError):
        return None
    if any(obj.name in scene.objects for obj in dropped for scene in bpy.data.scenes if scene != bpy.context.scene):
        return None
    # Do not reinterpret an unknown owned child hierarchy as a flat placement.
    if any(child.get(OWNER) == import_id and child.get(REVISION) == revision and child not in dropped
           for obj in dropped for child in obj.children):
        return None
    destinations = {}
    for obj in restored:
        category = str(obj.get('revit:category', 'Прочее'))
        matches = [item for item in root.children if item.get(CATEGORY) == category]
        if len(matches) != 1:
            return None
        destinations[obj] = matches[0]
    _detach_guests(dropped)
    remaining = [obj for obj in placements if obj not in dropped]
    if dropped and stash_deselected:
        if storage is None:
            storage = bpy.data.collections.new('_AGR retained — ' + import_id[:8])
            storage[OWNER] = import_id
            storage[REVISION] = revision
            storage[STASH_FLAG] = True
            storage.use_fake_user = True
        for obj in dropped:
            for collection in tuple(obj.users_collection):
                collection.objects.unlink(obj)
            storage.objects.link(obj)
    elif dropped:
        bpy.data.batch_remove(ids=tuple(dropped))
    for obj in restored:
        storage.objects.unlink(obj)
        destinations[obj].objects.link(obj)
        remaining.append(obj)
    if restored:
        bpy.context.view_layer.update()
    root[SELECTION] = json.dumps(sorted(selected))
    root[GEOMETRY_IDS] = json.dumps(sorted({_element_id(obj) for obj in remaining}))
    if not stash_deselected:
        root[AVAILABLE_IDS] = root[SELECTION]
        root[AVAILABLE_GEOMETRY_IDS] = root[GEOMETRY_IDS]
    return {'import_id': import_id, 'revision': revision, 'collection': root.name,
            'source_offset': list(root.get('xivgate_rbb_source_offset', (0, 0, 0))),
            'placements': len(remaining), 'removed': len(dropped), 'restored': len(restored), 'incremental': True,
            'prototypes': len({obj.data for obj in remaining if obj.type == 'MESH'}),
            'max_transform_error': 0.0, 'packed_images': 0, 'missing_images': []}


def _snapshot():
    return {kind: set(getattr(bpy.data, kind)) for kind in ('objects', 'collections', 'meshes', 'materials', 'images')}


def _remove_revision(revision):
    owned = {obj for obj in bpy.data.objects if obj.get(REVISION) == revision}
    # Preserve unrelated user objects even if parented to our previous result.
    _detach_guests(owned)
    # Individual ID removal rescans relations for every object and takes minutes
    # for a real facade. Batch only the explicitly owned IDs after detaching guests.
    if owned:
        bpy.data.batch_remove(ids=tuple(owned))
    collections = [item for item in bpy.data.collections if item.get(REVISION) == revision]
    # Prune leaves first; alphabetical datablock order is not hierarchy order.
    while collections:
        empty = {item for item in collections if not item.objects and not item.children}
        if not empty:
            break
        collections = [item for item in collections if item not in empty]
        bpy.data.batch_remove(ids=tuple(empty))
    for collection in collections:
        collection.name = 'Сохранённые пользовательские объекты'
        collection.pop(ROOT_FLAG, None)
    for kind in ('meshes', 'materials', 'images'):
        orphaned = [data for data in getattr(bpy.data, kind) if data.get(REVISION) == revision and data.users == 0]
        if orphaned:
            bpy.data.batch_remove(ids=tuple(orphaned))


def import_result(usd_path, *, import_id=None, preserve_instances=True, pack_images=True, source_path='', selected_element_ids=None):
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
    old_owned_objects = [obj for obj in bpy.data.objects if obj.get(OWNER) == identity and obj.get(REVISION) in old_revisions]
    if any(obj.name in scene.objects for scene in bpy.data.scenes if scene != bpy.context.scene for obj in old_owned_objects):
        raise RuntimeError('Объекты этого импорта также используются другой сценой. Выполните независимый импорт перед обновлением.')
    old_objects = {str(obj['revit:id']): obj.name for obj in bpy.data.objects
                   if obj.get(OWNER) == identity and obj.get(REVISION) in old_revisions and 'revit:id' in obj}
    old_categories = {}
    for item in bpy.data.collections:
        if item.get(OWNER) == identity and item.get(REVISION) in old_revisions:
            category = item.get(CATEGORY) or next((str(obj['revit:category']) for obj in item.objects if 'revit:category' in obj), '')
            if category:
                old_categories[str(category)] = item.name
    old_materials = {str(item['revit:source_material_id']): item.name for item in bpy.data.materials
                     if item.get(OWNER) == identity and item.get(REVISION) in old_revisions and 'revit:source_material_id' in item}
    before = _snapshot()
    desired_names = {}
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
        root_name = 'Revit — ' + (Path(source_path).stem if source_path else source.stem)
        collection.name = root_name
        desired_names[collection] = _preferred_name(old_roots[0].name if len(old_roots) == 1 else '', root_name)
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
            element = prim.GetAttribute('revit:element_id')
            obj['revit:element_id'] = str(element.Get()) if element and element.Get() is not None else str(obj['revit:id'])
            sources = prim.GetAttribute('revit:source_element_ids')
            if sources and sources.Get() is not None:
                obj['revit:source_element_ids'] = str(sources.Get())
            for property_name in ('revit:parent_id', 'revit:selection_parent_ids'):
                attribute = prim.GetAttribute(property_name)
                if attribute and attribute.Get() is not None:
                    obj[property_name] = str(attribute.Get())
            placement_name = str(prim.GetName()) + ('' if preserve_instances else '_mesh')
            previous_name = old_objects.get(str(obj['revit:id']), '')
            if not preserve_instances and previous_name:
                generated = re.fullmatch(re.escape(str(prim.GetName())) + r'(?:\.\d+)*_mesh(?:\.\d+)*', previous_name)
                if generated:
                    previous_name = placement_name
            target = conversion @ Matrix(tuple(tuple(row) for row in cache.GetLocalToWorldTransform(prim))).transposed()
            target.translation *= units
            if root:
                obj.parent = root
                obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = target
            members = [member for member in obj.instance_collection.all_objects if member.type == 'MESH']
            if len(members) != 1:
                raise RuntimeError('Нельзя раскрыть составной прототип без потери структуры')
            member = members[0]
            # Linked meshes behave like Alt+D: select visible geometry and edit
            # its shared mesh directly. No collection-instance Empty per window.
            data = member.data if preserve_instances else member.data.copy()
            replacement = bpy.data.objects.new(obj.name + '_mesh', data)
            collection.objects.link(replacement)
            for key, value in obj.items():
                replacement[key] = value
            replacement.data[OWNER] = identity
            replacement.data[REVISION] = revision
            mesh_prim = prim.GetChild('mesh')
            geometry_signature = mesh_prim.GetAttribute('revit:geometry_signature') if mesh_prim else None
            signature = geometry_signature.Get() if geometry_signature else None
            if isinstance(signature, str) and re.fullmatch(r'[0-9a-f]{64}', signature):
                replacement.data['revit:geometry_signature'] = signature
            replacement.parent = root
            target = target @ member.matrix_local
            replacement.matrix_world = target
            obj = replacement
            desired_names[obj] = _preferred_name(previous_name, placement_name)
            expected[obj] = target
            category = str(obj.get('revit:category', 'Прочее'))
            if category not in groups:
                group = bpy.data.collections.new(category)
                group[OWNER] = identity
                group[REVISION] = revision
                group[CATEGORY] = category
                collection.children.link(group)
                groups[category] = group
                desired_names[group] = _preferred_name(old_categories.get(category, ''), category)
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
                canonical_material_name = prim.GetDisplayName() or token
                attribute = prim.GetAttribute('revit:source_material_id')
                if attribute:
                    material['revit:source_material_id'] = str(attribute.Get())
                signature_attribute = prim.GetAttribute('revit:material_signature')
                signature = signature_attribute.Get() if signature_attribute else None
                if isinstance(signature, str) and re.fullmatch(r'[0-9a-f]{64}', signature):
                    material['revit:material_signature'] = signature
                desired_names[material] = _preferred_name(old_materials.get(str(material.get('revit:source_material_id', '')), ''), canonical_material_name)
        # Collection instancing is only the USD transport. Retain its shared mesh
        # data, but remove generated prototype objects and helper collections.
        helpers = [obj for obj in bpy.data.objects if obj not in before['objects'] and obj not in expected and obj != root]
        if helpers:
            bpy.data.batch_remove(ids=tuple(helpers))
        obsolete = [item for item in bpy.data.collections if item not in before['collections']
                    and item != collection and item not in groups.values()
                    and not any(obj.get(OWNER) != identity or obj.get(REVISION) != revision for obj in item.all_objects)]
        if obsolete:
            bpy.data.batch_remove(ids=tuple(obsolete))
        for kind in ('meshes', 'materials', 'images'):
            unused = [data for data in getattr(bpy.data, kind) if data not in before[kind] and data.users == 0]
            if unused:
                bpy.data.batch_remove(ids=tuple(unused))
        remaining_placements = [obj for obj in expected]
        collection[SELECTION] = json.dumps(sorted({str(value) for value in selected_element_ids}
                                                  if selected_element_ids is not None else {_element_id(obj) for obj in remaining_placements}))
        collection[GEOMETRY_IDS] = json.dumps(sorted({_element_id(obj) for obj in remaining_placements}))
        collection[AVAILABLE_IDS] = collection[SELECTION]
        collection[AVAILABLE_GEOMETRY_IDS] = collection[GEOMETRY_IDS]
        collection['xivgate_rbb_source_offset'] = list(stage.GetPrimAtPath('/World').GetAttribute('revit:source_offset_metres').Get())
        final_created = {kind: [data for data in getattr(bpy.data, kind) if data not in before[kind]] for kind in before}
        for old in old_revisions:
            if old and old != revision:
                _remove_revision(old)
        _restore_names(final_created, desired_names)
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


class _MergeDeclined(Exception):
    pass


def _discard_delta(before, keep_objects=(), keep_collections=()):
    keep_objects, keep_collections = set(keep_objects), set(keep_collections)
    objects = [obj for obj in bpy.data.objects if obj not in before['objects'] and obj not in keep_objects]
    if objects:
        bpy.data.batch_remove(ids=tuple(objects))
    collections = [item for item in bpy.data.collections
                   if item not in before['collections'] and item not in keep_collections]
    while collections:
        empty = {item for item in collections if not item.objects and not item.children}
        if not empty:
            break
        collections = [item for item in collections if item not in empty]
        bpy.data.batch_remove(ids=tuple(empty))
    for kind in ('meshes', 'materials', 'images'):
        unused = [data for data in getattr(bpy.data, kind) if data not in before[kind] and data.users == 0]
        if unused:
            bpy.data.batch_remove(ids=tuple(unused))


def _signature_index(values, key, identity, revision):
    result = {}
    for value in sorted(values, key=lambda item: (-item.users, item.name)):
        signature = value.get(key)
        if (value.get(OWNER) == identity and value.get(REVISION) == revision
                and isinstance(signature, str) and re.fullmatch(r'[0-9a-f]{64}', signature)):
            result.setdefault(signature, value)
    return result


def merge_result(usd_path, *, import_id, selected_element_ids, final_selection_ids,
                 preserve_instances=True, pack_images=True, source_path=''):
    """Stage and verify only genuinely new elements, then extend one owned import.

    Return None for ambiguous geometry or ownership so the controller can request
    a full export. Existing IDs, mesh edits, category collections, root transform,
    foreign objects and the user's active/selected objects are retained.
    """
    added = {str(value) for value in selected_element_ids}
    final = {str(value) for value in final_selection_ids}
    roots = [item for item in bpy.data.collections if item.get(OWNER) == import_id and item.get(ROOT_FLAG)]
    if len(roots) != 1 or not added or not added.issubset(final):
        return None
    root = roots[0]
    if root not in bpy.context.scene.collection.children_recursive:
        return None
    revision = root.get(REVISION)
    old_owned = [obj for obj in bpy.data.objects if obj.get(OWNER) == import_id and obj.get(REVISION) == revision]
    old_owned_set = set(old_owned)
    if any(obj.name in scene.objects for scene in bpy.data.scenes if scene != bpy.context.scene for obj in old_owned):
        return None
    if any(scene != bpy.context.scene and root in scene.collection.children_recursive for scene in bpy.data.scenes):
        return None
    visible = [obj for obj in root.all_objects if obj in old_owned_set and 'revit:id' in obj]
    all_old = [obj for obj in old_owned if 'revit:id' in obj]
    old_occurrences = {str(obj['revit:id']) for obj in all_old}
    root_objects = [obj for obj in root.all_objects if obj in old_owned_set and 'revit:source_offset_metres' in obj]
    if len(root_objects) != 1 or len(old_occurrences) != len(all_old):
        return None
    parent = root_objects[0]
    try:
        current = _stored_ids(root, SELECTION, {_element_id(obj) for obj in visible})
        available = _stored_ids(root, AVAILABLE_IDS, current)
        geometry = _stored_ids(root, GEOMETRY_IDS, {_element_id(obj) for obj in visible})
        available_geometry = _stored_ids(root, AVAILABLE_GEOMETRY_IDS, geometry)
        if (added & available or not final.issubset(available | added)
                or geometry != {_element_id(obj) for obj in visible}
                or available_geometry != {_element_id(obj) for obj in all_old}):
            return None
        for obj in all_old:
            if _element_id(obj) not in final and (_source_ids(obj) & final or _selection_parent_ids(obj) & final):
                return None
        old_sources = set().union(*(_source_ids(obj) for obj in all_old)) if all_old else set()
    except (ValueError, KeyError, TypeError):
        return None
    before = _snapshot()
    selected_objects = tuple(bpy.context.selected_objects)
    active_object = bpy.context.view_layer.objects.active
    metadata_keys = (SELECTION, GEOMETRY_IDS, AVAILABLE_IDS, AVAILABLE_GEOMETRY_IDS)
    original_metadata = {key: root.get(key) for key in metadata_keys}
    guarded_objects = set(old_owned)
    guarded_objects.update(obj for obj in bpy.data.objects if obj.parent in guarded_objects)
    original_objects = {obj: (tuple(obj.users_collection), obj.parent, obj.matrix_parent_inverse.copy(), obj.matrix_basis.copy())
                        for obj in guarded_objects}
    changed_old = False
    try:
        staged = import_result(usd_path, preserve_instances=preserve_instances, pack_images=pack_images,
                               source_path=source_path, selected_element_ids=added)
        fresh = next(item for item in bpy.data.collections if item.get(OWNER) == staged['import_id'] and item.get(ROOT_FLAG))
        fresh_objects = [obj for obj in fresh.all_objects if obj.get(OWNER) == staged['import_id'] and 'revit:id' in obj]
        if any(obj.type != 'MESH' or _element_id(obj) not in added or str(obj['revit:id']) in old_occurrences
               for obj in fresh_objects):
            raise _MergeDeclined()
        if any(_source_ids(obj) & old_sources or _selection_parent_ids(obj) & (final & available) for obj in fresh_objects):
            raise _MergeDeclined()
        fresh_element_ids = {_element_id(obj) for obj in fresh_objects}
        if any(_selection_parent_ids(obj) & fresh_element_ids for obj in all_old):
            raise _MergeDeclined()
        offset = list(root.get('xivgate_rbb_source_offset', ()))
        if len(offset) != 3 or max(abs(a - b) for a, b in zip(offset, staged['source_offset'])) > 0.000001:
            raise _MergeDeclined()
        root_transform = parent.matrix_world.copy()
        targets = {obj: root_transform @ obj.matrix_world.copy() for obj in fresh_objects}
        categories = {}
        for group in root.children:
            category = group.get(CATEGORY)
            if group.get(OWNER) == import_id and group.get(REVISION) == revision and category:
                if category in categories:
                    raise _MergeDeclined()
                categories[str(category)] = group
        destinations = {}
        for obj in fresh_objects:
            category = str(obj.get('revit:category', 'Прочее'))
            if category not in categories:
                candidates = [item for item in fresh.children if item.get(CATEGORY) == category]
                if len(candidates) != 1:
                    raise _MergeDeclined()
                categories[category] = candidates[0]
            destinations[obj] = categories[category]
        mesh_index = _signature_index(before['meshes'], 'revit:geometry_signature', import_id, revision)
        material_index = _signature_index(before['materials'], 'revit:material_signature', import_id, revision)
        reused_meshes = 0
        reused_materials = 0
        for obj in fresh_objects:
            signature = obj.data.get('revit:geometry_signature')
            if preserve_instances and signature in mesh_index:
                obj.data = mesh_index[signature]
                reused_meshes += 1
        for mesh in {obj.data for obj in fresh_objects if obj.data not in before['meshes']}:
            for index, material in enumerate(mesh.materials):
                signature = material.get('revit:material_signature') if material else None
                if signature in material_index:
                    mesh.materials[index] = material_index[signature]
                    reused_materials += 1
        for group in set(destinations.values()):
            if group not in before['collections']:
                fresh.children.unlink(group)
                root.children.link(group)
        for obj in fresh_objects:
            destination = destinations[obj]
            for previous in tuple(obj.users_collection):
                if previous != destination:
                    previous.objects.unlink(obj)
            if obj.name not in destination.objects:
                destination.objects.link(obj)
            obj.parent = parent
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = targets[obj]
        for kind in before:
            for data in getattr(bpy.data, kind):
                if data not in before[kind]:
                    data[OWNER] = import_id
                    data[REVISION] = revision
        _discard_delta(before, keep_objects=fresh_objects, keep_collections=destinations.values())
        bpy.context.view_layer.update()
        error = max((abs(obj.matrix_world[row][column] - target[row][column])
                     for obj, target in targets.items() for row in range(4) for column in range(4)), default=0.0)
        if error > 0.0005:
            raise _MergeDeclined()
        changed_old = True
        new_geometry = {_element_id(obj) for obj in fresh_objects}
        root[SELECTION] = json.dumps(sorted(current | added))
        root[AVAILABLE_IDS] = json.dumps(sorted(available | added))
        root[GEOMETRY_IDS] = json.dumps(sorted(geometry | new_geometry))
        root[AVAILABLE_GEOMETRY_IDS] = json.dumps(sorted(available_geometry | new_geometry))
        result = apply_selection(import_id, final, stash_deselected=True)
        if result is None:
            raise _MergeDeclined()
        result.update(added=len(fresh_objects), reused_meshes=reused_meshes, reused_materials=reused_materials,
                      packed_images=staged['packed_images'], missing_images=staged['missing_images'], max_transform_error=error)
        return result
    except Exception:
        if changed_old:
            for obj, (links, old_parent, inverse, basis) in original_objects.items():
                for collection in tuple(obj.users_collection):
                    if collection not in links:
                        collection.objects.unlink(obj)
                for collection in links:
                    if obj.name not in collection.objects:
                        collection.objects.link(obj)
                obj.parent = old_parent
                obj.matrix_parent_inverse = inverse
                obj.matrix_basis = basis
            for key, value in original_metadata.items():
                if value is None:
                    root.pop(key, None)
                else:
                    root[key] = value
            bpy.context.view_layer.update()
        _discard_delta(before)
        return None
    finally:
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        for obj in selected_objects:
            if obj.name in bpy.context.view_layer.objects:
                obj.select_set(True)
        if active_object is not None and active_object.name in bpy.context.view_layer.objects:
            bpy.context.view_layer.objects.active = active_object
        else:
            bpy.context.view_layer.objects.active = None
