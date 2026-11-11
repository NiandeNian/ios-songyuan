# ios-songyuan

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
- 真机已运行 XNU 22.6.0 的 RAM 用户空间，用户照片确认 PID 1 和持续的
  `init: alive` 输出，ADB 确认修复后的内核自动返回 Android 并完成新的启动。
- 上一版的 `Halt/Restart Timed Out` 已定位为 RAM 启动未注册 PSCI 重启回调；
  修复内核已通过 CI 和本轮真机重启验证。
- iOS 16.7.16 的 16 GiB HFSX 根镜像已安装到专用固定文件，完整原始存储
  SHA-256 与源镜像一致，75 个 extents 位置保持不变。
- 完整 iOS 16.7.16 用户空间已在真机运行：launchd 完成早期启动，
  SpringBoard、backboardd 已执行，连续运行 240 秒后按测试参数返回 Android。
  随后已显示苹果标志和持续推进的进度条，CPU 绘制链路已运行。
  1800 秒观察已按参数正常重启；现已启动无定时重启测试并再次显示苹果标志和进度条，
  桌面仍未确认。
- 完整载荷须合并 OS、Cryptex1 SystemOS、AppOS 三份信任缓存；
  仅 OS 缓存会导致 AMFI 拒绝 dyld shared cache。

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

下载单个内核可使用 `tools/songyuan/fetch_kernel.py --repo OWNER/REPO --run RUN_ID --out DIR`；
工具只接受成功的 CI，通过 ZIP Range 提取内核并检查 CRC，记录提交和 SHA-256。
`install_root.py` 使用 ADB sync 暂存并校验源文件，再在 Android 本地读取它写入
已检查的固定块区，核对写入量、写入哈希、原始存储哈希和 extent 稳定性。
`read_console.py` 在返回 Android 后读取并解析专用文件中的 `PANDORLG` 控制台日志。
`prepare_trust_cache.py --ipsw FILE --out DIR` 按 IPSW manifest 提取并合并目标设备
的三份信任缓存，记录版本、来源与 SHA-256。iOS 已读写根文件系统后，手机根镜像
的哈希会变化；最初的安装哈希仅用于验证初次安装。

`build_payload.py --fw-extents TABLE` 通过专用固定文件提供内核 VFS 安装包。
`mkfwpack.py` 生成包；`install_pack.py` 核对源、传输和原始存储 SHA-256，
核对固定映射，仅接受专用 files/fw 测试文件。`prepare_hid_pack.py disable|restore`
生成远程蓝牙 HID 插件的可恢复重命名操作。
`--wifi-mac-file FILE` 将私有 Android MAC 采集结果提供给现有网络身份发布器，
同时填入 iOS 优先读取的 `/chosen/mac-address-wifi0`；Wi-Fi 数据连接尚未适配。

V8 的 59 次诊断采样确认：1 个 CPU 已初始化，但 QoS 并行能力和工作队列缓存上限
全部为 0；LaunchServices 偏好队列持续有未执行任务。当前 PANDORA 内核启用 E/P
调度器，旧 SMP 类型设备树没有被计入 E/P CPU 数量。完整证据见
[iOS 启动诊断](device/IOS_BOOT_DIAGNOSIS.md)。

`build_payload.py --cpus 8 --amp` 同时选用 8 核并生成 6E+2P 拓扑；`--amp` 需要
包含补丁 0320 的内核。`--out DIR` 可将各轮镜像分别保存。V9 保留 V8 诊断内核，
使用本机已核对的 8 个 MPIDR，已通过 RAM 临时启动；用户仍看到苹果标志和进度条，
8/8 初始化结果、任务调度恢复和桌面仍须由后续日志及画面验证。

当前载荷不含 `pandora_reboot_after`，沿用 `wdt=-1`，保留根镜像已生成的数据库。
日志定期写入专用固定文件，读取须待手机返回 Android。苹果标志和 `exec SpringBoard`
均不能作为锁屏或桌面成功的证明。WSL2 已完成本轮载荷与 loader 构建；内核编译的
工具链限制见 [WSL 构建说明](tools/songyuan/WSL_BUILD.md)。
