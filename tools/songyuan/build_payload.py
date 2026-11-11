#!/usr/bin/env python3
"""Build a songyuan RAM boot image from an XNU kernel and measured board inputs."""
import argparse
from pathlib import Path
import subprocess
import hashlib
import struct
import sys
import os
import json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--llvm-bin', type=Path, required=True)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--ram-init', action='store_true')
    p.add_argument('--root-extents', type=Path)
    p.add_argument('--log-extents', type=Path)
    p.add_argument('--fw-extents', type=Path,
                   help='Verified pinned file pack for the kernel VFS installer')
    p.add_argument('--wifi-mac-file', type=Path,
                   help='Private Android MAC capture for MobileGestalt and the identity publisher')
    p.add_argument('--empty-syscfg', action='store_true',
                   help='Publish a header-only guest SysCfg for absent Apple diagnostic hardware')
    p.add_argument('--apple-adt', type=Path)
    p.add_argument('--trust-cache', type=Path, action='append', default=[])
    p.add_argument('--cmdline', default='')
    args = p.parse_args()
    if args.ram_init == bool(args.root_extents):
        p.error('Select --ram-init or --root-extents')
    for path in [args.kernel, args.root_extents, args.log_extents, args.fw_extents, args.wifi_mac_file,
                 args.apple_adt, *args.trust_cache]:
        if path is not None and not path.is_file():
            p.error(f'Missing input: {path}')
    if args.log_extents:
        table = json.loads(args.log_extents.read_text())
        if table['size'] < 4096 + (4 << 20):
            p.error('Console log requires a 4 MiB ring plus one 4 KiB header block')
    if not args.ram_init and (not args.apple_adt or not args.trust_cache):
        p.error('iOS root boot requires --apple-adt and at least one --trust-cache')
    if args.ram_init and args.fw_extents:
        p.error('The VFS installer requires an iOS root')
    if args.ram_init and args.empty_syscfg:
        p.error('Guest SysCfg is an iOS userspace diagnostic option')
    root = Path(__file__).resolve().parents[2]
    tools = root / 'tools/songyuan'
    out = root / ('out/xnu-init' if args.ram_init else 'out/ios')
    out.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if sys.platform == 'win32' else ''
    tool = lambda name: str(args.llvm_bin.resolve() / (name + suffix))
    env = os.environ.copy()
    if args.apple_adt:
        env['D22AP_ADT'] = str(args.apple_adt.resolve())
    call = lambda cmd: subprocess.run([str(v) for v in cmd], check=True, env=env)
    adt_cmd = [sys.executable, tools / 'mkadt_songyuan.py', out / 'songyuan.adt']
    for key in ('root_extents', 'log_extents', 'fw_extents'):
        value = getattr(args, key)
        if value:
            adt_cmd += ['--' + key.replace('_', '-'), value.resolve()]
    if args.wifi_mac_file:
        adt_cmd += ['--wifi-mac-file', args.wifi_mac_file.resolve()]
    if args.empty_syscfg:
        adt_cmd += ['--empty-syscfg']
    call(adt_cmd)
    rd = 'md0' if args.ram_init else 'ufsroot0'
    files = int(bool(args.fw_extents))
    board_cmdline = (f'debug=0x8 serial=3 cpus=1 keepsyms=1 -v -noprogress rd={rd} '
                     'songyuan=1 pandora_touch=0 pandora_gpu=0 pandora_adsp=0 '
                     'pandora_gauge=0 pandora_amp=0 pandora_therm=0 pandora_camera=0 '
                     f'pandora_wifi=0 pandora_cpu=0 pandora_files={files}')
    fixed_keys = {token.split('=', 1)[0] for token in board_cmdline.split() if '=' in token}
    if any(token.split('=', 1)[0] in fixed_keys for token in args.cmdline.split()):
        raise ValueError('Extra boot arguments duplicate a fixed board setting')
    cmdline = (board_cmdline + ' ' + args.cmdline).strip()
    if len(cmdline.encode()) >= 608:
        raise ValueError('Boot arguments exceed the XNU 608-byte command line')
    payload_cmd = [sys.executable, tools / 'mkpayload.py', args.kernel.resolve(),
                   out / 'songyuan.adt', out / 'payload.bin', '--cmdline', cmdline]
    if args.ram_init:
        call([tool('clang'), '-target', 'arm64-apple-ios16.0', '-c', tools / 'sweet-init.S',
              '-o', out / 'init.o'])
        call([sys.executable, tools / 'mkinit.py', out / 'init.o', out / 'init'])
        payload_cmd += ['--ramdisk', out / 'init']
    call(payload_cmd)
    if args.trust_cache:
        call([sys.executable, tools / 'addtrustcache.py', out / 'payload.bin',
              out / 'payload-trusted.bin', *[path.resolve() for path in args.trust_cache]])
        (out / 'payload-trusted.bin').replace(out / 'payload.bin')
    flags = ['--target=aarch64-none-elf', '-O2', '-ffreestanding', '-fno-builtin',
             '-nostdlib', '-fno-pic', '-fno-stack-protector', '-mgeneral-regs-only',
             '-mstrict-align', '-Wall', '-Wextra', '-Werror']
    (out / 'logo.bin').write_bytes(b'')
    objects = []
    for name in ('start.S', 'loader.c', 'payload.S', 'logo.S'):
        obj = out / (name + '.o')
        extra = []
        if name == 'payload.S':
            extra = ['-DPAYLOAD="' + (out / 'payload.bin').as_posix() + '"']
        if name == 'logo.S':
            extra = ['-DLOGO="' + (out / 'logo.bin').as_posix() + '"']
        call([tool('clang'), *flags, *extra, '-c', root / 'shim/loader' / name, '-o', obj])
        objects.append(obj)
    relocs = subprocess.check_output([tool('llvm-readelf'), '-r', out / 'loader.c.o']).decode()
    if 'R_AARCH64_ABS' in relocs:
        raise ValueError('Loader contains absolute relocations')
    call([tool('ld.lld'), '-T', root / 'shim/loader/loader.ld', '--build-id=none',
          *objects, '-o', out / 'loader.elf'])
    call([tool('llvm-objcopy'), '-O', 'binary', out / 'loader.elf', out / 'Image'])
    image = (out / 'Image').read_bytes()
    if image[56:60] != b'ARM\x64' or struct.unpack_from('<Q', image, 16)[0] != len(image):
        raise ValueError('Invalid complete loader Image header')
    call([sys.executable, tools / 'mkbootimg_v4.py', out / 'Image', out / 'boot.img'])
    (out / 'BOOTARGS.txt').write_text(cmdline + '\n')
    print('boot.img SHA256', hashlib.sha256((out / 'boot.img').read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
