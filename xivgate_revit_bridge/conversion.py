"""Build a compact USD handoff with Blender's bundled USD/Python runtime."""
import json
import hashlib
from pathlib import Path

from .geometry_cache import matrix_rows, safe_asset_path, validate_prototype


def srgb(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def build_usd(manifest_path, target_path, *, include_materials=True, include_textures=True, center_model=True, source_offset=None):
    import numpy as np
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt

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
    if not occurrences:
        raise ValueError('В выбранных элементах нет геометрии')
    ids = [str(item['id']) for item in occurrences]
    if len(ids) != len(set(ids)):
        raise ValueError('Повторяющиеся ID размещений')
    prototypes = {int(item['id']): item for item in manifest['prototypes']}
    for item in occurrences:
        matrix_rows(item['transform'])
        if int(item['prototype']) not in prototypes:
            raise ValueError('Не найден прототип элемента ' + str(item['id']))
    # Write only a fresh output; an incomplete stage is never a successful response.
    stage = Usd.Stage.CreateNew(str(target))
    world = UsdGeom.Xform.Define(stage, '/World')
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.Xform.Define(stage, '/Prototypes').CreateVisibilityAttr('invisible')
    materials = {}
    material_signatures = {int(key): hashlib.sha256(json.dumps(
        {field: value for field, value in material.items() if field != 'id'},
        sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
        for key, material in manifest['materials'].items()}
    missing = []
    if include_materials:
        for key, material in manifest['materials'].items():
            token = 'm_' + str(key).replace('-', 'n')
            usd_material = UsdShade.Material.Define(stage, '/Looks/' + token)
            usd_material.GetPrim().SetDisplayName(material['name'])
            usd_material.GetPrim().CreateAttribute('revit:source_material_id', Sdf.ValueTypeNames.String, custom=True).Set(str(material.get('source_material_id', key)))
            usd_material.GetPrim().CreateAttribute('revit:material_signature', Sdf.ValueTypeNames.String, custom=True).Set(material_signatures[int(key)])
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
    dtype = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
    binary_name = manifest.get('binary_file', 'geometry.bin')
    binary_path = safe_asset_path(source.parent, binary_name)
    with binary_path.open('rb') as binary:
        for prototype in prototypes.values():
            binary.seek(int(prototype['offset']))
            data = binary.read(int(prototype['bytes']))
            validate_prototype(prototype, data)
            records = np.frombuffer(data, dtype=dtype)
            vertices = np.ascontiguousarray(records['vertices'].reshape(-1, 8))
            if not np.isfinite(vertices).all():
                raise ValueError('Геометрия содержит нечисловые значения')
            path = '/Prototypes/p_' + str(prototype['id'])
            UsdGeom.Xform.Define(stage, path)
            mesh = UsdGeom.Mesh.Define(stage, path + '/mesh')
            geometry_identity = hashlib.sha256(np.ascontiguousarray(records['vertices']).tobytes())
            geometry_identity.update(bytes((include_materials, include_textures)))
            for key in records['material']:
                geometry_identity.update(bytes.fromhex(material_signatures[int(key)]))
            mesh.GetPrim().CreateAttribute('revit:geometry_signature', Sdf.ValueTypeNames.String, custom=True).Set(geometry_identity.hexdigest())
            mesh.CreateSubdivisionSchemeAttr('none')
            mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(vertices[:, :3])))
            mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(int(prototype['triangles']), 3, dtype=np.int32)))
            mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.arange(len(vertices), dtype=np.int32)))
            mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(vertices[:, 3:6])))
            mesh.SetNormalsInterpolation('vertex')
            UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set(Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(vertices[:, 6:8])))
            mesh.CreateDoubleSidedAttr(True)
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
    for item in occurrences:
        obj = UsdGeom.Xform.Define(stage, '/World/Elements/e_' + str(item['id']))
        obj.GetPrim().GetReferences().AddInternalReference('/Prototypes/p_' + str(item['prototype']))
        obj.GetPrim().SetInstanceable(True)
        obj.AddTransformOp().Set(Gf.Matrix4d(*[v for row in matrix_rows(item['transform']) for v in row]))
        for name in ('id', 'unique_id', 'name', 'category', 'workset', 'category_id', 'workset_id', 'type_name', 'family_name', 'decision', 'prototype'):
            obj.GetPrim().CreateAttribute('revit:' + name, Sdf.ValueTypeNames.String, custom=True).Set(str(item.get(name, '')))
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
    stage.GetRootLayer().Save()
    return {'usd_path': str(target), 'placements': len(occurrences), 'prototypes': len(prototypes), 'missing_textures': missing, 'source_offset': list(offset)}
