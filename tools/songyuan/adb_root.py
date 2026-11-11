#!/usr/bin/env python3
"""Run one explicitly quoted root command over ADB, optionally saving binary output."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', required=True)
    p.add_argument('--serial', required=True)
    p.add_argument('--out', type=Path)
    p.add_argument('command')
    args = p.parse_args()
    result = subprocess.run([args.adb, '-s', args.serial, 'exec-out',
                             'su -c ' + shlex.quote(args.command)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
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
