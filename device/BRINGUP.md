# songyuan bring-up record

## 2026-11-11: RAM XNU and complete payload preparation

The `rd=md0` test booted Darwin 22.6.0 / xnu-8796.141.3 on the phone.
User photographs show filesystem unmount, MACH Reboot and a halt/restart
timeout panic. This is consistent with the static init's 60-second reboot,
but the PID 1 console marker was not captured; it is not a complete iOS boot.

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
