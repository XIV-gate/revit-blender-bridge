"""Build a compact USD handoff with Blender's bundled USD/Python runtime."""
import json
import hashlib
from pathlib import Path

from .geometry_cache import matrix_rows, safe_asset_path, validate_prototype
from .core.texture_roles import height_map_descriptor


def srgb(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def build_usd(manifest_path, target_path, *, include_materials=True, include_textures=True, center_model=True, source_offset=None,
              match_instances=True, instance_tolerance_mm=0.1, use_revit_instances=True,
              filter_remote_geometry=True, selected_element_ids=None, import_mode='INSTANCES', progress_callback=None):
    import numpy as np
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt

    import_mode = str(import_mode).upper()
    if import_mode not in ('OBJECTS', 'INSTANCES', 'FAMILY'):
        raise ValueError('Неизвестный режим импорта')
    if center_model and source_offset is not None:
        try:
            source_offset = np.asarray(source_offset, dtype=np.float64)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError('Некорректное смещение модели') from error
        if source_offset.shape != (3,) or not np.isfinite(source_offset).all():
            raise ValueError('Некорректное смещение модели')
    def progress(message, factor, stats=None):
        if progress_callback is not None:
            progress_callback(dict(message=message, progress=factor, stats=stats or {}))

    source = Path(manifest_path).resolve()
    target = Path(target_path).resolve()
    if target.exists():
        raise FileExistsError(target)
    if not target.parent.is_dir():
        raise FileNotFoundError(target.parent)
    manifest = json.loads(source.read_text(encoding='utf-8-sig'))
    if manifest.get('schema') != 1 or manifest.get('units') != 'metres':
        raise ValueError('Неподдерживаемая схема геометрии')
    occurrences = manifest['occurrences']
    if selected_element_ids is not None:
        from .core.export_cache import snapshot_subset_compatible
        previous = set(map(str, selected_element_ids)) | {str(row['id']) for row in occurrences}
        if not snapshot_subset_compatible(manifest, selected_element_ids, previous):
            raise ValueError('Выбор требует новой выгрузки зависимых элементов из Revit')
        selected = set(selected_element_ids)
        occurrences = [row for row in occurrences if str(row['id']) in selected]
    if not occurrences:
        raise ValueError('В выбранных элементах нет геометрии')
    ids = [str(item['id']) for item in occurrences]
    original_id_set = set(ids)
    if len(ids) != len(original_id_set):
        raise ValueError('Повторяющиеся ID размещений')
    prototypes = {int(item['id']): item for item in manifest['prototypes']}
    for item in occurrences:
        matrix_rows(item['transform'])
        if int(item['prototype']) not in prototypes:
            raise ValueError('Не найден прототип элемента ' + str(item['id']))
    dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
    binary_name = manifest.get('binary_file', 'geometry.bin')
    binary_path = safe_asset_path(source.parent, binary_name)
    # Read-only mapped views keep all source variants available for re-grouping
    # without another Revit export or copying the complete binary into RAM.
    mapped = np.memmap(binary_path, dtype='u1', mode='r')
    records_by_id = {}
    for key, prototype in prototypes.items():
        start, size = int(prototype['offset']), int(prototype['bytes'])
        if start < 0 or size < 0 or start + size > len(mapped):
            raise ValueError('Прототип находится вне буфера геометрии')
        data = memoryview(mapped)[start:start + size]
        validate_prototype(prototype, data)
        records_by_id[key] = np.frombuffer(data, dtype=dtype)
    remote_report = {}
    if filter_remote_geometry:
        from .core.remote_components import filter_remote_components
        records_by_id, remote_report = filter_remote_components(occurrences, records_by_id)
    source_placements = len(occurrences)
    family_plan = None
    grouping_statistics = {}
    if import_mode == 'FAMILY':
        from .core.family_geometry import plan_family_geometry
        unique_bytes = sum(record.nbytes for record in records_by_id.values())
        expanded_estimate = sum(len(records_by_id[int(item['prototype'])]) for item in occurrences)
        # Small source meshes can have many owners: include planning/provenance
        # metadata as well as expanded USD/Blender arrays in the safety budget.
        estimated = expanded_estimate * 512 + unique_bytes * 2 + source_placements * 4096
        from .core.resources import memory_snapshot
        available = (memory_snapshot().get('ram') or {}).get('available_bytes')
        progress('Проверка геометрии семейств…', .05,
                 {'source_placements': source_placements, 'estimated_total_bytes': estimated,
                  'available_ram_bytes': available})
        if type(available) is int and available >= 0 and estimated > available * .75:
            raise ValueError('Для объединения семейств недостаточно свободной памяти: оценка '
                             f'{estimated / 1024**3:.1f} ГБ, доступно {available / 1024**3:.1f} ГБ. '
                             'Используйте режим инстансов или уменьшите выбор.')
        family_plan = plan_family_geometry(occurrences, records_by_id)
        grouping_statistics = dict(family_plan.statistics)
        grouping_statistics['estimated_total_bytes'] = estimated
        grouping_statistics['available_ram_bytes'] = available
        progress('План семейств готов: ' + str(grouping_statistics['expanded_triangles']) + ' треугольников',
                 .2, grouping_statistics)
        occurrences = family_plan.occurrences
        required = {int(item['prototype']) for item in occurrences}
        instance_statistics = {}
    elif import_mode != 'OBJECTS' and match_instances:
        from .core.instance_matching import plan_instances
        comparison_records = records_by_id
        centers = None
        if use_revit_instances:
            from .core.instance_frames import center_prototypes, compensated_transform
            comparison_records, centers = center_prototypes(records_by_id)
        instance_plan = plan_instances(occurrences, comparison_records, manifest['materials'],
            tolerance_mm=instance_tolerance_mm, use_revit=use_revit_instances)
        affine_plan = {'redirects': {}, 'scales': {}, 'statistics': {}}
        if use_revit_instances:
            from .core.affine_instances import plan_affine_instances
            affine_plan = plan_affine_instances(occurrences, records_by_id, manifest['materials'],
                tolerance_mm=instance_tolerance_mm)
        adjusted = []
        for item in occurrences:
            identifier = str(item['id'])
            signs = instance_plan.get('adjustments', {}).get(identifier, (1, 1, 1))
            anchor = instance_plan['redirects'][identifier]
            scales = (1, 1, 1)
            if identifier in affine_plan['redirects']:
                anchor = affine_plan['redirects'][identifier]
                scales = affine_plan['scales'][identifier]
                signs = (1, 1, 1)
            if centers is not None:
                # Compare centered geometry, but write the ORIGINAL anchor mesh.
                # Restore its source pivot through this placement compensation.
                transform = compensated_transform(item['transform'], centers[int(item['prototype'])], centers[int(anchor)], signs, scales=scales,
                    rotation=affine_plan.get('rotations',{}).get(identifier))
            else:
                transform = list(item['transform'])
                for axis, sign in enumerate(signs):
                    transform[axis * 3:axis * 3 + 3] = [sign * value for value in transform[axis * 3:axis * 3 + 3]]
            adjusted.append(dict(item, prototype=anchor, transform=transform))
        occurrences = adjusted
        required = {int(item['prototype']) for item in occurrences}
        instance_statistics = instance_plan['statistics']
        instance_statistics['legacy_representatives'] = instance_statistics['representatives']
        instance_statistics['representatives'] = len(required)
        instance_statistics['affine'] = affine_plan['statistics']
    else:
        required = {int(item['prototype']) for item in occurrences}
        instance_statistics = {}
    material_signatures = {int(key): hashlib.sha256(json.dumps(
        {field: value for field, value in material.items() if field != 'id'},
        sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
        for key, material in manifest['materials'].items()}
    if family_plan is not None:
        source_prototypes = {int(item['prototype']) for item in manifest['occurrences']
                             if str(item['id']) in original_id_set}
        if any(int(material) not in material_signatures for key in source_prototypes
               for material in np.unique(records_by_id[key]['material'])):
            raise ValueError('Не найден материал геометрии семейства')
    # Write only a fresh output; an incomplete stage is never a successful response.
    stage = Usd.Stage.CreateNew(str(target))
    world = UsdGeom.Xform.Define(stage, '/World')
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.Xform.Define(stage, '/Prototypes').CreateVisibilityAttr('invisible')
    materials = {}
    missing = []
    if include_materials:
        for key, material in manifest['materials'].items():
            token = 'm_' + str(key).replace('-', 'n')
            usd_material = UsdShade.Material.Define(stage, '/Looks/' + token)
            usd_material.GetPrim().SetDisplayName(material['name'])
            usd_material.GetPrim().CreateAttribute('revit:source_material_id', Sdf.ValueTypeNames.String, custom=True).Set(str(material.get('source_material_id', key)))
            usd_material.GetPrim().CreateAttribute('revit:material_signature', Sdf.ValueTypeNames.String, custom=True).Set(material_signatures[int(key)])
            height = height_map_descriptor(material) if include_textures else None
            if height and height['active']:
                if safe_asset_path(source.parent, height['path']).is_file():
                    usd_material.GetPrim().CreateAttribute('revit:height_texture', Sdf.ValueTypeNames.Asset, custom=True).Set(Sdf.AssetPath(height['path']))
                    usd_material.GetPrim().CreateAttribute('revit:height_amount', Sdf.ValueTypeNames.Double, custom=True).Set(height['amount'])
                    usd_material.GetPrim().CreateAttribute('revit:height_invert', Sdf.ValueTypeNames.Bool, custom=True).Set(height['invert'])
                    if type(height.get('rgb_amount')) in (int, float):
                        usd_material.GetPrim().CreateAttribute('revit:height_rgb_amount', Sdf.ValueTypeNames.Double, custom=True).Set(height['rgb_amount'])
                else:
                    missing.append(height['path'])
            elif height:
                usd_material.GetPrim().CreateAttribute('revit:height_skip_reason', Sdf.ValueTypeNames.String, custom=True).Set(height['reason'])
            shader = UsdShade.Shader.Define(stage, str(usd_material.GetPath()) + '/PreviewSurface')
            shader.CreateIdAttr('UsdPreviewSurface')
            shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*[srgb(float(c)) for c in material['color']]))
            shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(max(0.05, min(1, 1 - float(material.get('smoothness', 50)) / 100)))
            shader.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(max(0, min(1, 1 - float(material.get('transparency', 0)))))
            shader.CreateInput('ior', Sdf.ValueTypeNames.Float).Set(1.5)
            texture_name = material.get('base_texture')
            if include_textures and texture_name:
                image = safe_asset_path(source.parent, texture_name)
                if image.is_file():
                    reader = UsdShade.Shader.Define(stage, str(usd_material.GetPath()) + '/UV')
                    reader.CreateIdAttr('UsdPrimvarReader_float2')
                    reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
                    texture = UsdShade.Shader.Define(stage, str(usd_material.GetPath()) + '/BaseColor')
                    texture.CreateIdAttr('UsdUVTexture')
                    # Conversion outputs live beside the manifest, allowing portable asset paths.
                    if target.parent != source.parent:
                        raise ValueError('USD должен находиться рядом с манифестом и текстурами')
                    texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(Path(texture_name).as_posix()))
                    texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB')
                    texture.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set('repeat')
                    texture.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set('repeat')
                    texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
                    shader.GetInput('diffuseColor').ConnectToSource(texture.ConnectableAPI(), 'rgb')
                else:
                    missing.append(texture_name)
            usd_material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
            materials[int(key)] = usd_material
    world.GetPrim().CreateAttribute('revit:import_mode', Sdf.ValueTypeNames.String, custom=True).Set(import_mode)
    world.GetPrim().CreateAttribute('revit:source_placements', Sdf.ValueTypeNames.Int64, custom=True).Set(source_placements)
    def geometry_chunks():
        if family_plan is not None:
            yield from family_plan
        else:
            for prototype in prototypes.values():
                if int(prototype['id']) in required:
                    yield dict(prototype=int(prototype['id']), records=records_by_id[int(prototype['id'])])
    written_chunks = 0
    for geometry in geometry_chunks():
        prototype = {'id': geometry['prototype']}
        records = geometry['records']
        vertices = np.ascontiguousarray(records['vertices'].reshape(-1, 8))
        if not np.isfinite(vertices).all():
            raise ValueError('Геометрия содержит нечисловые значения')
        path = '/Prototypes/p_' + str(prototype['id'])
        UsdGeom.Xform.Define(stage, path)
        mesh = UsdGeom.Mesh.Define(stage, path + '/mesh')
        geometry_identity = hashlib.sha256(np.ascontiguousarray(records['vertices']).tobytes())
        geometry_identity.update(bytes((include_materials, include_textures)))
        if family_plan is not None:
            provenance = {name: geometry[name] for name in ('family_key', 'source_table', 'source_ranges')}
            geometry_identity.update(json.dumps(provenance, sort_keys=True, ensure_ascii=False,
                                                separators=(',', ':')).encode('utf-8'))
            geometry_identity.update(geometry['source_index'].tobytes())
        for key in records['material']:
            geometry_identity.update(bytes.fromhex(material_signatures[int(key)]))
        mesh.GetPrim().CreateAttribute('revit:geometry_signature', Sdf.ValueTypeNames.String, custom=True).Set(geometry_identity.hexdigest())
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(vertices[:, :3])))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(records), 3, dtype=np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.arange(len(vertices), dtype=np.int32)))
        mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(vertices[:, 3:6])))
        mesh.SetNormalsInterpolation('vertex')
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set(Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(vertices[:, 6:8])))
        mesh.CreateDoubleSidedAttr(True)
        if family_plan is not None:
            UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('revit_source_index', Sdf.ValueTypeNames.IntArray,
                                                   'uniform').Set(Vt.IntArray.FromNumpy(geometry['source_index']))
            for name in ('family_key', 'source_table', 'source_ranges'):
                value = geometry[name] if name == 'family_key' else json.dumps(geometry[name], ensure_ascii=False)
                mesh.GetPrim().CreateAttribute('revit:' + name, Sdf.ValueTypeNames.String, custom=True).Set(value)
            mesh.GetPrim().CreateAttribute('revit:import_mode', Sdf.ValueTypeNames.String, custom=True).Set('FAMILY')
        if materials:
            binding = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
            keys = np.unique(records['material'])
            if not all(int(key) in materials for key in keys):
                raise ValueError('Не найден материал геометрии')
            if len(keys) == 1:
                binding.Bind(materials[int(keys[0])])
            else:
                for key in keys:
                    subset = UsdGeom.Subset.Define(stage, str(mesh.GetPath()) + '/material_' + str(int(key)).replace('-', 'n'))
                    subset.CreateElementTypeAttr('face')
                    subset.CreateFamilyNameAttr('materialBind')
                    subset.CreateIndicesAttr(Vt.IntArray.FromNumpy(np.flatnonzero(records['material'] == key).astype(np.int32)))
                    UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(materials[int(key)])
                binding.SetMaterialBindSubsetsFamilyType('partition')
        written_chunks += 1
        if family_plan is not None:
            progress('Подготовлено семейств: ' + str(written_chunks) + ' / ' + str(len(required)),
                     .2 + .65 * written_chunks / max(1, len(required)), grouping_statistics)
    for item in occurrences:
        obj = UsdGeom.Xform.Define(stage, '/World/Elements/e_' + str(item['id']))
        obj.GetPrim().GetReferences().AddInternalReference('/Prototypes/p_' + str(item['prototype']))
        obj.GetPrim().SetInstanceable(True)
        obj.AddTransformOp().Set(Gf.Matrix4d(*[v for row in matrix_rows(item['transform']) for v in row]))
        for name in ('id', 'unique_id', 'name', 'category', 'workset', 'category_id', 'workset_id', 'type_name', 'family_name', 'decision', 'prototype'):
            obj.GetPrim().CreateAttribute('revit:' + name, Sdf.ValueTypeNames.String, custom=True).Set(str(item.get(name, '')))
        if family_plan is not None:
            for name in ('family_key', 'source_table', 'source_ranges'):
                value = item[name] if name == 'family_key' else json.dumps(item[name], ensure_ascii=False)
                obj.GetPrim().CreateAttribute('revit:' + name, Sdf.ValueTypeNames.String, custom=True).Set(value)
            obj.GetPrim().CreateAttribute('revit:import_mode', Sdf.ValueTypeNames.String, custom=True).Set('FAMILY')
        obj.GetPrim().CreateAttribute('revit:element_id', Sdf.ValueTypeNames.String, custom=True).Set(str(item['id']))
        obj.GetPrim().CreateAttribute('revit:source_element_ids', Sdf.ValueTypeNames.String, custom=True).Set(json.dumps(item.get('source_element_ids', [str(item['id'])])))
        obj.GetPrim().CreateAttribute('revit:parent_id', Sdf.ValueTypeNames.String, custom=True).Set(str(item.get('parent_id') or ''))
        obj.GetPrim().CreateAttribute('revit:selection_parent_ids', Sdf.ValueTypeNames.String, custom=True).Set(json.dumps(item.get('selection_parent_ids', [])))
    offset = Gf.Vec3d(0)
    if center_model:
        if source_offset is not None:
            if len(source_offset) != 3 or not np.isfinite(source_offset).all():
                raise ValueError('Некорректное смещение модели')
            offset = Gf.Vec3d(*source_offset)
        else:
            bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render']).ComputeWorldBound(world.GetPrim()).ComputeAlignedRange()
            center = bounds.GetMidpoint()
            offset = Gf.Vec3d(center[0], center[1], 0)
        world.AddTranslateOp().Set(-offset)
    world.GetPrim().CreateAttribute('revit:source_offset_metres', Sdf.ValueTypeNames.Double3, custom=True).Set(offset)
    progress('Запись геометрии…', .95, grouping_statistics)
    stage.GetRootLayer().Save()
    progress('Геометрия подготовлена', 1., grouping_statistics)
    return {'usd_path': str(target), 'placements': len(occurrences), 'prototypes': len(required),
            'instance_matching': instance_statistics, 'manifest_path': str(source),
            'missing_textures': missing, 'source_offset': list(offset), 'remote_components': remote_report,
            'import_mode': import_mode, 'source_placements': source_placements, 'grouping': grouping_statistics}
