"""Build a complete legacy Blender add-on ZIP without Autodesk SDK assemblies."""
import argparse
import ast
import hashlib
from pathlib import Path
import zipfile


def build(worker, output):
    source = Path(__file__).resolve().parents[1]
    version = (source / 'VERSION').read_text().strip()
    module = ast.parse((source / 'xivgate_revit_bridge' / '__init__.py').read_text(encoding='utf-8'))
    info = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(name, ast.Name) and name.id == 'bl_info' for name in node.targets))
    if '.'.join(map(str, info['version'])) != version:
        raise ValueError('Blender metadata differs from canonical VERSION')
    if 'AssemblyInformationalVersion("' + version + '")' not in (source / 'revit_worker' / 'AssemblyInfo.cs').read_text():
        raise ValueError('Worker metadata differs from canonical VERSION')
    worker, output = Path(worker).resolve(), Path(output).resolve()
    if not worker.is_file() or worker.name != 'XIVgate.RevitBridge.Worker.dll':
        raise ValueError('Choose the compiled XIVgate worker DLL')
    if output.exists() or not output.parent.is_dir():
        raise ValueError('Choose a fresh ZIP in an existing output directory')
    entries = {str(path.relative_to(source)).replace('\\', '/'): path.read_bytes()
               for path in sorted((source / 'xivgate_revit_bridge').rglob('*.py'))}
    entries['xivgate_revit_bridge/runtime/revit/2027/' + worker.name] = worker.read_bytes()
    entries['xivgate_revit_bridge/USER_GUIDE_RU.md'] = (source / 'docs' / 'USER_GUIDE_RU.md').read_bytes()
    entries['xivgate_revit_bridge/VERSION'] = (source / 'VERSION').read_bytes()
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)
    return {'zip': str(output), 'files': len(entries), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(build(args.worker, args.output))
