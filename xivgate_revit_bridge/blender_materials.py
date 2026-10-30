"""Small Blender material adapter for explicitly active Revit height images."""
import bpy
from .geometry_cache import safe_asset_path


def _skip(material, result, reason):
    result['reason'] = reason
    if material.library is None:
        material['revit:height_skip_reason'] = reason
    return result


def attach_height_map(material, descriptor, export_root):
    result = {'images': [], 'connected': False, 'reason': ''}
    if not material.use_nodes:
        return _skip(material, result, 'material_has_no_nodes')
    shaders = [node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED']
    if len(shaders) != 1 or shaders[0].inputs['Normal'].is_linked:
        return _skip(material, result, 'existing_or_ambiguous_normal')
    amount = descriptor.get('amount')
    if type(amount) not in (int, float) or not 0 < amount <= 1:
        return _skip(material, result, 'inactive_or_unsupported_source_amount')
    path = safe_asset_path(export_root, descriptor['path'])
    if not path.is_file():
        return _skip(material, result, 'missing_height_texture')
    # A separate image avoids changing the color space of an existing base-color
    # image even when the two roles refer to the same physical file.
    image = None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        if image.size[0] <= 0 or image.size[1] <= 0 or not len(image.pixels):
            raise OSError('Height image has no decoded pixels')
        image.colorspace_settings.name = 'Non-Color'
    except (RuntimeError, OSError):
        if image is not None and image.users == 0:
            bpy.data.images.remove(image)
        return _skip(material, result, 'height_image_decode_failed')
    nodes, links = material.node_tree.nodes, material.node_tree.links
    texture = nodes.new('ShaderNodeTexImage')
    texture.name = 'Revit Height'
    texture.image = image
    uv = nodes.new('ShaderNodeUVMap')
    uv.uv_map = 'st'
    bump = nodes.new('ShaderNodeBump')
    bump.name = 'Revit Bump'
    bump.inputs['Strength'].default_value = float(amount)
    bump.invert = descriptor.get('invert') is True
    # Distance remains Blender's default: Revit's relative amount does not
    # establish a physical displacement distance in metres.
    links.new(uv.outputs['UV'], texture.inputs['Vector'])
    links.new(texture.outputs['Color'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], shaders[0].inputs['Normal'])
    material['revit:height_source_amount'] = float(amount)
    material['revit:height_distance_mode'] = 'blender_default'
    material.pop('revit:height_skip_reason', None)
    result.update(images=[image], connected=True)
    return result
