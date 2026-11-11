#!/usr/bin/env python3
"""Cross-build the RAM-only songyuan hardware probe using LLVM on Windows/Linux."""
import argparse
from pathlib import Path
import subprocess
import hashlib
import struct
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--llvm-bin', type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = root / 'shim/hello'
    out = root / 'out/hello'
    out.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if sys.platform == 'win32' else ''

    def tool(name):
        return str(args.llvm_bin.resolve() / (name + suffix))

    flags = ['--target=aarch64-none-elf', '-O2', '-ffreestanding', '-fno-builtin',
             '-nostdlib', '-fno-pic', '-fno-stack-protector', '-mgeneral-regs-only',
             '-mstrict-align', '-Wall', '-Wextra', '-Werror']
    for name in ('start.S', 'hello.c'):
        subprocess.run([tool('clang'), *flags, '-c', str(source / name),
                        '-o', str(out / (name + '.o'))], check=True)
    reloc = subprocess.check_output([tool('llvm-readelf'), '-r', str(out / 'hello.c.o')]).decode()
    if 'R_AARCH64_ABS' in reloc:
        raise ValueError('Probe has absolute relocations and cannot be loaded anywhere')
    subprocess.run([tool('ld.lld'), '-T', str(source / 'hello.ld'), '--build-id=none',
                    str(out / 'start.S.o'), str(out / 'hello.c.o'), '-o', str(out / 'hello.elf')], check=True)
    subprocess.run([tool('llvm-objcopy'), '-O', 'binary', str(out / 'hello.elf'), str(out / 'Image')], check=True)
    kernel = (out / 'Image').read_bytes()
    image_size = struct.unpack_from('<Q', kernel, 16)[0]
    if kernel[56:60] != b'ARM\x64' or image_size < len(kernel) or image_size > 1048576:
        raise ValueError('Invalid arm64 Image header/size')
    (out / 'Image').write_bytes(kernel + b'\0' * (image_size - len(kernel)))
    subprocess.run([sys.executable, str(root / 'tools/songyuan/mkbootimg_v4.py'),
                    str(out / 'Image'), str(out / 'songyuan-hello-boot.img')], check=True)
    for name in ('Image', 'songyuan-hello-boot.img'):
        data = (out / name).read_bytes()
        print(f'{name}: {len(data)} bytes, SHA256 {hashlib.sha256(data).hexdigest()}')


if __name__ == '__main__':
    main()
