# SM8850 参考仓库

固定版本见 [references.json](references.json)。由用户提供的三个新增参考：

- [Typeun/myron-uefi](https://github.com/Typeun/myron-uefi)：Mu-Silicium 平台包、
  内存描述符、boot image header v0（BootShim + FD + 外部 ramdisk）方案。
- [Typeun/myron-device](https://github.com/Typeun/myron-device)：设备配置、ADT 和
  FT3683 触控驱动。其 README 的部分进度来自继承内容，需要单独核验真机证据。
- [Typeun/UniXNU-myron](https://github.com/Typeun/UniXNU-myron)：UniXNU 框架分支，
  采用 iPadOS 17 路线，ABI 和当前 XNU 8796 / iOS 16 路线不同。

myron-uefi 自己记录“没有在真机上 fastboot boot 过”，与 myron-device 继承的
SpringBoard 描述不能混为本机已经能启动的证据。

myron-device 的 `docs/STORAGE-AUDIT.md` 明确指出其假定的 `unixnu` GPT 分区
不存在，配置区间落在 Android userdata 内。不能把它的 LBA 范围用于本机。
当前原生 XNU 路线使用本机独立分配、pinfile 校验过的文件；不改 GPT。

本机 kernel 模块为 `focaltech_touch_3685g_1`，驱动报告 IC ID `0x5883`；
参考 FT3683 驱动的 `0x36/0x83` 识别条件不适用于本机，协议要继续核验。
