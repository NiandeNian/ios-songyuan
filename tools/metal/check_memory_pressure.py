#!/usr/bin/env python3
"""Run actual pure memory-pool checks without the runtime's POSIX-only test modules.

The pool and selected existing tests are copied verbatim. Only visibility changes
for standalone compilation; helpers called exclusively by Vulkan owners are omitted.
No replacement allocator or warning suppression is used.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def test_function(source, name):
    start = source.index('#[test]\nfn ' + name)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--rustc', type=Path, required=True)
    parser.add_argument('--linker', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    memory = (runtime / 'memory.rs').read_text(encoding='utf-8')
    start = memory.index('/// The block bookkeeping of one memory type:')
    pool = memory[start:memory.index("/// A device's live `VkDeviceMemory` blocks", start)]
    start = pool.index('    /// The payload of block `index`, to change.')
    end = pool.index('    /// Return `[offset', start)
    pool = pool[:start] + pool[end:]
    pool = pool[:pool.index('    /// Its live blocks, by kind, into `census`')] + '}\n'
    tests = (runtime / 'tests/memory.rs').read_text(encoding='utf-8')
    names = [
        'pooled_blocks_place_until_full_and_one_empty_block_is_kept',
        'dedicated_blocks_take_no_other_placement_and_free_with_their_resource',
        'pressure_releases_only_empty_spares_and_preserves_live_block_indices',
        'room_is_a_pooled_block_s_free_range_at_the_largest_alignment_asked',
    ]
    ranges = (runtime / 'range.rs').read_text(encoding='utf-8')
    # Public standalone modules keep externally callable allocator methods visible,
    # just as the real runtime's parent modules call them; no dead-code allowances.
    source = ('pub mod range {\n' + ranges.replace('pub(super)', 'pub') + '\n}\n'
              'use range::RangeAllocator;\n' + pool.replace('pub(super)', 'pub') + '\n' +
              '\n'.join(test_function(tests, name) for name in names))
    args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pressure-check-', dir=args.scratch) as scratch:
        path, binary = Path(scratch) / 'check.rs', Path(scratch) / 'check.exe'
        path.write_text(source, encoding='utf-8')
        subprocess.run([str(args.rustc.resolve()), '--test', '--edition=2024', '-O', '-D', 'warnings',
                        '-C', 'linker=' + str(args.linker.resolve()), str(path), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
