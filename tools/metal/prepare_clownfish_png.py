#!/usr/bin/env python3
"""Prepare a reversible Clownfish alpha-layer repair pack.

The 90-second ImageIO diagnostic proved that alpha-bearing HEIC layers have
complete CGImage metadata but no readable pixel backing on this device, while
the alpha-free Background.HEIC decodes fully.  The host-decoded PNG copies in
collection-assets are real RGBA PNGs with the same extents, so the smallest
product repair is to point the CAML `contents src` at those PNGs instead of
adding a decoder that does not exist here.

This tool only *builds a pack*; it never writes to the device.  It emits:
  firmware-clownfish.img          install PNGs + rewritten main.caml + backups
  firmware-clownfish-rollback.img restore the original main.caml
  plan.json                       exact paths, substitutions, static checks

Original HEIC files, wallpaper configuration, wallpaper.plist, index.xml and
assetManifest.caml are never modified or removed.  Each original main.caml is
also installed verbatim under a fixed backup suffix before being replaced, so
the change is reversible even if the rollback pack is not used.
"""
import argparse
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

TAGS = ("375w-812h", "393w-852h", "430w-932h")
LAYERS = ("Background", "Floating", "Foreground")
PREFIX = "/Library/Wallpaper/Collections/Clownfish~iphone.wallpaperCollection/"
BACKUP_SUFFIX = ".nezha-before-clownfish-20261104"
MAX_ENTRIES = 63
MAX_FILES_TXT = 16384
SRC_RE = re.compile(r'src="assets/([A-Za-z0-9_]+)\.HEIC"')


