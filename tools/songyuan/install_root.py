#!/usr/bin/env python3
"""Install an HFSX image only into an existing, pinned songyuan test file."""
import argparse
import hashlib
import json
from pathlib import Path
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
    p.add_argument('--yes', action='store_true')
    p.add_argument('image', type=Path)
    args = p.parse_args()
    if not args.yes or not args.file.startswith('/data/local/tmp/unixnu-songyuan/'):
        p.error('Requires --yes and a file inside /data/local/tmp/unixnu-songyuan/')
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
    print('Writing checked pinned extents', flush=True)
    command = f'{pin} rawwrite {quoted_file} /dev/block/by-name/userdata all --yes'
    base = '/data/local/tmp/unixnu-songyuan/install-' + uuid.uuid4().hex
    report, status = base + '.log', base + '.status'
    remote = (command + ' >' + report + ' 2>&1; code=$?; '
              'printf "%s" "$code" >' + status)
    with image.open('rb') as f:
        # exec-in preserves all input bytes on Windows. shell translates LF
        # bytes; exec-out never forwards stdin. Remote completion is separate.
        proc = subprocess.run(adb + ['exec-in', 'su -W -c ' + shlex.quote(remote)], stdin=f,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    print((proc.stdout + proc.stderr).decode(errors='replace'), flush=True)
    proc.check_returncode()
    for _ in range(60):
        finished = root(f'if test -f {status}; then cat {status}; fi').strip()
        if finished:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Writer completion not confirmed; do not boot')
    print(root(f'cat {report}').decode(), flush=True)
    if finished != b'0':
        raise RuntimeError('Checked raw writer failed; do not boot')
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
