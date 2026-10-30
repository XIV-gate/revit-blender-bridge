"""Download checksum-pinned compile references into an explicitly supplied existing cache.

No Autodesk assembly from this cache belongs in the deliverable. Microsoft runtime
dependencies for the Framework worker are copied explicitly by build.ps1.
"""
import argparse
import hashlib
from pathlib import Path
import stat
import urllib.request
import zipfile


PACKAGES = {
    ('nice3point.revit.api.revitapi', '2024.0.0'): '52917499eedf32a9aeb57a959cb43a16c48b809af3946e3d272244275ecfa70f',
    ('nice3point.revit.api.revitapiui', '2024.0.0'): 'c7f4d01607d09ed200ab53ea5aed1206faf4a1bd9b8d70d05809c679b410229d',
    ('nice3point.revit.api.revitapi', '2025.0.2'): 'f1710aa7b9b898d20ae04ef92e035fddf6e286921f4ad89581fa30e76d56cfd1',
    ('nice3point.revit.api.revitapiui', '2025.0.2'): '3704e36f06ad5bdcf25669d2158a35f8174e6db81488bedb6cb5c81798f87143',
    ('nice3point.revit.api.revitapi', '2026.0.4'): 'cd450c65ec6429ccdc4c53318edec54657f14bf1462cee21a7c992f6dfe93619',
    ('nice3point.revit.api.revitapiui', '2026.0.4'): '03c03168a795fc67e2e4b8e27e8bd5c482dfbeea1c6385eb9d53ace5b863e989',
    ('microsoft.netcore.app.ref', '8.0.33'): '154d7e2ad85d731061a153f6dc0b14a1f3d05de0790547571f6abf3c556e642e',
    ('system.text.json', '8.0.6'): 'a83dd61778d03bdf932ee056c09873de228302b26a7118b2ec476b09086bb5eb',
    ('system.text.encodings.web', '8.0.0'): '21442442457da68d4b0b442caab8a5ab03733ef9dcfb8795beafa10afabc7ef1',
    ('microsoft.bcl.asyncinterfaces', '8.0.0'): 'f5a5a68b03092ab2abf68843d4a4aea25dfbcbe8dd0f13c625cb779b6fc1927c',
    ('system.memory', '4.5.5'): '10f43da352a29fb2b3188e4edd4dcf5100194c8b526e4f61fe2e2b5623775a22',
    ('system.buffers', '4.5.1'): 'c30b3dd2c7e2f4cee4b823d692fd42118309b42ab1f5007f923d329a5b0d6b12',
    ('system.runtime.compilerservices.unsafe', '6.0.0'): '6c41b53e70e9eee298cff3a02ce5acdd15b04125589be0273f0566026720a762',
    ('system.threading.tasks.extensions', '4.5.4'): 'a304a963cc0796c5179f9c6b7d8022bbce3b2fa7c029eb6196f631f7b462d678',
    ('system.numerics.vectors', '4.4.0'): '6ae5d02b67e52ff2699c1feb11c01c526e2f60c09830432258e0809486aabb65',
}


def prepare(cache):
    cache = Path(cache).resolve(strict=True)
    if not cache.is_dir():
        raise ValueError('Reference cache must be an existing approved directory')
    for (name, version), expected in PACKAGES.items():
        filename = f'{name}.{version}.nupkg'
        archive = cache / filename
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            pending = cache / (filename + '.pending')
            try:
                with urllib.request.urlopen(f'https://api.nuget.org/v3-flatcontainer/{name}/{version}/{filename}', timeout=30) as response:
                    with pending.open('wb') as stream:
                        while block := response.read(1024 * 1024):
                            stream.write(block)
                if hashlib.sha256(pending.read_bytes()).hexdigest() != expected:
                    raise ValueError(f'Package checksum mismatch: {filename}')
                pending.replace(archive)
            finally:
                pending.unlink(missing_ok=True)
        target = cache / f'{name}.{version}'
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (target / member.filename).resolve().is_relative_to(target.resolve()) or stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(f'Unsafe package entry: {member.filename}')
            package.extractall(target)
        print(f'Verified {name} {version}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', help='Existing approved directory for all downloaded/extracted files')
    prepare(parser.parse_args().cache)
