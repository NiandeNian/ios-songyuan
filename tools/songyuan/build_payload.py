#!/usr/bin/env python3
"""Build a songyuan RAM boot image from an XNU kernel and measured board inputs."""
import argparse
from pathlib import Path
import subprocess
import hashlib
import struct
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--llvm-bin', type=Path, required=True)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--ram-init', action='store_true')
    p.add_argument('--root-extents', type=Path)
    p.add_argument('--log-extents', type=Path)
    p.add_argument('--cmdline', default='')
    args = p.parse_args()
    if args.ram_init == bool(args.root_extents):
        p.error('Select --ram-init or --root-extents')
    root = Path(__file__).resolve().parents[2]
    tools = root / 'tools/songyuan'
    out = root / ('out/xnu-init' if args.ram_init else 'out/ios')
    out.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if sys.platform == 'win32' else ''
    tool = lambda name: str(args.llvm_bin.resolve() / (name + suffix))
    call = lambda cmd: subprocess.run([str(v) for v in cmd], check=True)
    adt_cmd = [sys.executable, tools / 'mkadt_songyuan.py', out / 'songyuan.adt']
    for key in ('root_extents', 'log_extents'):
        value = getattr(args, key)
        if value:
            adt_cmd += ['--' + key.replace('_', '-'), value.resolve()]
    call(adt_cmd)
    rd = 'md0' if args.ram_init else 'ufsroot0'
    cmdline = (f'debug=0x8 serial=3 cpus=1 keepsyms=1 -v -noprogress rd={rd} '
               'songyuan=1 pandora_touch=0 pandora_gpu=0 pandora_adsp=0 '
               'pandora_gauge=0 pandora_amp=0 pandora_therm=0 pandora_camera=0 '
               'pandora_wifi=0 pandora_cpu=0 pandora_files=0 ' + args.cmdline).strip()
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
