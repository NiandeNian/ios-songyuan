#!/usr/bin/env python3
"""Run the runtime's exact descriptor memo checks without its POSIX-only test modules."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--rustc', type=Path, required=True)
    parser.add_argument('--linker', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    module = args.runtime.resolve() / 'descriptor_memo.rs'
    args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='descriptor-check-', dir=args.scratch) as scratch:
        path, binary = Path(scratch) / 'checks.rs', Path(scratch) / 'checks.exe'
        path.write_text(f'#[path = "{module.as_posix()}"] mod descriptor_memo;\n', encoding='utf-8')
        subprocess.run([str(args.rustc.resolve()), '--test', '--edition=2024', '-O', '-D', 'warnings',
                        '-C', 'linker=' + str(args.linker.resolve()), str(path), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
