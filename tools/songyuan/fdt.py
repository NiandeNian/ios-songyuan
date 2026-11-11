"""Small read-only Flattened Device Tree decoder (no external dependencies)."""
import struct


def nodes(data):
    if len(data) < 40:
        raise ValueError('Truncated FDT header')
    magic, total, off_struct, off_strings, _, version, _, _, len_strings, len_struct = struct.unpack_from('>10I', data)
    if magic != 0xd00dfeed or total > len(data) or version < 17:
        raise ValueError('Invalid FDT header')
    if off_struct + len_struct > total or off_strings + len_strings > total:
        raise ValueError('FDT blocks exceed total size')
    strings = data[off_strings:off_strings + len_strings]
    offset, end, stack, result = off_struct, off_struct + len_struct, [], {}
    while offset + 4 <= end:
        token = struct.unpack_from('>I', data, offset)[0]
        offset += 4
        if token == 1:
            stop = data.index(b'\0', offset, end)
            stack.append(data[offset:stop].decode())
            result['/' + '/'.join(stack[1:])] = {}
            offset = (stop + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name_offset = struct.unpack_from('>II', data, offset)
            offset += 8
            if not stack or offset + length > end or name_offset >= len(strings):
                raise ValueError('Invalid FDT property')
            name = strings[name_offset:strings.index(b'\0', name_offset)].decode()
            result['/' + '/'.join(stack[1:])][name] = data[offset:offset + length]
            offset = (offset + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            return result
        else:
            raise ValueError(f'Unknown FDT token {token}')
    raise ValueError('Missing FDT end token')


def cells(value):
    if len(value) % 4:
        raise ValueError('Property is not a cell array')
    return list(struct.unpack('>' + 'I' * (len(value) // 4), value))


def text(value):
    return value.rstrip(b'\0').decode(errors='replace')


def readable(value, name=''):
    if not value:
        return True
    string_property = (name in ('compatible', 'status', 'model', 'device_type', 'enable-method',
                       'method', 'label', 'function', 'pins', 'regulator-name') or
                       name.endswith(('-names', '-name', '-type')))
    if string_property and value.endswith(b'\0') and all(32 <= c < 127 or c == 0 for c in value):
        return value.rstrip(b'\0').decode().split('\0')
    if len(value) % 4 == 0:
        return [hex(n) for n in cells(value)]
    return value.hex()
