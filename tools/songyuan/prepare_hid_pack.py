#!/usr/bin/env python3
"""Prepare a reversible VFS rename for the optional remote Bluetooth HID filter."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ACTIVE = '/System/Library/HIDPlugins/SessionFilters/IOHIDRemoteSensorSessionFilter.plugin'
DISABLED_DIR = '/System/Library/HIDPlugins/SongyuanDisabled'
DISABLED = DISABLED_DIR + '/IOHIDRemoteSensorSessionFilter.plugin'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('disable', 'restore'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--size-mib', type=int, default=8)
    args = parser.parse_args()
    if args.size_mib <= 0:
        parser.error('Pack size must be positive')
    args.out.mkdir(parents=True, exist_ok=True)
    instructions = (f'- 0755 {DISABLED_DIR}\n> {ACTIVE} {DISABLED}\n'
                    if args.action == 'disable' else f'> {DISABLED} {ACTIVE}\n')
    manifest = args.out / 'files.txt'
    manifest.write_text(instructions)
    image = args.out / 'hid-files.img'
    subprocess.run([sys.executable, Path(__file__).with_name('mkfwpack.py'), str(image),
                    str(manifest), '--size', str(args.size_mib << 20)], check=True)
    print(instructions, end='')
    print('Pack SHA256', hashlib.sha256(image.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
