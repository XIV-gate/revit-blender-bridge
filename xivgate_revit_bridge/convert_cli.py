"""Run with Blender's bundled python.exe; never imports bpy or launches a DCC."""
import argparse
import json
import traceback
from pathlib import Path

from .conversion import build_usd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--response', required=True)
    parser.add_argument('--no-materials', action='store_true')
    parser.add_argument('--no-textures', action='store_true')
    parser.add_argument('--source-origin', action='store_true')
    parser.add_argument('--offset', nargs=3, type=float)
    parser.add_argument('--no-instance-matching', action='store_true')
    parser.add_argument('--instance-tolerance-mm', type=float, default=0.1)
    parser.add_argument('--no-revit-instances', action='store_true')
    args = parser.parse_args()
    try:
        result = build_usd(args.manifest, args.target, include_materials=not args.no_materials,
            include_textures=not args.no_textures, center_model=not args.source_origin, source_offset=args.offset,
            match_instances=not args.no_instance_matching, instance_tolerance_mm=args.instance_tolerance_mm,
            use_revit_instances=not args.no_revit_instances)
        response = {'success': True, **result}
    except Exception as error:
        response = {'success': False, 'error': str(error), 'details': traceback.format_exc()}
    Path(args.response).write_text(json.dumps(response, ensure_ascii=False), encoding='utf-8')
    return 0 if response['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
