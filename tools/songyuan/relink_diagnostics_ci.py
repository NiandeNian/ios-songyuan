#!/usr/bin/env python3
"""Recompile selected sources and relink the exact object set of a known Apple CI run."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess

CONFIG = 'DEVELOPMENT_ARM64_PANDORA'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--buildtree', type=Path, required=True)
    p.add_argument('--objects', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--baseline-run', type=int, required=True)
    p.add_argument('--patch', type=Path, action='append', default=[])
    p.add_argument('--source', action='append', required=True)
    a = p.parse_args()
    bt, objects, out = (x.resolve() for x in (a.buildtree, a.objects, a.out))
    out.mkdir(parents=True, exist_ok=True)
    config = bt / 'build/xnu.obj' / CONFIG
    native = objects / 'obj' / CONFIG
    libs = objects / 'linklibs'
    commands = []
    manifest = {'baseline_run': a.baseline_run, 'patches': [], 'sources': []}

    def invoke(cmd, cwd=None):
        commands.append({'argv': [str(x) for x in cmd], 'cwd': str(cwd) if cwd else None})
        (out / 'commands.json').write_text(json.dumps(commands, indent=2))
        subprocess.run(cmd, cwd=cwd, check=True)

    for patch in a.patch:
        patch = patch.resolve()
        invoke(['patch', '--batch', '-p1', '-i', str(patch)], cwd=bt / 'xnu')
        manifest['patches'].append({'path': patch.name, 'sha256': hashlib.sha256(patch.read_bytes()).hexdigest()})

    def rewrite(flags):
        text = flags.strip('\0\r\n ')
        text = text.replace('-DCONFIG_NMBCLUSTERS=((1024 * 512) / MCLBYTES)',
                            "'-DCONFIG_NMBCLUSTERS=((1024 * 512) / MCLBYTES)'")
        text = re.sub(r'/Users/runner/work/[^/ ]+/[^/ ]+/dxb', bt.as_posix(), text)
        text = re.sub(r'/Applications/[^ ]+/SDKs/[^ ]+\.sdk/+System/Library/Frameworks/Kernel\.framework/Versions/A/PrivateHeaders/platform',
                      (bt / 'sdk-kernel-platform/platform').as_posix(), text)
        return shlex.split(text)

    replacements = {}
    for source in a.source:
        relative = Path(source)
        if relative.is_absolute() or '..' in relative.parts or relative.suffix not in ('.c', '.cpp'):
            p.error('Source must be a relative .c/.cpp path inside xnu')
        component = relative.parts[0]
        if component not in ('osfmk', 'bsd', 'iokit', 'libkern', 'pexpert', 'security', 'libsa'):
            p.error('Unsupported source component')
        source_file = bt / 'xnu' / relative
        flags_file = config / component / 'DEVELOPMENT/.CFLAGS'
        if not flags_file.is_file():
            raise FileNotFoundError(flags_file)
        flags = rewrite(flags_file.read_text())[1:]
        compiler = subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip()
        extension = '.cpo' if relative.suffix == '.cpp' else '.o'
        object_file = out / (relative.stem + extension)
        if relative.suffix == '.cpp':
            flags += ['-std=gnu++17', '-fno-rtti', '-fno-exceptions']
        invoke([compiler, *flags, '-c', str(source_file), '-o', str(object_file)], cwd=flags_file.parent)
        key = (component, relative.stem)
        if key in replacements:
            raise ValueError('Duplicate replacement source')
        replacements[key] = object_file
        manifest['sources'].append({'path': source, 'sha256': hashlib.sha256(source_file.read_bytes()).hexdigest(),
                                    'object_sha256': hashlib.sha256(object_file.read_bytes()).hexdigest()})

    files, replaced = [], set()
    for original in (native / 'link.filelist').read_text().splitlines():
        if not original.strip():
            continue
        relative = Path(original.split('/build/xnu.obj/', 1)[1])
        key = (relative.parts[1], relative.stem)
        file = replacements.get(key, objects / 'obj' / relative)
        if key in replacements:
            if key in replaced:
                raise ValueError('Replacement matched more than one saved link object')
            replaced.add(key)
        if not file.is_file():
            raise FileNotFoundError(file)
        files.append(file)
    if replaced != replacements.keys():
        raise ValueError(f'Unmatched source objects: {replacements.keys() - replaced}')
    filelist = out / 'link.filelist'
    filelist.write_text(''.join(f'{f.as_posix()}\n' for f in files))
    cc = libs / 'libcc_kext.a'
    if not cc.exists():
        shutil.copyfile(libs / 'libclang_rt.cc_kext.a', cc)
    flags = rewrite((config / '.LDFLAGS').read_text())[1:]
    flags = [f'-L{libs.as_posix()}' if flag.startswith('-L') else flag for flag in flags]
    compiler = subprocess.check_output(['xcrun', '--find', 'clang++'], text=True).strip()
    extras = [native / name for name in ('version.o', 'lastkerneldataconst.o', 'lastkernelconstructor.o', 'nonlto.o')]
    for file in extras:
        if not file.is_file():
            raise FileNotFoundError(file)
        if file in files:
            raise ValueError(f'Link-only object also in saved file list: {file}')
    kernel = out / 'kernel.development.pandora'
    invoke([compiler, *flags, '-filelist', str(filelist), *[str(f) for f in extras], '-o', str(kernel)])
    strip = subprocess.check_output(['xcrun', '--find', 'strip'], text=True).strip()
    invoke([strip, '-S', str(kernel)])
    manifest.update({'linked_objects': len(files), 'replacements': len(replaced),
                     'kernel_sha256': hashlib.sha256(kernel.read_bytes()).hexdigest(),
                     'kernel_size': kernel.stat().st_size})
    (out / 'source.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
