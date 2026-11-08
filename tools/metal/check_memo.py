"""Compile the runtime's actual pure LRU and its existing checks on Windows.

The full runtime test crate contains POSIX-only process tests. This harness selects
the self-contained LRU implementation and existing memo tests verbatim, rather than
maintaining another implementation. It also runs the explicit release lookup cost
measurement. Scratch sources and executables are removed on success or failure.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def section(text, first, last):
    start = text.index(first)
    return text[start:text.index(last, start)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--memo', type=Path, help='saved original memo.rs for a before measurement')
    parser.add_argument('--rustc', type=Path, required=True)
    parser.add_argument('--linker', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--benchmark-only', action='store_true', help='measure an original source that predates a regression fix')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    memo = (args.memo or runtime / 'pipeline/memo.rs').read_text(encoding='utf-8')
    tests = (runtime / 'tests/memo.rs').read_text(encoding='utf-8')
    # Both versions keep these source boundaries; a changed module must update this
    # selector explicitly, never silently exercise an outdated copy of the algorithm.
    start = memo.index('pub(crate) struct Lru')
    implementation = memo[start:memo.index("/// A memo entry's key.", start)]
    selected = section(tests, '/// Measure the hot memo paths', '/// The owned `ob3_texture_array`')
    key_module = (runtime / 'key_hash.rs').as_posix()
    source = (f'#[path = "{key_module}"] mod key_hash;\n'
              'use key_hash::KeyMap;\nuse std::hash::Hash;\nuse std::sync::Arc;\n')
    if 'BTreeMap' in implementation:
        source += 'use std::collections::BTreeMap;\n'
    source += implementation + selected
    args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='memo-check-', dir=args.scratch) as directory:
        directory = Path(directory)
        path, binary = directory / 'memo.rs', directory / 'memo.exe'
        path.write_text(source, encoding='utf-8')
        subprocess.run([str(args.rustc.resolve()), '--edition=2024', '--test', '-O',
                        '-D', 'warnings', '-C', 'linker=' + str(args.linker.resolve()),
                        str(path), '-o', str(binary)], check=True)
        if not args.benchmark_only:
            subprocess.run([str(binary)], check=True)
        subprocess.run([str(binary), 'memo_lookup_cost', '--ignored', '--nocapture'], check=True)


if __name__ == '__main__':
    main()
