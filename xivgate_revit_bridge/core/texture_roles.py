"""Interpret only explicit supported Revit height maps; never infer normal maps."""
import math


def height_map_descriptor(material):
    properties = material.get('properties', {})
    candidates = [(key, path) for key, path in material.get('textures', {}).items()
                  if key.split('/')[0] in ('concrete_bump_map', 'generic_bump_map')
                  and key.lower().endswith('/unifiedbitmap_bitmap')]
    if not candidates:
        return None
    if len(candidates) != 1:
        return {'active': False, 'reason': 'ambiguous_height_maps'}
    key, path = candidates[0]
    schema = key.split('/')[0].split('_')[0]
    amount = properties.get(schema + '_bump_amount')
    prefix = key.rsplit('/', 1)[0]
    mode_keys = {'unifiedbitmapbumpmaptype', 'bumpmaptype', 'normalmode', 'normalmap',
                 'isnormalmap', 'unifiedbitmapisnormalmap', 'unifiedbitmapnormalmode'}
    if any(key.lower().startswith(prefix.lower() + '/') and key.rsplit('/', 1)[-1].replace('_', '').lower() in mode_keys for key in properties):
        # BumpMap.BumpmapType is documented, but arbitrary UnifiedBitmap mode
        # fields are not established here. Explicit modes are never guessed.
        return {'active': False, 'reason': 'unsupported_explicit_map_type'}
    supported = type(amount) in (int, float) and math.isfinite(amount) and 0 < amount <= 1
    disabled = any(properties.get(schema + '_bump_' + flag) is False for flag in ('toggle', 'enabled', 'on'))
    return {'active': bool(supported and not disabled), 'path': path, 'amount': amount,
            'invert': properties.get(prefix + '/unifiedbitmap_Invert', False) is True,
            'rgb_amount': properties.get(prefix + '/unifiedbitmap_RGBAmount'),
            'reason': '' if supported and not disabled else 'source_height_inactive' if disabled or amount == 0 else 'unsupported_source_amount'}
