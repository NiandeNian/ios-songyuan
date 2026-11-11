#!/usr/bin/env python3
"""Install an HFSX image only into an existing, pinned songyuan test file."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import shlex
import subprocess
import time
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', required=True)
    p.add_argument('--serial', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--extents-out', type=Path, required=True)
    p.add_argument('--file', default='/data/local/tmp/unixnu-songyuan/iosroot.img')
    p.add_argument('--staging-file', help='Ordinary Android file used for verified ADB sync transfer')
    p.add_argument('--yes', action='store_true')
    p.add_argument('image', type=Path)
    args = p.parse_args()
    directory = '/data/local/tmp/unixnu-songyuan/'
    args.file = posixpath.normpath(args.file)
    staging = posixpath.normpath(args.staging_file or (args.file + '.source'))
    if not args.yes or not args.file.startswith(directory):
        p.error('Requires --yes and a file inside /data/local/tmp/unixnu-songyuan/')
    if not staging.startswith(directory) or staging == args.file:
        p.error('Staging must be a different file inside /data/local/tmp/unixnu-songyuan/')
    image = args.image.resolve()
    size = image.stat().st_size
    with image.open('rb') as f:
        f.seek(1024)
        if f.read(2) != b'HX' or size % 4096:
            p.error('Expected a complete, 4 KiB aligned HFSX image')
    adb = [args.adb, '-s', args.serial]
    if subprocess.check_output(adb + ['shell', 'getprop', 'ro.product.device']).strip() != b'songyuan':
        p.error('Connected device is not songyuan')
    pin = '/data/local/tmp/unixnu-songyuan-pinfile'

    def root(command, timeout=180):
        r = subprocess.run(adb + ['shell', '-T', 'su -W -c ' + shlex.quote(command)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if r.returncode:
            raise RuntimeError((r.stderr + r.stdout).decode(errors='replace'))
        return r.stdout

    quoted_file = shlex.quote(args.file)
    extent_cmd = f'{pin} extents {quoted_file} --json'
    before = json.loads(root(extent_cmd))
    if before['size'] != size:
        p.error('Image must exactly match the already allocated test file')
    print(root(f'{pin} status {quoted_file}').decode(), end='', flush=True)
    print('Checking image SHA256 before writing', flush=True)
    digest = hashlib.sha256()
    with image.open('rb') as f:
        while block := f.read(8 << 20):
            digest.update(block)
    if digest.hexdigest() != args.sha256.lower():
        p.error('Image SHA256 does not match')
    source = shlex.quote(staging)
    partial = shlex.quote(staging + '.partial')
    root(f'if test -L {source} || test -L {partial}; then exit 1; fi')
    existing_size = root(f'if test -f {source}; then stat -c %s {source}; fi').strip()
    staged = False
    if existing_size == str(size).encode():
        print('Checking existing staging image SHA256', flush=True)
        staged = root(f'sha256sum {source}', timeout=1800).split()[0].decode() == args.sha256.lower()
    if not staged:
        # ADB sync transfers files without terminal newline conversion. A long
        # exec-in stream can lose its input channel, so raw writes read a local
        # verified file on Android instead of the USB shell's standard input.
        print('Transferring staging image with ADB sync', flush=True)
        subprocess.run(adb + ['push', str(image), staging + '.partial'], check=True, timeout=1800)
        print('Checking transferred image SHA256 on Android', flush=True)
        transferred = root(f'sha256sum {partial}', timeout=1800).split()[0].decode()
        if transferred != args.sha256.lower():
            raise RuntimeError('Staging image hash mismatch; no raw write performed')
        root(f'mv {partial} {source}; chmod 600 {source}')
    else:
        print('Reusing verified staging image', flush=True)
    if json.loads(root(extent_cmd)) != before:
        raise RuntimeError('Pinned extents changed during transfer; no raw write performed')
    print('Writing checked pinned extents', flush=True)
    command = f'{pin} rawwrite {quoted_file} /dev/block/by-name/userdata all --yes'
    base = '/data/local/tmp/unixnu-songyuan/install-' + uuid.uuid4().hex
    report, status = base + '.log', base + '.status'
    remote = (command + ' <' + source + ' >' + report + ' 2>&1; code=$?; '
              'printf "%s" "$code" >' + status)
    root(remote, timeout=1800)
    for _ in range(60):
        finished = root(f'if test -f {status}; then cat {status}; fi').strip()
        if finished:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Writer completion not confirmed; do not boot')
    written_report = root(f'cat {report}').decode()
    print(written_report, flush=True)
    if finished != b'0':
        raise RuntimeError('Checked raw writer failed; do not boot')
    written = re.search(r'wrote (\d+) blocks \((\d+) bytes\); sha256 of what was written: ([0-9a-f]{64})',
                        written_report)
    if not written or (int(written[1]), int(written[2]), written[3]) != (
            size // 4096, size, args.sha256.lower()):
        raise RuntimeError('Writer did not confirm the complete image and hash; do not boot')
    after = json.loads(root(extent_cmd))
    if after != before:
        raise RuntimeError('Pinned extents changed; do not boot the image')
    print('Checking raw device SHA256', flush=True)
    raw = root(f'{pin} rawhash {quoted_file}', timeout=1800).decode()
    print(raw, flush=True)
    if args.sha256.lower() not in raw.lower().split():
        raise RuntimeError('Raw device hash mismatch; do not boot the image')
    args.extents_out.parent.mkdir(parents=True, exist_ok=True)
    args.extents_out.write_text(json.dumps(after, indent=2) + '\n')
    print('Installed and verified; extent table unchanged', flush=True)


if __name__ == '__main__':
    main()
