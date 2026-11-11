# songyuan bring-up record

## 2026-11-11: full iOS userspace runs; display diagnosis continues

The first full-root boot mounted HFSX and executed Apple's launchd, but
AMFI rejected the dyld shared cache with `unsuitable CT policy 0`.
The OS StaticTrustCache alone did not include its CDHash
`2e6af99c2bbd9042f6b658b58739b6265cb3c46d`.
`prepare_trust_cache.py` now reads the target IPSW manifest and merges the
OS (2,760), Cryptex1 SystemOS (61) and AppOS (30) entries into one static
module with 2,851 entries. The combined cache includes this CDHash.

The second full iOS 16.7.16 / 20H392 boot validated the dyld subcaches,
completed launchd early boot, executed SpringBoard at 12.084 seconds and
backboardd at 12.504 seconds, and passed corecrypto's FIPS checks.
backboardd opened SweetHIDTouch and SweetHIDButtons. No SpringBoard or
backboardd CRASH was recorded, and no rendered GUI frame was recorded.
The user saw continuous terminal output and then Android return.
At 240.297 seconds the log explicitly recorded
`pandora_reboot_after=240 reached, resetting`; this was the configured
test timeout, with no panic recorded. The last repeating fairplayd and
bluetoothd messages do not establish why the GUI has not appeared.

The tested boot image SHA-256 is
`787984831ba5047b2ba1bbd423bb5e02ed65196ae112e6d5ffc6f69e99994e14`.
The root image is now being written by iOS; its initial installation
hash is no longer an expected steady-state hash. The root and log
pinned extent mappings remain unchanged in the next test's preflight.
The existing kernel starts `pandora_prof_start()` even with `pandora_cpu=0`,
so blocked-thread snapshots can be enabled without enabling SCMI probes.

## 2026-11-11: recovered transfer and verified root installation

The previous long `adb exec-in` transfer failed with a standard-input I/O
error. The installer now stages the image with ADB sync, verifies its
SHA-256 on Android, and redirects the checked writer's input from that
local file. A complete 16 GiB transfer succeeded at a reported 73.2 MB/s.
The writer confirmed 4,194,304 blocks / 17,179,869,184 bytes and SHA-256
`f888bcefd5539131469b630ead19ec53f9dd88ab767cee1c65b71e1f8ae54208`.
The independent raw read/hash returned the same digest, with all 75
pinned extents unchanged. This verifies installation, not an iOS boot.

XNU run `42942489397` completed successfully at commit `f365364`.
The compiled kernel is 17,570,792 bytes with SHA-256
`345a1aad4345f81e393e7db47e1daca0e5d14559b790f84b40b00ab4c85504c7`;
the RAM-only PSCI fallback's diagnostic string is present in the binary.
ZIP-range extraction verified its CRC and avoided downloading the rest of
the 63 MB debug artifact. NativePrelink passed again for the corrected
kernel: 11 Apple kexts, 22,537 relocations and 209 compatible vtables.

The corrected RAM test image SHA-256 is
`341f6f4636f3ec51b2253303fe8b574260b3ae4510318e784f776aab7dc126d8`.
The prepared full iOS image SHA-256 is
`17acb3ca09f1dbe354a11a3088ee1e96765e6a53487031bd30f55cb58a7fba8d`.
User photographs now confirm `init running as PID 1`, repeated `init: alive`
and the installed RAM-only PSCI fallback. The halt/restart timeout photos
carry the old kernel UUID `4750da68-a025-3fd3-9fca-a98b93bb32d8`; the corrected
kernel UUID is `15c52bd3-8add-3216-baf5-61bc4b881dfa`. ADB observed Android
return and complete a new boot with a changed boot ID. The RAM test and
restart are verified; full iOS and desktop behavior remain to be verified.
The log preflight and post-RAM read found no PANDORLG header, as the RAM
root does not initialize UFS; a later valid log will be new evidence.

## 2026-11-11: RAM XNU and complete payload preparation

The `rd=md0` test booted Darwin 22.6.0 / xnu-8796.141.3 on the phone.
User photographs show filesystem unmount, MACH Reboot and a halt/restart
timeout panic. The user later confirmed that this first test also displayed
PID 1 and continuous `init: alive`; the initial photographs captured only
the final reboot timeout. This verifies the first RAM userspace test, not
a complete iOS boot.

The normal PSCI callback is installed from `sweet_ufs_init()`, which RAM-only
boot does not call. Patch 9001 installs a fallback in the platform expert,
gated by `songyuan=1`. A successfully initialized UFS driver replaces it
with the normal flush-and-reset callback. The patch still needs CI and
phone verification.

The first console file was 4 MiB, smaller than the driver's 4 MiB ring plus
4 KiB header. It contains no valid PANDORLG record. A new 8 MiB pinned file
is allocated, and `build_payload.py` now rejects an undersized log table.

The local iPhone X iOS 16.7.16 / 20H392 HFSX root passed read-only fsck after
copying OS, SystemOS and AppOS. Its initial 16 GiB SHA-256 is
`f888bcefd5539131469b630ead19ec53f9dd88ab767cee1c65b71e1f8ae54208`.
This describes the host image; installation is not inferred from this hash.

NativePrelink verification passed for 11 Apple kexts, 22,537 relocations and
209 vtables. The prelinked image has virt_base `0xfffffff002000000`.
The full payload includes D22AP product properties and the original static
trust cache with 2,760 entries. Apple artifacts are kept outside Git.

Initial hardware settings remain one CPU with GPU, touch, audio, camera and
other unverified peripheral drivers disabled. Root and console writes are
limited to the independently allocated pinned files. Temporary boot uses
`fastboot boot`; no Android boot partition has been replaced.
