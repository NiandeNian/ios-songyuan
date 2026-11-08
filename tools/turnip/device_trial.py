"""Prepare, stage and inspect a bounded GPU component device trial.

Uses the existing component-update extents and the established VFS installer.
The caller explicitly starts the prepared image with tools/boot_nezha.py.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import adt
import addtrustcache
import mkadt_pandora
import mkpayload
sys.path.insert(0, str(ROOT / 'tools/pgputest'))
import mktc

DESTINATION = '/usr/lib/libvulkan_freedreno.dylib'
EXTENTS = ROOT / 'device/private/storage/metal-update-0013-extents.json'
PIN = '/data/local/tmp/nezha-ios/pinfile'
COMPONENTS = {'turnip': ('libvulkan_freedreno.dylib', DESTINATION),
              'metal': ('NativeAdrenoMetal', '/System/Library/Extensions/NativeAdrenoMetal.bundle/NativeAdrenoMetal'),
              'shim': ('NezhaStatusBar', '/System/Library/Extensions/NativeAdrenoMetal.bundle/NezhaStatusBar')}
FILES = {**COMPONENTS, 'clients': ('NezhaMetal.clients', '/System/Library/Extensions/NativeAdrenoMetal.clients')}
# Existing baseline executable: trusted and observed, never installed by a GPU update.
FILES['fairplay'] = ('pandora_sink', '/usr/libexec/pandora_sink')


def selected_files(args):
    selected = dict(COMPONENTS)
    if getattr(args, 'clients', None) or getattr(args, 'read_clients', False):
        selected['clients'] = FILES['clients']
    if getattr(args, 'fairplay_service', False) or getattr(args, 'read_fairplay', False):
        selected['fairplay'] = FILES['fairplay']
    return selected


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def build_loader(output, framebuffer=None):
    """Use the maintained loader builder with its sibling imports on embedded Python."""
    bootstrap = ('import runpy,sys; from pathlib import Path; '
                 'script=sys.argv.pop(1); sys.path.insert(0,str(Path(script).parent)); '
                 'runpy.run_path(script,run_name="__main__")')
    frame_args = ['--framebuffer', framebuffer] if framebuffer else []
    run(sys.executable, '-I', '-c', bootstrap, ROOT / 'tools/build_nezha.py', 'loader',
        '--llvm', WORKSPACE / 'work/toolchain/ndk/bin', '--payload', output / 'trusted.payload',
        *frame_args, '--out', output)


def unlimited(output, record):
    """Derive the user's existing unlimited RAM entry from an audited normal trial.

    SWEETXNU stores its command line in a fixed 608-byte header field. Only that
    field changes; the kernel, ADT, extent mapping and AMFI trust segment stay
    byte-for-byte those of the bounded candidate. This only assembles an image.
    """
    arguments = record['bootargs'].split()
    if arguments.count('pandora_reboot_after=90') != 1 or any(arg.startswith(('nezha_metal_validation=', 'nezha_surface_observe=', 'nezha_thumbnail_refresh=', 'pandora_stacks=', 'pandora_stacks_at=', 'pandora_stacks_every=')) for arg in arguments):
        raise ValueError('derive unlimited only from an audited normal 90-second trial')
    command = ' '.join(arg for arg in arguments if arg != 'pandora_reboot_after=90')
    payload = bytearray((output / 'trusted.payload').read_bytes())
    encoded = command.encode()
    if len(encoded) >= 608:
        raise ValueError('unlimited command line does not fit the payload')
    payload[72:680] = encoded + b'\0' * (608 - len(encoded))
    variant = output / 'unlimited'
    variant.mkdir(exist_ok=True)
    (variant / 'trusted.payload').write_bytes(payload)
    framebuffer = output / 'framebuffer.json'
    build_loader(variant, framebuffer if framebuffer.exists() else None)
    if (variant / 'nezha-loader-boot.img').read_bytes().count(payload) != 1:
        raise ValueError('unlimited image embeds a different payload')
    (variant / 'variant.json').write_text(json.dumps({
        'source_trial': str(output), 'bootargs': command, 'runtime_seconds': None,
        'device_booted': False, 'components': record['components']}, indent=2) + '\n')
    print(f'Assembled unlimited RAM image: {variant / "nezha-loader-boot.img"}; not booted.', flush=True)


def metadata(data):
    """Record Mach-O identity without file-integrity hashing."""
    if data[:4] != b'\xcf\xfa\xed\xfe':
        raise ValueError('expected a thin Mach-O component')
    uuid, cursor = None, 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, cursor)
        if size < 8 or cursor + size > len(data):
            raise ValueError('invalid Mach-O load command')
        if command == 0x1B:
            uuid = data[cursor + 8:cursor + 24].hex()
        cursor += size
    return {'bytes': len(data), 'uuid': uuid}


def file_metadata(name, data):
    if name != 'clients':
        if name == 'fairplay' and (len(data) < 32 or struct.unpack_from('<II', data, 4) != (0x100000c, 0) or
                                   struct.unpack_from('<I', data, 12)[0] != 2):
            raise ValueError('the installed FairPlay error responder must be an arm64 executable')
        if name == 'fairplay' and (b'pandora-sink: in place of' not in data or b'/System/Library/xpc/launchd.plist' not in data):
            raise ValueError('the baseline path does not contain the maintained daemon error responder')
        return metadata(data)
    if not data or len(data) >= 1024 or b'\0' in data:
        raise ValueError('client policy must fit the frontend\'s 1024-byte reader')
    return {'bytes': len(data), 'text': data.decode('utf-8')}


def package(entries):
    """Encode the existing PANDFW01 format with aligned payload records."""
    offset, records, bodies = 4096, [], []
    if len(entries) > 63:
        raise ValueError('too many firmware entries')
    for name, data in entries.items():
        encoded = name.encode()
        if len(encoded) >= 48:
            raise ValueError('firmware entry name is too long')
        records.append(struct.pack('<48sQQ', encoded, offset, len(data)))
        body = data + b'\0' * (-len(data) % 4096)
        bodies.append(body)
        offset += len(body)
    if offset > json.loads(EXTENTS.read_bytes())['size']:
        raise ValueError('update does not fit the existing pinned container')
    header = b'PANDFW01' + struct.pack('<II', len(entries), 0) + b''.join(records)
    return header + b'\0' * (4096 - len(header)) + b''.join(bodies)


def unpack(raw):
    """Read exact entry lengths; alignment padding is never part of a binary."""
    if raw[:8] != b'PANDFW01':
        raise ValueError('invalid firmware container')
    count = struct.unpack_from('<I', raw, 8)[0]
    if not 0 < count <= 63:
        raise ValueError('invalid firmware entry count')
    entries, ranges = {}, []
    for index in range(count):
        name, offset, size = struct.unpack_from('<48sQQ', raw, 16 + 64 * index)
        name = name.split(b'\0', 1)[0].decode()
        if not name or name in entries or offset < 4096 or offset % 4096 or offset + size > len(raw):
            raise ValueError('invalid firmware entry bounds')
        if any(offset < end and start < offset + size for start, end in ranges):
            raise ValueError('overlapping firmware entries')
        ranges.append((offset, offset + size))
        entries[name] = raw[offset:offset + size]
    return entries


def audit(output, record):
    """Check the install contract and actual embedded AMFI authorization before writing.

    CodeDirectory identities are required by AMFI, not file-integrity checks.
    Byte comparisons below establish which component the loader will install.
    """
    merged, _ = addtrustcache.raw_trust_cache(str(output / 'module1.tc'))
    trust = {merged[24 + i * 24:48 + i * 24] for i in range(struct.unpack_from('<I', merged, 20)[0])}
    for group in ('before', 'candidate'):
        for name in [*COMPONENTS, *(['fairplay'] if 'fairplay' in record['components'] else [])]:
            identity, kind = mktc.cdhash(output / group / name)
            if identity + bytes((kind, 0, 0, 0)) not in trust:
                raise ValueError(f'{group}/{name} is absent from the boot trust cache')
    for variant, group in (('update', 'candidate'), ('rollback', 'before')):
        entries = unpack((output / f'firmware-{variant}.img').read_bytes())
        install = ''
        for name in record['replacements']:
            entry, destination = FILES[name]
            if entries[entry] != (output / group / name).read_bytes():
                raise ValueError(f'{variant} container has a different {name}')
            install += f'{entry} 0755 {destination}.next\n> {destination}.next {destination}\n'
        if entries['files.txt'] != install.encode():
            raise ValueError(f'{variant} install list differs from the selected components')
    payload = (output / 'trusted.payload').read_bytes()
    if payload[72:680].split(b'\0', 1)[0].decode() != record['bootargs']:
        raise ValueError('payload boot arguments differ from the recorded trial')
    count = struct.unpack_from('<I', payload, 12)[0]
    _, _, offset, size = struct.unpack_from('<4Q', payload, 0x2A8 + 32 * (count - 1))
    embedded = payload[offset:offset + size]
    expected = addtrustcache.bundle([(output / 'module0.tc').read_bytes(), merged])
    if embedded != expected:
        raise ValueError('payload embeds different trust caches')
    image = (output / 'nezha-loader-boot.img').read_bytes()
    if image.count(payload) != 1:
        raise ValueError('boot image does not embed the prepared trusted payload exactly once')


def live_components(args, output):
    """Read installed files through the existing raw HFS reader while Android runs."""
    os.environ.update(ANDROID_SERIAL=args.serial, IOS_ADB=str(args.adb.resolve()),
                      IOS_IMAGE='/data/local/tmp/nezha-ios/root.img', IOS_PINFILE=PIN)
    import ioslog_fetch
    fs = ioslog_fetch.HFS(ioslog_fetch.Remote())
    catalog = fs.walk()
    result = {}
    output.mkdir(parents=True, exist_ok=True)
    selected = selected_files(args)
    for name, (_, path) in selected.items():
        parent = 2
        for component in PurePosixPath(path).parts[1:]:
            node = catalog.get((parent, component))
            if node is None:
                raise ValueError(f'missing installed component: {path}')
            parent = node[1]
        data = fs.read_fork(node[3][0], node[1])
        (output / name).write_bytes(data)
        result[name] = {'path': path, **file_metadata(name, data)}
    return result


def prepare(args):
    # Every ordinary Nezha trial keeps the device-proven unavailable-service
    # responder. A GPU profiler seed must not silently disable locationd again.
    args.fairplay_service = True
    args.out.mkdir(parents=True, exist_ok=True)
    before_file = args.out / 'installed-before.json'
    if before_file.exists():
        raise ValueError('use a new output directory: an old installed snapshot must not be reused')
    args.read_clients = bool(args.enable_client)
    before_file.write_text(json.dumps(live_components(args, args.out / 'before'), indent=2) + '\n')
    replacements = ([] if args.preserve_turnip else ['turnip']) + [name for name in ('metal', 'shim', 'clients') if getattr(args, name)]
    if args.enable_client:
        replacements.append('clients')
    if not replacements:
        raise ValueError('no replacement selected')
    candidate = args.out / 'candidate'
    candidate.mkdir(exist_ok=True)
    selected = selected_files(args)
    for name in selected:
        path = (None if args.preserve_turnip else args.binary) if name == 'turnip' else getattr(args, name, None)
        if name == 'clients' and args.enable_client:
            names = (args.out / 'before/clients').read_text().replace(',', ' ').split()
            for added in args.enable_client:
                if not added or any(char.isspace() or char in ',*\0' for char in added):
                    raise ValueError('enable-client takes one explicit process name')
                if added not in names:
                    names.append(added)
            data = (' '.join(names) + '\n').encode('utf-8')
        else:
            data = (path or args.out / 'before' / name).read_bytes()
        file_metadata(name, data)
        (candidate / name).write_bytes(data)
    # Keep the original firmware assets available to the kernel, but replace its
    # install list so an unrelated old settings/job installation is not replayed.
    adb = [str(args.adb.resolve()), '-s', args.serial]
    raw = subprocess.check_output(adb + ['exec-out', 'su -c ' + shlex.quote(
        f'{PIN} rawread /data/local/tmp/nezha-ios/fw.img /dev/block/by-name/userdata 2>/dev/null')])
    (args.out / 'original-fw.img').write_bytes(raw)
    entries = unpack(raw)
    install = ''
    for name in replacements:
        entry, destination = FILES[name]
        install += f'{entry} 0755 {destination}.next\n> {destination}.next {destination}\n'
    entries['files.txt'] = install.encode()
    for variant, group in (('update', 'candidate'), ('rollback', 'before')):
        for name in replacements:
            entries[FILES[name][0]] = (args.out / group / name).read_bytes()
        (args.out / f'firmware-{variant}.img').write_bytes(package(entries))

    # CodeDirectory identities here are required AMFI trust entries, not file
    # integrity checks. Trust both the candidate and the saved rollback.
    run(sys.executable, ROOT / 'tools/pgputest/mktc.py', args.out / 'components.tc',
        *(args.out / group / name for group in ('before', 'candidate')
          for name in [*COMPONENTS, *(['fairplay'] if args.fairplay_service else [])]))
    (args.out / 'module0.tc').write_bytes((args.seed / 'module0.tc').read_bytes())
    merged, _ = addtrustcache.raw_trust_cache(str(args.seed / 'module1.tc') + '+' + str(args.out / 'components.tc'))
    (args.out / 'module1.tc').write_bytes(merged)
    source = (args.seed / 'kernel.payload').read_bytes()
    command = source[72:680].split(b'\0', 1)[0].decode()
    if command.split().count('pandora_reboot_after=90') != 1:
        raise ValueError('expected the established 90-second seed')
    # Diagnostic work is opt-in per trial, never inherited from a seed. Keep the
    # established bounded reboot even when exercising a potentially stuck path.
    command = ' '.join(arg for arg in command.split() if not arg.startswith(('nezha_metal_validation=', 'nezha_surface_observe=', 'nezha_thumbnail_refresh=', 'pandora_stacks=', 'pandora_stacks_at=', 'pandora_stacks_every=')))
    # Old GPU profiling seeds predate the accepted Wi-Fi profile. The driver is
    # opt-in, so inheriting those seeds without this flag disables en0 entirely.
    # Preserve manual association; this flag only initializes the existing driver.
    wifi = [arg for arg in command.split() if arg.startswith('nezha_wifi=')]
    if wifi and wifi != ['nezha_wifi=1']:
        raise ValueError('the Nezha device trial requires the accepted nezha_wifi=1 profile')
    if not wifi:
        command += ' nezha_wifi=1'
    if args.fairplay_service:
        # CLMescalSigner waits forever on MIG 1203 when this unavailable hardware
        # service is only blocked. Reuse the installed baseline responder, whose
        # genuine MIG_BAD_ID releases locationd initialization (tools/fairplay).
        swap = 'sweetexecswap=fairplayd.H2:/usr/libexec/pandora_sink'
        existing = [arg for arg in command.split() if arg.startswith('sweetexecswap=')]
        if existing and existing != [swap]:
            raise ValueError('cannot replace an unrelated executable-swap profile')
        if not existing:
            command += ' ' + swap
        tokens = []
        for arg in command.split():
            if arg.startswith('sweetnoexec='):
                blocked = [name for name in arg.split('=', 1)[1].split(',') if name != 'fairplayd.H2']
                # This board has no cellular stack. Its noncellular CommCenter
                # still crashes; PrivacySettings blocks on its subscription RPC
                # until the crash-stop sink starts ~50 s later. Use that existing
                # error responder immediately, without changing telephony APIs.
                if 'CommCenter' not in blocked:
                    blocked.append('CommCenter')
                if blocked:
                    tokens.append('sweetnoexec=' + ','.join(blocked))
            else:
                tokens.append(arg)
        command = ' '.join(tokens)
        if not any(arg.startswith('sweetnoexec=') for arg in tokens):
            command += ' sweetnoexec=CommCenter'
        sink = [arg for arg in command.split() if arg.startswith('sweetsink=')]
        if sink and sink not in (['sweetsink=1'], ['sweetsink=2']):
            raise ValueError('cannot replace a custom daemon-sink profile')
        command = ' '.join(arg for arg in command.split() if not arg.startswith('sweetsink=')) + ' sweetsink=2'
    # A Settings navigation/hang trial needs enough time to reach the target
    # page. Keep the normal entry at 90 seconds and allow one bounded extension.
    if args.seconds != 90:
        command = ' '.join(f'pandora_reboot_after={args.seconds}' if arg == 'pandora_reboot_after=90' else arg
                           for arg in command.split())
    if args.metal_validation:
        command += ' nezha_metal_validation=1'
    if args.surface_observation:
        command += ' nezha_surface_observe=1'
    if args.astronomy_thumbnail_refresh:
        command += ' nezha_thumbnail_refresh=1'
    # Reuse the kernel's existing read-only stack sampler for a concrete UI hang.
    # Its default first snapshot is 60 seconds; retaining that default keeps the
    # service baseline plus two process names inside the 608-byte boot field.
    if args.stack_process:
        names = args.stack_process
        if len(names) > 4 or any(not name.isascii() or not name.replace('_', '').isalnum() or len(name) > 16 for name in names):
            raise ValueError('stack sampling accepts at most four ASCII process names of up to 16 characters')
        command = ' '.join(arg for arg in command.split() if not arg.startswith(('pandora_prof=', 'pandora_prof_hz=', 'pandora_stacks=', 'pandora_stacks_at=', 'pandora_stacks_every=')))
        command += ' pandora_stacks=' + ','.join(names) + ':20'
    offset, size = struct.unpack_from('<QQ', source, 32)
    tree, _ = adt.parse(source[offset:offset + size])
    chosen = next(node for node in tree.children if node.name == 'chosen')
    replacement = dict(mkadt_pandora.fw_extent_props(EXTENTS))
    for name in replacement:
        if sum(key == name for key, _, _ in chosen.props) != 1:
            raise ValueError(f'firmware extent property missing or duplicated: {name}')
    chosen.props = [(name, flags, replacement.get(name, data)) for name, flags, data in chosen.props]
    (args.out / 'device.adt').write_bytes(adt.serialize(tree))
    payload, _ = mkpayload.build(args.kernel, args.out / 'device.adt', command)
    (args.out / 'kernel.payload').write_bytes(payload)
    run(sys.executable, ROOT / 'tools/addtrustcache.py', args.out / 'kernel.payload',
        args.out / 'trusted.payload', args.out / 'module0.tc', args.out / 'module1.tc')
    # Preserve the known framebuffer measurements instead of guessing geometry.
    measured = {}
    for line in (args.seed / 'framebuffer.h').read_text().splitlines():
        if line.startswith('#define NEZHA_FB_'):
            _, name, value = line.split()
            measured[name.removeprefix('NEZHA_FB_').lower()] = int(value.removesuffix('ull'))
    if measured:
        if set(measured) != {'base', 'width', 'height', 'stride', 'format', 'unpack'}:
            raise ValueError('incomplete seed framebuffer measurements')
        (args.out / 'framebuffer.json').write_text(json.dumps(measured))
    build_loader(args.out, args.out / 'framebuffer.json' if measured else None)
    record = {'candidate': metadata((candidate / 'turnip').read_bytes()), 'seed': str(args.seed),
              'kernel': str(args.kernel.resolve()),
              'components': {name: file_metadata(name, (candidate / name).read_bytes()) for name in selected},
              'replacements': replacements,
              'runtime_seconds': args.seconds, 'bootargs': command, 'extents': str(EXTENTS),
              'device_staged': False, 'installed_verified': False, 'runtime_accepted': False}
    audit(args.out, record)
    (args.out / 'trial.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'stage', 'verify', 'audit', 'unlimited'))
    parser.add_argument('--adb', type=Path, default=Path(r'C:\Users\1\Downloads\adb\adb.exe'))
    parser.add_argument('--serial', default='2d611f9f')
    parser.add_argument('--binary', type=Path, default=ROOT / 'build/turnip-local/libvulkan_freedreno.dylib')
    parser.add_argument('--preserve-turnip', action='store_true', help='keep installed Turnip and replace only explicit Metal/shim inputs')
    parser.add_argument('--metal', type=Path, help='optional Metal replacement; defaults to preserving installed Metal')
    parser.add_argument('--shim', type=Path, help='optional shim replacement; defaults to preserving the installed shim')
    parser.add_argument('--metal-validation', action='store_true', help='enable the optional shim hardware checks for this bounded trial only')
    parser.add_argument('--surface-observation', action='store_true', help='record bounded completed IOSurface pixel samples for this trial only')
    parser.add_argument('--astronomy-thumbnail-refresh', action='store_true', help='enable the explicit one-shot astronomy gallery repair shim for this trial only')
    parser.add_argument('--stack-process', action='append', default=[], help='sample existing kernel thread stacks of this process at the kernel default start time and every 20 seconds, replacing compositor profiling')
    parser.add_argument('--seconds', type=int, choices=(90, 120), default=90, help='bounded RAM trial duration; use 120 only when reaching a Settings page requires more time')
    client_policy = parser.add_mutually_exclusive_group()
    client_policy.add_argument('--clients', type=Path, help='optional native Metal client policy, backed up and installed with the component')
    client_policy.add_argument('--enable-client', action='append', default=[], help='append one process to the live policy, preserving current clients')
    parser.add_argument('--kernel', type=Path, default=ROOT / 'build/linux/kernel.nezha-crash-accounting.prelinked')
    parser.add_argument('--seed', type=Path, default=ROOT / 'build/nezha-prof-90-v3')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.action == 'prepare':
        prepare(args)
        return
    record_file = args.out / 'trial.json'
    record = json.loads(record_file.read_bytes())
    args.read_clients = 'clients' in record['components']
    args.read_fairplay = 'fairplay' in record['components']
    audit(args.out, record)
    if args.action == 'unlimited':
        unlimited(args.out, record)
        return
    if args.action == 'audit':
        print('Container, component trust, payload and boot image agree.', flush=True)
        return
    if args.action == 'stage':
        run(sys.executable, ROOT / 'tools/write_nezha_image.py', '--adb', args.adb,
            '--serial', args.serial, '--extents', EXTENTS, args.out / 'firmware-update.img')
        record['device_staged'] = True
    else:
        after = live_components(args, args.out / 'after')
        (args.out / 'installed-after.json').write_text(json.dumps(after, indent=2) + '\n')
        record['installed_components_verified'] = {
            name: (args.out / 'after' / name).read_bytes() == (args.out / 'candidate' / name).read_bytes()
            for name in record['components']}
        record['installed_verified'] = all(record['installed_components_verified'].values())
        record_file.write_text(json.dumps(record, indent=2) + '\n')
        if not record['installed_verified']:
            raise RuntimeError('installed GPU components differ from the prepared combination')
        print(json.dumps(after, indent=2), flush=True)
    record_file.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
