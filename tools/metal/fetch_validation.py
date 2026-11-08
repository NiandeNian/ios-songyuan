#!/usr/bin/env python3
"""Read optional Metal validation receipts and pixels from the offline iOS volume.

Uses the existing read-only HFS reader while Android runs. Every boot has its
own directory; stale receipts are kept separate rather than reported as current.
"""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', type=Path, required=True)
    parser.add_argument('--serial', default='2d611f9f')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(ANDROID_SERIAL=args.serial, IOS_ADB=str(args.adb.resolve()),
                      IOS_IMAGE='/data/local/tmp/nezha-ios/root.img',
                      IOS_PINFILE='/data/local/tmp/nezha-ios/pinfile')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ioslog_fetch import HFS, Remote
    fs = HFS(Remote())
    catalog = fs.walk()
    parent = 2
    for part in ('private', 'var', 'mobile', 'Library', 'Logs', 'NezhaMetalValidation'):
        node = catalog.get((parent, part))
        if node is None or node[0] != 1:
            raise SystemExit(f'validation output directory missing at {part}')
        parent = node[1]
    receipts = []
    for (owner, boot), folder in catalog.items():
        if owner != parent or folder[0] != 1:
            continue
        if boot in ('.', '..') or '/' in boot or '\\' in boot:
            raise ValueError('invalid validation directory name')
        output = args.out / boot
        output.mkdir(parents=True, exist_ok=True)
        for (owner, name), node in catalog.items():
            if owner != folder[1] or node[0] != 2:
                continue
            if name not in ('receipt.json', 'ci-gradient.bgra', 'ci-surface.bgra', 'mps-blur.bgra',
                            'astc-thumbnail.bgra', 'descriptor-stripes.bgra', 'encoding-output.bgra', 'mps-temporary.bgra',
                            'imageio-0.bgra', 'imageio-1.bgra', 'imageio-2.bgra',
                            'imageio-raw-0.data', 'imageio-raw-1.data', 'imageio-raw-2.data'):
                continue
            if node[3][1] & 0x20:
                raise ValueError(f'compressed validation output: {boot}/{name}')
            data = fs.read_fork(node[3][0], node[1])
            (output / name).write_bytes(data)
            if name == 'receipt.json':
                receipt = json.loads(data)
                receipts.append({'directory': boot, 'receipt': receipt})
    (args.out / 'receipts.json').write_text(json.dumps(receipts, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipts, indent=2))
    if not receipts:
        raise SystemExit('no validation receipts found')


if __name__ == '__main__':
    main()
