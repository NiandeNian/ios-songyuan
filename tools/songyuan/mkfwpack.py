#!/usr/bin/env python3
"""Pack files for XNU's pinned-file firmware reader and VFS installer.

Block 0: PANDFW01, u32 count, u32 reserved, then count entries containing
char name[48], u64 offset, u64 size. Files start at 4 KiB boundaries.
The optional files.txt entry describes VFS writes, mkdirs and renames.
"""
import argparse
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('files', type=Path, nargs='+')
    parser.add_argument('--size', type=int, help='Pad to this pinned file size in bytes')
    args = parser.parse_args()
    if not 0 < len(args.files) <= 63:
        parser.error('The firmware directory holds 1 to 63 files')
    names = [path.name.encode() for path in args.files]
    if len(set(names)) != len(names) or any(len(name) >= 48 for name in names):
        parser.error('File basenames must be unique and shorter than 48 bytes')
    if args.out.resolve() in [path.resolve() for path in args.files]:
        parser.error('Output cannot overwrite an input')
    entries, blobs, offset = [], [], 4096
    for path, name in zip(args.files, names):
        data = path.read_bytes()
        entries.append(struct.pack('<48sQQ', name, offset, len(data)))
        blob = data + b'\0' * (-len(data) % 4096)
        blobs.append(blob)
        offset += len(blob)
    size = args.size if args.size is not None else offset
    if size < offset or size % 4096:
        parser.error('Pack size must be 4 KiB aligned and hold all files')
    header = b'PANDFW01' + struct.pack('<II', len(entries), 0) + b''.join(entries)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('wb') as stream:
        stream.write(header + b'\0' * (4096 - len(header)))
        for blob in blobs:
            stream.write(blob)
        stream.truncate(size)
    print(f'{args.out}: {len(entries)} entries, {offset} bytes used, {size} bytes allocated')


if __name__ == '__main__':
    main()
