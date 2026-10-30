"""Readable Revit names with stable identity suffixes and Blender 4.2 limits."""
import hashlib
import re

# Blender 4.2 allows 63 UTF-8 bytes. Reserve four for its genuine .001 collision.
NAME_BYTES = 59


def _clean(value):
    return ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).split())


def _value(metadata, key):
    return _clean(metadata.get('revit:' + key, metadata.get(key, '')))


def _clip(value, limit):
    return value.encode('utf-8')[:max(0, limit)].decode('utf-8', errors='ignore').rstrip()


def _identifier(value):
    value = _clean(value) or '?'
    if len(value.encode()) > 24:
        value = _clip(value, 12) + '~' + hashlib.sha256(value.encode()).hexdigest()[:8]
    return value


def _parts(metadata, mesh=False):
    category = _value(metadata, 'category') or _value(metadata, 'category_name') or 'Элемент Revit'
    family = _value(metadata, 'family_name')
    kind = _value(metadata, 'type_name')
    name = _value(metadata, 'name')
    values = [category, family, kind]
    if not mesh or not family and not kind:
        if name and not re.fullmatch(r'(?:e_|p_)?\d+(?:\.\d+)*(?:_mesh)?', name, re.IGNORECASE):
            values.append(name)
    result = []
    seen = set()
    for value in values:
        if value and value.casefold() not in seen:
            result.append(_clip(value, 256))
            seen.add(value.casefold())
    return result


def _compose(parts, suffix):
    budget = NAME_BYTES - len(suffix.encode('utf-8'))
    # The instance name is optional when family/type already describe the object.
    if len(parts) > 3 and len(' — '.join(parts).encode()) > budget:
        parts = parts[:-1]
    parts = list(parts)
    while len(' — '.join(parts).encode('utf-8')) > budget:
        index = max(range(len(parts)), key=lambda item: len(parts[item].encode()))
        parts[index] = parts[index][:-1].rstrip()
        if not parts[index]:
            parts.pop(index)
        if not parts:
            break
    return (' — '.join(parts).rstrip(' —') + suffix).strip()


def object_name(metadata):
    identifier = _value(metadata, 'id') or _value(metadata, 'element_id')
    return _compose(_parts(metadata), ' [' + _identifier(identifier) + ']')


def mesh_name(metadata, geometry_signature='', element_id=None):
    if element_id is not None:
        suffix = ' [' + _identifier(element_id) + ']'
    elif isinstance(geometry_signature, str) and re.fullmatch(r'[0-9a-fA-F]{64}', geometry_signature):
        suffix = ' [g:' + geometry_signature[:8].lower() + ']'
    else:
        prototype = _value(metadata, 'prototype') or _value(metadata, 'id')
        suffix = ' [p:' + _identifier(prototype) + ']'
    return _compose(_parts(metadata, mesh=True), suffix)


def is_generated_name(current, generated_name=None):
    """An exact marker wins; without one recognize only known old transport names."""
    if isinstance(generated_name, str) and generated_name:
        return current == generated_name
    return bool(re.fullmatch(r'(?:e_\d+(?:\.\d+)*(?:_mesh)?|p_\d+|mesh|\d+)(?:\.\d+)*',
                             current, re.IGNORECASE))
