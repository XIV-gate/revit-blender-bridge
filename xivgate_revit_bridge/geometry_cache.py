"""Host-independent cache validation. No Blender or Autodesk dependency."""
import hashlib
import math
from pathlib import Path


def validate_prototype(prototype, data):
    expected = int(prototype['triangles']) * 104
    if expected < 0 or expected != int(prototype['bytes']) or len(data) != expected:
        raise ValueError('Повреждён или обрезан буфер геометрии')
    digest = prototype.get('sha256')
    if digest and hashlib.sha256(data).hexdigest().casefold() != digest.casefold():
        raise ValueError('Контрольная сумма геометрии не совпадает')


def matrix_rows(values):
    if len(values) != 12 or not all(math.isfinite(value) for value in values):
        raise ValueError('Некорректная матрица размещения')
    return tuple(tuple(values[i:i + 3]) + (0,) for i in (0, 3, 6)) + (tuple(values[9:12]) + (1,),)


def safe_asset_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute():
        raise ValueError('Путь текстуры должен быть относительным')
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Текстура находится вне папки выгрузки')
    return target
