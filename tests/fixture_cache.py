"""Small authored cache fixture, independent of Autodesk/Blender and private models."""
import hashlib
import json
from pathlib import Path
import struct
import zlib


def _png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def write_fixture(root, include_ids=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    triangles = [
        ((0, 0, 0, 0, -1, 0, 0, 0), (2, 0, 0, 0, -1, 0, 1, 0), (0, 0, 3, 0, -1, 0, 0, 1)),
        ((0, 0, 0, 0, -1, 0, 0, 0), (1, 0, 0, 0, -1, 0, 1, 0), (0, 0, 1, 0, -1, 0, 0, 1)),
    ]
    occurrences = []
    for identifier, prototype, translation, mirrored in (('11', 0, (100, 200, 0), False), ('12', 0, (110, 200, 0), True), ('13', 1, (105, 204, 3), False)):
        if include_ids is not None and identifier not in include_ids:
            continue
        occurrences.append({'id': identifier, 'unique_id': 'unique-' + identifier, 'name': 'Fixture ' + identifier,
            'category': 'Окна' if prototype == 0 else 'Стены', 'category_id': 'windows' if prototype == 0 else 'walls',
            'workset': 'Фасады', 'workset_id': 'facade', 'prototype': prototype, 'decision': 'keep',
            'transform': [-1 if mirrored else 1, 0, 0, 0, 1, 0, 0, 0, 1, *translation]})
    needed = {item['prototype'] for item in occurrences}
    prototypes = []
    with (root / 'geometry.bin').open('wb') as binary:
        for index in sorted(needed):
            data = struct.pack('<24fq', *[value for vertex in triangles[index] for value in vertex], 7)
            prototypes.append({'id': index, 'offset': binary.tell(), 'bytes': len(data), 'triangles': 1, 'sha256': hashlib.sha256(data).hexdigest()})
            binary.write(data)
    # Authored, decodable 2x2 RGBA PNG with varying grayscale height values.
    image = root / 'pixel.png'
    pixels = b'\0' + bytes((0, 0, 0, 255, 255, 255, 255, 255)) + b'\0' + bytes((64, 64, 64, 255, 128, 128, 128, 255))
    image.write_bytes(b'\x89PNG\r\n\x1a\n' + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0))
                      + _png_chunk(b'IDAT', zlib.compress(pixels)) + _png_chunk(b'IEND', b''))
    manifest = {'schema': 1, 'units': 'metres', 'up': 'Z', 'cache_reusable': True, 'cache_dependencies': [],
        'prototypes': prototypes, 'occurrences': occurrences,
        'materials': {'7': {'id': 7, 'source_material_id': 'source-7', 'name': 'Fixture facade material', 'color': [0.7, 0.5, 0.3],
                          'transparency': 0, 'smoothness': 35, 'properties': {}, 'base_texture': 'pixel.png'}},
        'summary': {'occurrences': len(occurrences), 'prototypes': len(prototypes)}, 'warnings': []}
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return path
