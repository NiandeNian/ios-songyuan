#!/usr/bin/env python3
"""Install a verified firmware/file pack into a dedicated pinned songyuan file."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import shlex
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--extents-out', type=Path, required=True)
    parser.add_argument('--file', default='/data/local/tmp/unixnu-songyuan/ios-files-v1.img')
    parser.add_argument('--create', action='store_true', help='Allocate a new pinned file if absent')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    filename = posixpath.normpath(args.file)
    if not args.yes or not re.fullmatch(
            r'/data/local/tmp/unixnu-songyuan/(?:ios-)?(?:files|fw)[a-zA-Z0-9_-]*\.img', filename):
        parser.error('Requires --yes and a dedicated files/fw pack path inside unixnu-songyuan')
    image = args.image.resolve()
    size = image.stat().st_size
    with image.open('rb') as stream:
        header = stream.read(4096)
    if size % (1 << 20) or header[:8] != b'PANDFW01':
        parser.error('Expected a firmware pack padded to a whole MiB')
    count = struct.unpack_from('<I', header, 8)[0]
    if not 0 < count <= 63:
        parser.error('Invalid firmware directory count')
    names = set()
    end = 4096
    for index in range(count):
        name, offset, length = struct.unpack_from('<48sQQ', header, 16 + 64 * index)
        name = name.split(b'\0', 1)[0]
        if (not name or name in names or b'/' in name or offset % 4096 or
                offset < end or offset + length > size):
            parser.error('Invalid, duplicate or overlapping firmware entry')
        names.add(name)
        end = (offset + length + 4095) & ~4095
    digest = hashlib.sha256()
    with image.open('rb') as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    if digest.hexdigest() != args.sha256.lower():
        parser.error('Pack SHA-256 does not match')
    adb = [args.adb, '-s', args.serial]
    if subprocess.check_output(adb + ['shell', 'getprop', 'ro.product.device']).strip() != b'songyuan':
        parser.error('Connected device is not songyuan')
    pin = '/data/local/tmp/unixnu-songyuan-pinfile'

    def root(command):
        result = subprocess.run(adb + ['shell', '-T', 'su -W -c ' + shlex.quote(command)],
                                capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).decode(errors='replace'))
        return result.stdout

    staging = filename + '.source'
    quoted = shlex.quote(filename)
    source, partial = shlex.quote(staging), shlex.quote(staging + '.partial')
    root(f'if test -L {quoted} || test -L {source} || test -L {partial}; then exit 1; fi')
    exists = bool(root(f'if test -e {quoted}; then printf yes; fi'))
    if exists and not args.extents_out.is_file():
        parser.error('Existing pack requires its previously recorded extent table')
    if not exists:
        if not args.create:
            parser.error('Pack file is absent; --create is required for a new allocation')
        print(root(f'{pin} create {quoted} {size >> 20}').decode(), flush=True)
    extent_command = f'{pin} extents {quoted} --json'
    before = json.loads(root(extent_command))
    if before['size'] != size:
        parser.error('Pack size differs from the pinned allocation')
    status = root(f'{pin} status {quoted}').decode()
    if ': pinned ' not in status:
        parser.error('Pack file is not pinned')
    print(status, flush=True)
    if exists and before != json.loads(args.extents_out.read_text()):
        parser.error('Pack extent mapping changed; no write performed')
    if not exists:
        args.extents_out.parent.mkdir(parents=True, exist_ok=True)
        args.extents_out.write_text(json.dumps(before, indent=2) + '\n')
    subprocess.run(adb + ['push', str(image), staging + '.partial'], check=True, timeout=180)
    if root(f'sha256sum {partial}').split()[0].decode() != args.sha256.lower():
        raise RuntimeError('Transferred staging pack SHA-256 mismatch')
    root(f'mv {partial} {source}; chmod 600 {source}')
    if json.loads(root(extent_command)) != before:
        raise RuntimeError('Pack extents changed during transfer')
    report = root(f'{pin} rawwrite {quoted} /dev/block/by-name/userdata all --yes <{source}').decode()
    print(report, flush=True)
    match = re.search(r'wrote (\d+) blocks \((\d+) bytes\); sha256 of what was written: ([0-9a-f]{64})', report)
    if not match or (int(match[1]) * 4096, int(match[2]), match[3]) != (size, size, args.sha256.lower()):
        raise RuntimeError('Writer did not confirm complete pack bytes and SHA-256')
    if json.loads(root(extent_command)) != before:
        raise RuntimeError('Pack extents changed during writing')
    raw = root(f'{pin} rawhash {quoted} /dev/block/by-name/userdata').decode()
    print(raw, flush=True)
    if args.sha256.lower() not in raw.split():
        raise RuntimeError('Raw device pack SHA-256 mismatch')
    args.extents_out.with_suffix('.install.json').write_text(json.dumps({
        'file': filename, 'size': size, 'sha256': args.sha256.lower(),
        'entries': count, 'extent_mapping_unchanged': True,
    }, indent=2) + '\n')
    print('Installed checked pack; pinned extent mapping unchanged', flush=True)


if __name__ == '__main__':
    main()
