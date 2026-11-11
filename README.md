# UniXNU-songyuan

Redmi K100 Pro Max (`songyuan`, SM8850/CANOE) UniXNU bring-up, based on
[u0er/unixnu-xiaomi17u](https://github.com/u0er/unixnu-xiaomi17u)
at `a87da37e164f23502d845e86f6686b3aeda08f48`. Upstream history is preserved.

## 当前状态

- 已连接真机并确认 `songyuan`、SM8850、16 GB RAM、1200×2608 屏幕。
- 当前系统：Android 17 / HyperOS `OS4.0.7.0.XGNCNXM`，槽位 A，KernelSU root。
- `/proc/bootconfig` 确认 Bootloader **unlocked** / AVB orange；Android 属性报告的
  locked/green 与启动参数不一致，不能用这些属性判断实际解锁状态。
- 参考仓库使用 `PANDORA` 内核配置；该名称不是本设备已完成适配的证明。
- 尚未验证本设备上的 UEFI、XNU 或 iOS 用户空间启动。

## 编译

GitHub Actions 的 `songyuan-xnu-baseline` 工作流先验证继承的 XNU 补丁能否编译。
默认构建 macOS 13.5 对应的 XNU 8796、DEVELOPMENT、ARM64/PANDORA、4 KiB 页。
`xnu-kernel` 是内核构建产物，**不是可直接刷入手机的 Android boot.img**。

上游 `relink.yml` 明确记录：完整 CI 内核可能丢失低地址 `__PRELINK_TEXT`，
改变 virt_base 并导致外部中止。重链接继承的是 Nezha 驱动，仍须逐项核对本机硬件。
编译成功、Mach-O 布局检查、UEFI 启动、XNU 启动、iOS 桌面是不同验证阶段。

设备资料见 [device/FINDINGS.md](device/FINDINGS.md)。原始设备树、启动分区备份和
设备日志仅保存在 `.local/`，不提交到公共仓库。
