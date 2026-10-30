"""Reviewable manifest plans and conservative, explicitly invoked installation."""
import os
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET
from .jobs import source_sha256

ADDIN_ID = '03BA631F-3053-469E-BBDC-9FF4E6C072F8'
ADDIN_NAME = 'XIVgate Revit Blender Bridge'
MANIFEST_NAME = 'XIVgate.RevitBridge.addin'
FULL_CLASS_NAME = 'XIVgate.RevitBridge.WorkerApp'


def manifest_plan(revit_exe, worker_dll, year=2027, appdata_root=None, addin_folder=None):
    """Plan one pointer, never duplicate it across competing registration folders.

    The 2027 web guide names "Revit 2027", while the installed 2027
    RevitAddInUtility reports the bare year. Prefer an explicit utility result;
    otherwise use an existing unambiguous folder, or the utility's bare year.
    Generating a plan performs no profile writes.
    """
    root = Path(appdata_root if appdata_root is not None else os.environ['APPDATA']).resolve()
    base = root / 'Autodesk' / 'Revit' / 'Addins'
    standard = base / str(year)
    documented = base / ('Revit ' + str(year))
    if addin_folder is not None:
        folder = Path(addin_folder).resolve()
    elif standard.is_dir() and documented.is_dir():
        raise ValueError('Обнаружены два каталога регистрации; укажите путь из RevitAddInUtility')
    elif documented.is_dir():
        folder = documented
    else:
        folder = standard
    target = folder / MANIFEST_NAME
    xml = ET.Element('RevitAddIns')
    addin = ET.SubElement(xml, 'AddIn', Type='Application')
    values = {'Name': ADDIN_NAME, 'Assembly': str(Path(worker_dll).resolve()), 'AddInId': ADDIN_ID,
              'FullClassName': FULL_CLASS_NAME, 'VendorId': 'XIVG', 'VendorDescription': 'XIVgate',
              'AllowLoadIntoExistingSession': 'False'}
    for name, value in values.items():
        ET.SubElement(addin, name).text = value
    return {'path': str(target), 'content': '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(xml, encoding='unicode') + '\n'}


def _owned_manifest(content):
    try:
        root = ET.fromstring(content)
        entries = list(root)
        if root.tag != 'RevitAddIns' or len(entries) != 1:
            return None
        entry = entries[0]
        expected = {'Name': ADDIN_NAME, 'AddInId': ADDIN_ID, 'FullClassName': FULL_CLASS_NAME, 'VendorId': 'XIVG',
                    'AllowLoadIntoExistingSession': 'False'}
        if entry.tag != 'AddIn' or entry.get('Type') != 'Application':
            return None
        for name, value in expected.items():
            if len(entry.findall(name)) != 1 or entry.findtext(name) != value:
                return None
        if len(entry.findall('Assembly')) != 1:
            return None
        assembly = entry.findtext('Assembly')
        return Path(assembly) if assembly and Path(assembly).is_absolute() else None
    except (ET.ParseError, ValueError):
        return None


def manifest_matches(plan):
    """Whether an owned registration points at the same verified worker bytes.

    A packaged copy may live at another path. This check never changes the
    registration and requires both assemblies to exist, even for identical XML.
    """
    try:
        target = Path(plan['path'])
        if not target.is_absolute() or target.name != MANIFEST_NAME:
            return False
        expected = _owned_manifest(plan['content'])
        registered = _owned_manifest(target.read_text(encoding='utf-8-sig'))
        if expected is None or registered is None:
            return False
        if not expected.is_file() or not registered.is_file():
            return False
        return source_sha256(expected) == source_sha256(registered)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return False


def install_manifest(plan):
    target = Path(plan['path'])
    if target.name != MANIFEST_NAME or not target.is_absolute():
        raise ValueError('Недопустимый путь регистрации')
    assembly = _owned_manifest(plan['content'])
    if assembly is None:
        raise ValueError('Манифест не принадлежит XIVgate')
    if not assembly.is_file():
        raise FileNotFoundError('Не найдена сборка Revit: ' + str(assembly))
    if target.exists():
        existing = target.read_text(encoding='utf-8-sig')
        if _owned_manifest(existing) is None:
            raise ValueError('Существующий манифест принадлежит другому приложению')
        if manifest_matches(plan):
            return target
        raise ValueError('Существующий манифест XIVgate отличается; требуется явное ручное переподключение. Автоматическая замена запрещена')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(plan['content'])
            stream.flush()
            os.fsync(stream.fileno())
        # A new install uses exclusive creation: never overwrite a racing foreign file.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise ValueError('Путь регистрации уже занят') from None
    finally:
        temporary.unlink(missing_ok=True)
    return target
