# iOS 16.7.16 / 20H392 启动诊断

## V8：任务并行上限为零

V8 于 2026-11-11 22:35:48 +08:00 通过 `fastboot boot` 临时启动。用户确认苹果标志
和进度条，随后返回 Android 读取日志。持久控制台完整保存 1,518,168 字节；两次
读取相同，最后一条时间为 1768.441 秒，约 29.47 分钟。没有 kernel panic 标记。

从 18.320 到 1767.683 秒，共 59 轮 `lsd[104]` 诊断，结果如下。

| 观测 | 全部采样的结果 |
| --- | --- |
| 已上线 / 物理 / 逻辑 CPU | 1 / 1 / 1 |
| 直接查询 QoS 1–6 的逻辑及物理并行数 | 全部为 0 |
| 工作队列缓存 QoS 1–7 上限 | 全部为 0 |
| `lsd` 主线程 | tid 454，持续等待 `_LSPreferences::with` 的 `dispatch_sync` |
| 安全偏好队列 | `0x103d5ddd0`，enqueued=1，无当前 owner，头尾指针全程相同 |
| 队列 head / tail | `0x101000b40` / `0x16ef5d290` |
| 工作队列线程 | 持续处于 parked 状态；末次统计 run=0 |

系统保存的迁移 2 分钟和 10 分钟 stackshot 中，`lsd` 用户 CPU 时间都为
0.451618489 秒，系统 CPU 时间都为 0.056149843 秒，tid 454 等待地址相同。
这些报告按本轮 PID/TID 与控制台交叉筛选；原生 absolute counter 的原点不宣称
等于 kernel 启动时间。末段每 30 秒的 UFS 设备工作时间约 8–27 ms，锁等待为 0。

## 源码因果链

补丁 `0320-amp-pspill.patch` 为 PANDORA 设置 `MAX_CPU_CLUSTERS=2` 和
`__ARM_AMP__=1`。该内核使用 E/P 调度策略。`sched_edge_qos_max_parallelism()`
通过 `ml_get_cpu_number_type(CLUSTER_TYPE_E/P, ...)` 统计 CPU；后台类只计 E 核，
其他普通 QoS 计 E+P 核。

此前载荷构建器没有把 `--amp` 传给设备树生成器，CPU 的 `cluster-type` 为 0
（SMP），因而 E/P 统计为零。`workq_open()` 将该结果缓存到
`wq_max_parallelism[]`；`workq_constrained_allowance()` 仅在上限大于当前活动及
busy 线程数量时允许派发，零上限因此阻止受限任务执行。

队列中仍有任务，主线程同步等它完成，线程又无法获得并行额度。这条链解释了本轮
LaunchServices 持续等待，并支持先修正设备树。它不证明桌面后续关卡也已经解决。
未清除 LaunchServices 数据库、强制解锁队列或绕过同步返回。

## V9：8 核及 E/P 拓扑验证轮

用户要求直接启动 8 核后，构建器新增 `--cpus`、`--amp` 和独立的 `--out`。
本机 FDT 的 8 个 MPIDR 已与生成设备树逐项比较：

| 逻辑核 | MPIDR | 调度类型 |
| --- | --- | --- |
| 0–5 | `0x0, 0x100, 0x200, 0x300, 0x400, 0x500` | E |
| 6–7 | `0x10000, 0x10100` | P |

V9 由 WSL2 Ubuntu / LLVM 20 构建，沿用 V8 诊断内核，实际载荷包含唯一的
`cpus=8`、6E+2P 设备树和 `songyuan_lsdiag=1`，不含定时重启参数。
根、控制台和文件包的固定映射在启动前重新读取并核对。于 23:17:37 +08:00 通过
`fastboot boot` 启动；用户确认苹果标志和进度条，约 5 分钟后仍为该画面。

下一步读取 V9 控制台确认 `8/8 CPUs initialized`、QoS 返回 6 或 8，以及 `lsd`
是否离开原等待。当前没有锁屏/桌面成功结论，也没有仅凭 `cpus=8` 宣称 8 核已验证。

## 证据标识与限制

| 项目 | SHA-256 |
| --- | --- |
| V8 控制台文本 | `19f8a7261b284167baefee9d583d927951b25f35077144862766915cd038ef52` |
| V8/V9 诊断内核 | `2e02ebe00b6a2fe82076c287c1bac7caff41fbb53c87c290430a7f844268b13d` |
| V8 镜像 | `408ba6d9ad221e63e946d976e096c4f73c32bae3ff3381ce15c099d3c11fee41` |
| V9 镜像 | `e63bde19efd54c3b8200b49439568c6c13c1ec797660b39b8257f22583bef794` |

原始日志和真机身份保留在本地 `.local/device`。V8 的 shared cache base/slide 为
`0x1bb2fc000/0x3b2fc000`。本次统一日志归档按 HFS 墙上时间筛选时，被时间更长的
历史 V7 记录影响，已捕获的归档没有该 V8 标识，故其消息不作为 V8 证据。后续采集
应包含所有 trace 文件，再按 kernel 记录的 cache base/slide 和 boot UUID 分轮。
