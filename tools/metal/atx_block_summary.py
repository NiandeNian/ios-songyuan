#!/usr/bin/env python3
"""Read-only analysis of UniXNU serialized gallery snapshots (*.atx).

An .atx produced by this port has an Apple AAPL/HEAD container, a 16-byte
layout watermark at offset 76, then a 64-byte NZLayoutHeader at 0x4000 and a
linear ASTC 4x4 payload.  The payload blocks are constant-color ASTC blocks:
the first eight bytes select the constant-color mode, the last eight carry the
RGBA endpoints.  A solid opaque black block is therefore a *real encoded*
black pixel block, not an unwritten/zero-filled one.

This tool distinguishes three mutually exclusive hypotheses:
  * "the encoder never wrote the pixels"  -> all-zero payload blocks
  * "the CGImage/layout upload path lost data" -> zero or malformed payload
  * "the render target really was black" -> valid constant-black blocks

It never decodes or rewrites the snapshot, never computes a hash/CRC, and does
not touch the device.
"""
import argparse
import json
import struct
import sys
from collections import Counter
from pathlib import Path

AAPL = b"AAPL\r\n\x1a\n"
LAYOUT_MAGIC = b"UniXNU ASTC v1"
HEADER_OFFSET = 0x4000
HEADER_SIZE = 64
PAYLOAD_OFFSET = HEADER_OFFSET + HEADER_SIZE
BLOCK_BYTES = 16
CONSTANT_PREFIX = bytes.fromhex("fcfdffffffffffff")
CONSTANT_BLACK = CONSTANT_PREFIX + bytes.fromhex("000000000000ffff")


def summarize(path):
    raw = Path(path).read_bytes()
    out = {"file": str(path), "bytes": len(raw)}
    out["aapl_container"] = raw.startswith(AAPL)
    out["aapl_width_height"] = list(struct.unpack_from("<II", raw, 0x28)) if len(raw) >= 0x30 else None
    out["watermark_at_76"] = raw[76:76 + 16] == LAYOUT_MAGIC + b"\0\0"

    header = raw[HEADER_OFFSET:HEADER_OFFSET + HEADER_SIZE]
    if len(header) != HEADER_SIZE or not header.startswith(LAYOUT_MAGIC):
        out["error"] = "NZLayoutHeader missing at 0x4000"
        return out
    width, height, levels, payload, block_w, block_h = struct.unpack_from("<6Q", header, 16)
    out["layout"] = dict(width=width, height=height, levels=levels,
                         payload=payload, block_width=block_w, block_height=block_h)
    out["payload_actual_bytes"] = len(raw) - PAYLOAD_OFFSET
    out["payload_matches_header"] = out["payload_actual_bytes"] >= payload

    data = raw[PAYLOAD_OFFSET:PAYLOAD_OFFSET + payload]
    count = len(data) // BLOCK_BYTES
    blocks = [data[i * BLOCK_BYTES:(i + 1) * BLOCK_BYTES] for i in range(count)]
    frequencies = Counter(blocks)
    columns = (width + block_w - 1) // block_w
    rows = (height + block_h - 1) // block_h
    out["blocks"] = count
    out["expected_blocks"] = columns * rows
    out["all_zero_blocks"] = frequencies[b"\0" * BLOCK_BYTES]
    out["constant_black_blocks"] = frequencies[CONSTANT_BLACK]
    out["constant_color_blocks"] = sum(n for b, n in frequencies.items() if b.startswith(CONSTANT_PREFIX))
    out["unique_blocks"] = len(frequencies)
    out["nonblack_blocks"] = count - out["constant_black_blocks"]
    out["nonzero_payload_bytes"] = sum(1 for byte in data if byte)

    # Where the surviving non-black content sits.  A snapshot that contains
    # only ClockKit text will cluster its non-black blocks in the top rows.
    by_row = Counter()
    for index, block in enumerate(blocks):
        if block != CONSTANT_BLACK:
            by_row[index // columns] += 1
    out["nonblack_rows"] = len(by_row)
    out["nonblack_row_range"] = [min(by_row), max(by_row)] if by_row else None
    out["nonblack_top_rows"] = by_row.most_common(6)
    out["content_confined_to_top_12_rows"] = (
        bool(by_row) and max(by_row) < 12 and rows >= 20)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="write the full report as JSON")
    args = parser.parse_args()
    report = [summarize(p) for p in args.files]
    for item in report:
        print("== %s ==" % Path(item["file"]).name)
        if "error" in item:
            print("  ERROR:", item["error"])
            continue
        layout = item["layout"]
        print("  AAPL container=%s watermark=%s extent=%dx%d levels=%d block=%dx%d" % (
            item["aapl_container"], item["watermark_at_76"], layout["width"], layout["height"],
            layout["levels"], layout["block_width"], layout["block_height"]))
        print("  blocks=%d (expected %d) unique=%d all_zero=%d constant_black=%d constant_color=%d" % (
            item["blocks"], item["expected_blocks"], item["unique_blocks"], item["all_zero_blocks"],
            item["constant_black_blocks"], item["constant_color_blocks"]))
        print("  nonblack=%d across %d rows, range=%s, top rows=%s" % (
            item["nonblack_blocks"], item["nonblack_rows"], item["nonblack_row_range"],
            item["nonblack_top_rows"]))
        print("  verdict: %s" % (
            "unwritten/zero payload" if item["all_zero_blocks"] > item["blocks"] // 2 else
            "valid ASTC constant-black blocks (render target was black)" if item["constant_black_blocks"] > item["blocks"] // 2 else
            "mixed/colored content"))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("report: %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
