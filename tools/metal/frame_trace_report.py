#!/usr/bin/env python3
"""Read one process's frame trace written by the NativeAdrenoMetal frontend, read-only.

  frame_trace_report.py frames.trace [--json OUT]

The writer (`NativeBridgeMetalFrontend.mm`, `traceStartWriter`) appends 72-byte little-endian
records, `struct TraceRec {uint32_t type,idx; uint64_t t0,t1,t2,t3; uint32_t a,b,c,d,tid,
cpu0,cpu1,tid2;}`. Type 1 is one committed command buffer:

  t0  created (host µs)          t1  committed
  t2  scheduled callback         t3  completion callback  (0 when the event did not happen)
  a   render passes              b   draws
  c   pixels >> 10               d   blits

The report separates the four intervals the frontend can observe without GPU timestamps:
encoding (t0..t1), commit→scheduled (t1..t2), scheduled→completion (t2..t3), commit→completion
(t1..t3). The last one **includes GPU execution and completion-callback latency**; it is an upper
bound, not a GPU time. GPU time itself comes from MTLCommandBuffer.GPUStartTime/GPUEndTime
(docs/phase1/P1-C-gpu-timing.md).
"""
import argparse
import json
import statistics
import struct
import sys

REC = struct.Struct('<IIQQQQIIIIIIII')
assert REC.size == 72


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    at = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[at]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace')
    parser.add_argument('--json', help='also write the summary here')
    args = parser.parse_args()

    data = open(args.trace, 'rb').read()
    if len(data) % REC.size:
        sys.exit(f'{args.trace}: {len(data)} bytes is not a multiple of {REC.size}')
    frames = []
    threads = 0
    for at in range(0, len(data), REC.size):
        typ, idx, t0, t1, t2, t3, a, b, c, d, tid, cpu0, cpu1, tid2 = REC.unpack_from(data, at)
        if typ == 4:
            threads += 1
            continue
        if typ != 1 or not t1:
            continue
        frames.append({'idx': idx, 'encode': t1 - t0, 'to_scheduled': (t2 - t1) if t2 else None,
                       'to_complete': (t3 - t2) if (t3 and t2) else None,
                       'total': (t3 - t1) if t3 else None, 'passes': a, 'draws': b,
                       'kpx': c, 'blits': d})
    if not frames:
        sys.exit(f'{args.trace}: no type-1 records ({len(data) // REC.size} records)')

    def col(name):
        return [f[name] for f in frames if f[name] is not None]

    def stats(name):
        values = col(name)
        if not values:
            return None
        return {'n': len(values), 'mean': round(statistics.fmean(values), 1),
                'p50': percentile(values, 0.50), 'p95': percentile(values, 0.95),
                'max': max(values)}

    total = [f['total'] for f in frames if f['total'] is not None]
    interval = [b['idx'] - a['idx'] for a, b in zip(frames, frames[1:]) if b['idx'] > a['idx']]
    summary = {
        'records': len(data) // REC.size, 'thread_names': threads, 'frames': len(frames),
        'encode_us': stats('encode'), 'commit_to_scheduled_us': stats('to_scheduled'),
        'scheduled_to_complete_us': stats('to_complete'), 'commit_to_complete_us': stats('total'),
        'passes_per_frame': stats('passes'), 'draws_per_frame': stats('draws'),
        'kpx_per_frame': stats('kpx'),
        'ordered': sum(1 for d in interval if d > 0), 'out_of_order': sum(1 for d in interval if d <= 0),
    }
    print(json.dumps(summary, indent=2))
    if args.json:
        open(args.json, 'w', encoding='utf-8').write(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
