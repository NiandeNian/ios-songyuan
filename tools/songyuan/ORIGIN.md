`adt.py`, `mkpayload.py`, `mkinit.py`, `mkiosroot.sh` and `sweet-init.S` come from
dipa-studio/ios-sweet at 45263bee51df4a0f55cdf9c649af886399488ddb
(MIT; see LICENSE.ios-sweet). `mkpayload.py` includes
the lowest-segment virt_base change required by Data A Live prelinking.

`mkadt_songyuan.py`, `mkbootimg_v4.py`, `mkprelink.py`, `addtrustcache.py`,
`native_prelink/` and the files under `shim/loader`
come from ShueiTsuki/data-a-live at d2c7089b0a2e6da2b1e7e843337b503ec032b2c5.
The MIT notice is preserved in shim/hello/ORIGIN.md. The display dimensions,
synthetic identity and usable RAM limit are adapted to the measured songyuan.
`native_prelink/` retains UniXNU's BSD-2-Clause-Patent notice in its LICENSE;
the unchanged upstream module provenance is described in its README.md.

The capture, boot, install and ARM64 slice helpers are local songyuan tools.
Apple ADT, trust caches, kernelcache, prelinked kernel and root images remain
local inputs under `.local/` or ignored build outputs, not repository contents.

The native boot-loader route follows the requested u0er/unixnu-xiaomi17u
XNU 8796 patch set. These sources do not contain an Apple restore image.
