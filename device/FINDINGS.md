# songyuan 实机资料

采集日期：2026-11-11（Asia/Shanghai）。资料来自已连接真机的 ADB / KernelSU。

| 项目 | 实测值 |
| --- | --- |
| 用户确认型号 | Redmi K100 Pro Max |
| Android 产品代号 | songyuan |
| Android 型号属性 | M098FE |
| SoC / machine / soc_id | SM8850 / CANOE / 660 |
| 系统 | Android 17 / OS4.0.7.0.XGNCNXM |
| 内存 | MemTotal 15447612 kB（16 GB SKU） |
| 物理屏幕尺寸 | 1200×2608 |
| 当前槽位 | _a |
| Root | KernelSU，uid=0 |
| 实际 Bootloader 状态 | bootconfig: vbmeta.device_state=unlocked |
| 实际 AVB 状态 | bootconfig: verifiedbootstate=orange, veritymode=disabled |
| DTB / DTBO 索引 | 2 / 1 |
| UFS 控制器 | 0x01d84000 |
| USB 控制器 | 0x0a600000 |
| 当前屏幕节点 | qcom,mdss_dsi_songyuan_42_0d_0a_p2_dsc_cmd |

`ro.boot.flash.locked=1` 和 `ro.boot.verifiedbootstate=green` 与 `/proc/bootconfig`
不一致，应以启动参数及必要时 fastboot 查询为准。

## 需要实测的板级差异

参考 Nezha 补丁把触控 AVDD 改为 GPIO 215、触控阵列改为 18×39，屏幕节点为
`p1_42_0d_0b_p21`。本机虽然分辨率相同，面板名称不同，不能直接套用其
触控、电源、亮度、相机、音频和热管理配置。

本机 FDT 已提取到 [hardware.json](hardware.json)。实际触控绑定 `fts_ts`，
`fts_driver_info` 报告 IC ID `0x5883`、FocalTech V4.1、超分辨率坐标
119999×260799（比例 100），不是 Synaptics。QUP4 SE0 = 0x01a80000，
IRQ GPIO 158、reset GPIO 66、AVDD GPIO **117**、iovdd L4I_E0。
参考仓库 GPIO 215 不适用于本机。

## 启动验证

2026-11-11 的 `songyuan-hello` 临时 RAM 启动已得到用户照片确认：

| 项目 | 探针屏幕值 |
| --- | --- |
| 执行级别 | EL1 |
| Image 加载地址 | 0xC7800000 |
| FDT | 0xD5C29000，FDT ok |
| MIDR | 0x512F0021 |
| MPIDR | 0x80000000（启动 CPU affinity 0） |
| MMFR0 | 0x2100122100001024 |
| 页大小 | 16K NO / 4K YES / 64K YES |
| 物理地址宽度 | 44 bits |
| CNTFRQ | 19200000 Hz |
| 帧缓冲 | 0xFC800000、1200×2608、stride 4800 |
| 色序 | 照片中 red / green / blue / white 正常 |
| 快速引导 | fastboot product=songyuan、unlocked=yes，Sending/Booting OKAY |

随后 XNU RAM 载荷临时启动的照片确认 Darwin 22.6.0（xnu-8796.141.3），
`rd=md0`、`cpus=1`，已进入 `fs_unmountall(system)` / `MACH Reboot`，最后
报 `Halt/Restart Timed Out @IOPlatformExpert.cpp:884`。最小 init 的设计行为
是在约 60 秒后调用 reboot。用户随后补充确认：第一版同样显示了 PID 1
和持续的 `init: alive`；此前照片记录的是最后出现的重启超时画面。
RAM 路线不初始化 UFS，因此没有注册 UFS 中的 PSCI 重启回调；新增
`9001-songyuan-ram-psci-restart.patch` 在平台初始化时安装备用回调。
修复内核已通过 CI；本轮用户照片明确显示 PID 1、持续的 `init: alive`
和回调安装标记，ADB 随后确认 Android 完成了新的启动。超时照片的
内核 UUID `4750da68-a025-3fd3-9fca-a98b93bb32d8` 对应上一版，而本轮
内核 UUID 为 `15c52bd3-8add-3216-baf5-61bc4b881dfa`。

完整 iOS 16.7.16 / 20H392 用户空间已在真机运行。补齐 OS、Cryptex1 SystemOS、
AppOS 信任缓存后，dyld 校验成功，launchd 完成早期启动，SpringBoard 和 backboardd
已执行；backboardd 打开了 SweetHIDTouch 和 SweetHIDButtons。用户确认持续终端
输出。日志在 240.297 秒明确记录测试参数触发重启，没有 panic 记录。
iOS 随后显示了苹果标志和进度条：停用阻塞 HID 初始化的可选远程蓝牙插件后，
backboardd 在 48.158 秒送出第一张 BGRA 画面，持续 CPU 绘制。该轮在 120.461 秒
按测试参数重启。SpringBoard 后续等待 LaunchServices 数据库；设备能力服务等待
网络身份查询。第五轮确认身份已发布，进度条推进到约五分之一；361.000 秒的
日志明确记录 `pandora_reboot_after=360` 主动复位，没有内核 panic。
lsd 的网络通知等待和 MobileGestaltHelper 的诊断配置等待仍在，身份发布不能
等同于查询已解除。新参考记录首次启动等待可达 14–25 分钟以上，其后续状态记录
将其中 835 秒的一轮归因于 assetsd 等待，因此不能仅凭时长认定正在建库。
第六轮已运行至 1801.561 秒，并按 `pandora_reboot_after=1800` 正常复位；
1849.625 秒确认 Android 完成新的启动。用户随后暂停该方案，现已要求恢复。
第七轮已通过 `fastboot boot` 启动；实际载荷不含定时重启参数，沿用 `wdt=-1`，
保留手机已生成的数据库，并加入直接 Wi-Fi 地址与空 SysCfg 的设备信息输入。
用户再次确认苹果标志和进度条。锁屏或桌面，以及超过原 30 分钟窗口的持续运行，
仍须观察；设备信息等待是否解除须读取本轮日志验证。
启动分区备份大小和 SHA-256 已与真机逐一比较。
