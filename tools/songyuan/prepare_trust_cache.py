#!/usr/bin/env python3
"""Merge the target IPSW's OS and Cryptex1 trust caches for an ignition-level-1 root."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import zipfile

from addtrustcache import raw_trust_cache

CACHE_KEYS = ('StaticTrustCache', 'Cryptex1,SystemTrustCache', 'Cryptex1,AppTrustCache')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipsw', type=Path, required=True)
    parser.add_argument('--device-class', default='d22ap')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sources = []
    paths = []
    with zipfile.ZipFile(args.ipsw) as archive:
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        identities = [item for item in manifest['BuildIdentities']
                      if item['Info']['DeviceClass'] == args.device_class]
        if not identities:
            parser.error('Device class is absent from the IPSW manifest')
        identity = identities[0]
        for key in CACHE_KEYS:
            if key not in identity['Manifest']:
                parser.error(f'IPSW manifest lacks required trust cache: {key}')
            name = identity['Manifest'][key]['Info']['Path']
            if not name.endswith('.trustcache') or archive.getinfo(name).file_size > 1 << 20:
                parser.error(f'Unexpected trust cache member: {key}')
            data = archive.read(name)  # ZIP verifies the original member's CRC.
            target = args.out / Path(name).name
            target.write_bytes(data)
            raw, count = raw_trust_cache(str(target))
            paths.append(str(target))
            sources.append({'component': key, 'path': name, 'entries': count,
                            'sha256': hashlib.sha256(data).hexdigest()})
            print(f'{key}: {count} entries', flush=True)
    merged, count = raw_trust_cache('+'.join(paths))
    target = args.out / 'combined-static.trustcache'
    target.write_bytes(merged)
    (args.out / 'combined-static.json').write_text(json.dumps({
        'device_class': args.device_class, 'version': manifest['ProductVersion'],
        'build': manifest['ProductBuildVersion'], 'sources': sources, 'entries': count,
        'sha256': hashlib.sha256(merged).hexdigest(),
    }, indent=2) + '\n')
    print(f'Combined static trust cache: {count} entries, {len(merged)} bytes', flush=True)
    print(target.resolve(), flush=True)


if __name__ == '__main__':
    main()
