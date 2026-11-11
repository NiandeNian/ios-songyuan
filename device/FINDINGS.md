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

下一步从本机 FDT 提取内存/保留内存、CPU MPIDR、GIC、面板、触控 GPIO、
GENI/SPI、调压器和 UFS 配置，作为板级补丁的依据。

## 启动验证

尚无本机 UEFI、XNU 或 iOS 用户空间启动证据。启动分区备份保存在 `.local/`。
