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
- 自定义 ARM64 RAM 探针已启动并显示诊断屏幕，用户照片确认 EL1、4K 页和帧缓冲。
- 真机已运行 XNU 22.6.0，并到达 RAM 测试的文件系统卸载/重启流程；用户照片
  记录了 `Halt/Restart Timed Out`。照片没有包含 PID 1 的运行标记。
- 已定位 RAM 启动未注册 PSCI 重启回调的问题，新增补丁正在等待编译和实测。
- 尚未验证完整 iOS 用户空间或桌面启动。

## 编译

GitHub Actions 的 `songyuan-xnu-baseline` 工作流先验证继承的 XNU 补丁能否编译。
默认构建 macOS 13.5 对应的 XNU 8796、DEVELOPMENT、ARM64/PANDORA、4 KiB 页。
`xnu-kernel` 是内核构建产物，**不是可直接刷入手机的 Android boot.img**。

上游 `relink.yml` 明确记录：完整 CI 内核可能丢失低地址 `__PRELINK_TEXT`，
改变 virt_base 并导致外部中止。重链接继承的是 Nezha 驱动，仍须逐项核对本机硬件。
编译成功、Mach-O 布局检查、UEFI 启动、XNU 启动、iOS 桌面是不同验证阶段。

设备资料见 [device/FINDINGS.md](device/FINDINGS.md)。原始设备树、启动分区备份和
设备日志仅保存在 `.local/`，不提交到公共仓库。

完整 iOS 载荷工具支持原始 Apple ADT、NativePrelink 驱动和静态信任缓存。
根镜像安装工具只写入 `/data/local/tmp/unixnu-songyuan/` 内已分配并固定的
测试文件 extents，安装前核对源 SHA-256，安装后核对原始设备 SHA-256 和
extent 稳定性。日志文件至少需要 4 MiB 环形缓冲加一个 4 KiB 头部块。
当前使用 8 MiB 专用日志文件。详见 [device/BRINGUP.md](device/BRINGUP.md)。
