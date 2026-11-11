#!/usr/bin/env python3
"""Read-only ADB capture; raw backups stay under the ignored .local directory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shlex
import sys
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    out = Path(__file__).resolve().parents[2] / '.local' / 'device'
    out.mkdir(parents=True, exist_ok=True)
    adb = [args.adb, '-s', args.serial]

    def capture(name, command):
        path = out / name
        temp = path.with_suffix(path.suffix + '.partial')
        subprocess.run([sys.executable, Path(__file__).with_name('adb_root.py'),
                        '--adb', args.adb, '--serial', args.serial, '--out', temp, command], check=True)
        temp.replace(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f'{name}: {path.stat().st_size} bytes, SHA256 {digest}', flush=True)
        return {'file': name, 'size': path.stat().st_size, 'sha256': digest}

    device = subprocess.check_output(adb + ['shell', 'getprop', 'ro.product.device'],
                                     timeout=15).decode().strip()
    if device != 'songyuan':
        raise RuntimeError(f'Expected songyuan; connected device is {device!r}')
    slot = subprocess.check_output(adb + ['shell', 'getprop', 'ro.boot.slot_suffix'],
                                   timeout=15).decode().strip()
    if slot not in ('_a', '_b'):
        raise RuntimeError(f'Unexpected slot {slot!r}')
    manifest = {'captured_utc': datetime.now(timezone.utc).isoformat(),
                'device': device, 'slot': slot, 'files': []}
    captures = [('bootconfig.txt', 'cat /proc/bootconfig'),
                ('cmdline.txt', 'cat /proc/cmdline'),
                ('iomem.txt', 'cat /proc/iomem'),
                ('partitions.txt', 'ls -l /dev/block/by-name; cat /proc/partitions'),
                ('live.dtb', 'cat /sys/firmware/fdt'),
                ('live-devicetree.tar', 'tar -cf - -C /sys/firmware/devicetree/base .')]
    for partition in ('boot', 'init_boot', 'vendor_boot', 'dtbo', 'vbmeta'):
        name = partition + slot
        captures.append((name + '.img', f'dd if=/dev/block/by-name/{name} bs=1048576 2>/dev/null'))
    for name, command in captures:
        entry = capture(name, command)
        if name.endswith('.img'):
            partition = name[:-4]
            expected = subprocess.check_output(
                adb + ['shell', '-T', 'su -W -c ' + shlex.quote(
                       f'blockdev --getsize64 /dev/block/by-name/{partition}; '
                       f'sha256sum /dev/block/by-name/{partition}')], timeout=180
            ).decode().split()
            if entry['size'] != int(expected[0]) or entry['sha256'] != expected[1]:
                raise RuntimeError(f'{name}: backup does not match device size/SHA256')
        manifest['files'].append(entry)
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
