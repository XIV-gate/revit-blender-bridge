"""Launch tests only with an explicitly chosen, project-owned isolated profile."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--blender', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--script', default='test_blender_integration.py')
    parser.add_argument('--timeout', type=float)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if root.drive.casefold() != 'd:' or not root.is_dir():
        raise ValueError('Choose an approved existing task workspace on D:')
    source = Path(__file__).resolve().parents[1]
    profile = root / ('profile_' + Path(args.blender).parent.name.replace(' ', '_'))
    env = os.environ.copy()
    for variable, folder in (('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
                             ('BLENDER_USER_DATAFILES', 'datafiles'), ('BLENDER_USER_EXTENSIONS', 'extensions'),
                             ('TEMP', 'temp'), ('TMP', 'temp')):
        path = profile / folder
        path.mkdir(parents=True, exist_ok=True)
        env[variable] = str(path)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', RBB_SOURCE_DIR=str(source), RBB_TEST_ROOT=str(root))
    command = [args.blender, '--background', '--disable-autoexec', '--python-exit-code', '1', '--python', str(source / 'tools' / args.script)]
    return subprocess.run(command, env=env, cwd=root, check=False, timeout=args.timeout).returncode


if __name__ == '__main__':
    raise SystemExit(main())
