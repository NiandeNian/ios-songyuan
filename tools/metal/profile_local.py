"""Resolve kernel sampling stacks against local Mach-O components on Windows.

Uses the existing profiler log parser. Only explicitly supplied images are named;
shared-cache samples remain external. Separate symbol files must contain the same
code at the same addresses as the installed component, so stale symbols fail.
"""
import argparse
import bisect
from collections import Counter
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prof


def macho(path):
    data = path.read_bytes()
    if data[:4] != b'\xcf\xfa\xed\xfe':
        raise ValueError(f'{path}: expected thin 64-bit Mach-O')
    cursor, segment, text, symtab = 32, None, None, None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, cursor)
        if size < 8 or cursor + size > len(data):
            raise ValueError(f'{path}: invalid load command')
        if command == 2:
            symtab = struct.unpack_from('<4I', data, cursor + 8)
        if command == 0x19:
            if data[cursor + 8:cursor + 24].rstrip(b'\0') == b'__TEXT':
                segment = struct.unpack_from('<Q', data, cursor + 24)[0]
            for index in range(struct.unpack_from('<I', data, cursor + 64)[0]):
                section = cursor + 72 + index * 80
                if data[section:section + 16].rstrip(b'\0') == b'__text':
                    address, length, offset = struct.unpack_from('<QQI', data, section + 32)
                    text = address, data[offset:offset + length]
        cursor += size
    if segment is None or text is None or symtab is None:
        raise ValueError(f'{path}: missing code or symbols')
    symbol_offset, count, string_offset, string_size = symtab
    strings = data[string_offset:string_offset + string_size]
    symbols = []
    for index in range(count):
        string, kind, _, _, address = struct.unpack_from('<IBBHQ', data, symbol_offset + 16 * index)
        if kind & 0xE0 or kind & 0xE != 0xE or not text[0] <= address < text[0] + len(text[1]):
            continue
        symbols.append((address, strings[string:strings.index(0, string)].decode('utf-8')))
    return segment, text, sorted(symbols)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--component', nargs=3, action='append', required=True,
                        metavar=('NAME', 'BINARY', 'SYMBOLS'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    images, total, _, _, _, reports = prof.parse_log(str(args.log), 0)
    components = []
    for name, binary, symbols in args.component:
        segment, code, _ = macho(Path(binary))
        symbol_segment, symbol_code, names = macho(Path(symbols))
        if (segment, code) != (symbol_segment, symbol_code):
            raise ValueError(f'{name}: symbols describe different code or addresses')
        loads = [base for base, path in images.items() if Path(path).name == name]
        if len(loads) != 1:
            raise ValueError(f'{name}: expected one loaded image, found {len(loads)}')
        components.append((name, loads[0], segment, code[0], len(code[1]), names,
                           [address for address, _ in names]))

    def resolve(pc):
        for name, load, segment, start, length, symbols, addresses in components:
            address = pc - load + segment
            if start <= address < start + length:
                index = bisect.bisect_right(addresses, address) - 1
                return name + ':' + (symbols[index][1] if index >= 0 else '<unnamed>')
        return '<external>'

    own, inclusive, samples = Counter(), Counter(), 0
    for (lr, frames), (user, _) in total['s'].items():
        samples += user
        names = [resolve(pc - (4 if index else 0)) for index, pc in enumerate(frames)]
        if lr:
            names.append(resolve(lr - 4))
        own[names[0]] += user
        for name in set(names):
            inclusive[name] += user
    result = {'reports': reports, 'user_samples': samples,
              'scope': 'Explicit local images only; inclusive counts overlap',
              'self': own.most_common(), 'inclusive': inclusive.most_common()}
    args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'reports': reports, 'user_samples': samples, 'self_top': own.most_common(8)}, indent=2))


if __name__ == '__main__':
    main()
