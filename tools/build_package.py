"""Build a complete legacy Blender add-on ZIP without Autodesk SDK assemblies."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import sys
import zipfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build(worker, output, loaders=None, loader_lock=None):
    source = Path(__file__).resolve().parents[1]
    version = (source / 'VERSION').read_text().strip()
    module = ast.parse((source / 'xivgate_revit_bridge' / '__init__.py').read_text(encoding='utf-8'))
    info = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(name, ast.Name) and name.id == 'bl_info' for name in node.targets))
    if '.'.join(map(str, info['version'])) != version:
        raise ValueError('Blender metadata differs from canonical VERSION')
    if 'AssemblyInformationalVersion("' + version + '")' not in (source / 'revit_worker' / 'AssemblyInfo.cs').read_text():
        raise ValueError('Worker metadata differs from canonical VERSION')
    workers = worker if isinstance(worker, dict) else {'2027': worker}
    workers = {key: Path(path).resolve() for key, path in workers.items()}
    if '2027' not in workers or set(workers) - {'2024', '2025', '2025-net10', '2026', '2026-net10', '2027'}:
        raise ValueError('Choose known Revit variants including 2027')
    if any(not path.is_file() or path.name != 'XIVgate.RevitBridge.Worker.dll' for path in workers.values()):
        raise ValueError('Choose compiled XIVgate worker DLLs')
    output = Path(output).resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError('Choose a fresh ZIP in an existing output directory')
    module_root = source / 'xivgate_revit_bridge'
    files = {path.relative_to(module_root).as_posix(): path.read_bytes()
             for path in sorted(module_root.rglob('*.py'))
             if not any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(module_root).parts)}
    # Existing clients permit verified Python data modules but reject additional
    # runtime DLL paths. A literal payload keeps in-app migration compatible;
    # setup extracts frozen loader bytes to their independent immutable path.
    loader_metadata = None
    if loaders is not None:
        if (not isinstance(loaders, dict) or set(loaders) != set(workers)
                or not isinstance(loader_lock, dict) or set(loader_lock) != set(workers)):
            raise ValueError('Frozen loaders and their hash lock must match every worker variant')
        payloads = {}
        for key, loader in sorted(loaders.items()):
            loader = Path(loader).resolve(strict=True)
            data = loader.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if (loader.name != 'XIVgate.RevitBridge.Loader.dll' or not data.startswith(b'MZ')
                    or digest != loader_lock[key]):
                raise ValueError('Loader differs from its frozen hash lock: ' + key)
            payloads[key] = {'sha256': digest, 'data': base64.b64encode(data).decode('ascii')}
        literal = {'schema': 1, 'abi': 1, 'loaders': payloads}
        files['core/loader_payload.py'] = ('# Generated frozen loader data; never execute this module.\nPAYLOAD = ' + repr(literal) + '\n').encode('utf-8')
        loader_metadata = {'abi': 1, 'sha256': {key: record['sha256'] for key, record in payloads.items()}}
    worker_names, runtime_files = {}, []
    from xivgate_revit_bridge.core.updates import _NET48_DEPENDENCIES
    for key, path in workers.items():
        folder = f'runtime/revit/{key}/{version}/'
        worker_names[key] = folder + path.name
        files[worker_names[key]] = path.read_bytes()
        allowed = {'XIVgate.RevitBridge.Worker.deps.json', 'XIVgate.RevitBridge.Worker.runtimeconfig.json'}
        if key == '2024':
            allowed.update(_NET48_DEPENDENCIES)
            missing = [name for name in _NET48_DEPENDENCIES if not (path.parent / name).is_file()]
            if missing:
                raise ValueError('Missing required .NET Framework dependencies: ' + ', '.join(sorted(missing)))
        for name in sorted(allowed):
            dependency = path.parent / name
            if dependency.is_file():
                files[folder + name] = dependency.read_bytes()
                runtime_files.append(folder + name)
    files['USER_GUIDE_RU.md'] = (source / 'docs' / 'USER_GUIDE_RU.md').read_bytes()
    files['THIRD_PARTY_NOTICES.md'] = (source / 'docs' / 'THIRD_PARTY_NOTICES.md').read_bytes()
    files['VERSION'] = (version + '\n').encode('utf-8')
    manifest = dict(schema=1, id='xivgate_revit_bridge', version=version,
                    blender_min=list(info['blender']), worker=worker_names['2027'],
                    workers=worker_names, runtime_files=runtime_files,
                    files={name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(files.items())})
    if loader_metadata is not None:
        manifest['loader_payloads'] = loader_metadata
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
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--worker', help='Single Revit 2027 worker for development checks')
    inputs.add_argument('--workers-root', help='Directory containing all six variant subdirectories')
    parser.add_argument('--output', required=True)
    parser.add_argument('--loaders-root', help='Frozen loader variants; embedded for old-updater compatibility')
    parser.add_argument('--loader-lock', help='Approved JSON hash lock for the frozen loader ABI')
    args = parser.parse_args()
    workers = ({key: Path(args.workers_root) / key / 'XIVgate.RevitBridge.Worker.dll'
                for key in ('2024', '2025', '2025-net10', '2026', '2026-net10', '2027')}
               if args.workers_root else args.worker)
    if bool(args.loaders_root) != bool(args.loader_lock):
        parser.error('--loaders-root and --loader-lock must be used together')
    keys = workers.keys() if isinstance(workers, dict) else ('2027',)
    loaders = ({key: Path(args.loaders_root) / key / 'XIVgate.RevitBridge.Loader.dll'
                for key in keys} if args.loaders_root else None)
    lock = json.loads(Path(args.loader_lock).read_text(encoding='utf-8')) if args.loader_lock else None
    print(build(workers, args.output, loaders=loaders, loader_lock=lock))
