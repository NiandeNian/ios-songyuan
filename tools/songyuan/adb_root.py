#!/usr/bin/env python3
"""Run one explicitly quoted root command over ADB, optionally saving binary output."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', required=True)
    p.add_argument('--serial', required=True)
    p.add_argument('--out', type=Path)
    p.add_argument('command')
    args = p.parse_args()
    adb = [args.adb, '-s', args.serial]
    command = args.command
    mode = ['shell', '-T']
    status = diagnostic = None
    if args.out:
        # Windows adb shell translates binary LF bytes. exec-out is binary,
        # but merges stderr and omits exit status, so keep these in sidecars.
        tag = uuid.uuid4().hex
        base = '/data/local/tmp/unixnu-songyuan-capture-' + tag
        status, diagnostic = base + '.status', base + '.stderr'
        command = ('{ ' + command + '; } 2>' + shlex.quote(diagnostic) +
                   '; code=$?; printf "%s" "$code" >' + shlex.quote(status))
        mode = ['exec-out']
    result = subprocess.run(adb + mode + ['su -W -c ' + shlex.quote(command)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if status:
        control = subprocess.run(adb + ['shell', '-T', 'su -W -c ' + shlex.quote(
            'cat ' + status + '; cat ' + diagnostic + ' >&2; rm -f ' + status + ' ' + diagnostic)],
            capture_output=True, timeout=30)
        if control.returncode or control.stdout.strip() != b'0':
            sys.stderr.buffer.write(control.stderr)
            raise SystemExit(1)
    if result.returncode:
        sys.stderr.buffer.write(result.stderr + result.stdout)
        raise SystemExit(result.returncode)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(result.stdout)
    else:
        sys.stdout.buffer.write(result.stdout)


if __name__ == '__main__':
    main()
