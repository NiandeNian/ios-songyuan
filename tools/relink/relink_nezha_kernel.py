#!/usr/bin/env python3
"""Relink run 62 with the compiled Nezha drivers, using Apple's ld64 port."""
import argparse
from pathlib import Path
import re
import shlex
import shutil
import subprocess

CONFIG = 'DEVELOPMENT_ARM64_PANDORA'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--buildtree', type=Path, required=True)
    p.add_argument('--ci', type=Path, required=True)
    p.add_argument('--objects', type=Path, required=True)
    p.add_argument('--ld', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    bt, ci, objects, out = (x.resolve() for x in (a.buildtree, a.ci, a.objects, a.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    config = bt / 'build/xnu.obj' / CONFIG
    replacements = {f.name.split('__')[-1].split('.')[0]: f for f in objects.glob('*.o')}
    additions = {'PeachMhiFirmware', 'PeachMhiHost', 'PeachWlfw',
                 'PeachCe', 'PeachHtc', 'PeachWmi', 'PeachHtt', 'PeachSrng', 'PeachRx', 'PeachTx', 'PeachStation', 'PeachRsn', 'PeachNet', 'PeachApple80211', 'PeachWLAN', 'pandora_wifi', 'pandora_amp_tas'}
    wanted = {'pandora_gpu', 'pandora_touch', 'pandora_adsp', 'pandora_files', 'pandora_dpu', 'pandora_cpu', 'pandora_prof', 'kern_exec', 'OSKext', 'SweetFramebuffer', 'SweetPlatformExpert', 'sweet_ufs'}
    if 'pandora_amp' in replacements:
        wanted.add('pandora_amp')
    if 'SweetSurface' in replacements:
        wanted.add('SweetSurface')
    if 'kern_exit' in replacements:
        wanted.add('kern_exit')
    if replacements.keys() != wanted | additions:
        raise ValueError(f'expected exactly the Nezha driver objects, got {list(replacements)}')
    files, replaced = [], set()
    for original in (config / 'link.filelist').read_text().splitlines():
        stem = Path(original).stem
        if stem in replacements:
            file = replacements[stem]
            replaced.add(stem)
        else:
            file = ci / 'obj' / original.split('/build/xnu.obj/')[1]
        if not file.is_file():
            raise FileNotFoundError(file)
        files.append(file)
    if replaced != wanted:
        raise ValueError(f'drivers missing from upstream filelist: {wanted - replaced}')
    files.extend(replacements[name] for name in sorted(additions))
    filelist = out.with_name(out.name + '.filelist')
    filelist.write_text(''.join(f'{f.as_posix()}\n' for f in files))
    text = (config / '.LDFLAGS').read_text().strip('\0\r\n ')
    text = re.sub(r'/Users/runner/work/[^/ ]+/[^/ ]+/dxb', bt.as_posix(), text)
    args = [str(a.ld.resolve()), '-arch', 'arm64']
    for flag in shlex.split(text)[1:]:
        if flag.startswith('-Wl,'):
            args.extend(flag[4:].split(','))
        elif flag.startswith('-l'):
            args.append(flag)
    # Windows: ld64.lld cannot open /dev/null for empty -sectcreate; use a real empty file.
    nullfile = out.with_name(out.name + '.null')
    nullfile.write_bytes(b'')
    for i, a in enumerate(args):
        if a == '/dev/null':
            args[i] = str(nullfile)
    cc = ci / 'linklibs/libcc_kext.a'
    if not cc.exists():
        shutil.copyfile(ci / 'linklibs/libclang_rt.cc_kext.a', cc)
    args += ['-L', str(ci / 'linklibs'), '-platform_version', 'macos', '14.0', '14.0',
             '-filelist', str(filelist)]
    args += [str(ci / 'obj' / CONFIG / n) for n in
             ('version.o', 'lastkerneldataconst.o', 'lastkernelconstructor.o', 'nonlto.o')]
    args += ['-o', str(out)]
    print(f'Linking {len(files)} objects: {len(replaced)} Nezha replacements, {len(additions)} additions', flush=True)
    subprocess.run(args, check=True)


if __name__ == '__main__':
    main()
