# songyuan bring-up record

## 2026-11-11: original iOS plan resumed without a timed reset

The sixth test completed its configured 1800-second observation. Its console
records `pandora_reboot_after=1800 reached, resetting` at 1801.561 seconds,
and Android completed a new boot at 1849.625 seconds. The user confirmed an
Apple logo and incomplete progress bar; no lock screen or home screen was
confirmed before the project was paused.

The user resumed the original iOS 16.7.16 plan with no timed reset. The
seventh image was accepted by `fastboot boot`, and the user again confirmed
an Apple logo and progress bar. Its embedded 513-byte command line was
checked against the built payload: `pandora_reboot_after` is absent and
`wdt=-1` remains present. The kernel initializes the test-reset duration to
zero when the argument is absent. A usable GUI and sustained runtime beyond
the previous 30-minute limit remain unverified.

The same prelinked restart-fixed kernel, three-cache trust module and
phone-written HFSX root are retained. Preflight confirmed all 75 root, one
log and four file-pack extents match their saved pinned mappings. The image
includes the previously prepared direct `/chosen/mac-address-wifi0` input
and a header-only, zero-entry `/chosen/syscfg-v2-data` container. These are
intended to avoid the observed MobileGestalt fallback waits; their runtime
effect requires the new console. Four processes are sampled every 120
seconds, within the compiled kernel's four-name limit: SpringBoard, lsd,
MobileGestaltHelper and assetsd. IOKit logging uses the helper's full name.

No Android boot partition was flashed. The test image SHA-256 is
`4c332fbce689576064eba6f543c09f49b440947a8a95d5d383c9fc13f245ceb0`.
Launch metadata and exact arguments are saved locally under
`.local/device/ios-untimed-v7-*`; Apple artifacts and unique identifiers
remain outside Git. Console collection requires a later return to Android.

## 2026-11-11: network identity and extended GUI observation

The fifth full-root test published patch 0337's network identity at 0.157
seconds. The user confirmed that the Apple progress bar advanced, and the
last photograph shows approximately one fifth completed. The return to
Android was the configured test reset: the console records
`pandora_reboot_after=360 reached, resetting` at 361.000 seconds, followed
by a completed Android boot after 407.609 seconds. No kernel panic was
recorded. The boot image SHA-256 is
`afe13227ec05ede47adb9c0c29aa3e4560cad025cda7d3f5d43f6dc4ecd170ca`.

Twelve snapshots still show SpringBoard waiting for the LaunchServices
server store. lsd waits in MobileGestalt's IONetworkController notification
helper, despite the published identity. MobileGestaltHelper also waits for
`AppleDiagnosticDataAccessReadOnly` while reading `AppleDiagnosticDataSysCfg`.
These service names were recovered from the corresponding iOS 16.7.16
cache instructions and constant strings; large offsets from the nearest
exported `MGSetAnswer` symbol are not actual function names.

The newly supplied references are yechenan/data-a-live at
`6547194c4883efad2f8a42ad46e95874dd5e7576` and yechenan/pudding-ios at
`1d23efa6f3ff5e2f6a7b63ba61188ac40f5a3dbd`. Their handoff notes report
14 to more than 25 minutes for first-boot waits. The later pudding status
record corrects the 835-second case: assetsd, rather than a database rebuild,
blocked that run. A six-minute reset cannot establish that the GUI will never
start, and a long delay alone does not prove database work. The sixth test uses
1800 seconds, snapshots every 120 seconds, and
`sweetfb_refresh=60 sweetfb_hz=30 sweetiolog=lsd,MobileGestaltHel` to reduce
display work and observe the outstanding matching calls. Phone-created
databases are retained between tests; the initial root installation hash
is not used to reject a root that iOS has subsequently written.

Further inspection of MobileGestalt's Wi-Fi address handler shows that it
first reads `IODeviceTree:/chosen/mac-address-wifi0`; only when absent does
it try sdio/wlan/marconi-wifi registry entries and the network-controller
notification helper. `--wifi-mac-file` now also supplies this direct property,
using the same validated private Android capture. This ADT change is prepared
for the following test and has not yet been verified on the phone.

## 2026-11-11: first iOS-rendered boot screen

Four snapshots from the third full-root test located the first display
block: backboardd's main thread waited in `IOHIDSessionOpen`, while its
remote HID session filter waited in MobileBluetooth / MobileGestalt's
synchronous XPC query. SpringBoard waited in `BKSDisplayGetMainScreenInfo`.

The fourth test used an independently allocated 8 MiB pinned file pack
and the kernel VFS installer to move only
`IOHIDRemoteSensorSessionFilter.plugin` from SessionFilters into
`HIDPlugins/SongyuanDisabled`. The installer completed at 2.767 seconds
with two writes and zero failures. The original bundle is retained and
the reverse rename restores it; `prepare_hid_pack.py` prepares either
operation without including Apple binaries.

backboardd opened the framebuffer, presented its first 1125 x 2436 BGRA
surface at 48.158 seconds and continued CPU rendering at about 79 fps.
The user photograph confirms an Apple logo and progress bar. The test
ended at 120.461 seconds with the configured `pandora_reboot_after=120`
reset and no panic. This verifies the iOS boot screen, not a usable GUI.
The boot image SHA-256 is
`2974b2e240e177351ae8a64796020f04023678dc7510261a49d0ea9beef26472`.

Later snapshots show SpringBoard waiting for LaunchServices' database,
and MobileGestaltHelper waiting on a network identity query. The compiled
kernel already contains patch 0337's IONetworkController identity publisher,
but the songyuan ADT did not supply its `nezha-wifi-mac` input. The next
test supplies the private Android WLAN MAC capture (addr_assign_type 0)
through `--wifi-mac-file`. This publishes identity without starting Wi-Fi
hardware or registering a network transport. The fifth test verified publication,
but the remaining waits above require further diagnosis.

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
