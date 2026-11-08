"""Inspect the iOS Turnip dylib's loader contract without executing it."""
import argparse
import json
import re
import struct
from pathlib import Path


def inspect(path):
    """Read load commands, actual exported names and unresolved symbol bindings."""
    data = path.read_bytes()
    magic, cpu, subtype, filetype, count, _, _, _ = struct.unpack_from('<8I', data)
    if (magic, cpu, subtype, filetype) != (0xFEEDFACF, 0x100000C, 0, 6):
        raise ValueError('expected a thin arm64 Mach-O dylib')
    libraries, commands = [], {}
    cursor = 32
    for _ in range(count):
        command, size = struct.unpack_from('<II', data, cursor)
        if size < 8 or cursor + size > len(data):
            raise ValueError('invalid load command')
        commands[command] = cursor
        if command in (0xC, 0xD, 0x80000018, 0x8000001F, 0x20, 0x80000023):
            start = cursor + struct.unpack_from('<I', data, cursor + 8)[0]
            name = data[start:data.index(0, start, cursor + size)].decode()
            if command == 0xD:
                install_name = name
            else:
                libraries.append(name)
        cursor += size
    platform, minimum, sdk = struct.unpack_from('<III', data, commands[0x32] + 8)
    if platform != 2 or minimum != 0x100000:
        raise ValueError('expected iOS 16.0 device platform')
    symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', data, commands[2] + 8)
    strings = data[stroff:stroff + strsize]
    undefined, strong_lookup = [], []
    for index in range(nsyms):
        string, kind, _, desc, value = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
        if kind & 0xE0 or kind & 0xE or value or not kind & 1:
            continue
        name = strings[string:strings.index(0, string)].decode()
        ordinal = desc >> 8
        weak = bool(desc & 0x40)
        origin = libraries[ordinal - 1] if 1 <= ordinal <= len(libraries) else ordinal
        undefined.append({'name': name, 'weak': weak, 'library': origin})
        if ordinal in (0, 0xFE) and not weak:
            strong_lookup.append(name)

    if 0x80000033 in commands:
        exportoff, exportsize = struct.unpack_from('<II', data, commands[0x80000033] + 8)
    else:
        exportoff, exportsize = struct.unpack_from('<II', data, commands[0x80000022] + 40)
    trie = data[exportoff:exportoff + exportsize]
    exports = set()

    def uleb(at):
        value, shift = 0, 0
        while True:
            byte = trie[at]
            at += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, at
            shift += 7

    def visit(at, prefix):
        terminal, after = uleb(at)
        if terminal:
            exports.add(prefix)
        child = after + terminal
        children = trie[child]
        child += 1
        for _ in range(children):
            end = trie.index(0, child)
            suffix = trie[child:end].decode()
            offset, child = uleb(end + 1)
            visit(offset, prefix + suffix)

    visit(0, '')
    required = {'_vk_icdGetInstanceProcAddr', '_vk_icdNegotiateLoaderICDInterfaceVersion'}
    if not required <= exports:
        raise ValueError(f'missing ICD exports: {sorted(required - exports)}')
    signature_offset, signature_size = struct.unpack_from('<II', data, commands[0x1D] + 8)
    result = {
        'file': str(path.resolve()), 'bytes': len(data), 'format': 'Mach-O arm64 dylib',
        'platform': 'iOS', 'minimum_os': '16.0',
        'sdk': f'{sdk >> 16}.{sdk >> 8 & 255}.{sdk & 255}',
        'install_name': install_name, 'dependencies': libraries,
        'required_icd_exports': sorted(required), 'export_count': len(exports),
        'undefined_symbols': len(undefined),
        'optional_weak_lookup_count': sum(item['weak'] and item['library'] in (0, 0xFE) for item in undefined),
        'unresolved_strong_lookup': strong_lookup,
        'code_signature': {'offset': signature_offset, 'bytes': signature_size},
    }
    return result


def inspect_libraries(directory):
    """Require exported helper functions, not a linked shader with a dummy main."""
    result = {}
    for name in ('float32_spv.h', 'float64_spv.h'):
        path = directory / name
        words = [int(word, 16) for word in re.findall(r'0x[0-9a-fA-F]+', path.read_text())]
        if len(words) < 5 or words[0] != 0x07230203:
            raise ValueError(f'{name}: invalid SPIR-V header')
        at, functions, exports, entrypoints = 5, 0, 0, 0
        while at < len(words):
            size, opcode = words[at] >> 16, words[at] & 65535
            if not size or at + size > len(words):
                raise ValueError(f'{name}: invalid SPIR-V instruction')
            functions += opcode == 54
            entrypoints += opcode == 15
            # OpDecorate LinkageAttributes, with the Export linkage type.
            exports += opcode == 71 and size >= 5 and words[at + 2] == 41 and words[at + size - 1] == 0
            at += size
        if not functions or not exports or entrypoints:
            raise ValueError(f'{name}: expected an exported function library without an entry point')
        result[name] = {'bytes': path.stat().st_size, 'functions': functions,
                        'exported_functions': exports, 'entry_points': entrypoints}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--shader-dir', type=Path)
    args = parser.parse_args()
    result = inspect(args.binary)
    if args.shader_dir:
        result['spirv_libraries'] = inspect_libraries(args.shader_dir)
    rendered = json.dumps(result, indent=2) + '\n'
    if args.out:
        args.out.write_text(rendered, encoding='utf-8')
    print(rendered, end='')
    if result['unresolved_strong_lookup']:
        raise SystemExit('unresolved strong symbols would fail at load time')


if __name__ == '__main__':
    main()
