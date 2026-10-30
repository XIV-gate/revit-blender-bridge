"""Build a complete legacy Blender add-on ZIP without Autodesk SDK assemblies."""
import argparse
import ast
import hashlib
import json
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
    module_root = source / 'xivgate_revit_bridge'
    files = {path.relative_to(module_root).as_posix(): path.read_bytes()
             for path in sorted(module_root.rglob('*.py'))
             if not any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(module_root).parts)}
    worker_name = f'runtime/revit/2027/{version}/' + worker.name
    files[worker_name] = worker.read_bytes()
    files['USER_GUIDE_RU.md'] = (source / 'docs' / 'USER_GUIDE_RU.md').read_bytes()
    files['VERSION'] = (version + '\n').encode('utf-8')
    manifest = dict(schema=1, id='xivgate_revit_bridge', version=version,
                    blender_min=list(info['blender']), worker=worker_name,
                    files={name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(files.items())})
    files['PACKAGE_MANIFEST.json'] = json.dumps(manifest, sort_keys=True, indent=2).encode('utf-8')
    entries = {'xivgate_revit_bridge/' + name: payload for name, payload in files.items()}
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
