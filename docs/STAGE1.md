# 第一阶段：Nova、KPM 与 SUSFS

本阶段在固定的 LineageOS 24.0 floral 内核上集成 SukiSU Nova builtin-new、SUSFS 2.4.0 和配套 KernelPatch loader，支持 Pixel 4（flame）与 Pixel 4 XL（coral）。阶段分支为 `feature/stage1-nova-full`；容器支持和后续性能优化分别属于 Stage 2、Stage 3。

## 来源与适配

组件版本和 Git 提交统一见 [sources.json](../ci/sources.json)，KP 编译器见 [kpm-sources.json](../ci/kpm-sources.json)。Nova 内核版本固定为 30007，配套 Manager 为 v3.0.0；保留 Manager 包名与签名认证。外部 NyaZygisk、Magic Mount 模块独立安装，不嵌入内核或刷入包。

Nova 通过 builtin-new 接入，保留输入事件安全模式接线。SUSFS 接入 VFS、proc、挂载、statx、SELinux 审计与 kallsyms；私有 statx 标志在内核路径内使用，返回用户态前清除。隐藏 pagemap 映射逐页输出零条目，保持读取长度和偏移。

挂载列表在打开时选择视图，过滤后复用原生输出函数。inotify fdinfo 保留被监视 inode 的 SUS_KSTAT 处理，沿用原生两参数回调。pagemap 适配覆盖当前启用的普通页路径，未启用的 hugetlb 回调保持原生实现。

4.14 的 KPM 适配使用 `sukisu_kpm_legacy_entry` 薄桥，沿 Nova 现有授权入口调用配套 KP loader，不启用额外 APatch Root 入口。KP 在完整内核链接与检查通过后注入；原始 `Image.unpatched`、`Image.lz4.unpatched` 保留在本次构建 Artifact 中。

Nova 本地适配集中在三个文件：`Makefile` 保留冻结身份、LOS 编译保护、SELinux 生成头依赖及 4.14 KPM 宏；`extension/kpm/kpm.c` 保留旧内核薄桥；`feature/selinux_hide.c` 保留两个宿主查询接口。其余 Nova 文件沿用锁定的上游实现。

preinit 沿用上游文件名处理，宿主保留跨 exec 的引用，并按上游 `-EINVAL` 返回值重试原 init 一次。sucompat 的 access/stat 查询恢复上游行为，未授权进程可能查询到 su，实际执行仍经过 Nova 授权检查。`do_filp_open` 的单次 fresh-open 重试保持。

SELinux 查询通过现有接口传入完整请求，保留来源与目标上下文的配对信息。状态页初始化及慢 AVC 审计 hook 沿用上游；Nova 的 `selinux_hide`、`avc_spoof` 与 SUSFS AVC 开关各自生效。上游状态页路径可能额外分配一个 4 KiB 页面，第二次分配失败时隐藏状态页可能未启用。

## 编译保护与 ABI

沿用官方 LOS 的 `CONFIG_LTO_CLANG=y`、禁用 ThinLTO、非 permissive CFI、Shadow Call Stack、强栈保护、栈初始化、`REFCOUNT_FULL`、`AUDIT` 和 `FRAME_WARN=3072`。全局真实 SELinux Enforcing、原生 setenforce 权限检查及 Manager 认证保持。Nova 慢 AVC 审计转发与 KP 桥接沿用上游局部 CFI 例外，KP 的 SCS 覆盖保持上游范围；不把例外扩散到其他代码。

64 位 `CROSS_COMPILE` 保持为空，使用树内 GNU AArch64 Clang target。构建前检查真实 bitcode 的 `aarch64-unknown-linux-gnu` triple，链接后检查全局栈 canary 引用及保护代码，避免误用 Android 用户态 TLS canary ABI。

内核 release 保持 `4.14.357-openela-gbb17b7de4b57`。两个机型的原厂 vendor 模块共用 [vendor ABI 基线](../ci/reference/vendor-abi.json)，仅保存完整 `vermagic` 和去重后的符号 CRC。构建检查实际生成的模块 vermagic，并将所有基线符号与 `Module.symvers` 比对。

## 构建与产物

Actions 的 **Floral kernel** 工作流支持 `stage1` 和 `baseline`；`source_ref` 接受分支或提交。基线直接归档固定官方源码，使用当前流水线，无独立 CI 拷贝分支。`make_jobs=auto` 按可用 CPU 设置并行度；Colab、Full LTO 与 swap 约定见 [CI 说明](../ci/README.md)。

Clang 从固定 [GitHub Release 缓存](https://github.com/Mr-Metatron/android_kernel_google_floral/releases/tag/toolchain-clang-r596125-291828893998) 下载，仅校验压缩包一次 SHA256，解压即用。最终交付 ZIP 提供一层 SHA256；源码、目标文件、内核镜像、报告和解压树不再重复计算哈希。

默认输出两个机型各自的 AnyKernel3 ZIP、`Image.lz4`、配置、符号表、ABI 与编译检查结果。Pixel 4 使用 boot v2 的独立 DTB 段，因此内核文件沿官方配置保持 `Image.lz4`。AnyKernel3 替换当前槽位的内核，沿用现有 ramdisk 与 DTB，不安装 vendor 模块或修改 dtbo。

需要 Fastboot 镜像时，在手动工作流传入当前 ROM 的 `boot_image_url` 并选择 `boot_device`；打包调用固定 AnyKernel3 内的 `magiskboot`。默认构建不依赖某个日期的 nightly boot URL。使用方式见 [打包说明](../ci/packaging/README.md)。

每次构建的实际源码提交、命令、日志、检查结果及资源记录只保存在 Actions Artifacts；下载链接和检查报告不固化进 Git。源码说明仅保留设计、适配与已知边界。

## 上游更新设计

自动跟进尚未实施。后续更新以 `sources.json` 为唯一人工维护的身份来源，导入固定的 `builtin-new` 候选提交，重放最小补丁，并生成 Makefile 的冻结身份块。普通 make 与 CI 使用相同身份；构建时不运行上游 Git/Curl 版本探测。

候选复用现有工作流和检查，先编译 `ksu.o`，再执行完整构建；显式调度时传入候选完整 SHA 和 `stage=stage1`。更新流程需在缺少 Nova、补丁或宿主接口不兼容、构建检查失败时停止，不降为 baseline，也不推进当前可用版本。SUSFS、KP、Manager 和工具链继续独立锁定。

## 设备验收与回退

设备验收覆盖启动与 FBE、Manager 识别、授权与撤权、模块安装和卸载、安全模式、preinit、KPM 生命周期、SUSFS 规则、SELinux Enforcing，以及 Wi-Fi、蜂窝、人脸、Soli 与休眠。切出 Stage 2 前完成 Stage 1 系统性回归；自动构建不会自行声明实机验收通过。

刷入使用对应机型的 AnyKernel3 ZIP，或与当前 ROM 匹配的可选 Fastboot 包。刷入前保存当前槽位 boot 与对应 ROM 的原厂 boot；恢复时写回同槽位的备份，并按原有恢复路径禁用 KPM 自加载和普通模块。
