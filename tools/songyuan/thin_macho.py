#!/usr/bin/env python3
"""Extract ordinary ARM64 from an Apple fat Mach-O container without modifying it."""
import argparse
from pathlib import Path
import struct


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    data = a.input.read_bytes()
    if data[:4] == b'\xca\xfe\xba\xbe':
        count = struct.unpack_from('>I', data, 4)[0]
        matches = []
        for i in range(count):
            cpu, sub, offset, size, _ = struct.unpack_from('>5I', data, 8 + 20 * i)
            if cpu == 0x100000c and sub == 0:
                if offset + size > len(data):
                    raise ValueError('Fat Mach-O slice exceeds the image')
                matches.append(data[offset:offset + size])
        if len(matches) != 1:
            raise ValueError('Expected one ordinary ARM64 slice')
        data = matches[0]
    if struct.unpack_from('<3I', data) != (0xfeedfacf, 0x100000c, 0):
        raise ValueError('Not an ordinary ARM64 Mach-O')
    a.output.write_bytes(data)
    print(f'Extracted ARM64 Mach-O: {len(data)} bytes')


if __name__ == '__main__':
    main()
