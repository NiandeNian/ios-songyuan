#!/usr/bin/env python3
"""Fetch and decode the last XNU console from the pinned log file in Android."""
import argparse
from pathlib import Path
import posixpath
import shlex
import struct
import subprocess
import sys

HEADER_SIZE = 4096
RING_SIZE = 4 << 20
PINFILE = '/data/local/tmp/unixnu-songyuan-pinfile'


def decode(raw):
    if len(raw) < HEADER_SIZE or raw[:8] != b'PANDORLG':
        raise ValueError('No PANDORLG header; this file contains no confirmed console flush')
    version, size, total, sequence = struct.unpack_from('<IIQQ', raw, 8)
    if version != 1 or size != RING_SIZE or len(raw) < HEADER_SIZE + size:
        raise ValueError('Unsupported or incomplete console ring')
    ring = raw[HEADER_SIZE:HEADER_SIZE + size]
    if total <= size:
        data = ring[:total]
    else:
        start = total % size
        data = ring[start:] + ring[:start]
    return total, sequence, data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--file', default='/data/local/tmp/unixnu-songyuan/ioslog-v2.img')
    parser.add_argument('--raw-out', type=Path, required=True)
    parser.add_argument('--text-out', type=Path, required=True)
    args = parser.parse_args()
    filename = posixpath.normpath(args.file)
    if not filename.startswith('/data/local/tmp/unixnu-songyuan/'):
        parser.error('Console must be a file inside /data/local/tmp/unixnu-songyuan/')
    adb = [args.adb, '-s', args.serial]
    if subprocess.check_output(adb + ['shell', 'getprop', 'ro.product.device']).strip() != b'songyuan':
        parser.error('Connected device is not songyuan')
    command = f'{PINFILE} rawread {shlex.quote(filename)} /dev/block/by-name/userdata {HEADER_SIZE + RING_SIZE}'
    subprocess.run([sys.executable, Path(__file__).with_name('adb_root.py'),
                    '--adb', args.adb, '--serial', args.serial, '--out', args.raw_out, command], check=True)
    try:
        total, sequence, data = decode(args.raw_out.read_bytes())
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    args.text_out.parent.mkdir(parents=True, exist_ok=True)
    args.text_out.write_bytes(data)
    print(f'Console: {total} bytes written, flush {sequence}, {len(data)} bytes retained', flush=True)
    print(args.text_out.resolve(), flush=True)


if __name__ == '__main__':
    main()