def png_info(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    if color != 6 or depth != 8:
        raise ValueError("PNG is not 8-bit RGBA (color type %d)" % color)
    return width, height


def pack(entries, extents_size):
    """PANDFW01 container, identical layout to the maintained GPU updater."""
    if len(entries) > MAX_ENTRIES:
        raise ValueError("too many firmware entries: %d" % len(entries))
    offset, records, bodies = 4096, [], []
    for name, data in entries.items():
        encoded = name.encode()
        if len(encoded) >= 48:
            raise ValueError("entry name too long: %s" % name)
        records.append(struct.pack("<48sQQ", encoded, offset, len(data)))
        body = data + b"\0" * (-len(data) % 4096)
        bodies.append(body)
        offset += len(body)
    if offset > extents_size:
        raise ValueError("pack %d bytes exceeds pinned container %d" % (offset, extents_size))
    header = b"PANDFW01" + struct.pack("<II", len(entries), 0) + b"".join(records)
    if len(header) > 4096:
        raise ValueError("directory does not fit one block")
    raw = header + b"\0" * (4096 - len(header)) + b"".join(bodies)
    return raw, offset


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", type=Path, required=True, help="collection-assets directory (manifest.json + PNG + CAML)")
    ap.add_argument("--extents", type=Path, required=True, help="pinned GPU update extents JSON (for capacity)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    manifest = json.loads((args.assets / "manifest.json").read_text(encoding="utf-8"))
    extents = json.loads(args.extents.read_text(encoding="utf-8"))["size"]

    # Index the extracted, device-identical Clownfish files.
    mains, heics = {}, defaultdict(list)
    for item in manifest:
        source = item["source"]
        if not source.startswith(PREFIX) or "Clownfish-" not in source:
            continue
        tag = next((t for t in TAGS if "Clownfish-" + t in source), None)
        layer = next((l for l in LAYERS if "Clownfish_" + l + "-" in source), None)
        if tag is None or layer is None or not item.get("local"):
            continue
        if source.endswith("/main.caml"):
            mains[(tag, layer)] = (source, item["local"])
        elif source.endswith(".HEIC"):
            heics[(tag, layer)].append((source.rsplit("/", 1)[-1], item["local"], source))

    missing = [(t, l) for t in TAGS for l in LAYERS if (t, l) not in mains]
    if missing:
        raise SystemExit("missing main.caml for %s" % missing)

    # The alpha-bearing names are the 375w Floating/Foreground assets.  The
    # 393w/430w HEICs were verified byte-identical to these, so one PNG per
    # name is enough for all three device variants.
    alpha_names = sorted(name[:-5] for name, _, _ in heics[("375w-812h", "Floating")] +
                         heics[("375w-812h", "Foreground")])
    png_bytes, png_meta = {}, {}
    for tag, layer in [("375w-812h", "Floating"), ("375w-812h", "Foreground")]:
        for filename, local, _ in heics[(tag, layer)]:
            stem = filename[:-5]
            png_path = args.assets / (local[:-5] + ".png")
            data = png_path.read_bytes()
            width, height = png_info(data)
            png_bytes[stem] = data
            png_meta[stem] = {"source_heic": filename, "png": png_path.name,
                              "width": width, "height": height, "bytes": len(data)}
    if set(png_bytes) != set(alpha_names):
        raise SystemExit("PNG set does not match alpha HEIC set")

    entries, lines, report = {}, [], []
    for stem, data in png_bytes.items():
        entries["png_" + stem + ".png"] = data

    # Install PNGs into the exact assets directory each layer reads from.
    for tag in TAGS:
        for layer in LAYERS:
            caml_path, local = mains[(tag, layer)]
            ca_dir = caml_path.rsplit("/main.caml", 1)[0]
            original = (args.assets / local).read_bytes()
            text = original.decode("utf-8")
            names = sorted(set(SRC_RE.findall(text)))
            alpha_here = [n for n in names if n in png_bytes]
            patched = text
            for stem in alpha_here:
                old = 'src="assets/%s.HEIC"' % stem
                new = 'src="assets/%s.png"' % stem
                count = patched.count(old)
                if not count:
                    raise SystemExit("expected %s in %s" % (old, local))
                patched = patched.replace(old, new)
            for stem in alpha_here:
                if 'src="assets/%s.HEIC"' % stem in patched:
                    raise SystemExit("unreplaced alpha src in %s" % local)
            # Background.HEIC decodes correctly on this device and is left alone.
            kept_background = 'src="assets/Background.HEIC"' in patched
            key_tag = tag.split("-")[0]
            caml_entry = "caml_%s_%s" % (key_tag, layer)
            backup_entry = "bak_%s_%s" % (key_tag, layer)
            entries[caml_entry] = patched.encode("utf-8")
            entries[backup_entry] = original
            for stem in alpha_here:
                lines.append("png_%s.png 0644 %s/assets/%s.png" % (stem, ca_dir, stem))
            lines.append("%s 0644 %s/main.caml%s" % (backup_entry, ca_dir, BACKUP_SUFFIX))
            lines.append("%s 0644 %s/main.caml.next" % (caml_entry, ca_dir))
            lines.append("> %s/main.caml.next %s/main.caml" % (ca_dir, ca_dir))
            report.append({"tag": tag, "layer": layer, "ca_dir": ca_dir,
                           "main_caml": caml_path, "local": local,
                           "replaced_src": alpha_here, "background_heic_kept": kept_background,
                           "caml_entry": caml_entry, "backup_entry": backup_entry})

    files_txt = ("\n".join(lines) + "\n").encode()
    if len(files_txt) > MAX_FILES_TXT:
        raise SystemExit("files.txt %d bytes exceeds installer limit %d" % (len(files_txt), MAX_FILES_TXT))
    entries["files.txt"] = files_txt
    for line in lines:
        path = line.rsplit(" ", 1)[-1]
        if not line.startswith(">"):
            if not path.startswith(PREFIX) or ".." in path:
                raise SystemExit("refused install path: %s" % path)
    raw, size = pack(entries, extents)

    # Rollback pack: restore the nine original main.caml files.  PNGs stay in
    # place but are unreferenced, which is harmless and keeps rollback small.
    rb_entries, rb_lines = {}, []
    for item in report:
        tag_key = item["tag"].split("-")[0]
        entry = "rb_%s_%s" % (tag_key, item["layer"])
        rb_entries[entry] = entries[item["backup_entry"]]
        ca = item["ca_dir"]
        rb_lines.append("%s 0644 %s/main.caml.next" % (entry, ca))
        rb_lines.append("> %s/main.caml.next %s/main.caml" % (ca, ca))
    rb_txt = ("\n".join(rb_lines) + "\n").encode()
    if len(rb_txt) > MAX_FILES_TXT:
        raise SystemExit("rollback files.txt too large")
    rb_entries["files.txt"] = rb_txt
    rb_raw, rb_size = pack(rb_entries, extents)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "files").mkdir(exist_ok=True)
    for name, data in entries.items():
        (out / "files" / name).write_bytes(data)
    (out / "firmware-clownfish.img").write_bytes(raw)
    (out / "firmware-clownfish-rollback.img").write_bytes(rb_raw)
    plan = {
        "collection_prefix": PREFIX,
        "tags": list(TAGS),
        "alpha_names": alpha_names,
        "png": png_meta,
        "layers": report,
        "install": {"entries": len(entries), "container_bytes": size,
                    "files_txt_bytes": len(files_txt), "lines": len(lines)},
        "rollback": {"entries": len(rb_entries), "container_bytes": rb_size,
                     "files_txt_bytes": len(rb_txt), "lines": len(rb_lines)},
        "extents_capacity_bytes": extents,
        "backup_suffix": BACKUP_SUFFIX,
        "original_heic_preserved": True,
        "wallpaper_config_files_touched": [],
        "pixels_independently_verified_on_device": False,
    }
    (out / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("entries=%d container=%d/%d bytes files.txt=%d bytes lines=%d" %
          (len(entries), size, extents, len(files_txt), len(lines)))
    print("rollback entries=%d container=%d bytes files.txt=%d bytes" %
          (len(rb_entries), rb_size, len(rb_txt)))
    print("replaced alpha src per layer:", [(r["tag"], r["layer"], len(r["replaced_src"])) for r in report])
    print("all original main.caml backed up, Background.HEIC left untouched")
    print("output:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
