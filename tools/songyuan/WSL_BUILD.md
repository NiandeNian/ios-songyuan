# WSL2 构建范围

2026-11-11，本机 Ubuntu WSL2 的 LLVM 20 已成功编译 V8 的 `pandora_prof.c`
诊断单元，并完成 V9 的 ADT、载荷、AArch64 loader 和 Android boot image 构建。
可将 `build_payload.py --llvm-bin /usr/lib/llvm-20/bin` 与本地输入路径配合使用，
8 核验证轮另加 `--cpus 8 --amp --out DIR`。构建命令仅生成文件。

完整 XNU 的 Apple typed-allocation 编译扩展仍有约束：上游 LLVM 不具备所需的
Apple allocation builtins，不能用伪造宏替代类型分配元数据。本轮 V8 完整内核由
Apple CI 增量编译并链接；V9 复用同一内核，只在 WSL2 重建载荷和 loader。

## Mach-O 链接器

`ld64.lld-20` 忽略本内核需要的 `image_base`、`segaddr`、`segment_order` 和
`alias_list`，其结果不适合作为本项目的内核。已在隔离目录成功构建真实 Apple
ld64 Linux port：

| 组件 | 版本 / 固定源码提交 |
| --- | --- |
| [cctools-port](https://github.com/tpoechtrager/cctools-port) | cctools 1035.1.102 / ld64 957.1；`ef969083aecf77dee1814d6961a06b88af23edef` |
| [apple-libdispatch](https://github.com/tpoechtrager/apple-libdispatch) | `68ec662b37f62e97c3c22083cf0a6311ac7bc17c` |
| 宿主编译器 | clang/clang++ 20.1.8 |

Ubuntu 22.04 自带的旧 `libdispatch-dev` 缺少 `DISPATCH_APPLY_AUTO`，使 ld64 编译
失败。改为单独构建上述 libdispatch，再用 cctools 的 `--with-libdispatch=PREFIX`
配置，已完成编译及安装。未修改系统全局链接器或原始源码。

Windows 检出可能把 shell 脚本转换成 CRLF。使用 `git archive COMMIT` 将固定源码
导出到 WSL 文件系统，可保留 Git 中的 LF 与执行位。本机工具位于
`/root/ios-songyuan-toolchain/prefix/bin/arm64-apple-darwin-ld`。

ld64 可运行及解析所需选项，不等于完整内核已经由 WSL 验证。后续本地重链接还须
使用同一成功 CI 的全部对象、link-only 对象、别名表及 runtime libraries，核对
生成内核的段地址、入口和预链接契约，再进行真机测试。
