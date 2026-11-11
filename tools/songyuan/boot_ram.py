#!/usr/bin/env python3
"""Temporarily boot a validated Android-format image on the named songyuan device."""
import argparse
from pathlib import Path
import hashlib
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', required=True)
    p.add_argument('--fastboot', required=True)
    p.add_argument('--serial', required=True)
    p.add_argument('image', type=Path)
    args = p.parse_args()
    image = args.image.resolve()
    data = image.read_bytes()
    if data[:8] != b'ANDROID!' or len(data) > 805306368:
        raise ValueError('Image is not an Android boot image or exceeds the measured download limit')
    adb = [args.adb, '-s', args.serial]
    fb = [args.fastboot, '-s', args.serial]

    def call(cmd):
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=30)
        print(r.stdout, end='', flush=True)
        r.check_returncode()
        return r.stdout

    if call(adb + ['shell', 'getprop', 'ro.product.device']).strip() != 'songyuan':
        raise ValueError('ADB device is not songyuan')
    print('RAM image SHA256', hashlib.sha256(data).hexdigest(), flush=True)
    call(adb + ['reboot', 'bootloader'])
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        r = subprocess.run([args.fastboot, 'devices'], capture_output=True, text=True, timeout=10)
        if args.serial in r.stdout:
            break
        time.sleep(1)
    else:
        raise RuntimeError('No matching fastboot device')
    product = call(fb + ['getvar', 'product'])
    if 'product: songyuan' not in product:
        raise ValueError('Fastboot device is not songyuan')
    unlocked = call(fb + ['getvar', 'unlocked'])
    if 'unlocked: yes' not in unlocked:
        raise ValueError('Fastboot device is not unlocked')
    call(fb + ['boot', str(image)])


if __name__ == '__main__':
    main()
