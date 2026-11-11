#!/usr/bin/env python3
"""Fetch only the kernel from a successful GitHub Actions artifact using ZIP ranges.

Requires the authenticated gh CLI. Debug symbols and debugger
scripts remain in the archive; zipfile verifies the selected member's CRC.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

class ArtifactRanges(io.RawIOBase):
    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.cache_start = 0
        self.cache = b''
        _, total = self.fetch(0, 0)
        self.size = total

    def fetch(self, start, end):
        # gh handles authentication and redirects; credentials and signed CDN
        # URLs stay out of command arguments, local manifests and diagnostics.
        response = subprocess.run(['gh', 'api', self.path, '--include',
                                   '-H', f'Range: bytes={start}-{end}'],
                                  capture_output=True, timeout=180)
        headers, separator, data = response.stdout.partition(b'\r\n\r\n')
        if not separator:
            headers, separator, data = response.stdout.partition(b'\n\n')
        fields = dict(line.decode().split(':', 1) for line in headers.splitlines() if b':' in line)
        fields = {key.lower(): value.strip() for key, value in fields.items()}
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', fields.get('content-range', ''))
        if response.returncode or not separator or not match or (
                int(match[1]), int(match[2]), len(data)) != (start, end, end - start + 1):
            raise RuntimeError('Artifact server did not return the requested ZIP range')
        return data, int(match[3])

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        target = offset if whence == io.SEEK_SET else (
            self.pos + offset if whence == io.SEEK_CUR else self.size + offset)
        if target < 0:
            raise ValueError('Negative ZIP offset')
        self.pos = target
        return target

    def read(self, size=-1):
        size = min(self.size - self.pos, size if size >= 0 else self.size)
        if size <= 0:
            return b''
        end = self.pos + size
        if not (self.cache_start <= self.pos and end <= self.cache_start + len(self.cache)):
            start = self.pos - self.pos % 65536
            stop = min(self.size, max(end, start + 65536))
            self.cache, total = self.fetch(start, stop - 1)
            if total != self.size:
                raise RuntimeError('Artifact size changed during download')
            self.cache_start = start
        data = self.cache[self.pos - self.cache_start:end - self.cache_start]
        self.pos = end
        return data


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    base = f'repos/{args.repo}/actions/runs/{args.run}'
    run = api(base)
    if run['status'] != 'completed' or run['conclusion'] != 'success':
        parser.error('Source run must have completed successfully')
    artifacts = api(base + '/artifacts')['artifacts']
    artifact, = [item for item in artifacts if item['name'] == 'xnu-kernel' and not item['expired']]
    remote = ArtifactRanges(f"repos/{args.repo}/actions/artifacts/{artifact['id']}/zip")
    with zipfile.ZipFile(remote) as archive:
        member, = [item for item in archive.infolist()
                   if Path(item.filename).name == 'kernel.development.pandora']
        print(f'Fetching kernel: {member.compress_size} compressed bytes of {remote.size}', flush=True)
        data = archive.read(member)
    if data[:4] != b'\xcf\xfa\xed\xfe':
        raise RuntimeError('Downloaded member is not a 64-bit Mach-O kernel')
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / 'kernel.development.pandora'
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (args.out / 'source.json').write_text(json.dumps({
        'repository': args.repo, 'run': args.run, 'head_sha': run['head_sha'],
        'artifact': artifact['id'], 'bytes': len(data), 'sha256': digest,
    }, indent=2) + '\n')
    print(f'Verified kernel: {len(data)} bytes, SHA256 {digest}', flush=True)


if __name__ == '__main__':
    main()
