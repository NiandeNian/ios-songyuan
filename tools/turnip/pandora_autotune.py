#!/usr/bin/env python3
"""Turnip autotune tuning for SM8850: prefer GMEM when samples are unavailable.

Two changes to tu_autotune.cc:

1. Enable TUNE_SMALL by default so render passes with < 5 draw calls enter the
   autotuner instead of unconditionally falling back to SYSMEM.

2. When mean_samples_passed == 0 (sample tracking is unavailable), the bandwidth
   algorithm's draw-call cost estimate is zero, which makes SYSMEM look free and
   every pass chose SYSMEM (82.7% on SM8850).  Prefer GMEM in that case since
   GMEM is the natural path for Adreno's on-chip tile memory.

  pandora_autotune.py MESA_DIR

Exact text edits, asserted; run once on a patched tree (local: tools/turnip/build_ios_linux.sh; CI: turnip.yml).
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1]) / 'src/freedreno/vulkan/tu_autotune.cc'
s = p.read_text()
for old, new in (
        # 1. Default mod_flags: enable TUNE_SMALL so small RPs enter the autotuner.
        ('uint8_t mod_flags = 0;    /* See mod_flag enum. */',
         'uint8_t mod_flags = (uint8_t) mod_flag::TUNE_SMALL; /* See mod_flag enum. */'),
        # 2. When samples are unavailable, prefer GMEM (SYSMEM bandwidth looks artificially cheap).
        ('''         bool select_sysmem = sysmem_bandwidth <= gmem_bandwidth;''',
         '''         /* data-a-live (tools/turnip/pandora_autotune.py): when no samples are tracked
          * (mean_samples == 0), the draw call bandwidth estimate is zero, which makes
          * SYSMEM look artificially cheap.  Prefer GMEM in that case since we cannot
          * measure the real cost.  This fixes the common case where all passes chose
          * SYSMEM on SM8850 (82.7%). */
         bool select_sysmem = (mean_samples > 0.0) && (sysmem_bandwidth <= gmem_bandwidth);'''),
        # 3. Same default in get_env_config's local mod_flags variable.
        ('''      const char *flags_env_str = os_get_option("TU_AUTOTUNE_FLAGS");
      uint32_t mod_flags = 0;''',
         '''      const char *flags_env_str = os_get_option("TU_AUTOTUNE_FLAGS");
      uint32_t mod_flags = (uint32_t) mod_flag::TUNE_SMALL;'''),
):
    if old not in s:
        raise SystemExit(f'pandora_autotune: anchor not found:\n{old[:120]}')
    s = s.replace(old, new, 1)
p.write_text(s)
print(f'pandora_autotune: patched {p}')
