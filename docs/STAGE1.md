# 第一阶段：Nova、KPM 与 SUSFS

本阶段已在固定的 LineageOS 24.0 floral 内核上完成 SukiSU Nova builtin-new、SUSFS 2.4.0 和配套 KernelPatch loader 的集成、完整编译、静态检查与刷入包生成。交付分支为 `feature/stage1-nova-full`，本次镜像实际源码为 `bda78657e7b36e465956c20a398809b827438faa`。设备启动、Root、KPM 生命周期与日用功能统一标记为 `device_tested=false`，尚未创建实机 accepted 标签。

## 下载与适配范围

[下载完整第一阶段 Artifact](https://github.com/Mr-Metatron/android_kernel_google_floral/actions/runs/45588642392/artifacts/15011998800)，[查看成功的构建记录](https://github.com/Mr-Metatron/android_kernel_google_floral/actions/runs/45588642392)。Artifact 名为 `floral-stage1-bda78657e7b36e465956c20a398809b827438faa`，大小 261,850,735 字节，SHA256 为 `076fde56000e858c578676d1d1b045239a7e0e66cb303a2bb581878c8381d356`。

该批产物于 2026-11-25 18:28（Asia/Singapore）完成构建和上传，保留至 **2026-12-25 18:27:55（Asia/Singapore）**，请在到期前下载。构建与清单中的 `source_commit`、`pipeline_commit` 均为 `bda78657e7b36e465956c20a398809b827438faa`，`source_dirty=false`。Actions 页面显示的默认分支工作流提交为 `ae88c2255d560ca6e497acdcb024febf12268ff2`；实际镜像源码以 Artifact 清单为准。

| 机型 | 固定适配 ROM | 主要刷入包 | 独立镜像 |
| --- | --- | --- | --- |
| Pixel 4 / flame | `lineage-24.0-20261119-nightly-flame` | `stage1/packages/flame/AnyKernel3-flame-stage1.zip` | `stage1/packages/flame/boot.img` |
| Pixel 4 XL / coral | `lineage-24.0-20261120-nightly-coral` | `stage1/packages/coral/AnyKernel3-coral-stage1.zip` | `stage1/packages/coral/boot.img` |

每个 AnyKernel3 ZIP 只接受对应机型、Android 17 和目标 A/B boot 布局，保留设备当前 ramdisk。独立 `boot.img` 使用表内 ROM 的原厂 ramdisk 与 DTB；选择独立镜像时应匹配机型和 ROM。Pixel 4 使用 `boot` 分区。

| 文件 | SHA256 |
| --- | --- |
| `packages/flame/boot.img` | `9101db1277ad11228a54979d68144fe5fb4bf0f5376d19aaa62df41cd2547035` |
| `packages/flame/AnyKernel3-flame-stage1.zip` | `cd49319d2b95d628ab54f0cd76623a903b2d293a8e362ff6458f9dc8b7ca35cf` |
| `packages/coral/boot.img` | `9f3c226e94f12d7cd4ffbf32894160fad3a9b4703c328358e457a2337b5af574` |
| `packages/coral/AnyKernel3-coral-stage1.zip` | `0f4755dc7adbc994c57724388506fcbdda375fdc36c9de20a91acfa476387b4e` |

解压后的 `stage1/` 根目录还包含最终 `Image`、`Image.lz4`、KPM 注入前的 `Image.unpatched` 和 `Image.lz4.unpatched`、`.config`、`kernel.config`、`Module.symvers`、`System.map`、构建日志及清单。`stage1/packages/rollback/` 包含两机型固定版本的原厂 boot。`stage1/packages/package-manifest.json` 与同目录的 `SHA256SUMS` 记录刷入包及回退文件；原始和最终 Image 的哈希记录在 `stage1/kpm-manifest.json`。

## 实现与保护配置

Nova 采用内建模式，开启 KPM、SUSFS inline hooks 与方案指定的 SUS_PATH、SUS_MOUNT、SUS_KSTAT、SPOOF_UNAME、日志、符号隐藏、CMDLINE、OPEN_REDIRECT、SUS_MAP、PRIORITY_UID_FILTER。Nova mount_hide 与 ADB root 的运行时默认值保持关闭；内建 Zygisk 未引入，hostsredirect 配置关闭。

配置以原厂 `floral_defconfig` 为基础，对实际产物的 [配置比较结果](evidence/stage1-config-comparison.json) 显示，原有配置项的值保持一致，新增 17 项启用的 Root/SUSFS 设置以及 6 项显式关闭的 Nova 设置。Full Clang LTO、非 permissive CFI、SCS、强栈保护、栈初始化、REFCOUNT_FULL、AUDIT、MODULES、MODVERSIONS 与 `FRAME_WARN=3072` 保持原厂值；全局 SELinux Enforcing 路径保留。Nova unity 对象继承父内核保护选项，原上游关闭 SCS 和栈保护的 Makefile 行为已移除。

Clang 目标固定为 `aarch64-linux-gnu`，LLVM IR 对应 `aarch64-unknown-linux-gnu`，与本树的全局 `__stack_chk_guard` ABI 一致。完整编译前检查真实 `fs/open.o` 和 `fs/namei.o` 的命令与 IR；链接后核对 `do_sys_open`、`do_filp_open` 及 Nova 函数的 guard 加载、栈保存、比较与失败分支。Android target 的用户态 TLS guard 已用原厂和旧候选机器码对照识别，修正及负例记录见 [canary ABI 审阅](../ci/reference/clang-target-canary-review.json)。

接线覆盖 native/compat exec、access、stat、init.rc 的 stat/read/read_iter、reboot 分派、setresuid、input safe mode、preinit、VFS/proc/maps 以及受 Nova 原有作用域限制的 SELinux 查询。preinit 使用配对的 `getname_kernel` / `putname`，支持 fd 0，并为 runner 加载失败保留一次原始 init 重试。

普通文件的 OPEN_REDIRECT 使用新文件对象，完整重走一次原生 `do_filp_open` 流程，保留 RCU/失效路径重试及符号链接处理。这修正了所选 4.14 参考补丁复用 `FILE_OPENED` 状态、可能触发断言的接线问题；原文件名所有权和 O_PATH/TMPFILE 独立路径保持原有处理。来源、所有权审阅与对象编译记录见 [重定向修复记录](../ci/reference/susfs-open-redirect-review.json)。

方案关于 input 的一处描述按固定源码修正：所选 Nova 在 `CONFIG_KSU_SUSFS=y` 下确实导出 `ksu_handle_input_handle_event()`，并跳过自己的 input_handler 注册。因此父内核保留唯一一处 inline 回调，供音量键 safe mode 使用。详情见 [Nova 源码核对记录](../ci/reference/nova-source-audit.json)。

## KPM 桥与保护范围

4.14 适配新增内核内部、带类型且经导出的 `sukisu_kpm_legacy_entry(const struct pt_regs *)`。Nova 继续沿原来的 Manager/root 授权和 ABI 2 分派；KP 在无 syscall wrapper 的分支用现有 `hook_wrap1` 接入该入口，保持原生 syscall ABI。修改限于薄桥，loader、重定位器与汇编引擎沿用固定上游实现。

CFI 跳板处理沿用 KP 已有的 `branch_func_addr`：跟随 ARM64 分支并跳过 BTI 后，在实际函数体安装 hook。本次最终镜像中，桥的裸名符号指向 `0xffffff8011718350` 的 148 字节函数体，`.cfi_jt` 位于 `0xffffff8011ba93f4`；独立解码确认跳板分支指向该函数体，函数体全部 37 条指令与原始 Image 一致，KPM 注入后保持不变。

CI 先完成原生内核链接与保护检查，再编译匹配的 KP、核对未定义符号和桥入口、向 raw Image 注入 loader，最后压缩并打包。默认嵌入的 KPM 模块列表为空，APatch 的额外 Root/Android 初始化入口未启用。

现成 KP 对 KP text、hook 与 KPM ROX 地址具有局部 CFI 放行；KP 的 `-ffixed-x18` 用于保留寄存器，其 SCS 覆盖与 LOS 本体不同。空 KPM 列表下，KP 的兼容 hook 也可能存在。上述范围记录在 `kpm-manifest.json`，LOS 本体的保护配置和 Nova 编译保护继续保留。

固定 KP 有一条已审查的栈帧告警：`kernel/patch/android/userd.c` 的 `before_openat.part.0` 为 4384 字节。该回调只由未调用的 `android_user_init` 注册，属于未启用的 APatch init.rc 路径；保留原实现和告警证据。新出现的栈帧告警需另行审阅。

## 构建证据

本次构建的配置、GNU target、完整链接、Nova 保护及 flame/coral 模块 ABI 六份检查报告均为 `passed=true`、`errors=[]`。LOS/Nova 的栈帧告警列表为空。两机型分别核验 27 个原厂 vendor 模块、2,555 条符号导入记录，导出 CRC 与完整 vermagic 全部匹配。

下载完成后再次从实际 raw Image 独立解码：`do_sys_open`、`do_filp_open`、`ksu_setup_preinit_file` 的六组取值指令均解析到全局 `__stack_chk_guard=0xffffff801231c450`，失败分支指向 `__stack_chk_fail`；Nova 的 x18 保存/恢复与 `__cfi_slowpath` 检查链可见。被检查的四个完整函数范围共 1,936 字节，在 KPM 注入前后逐字节一致。结果与输入文件哈希见 [独立机器码核验](evidence/stage1-machinecode-verification.json)；KPM 压缩读回、ELF 未定义符号及最终桥指令见 [KPM 核验](evidence/stage1-kpm-verification.json)。

两份 boot 的 64 MiB/v2/4096 字节页布局、派生 SHA1、AVB hash/footer、非内核段保留及 AnyKernel3 payload 均通过独立读回；AOSP `avbtool verify_image` 对两份真实镜像均返回成功，AVB 算法保持原厂的 `NONE`。完整 Artifact ZIP CRC、14 项包级 SHA256 及每个 AnyKernel3 内部 17 项 SHA256 同时通过。详见 [刷入包核验](evidence/stage1-package-verification.json) 与 [交付清单](stage1-delivery-verification.json)。

| 检查 | Artifact 内证据 | 范围 |
| --- | --- | --- |
| 完整工具链 | `stage1/clang-tree-check.json`、`stage1/compiler.sha256` | 官方固定目录的全部文件、执行权限与符号链接；Clang/LLD 独立 SHA256 |
| 构建资源 | `stage1/resource-monitor.log` | 每 30 秒记录 RAM、swap、磁盘、链接器 RSS 与系统压力 |
| 编译目标与 canary ABI | `stage1/checks-target.json`、`stage1/kernel-canary-disassembly.txt` | GNU command/IR 目标；最终全局 canary 指令证据并入 `checks-linked.json` |
| 配置与完整链接 | `stage1/checks-config.json`、`stage1/checks-linked.json`、`stage1/build.log` | 必需配置、保护符号、3072 字节栈帧告警 |
| Nova 保护 | `stage1/nova-protection-check.json`、`stage1/nova-compile-command.txt`、`stage1/nova-protection-disassembly.txt` | 实际编译命令、LLVM IR 与最终函数机器码的 CFI/SCS/栈保护证据 |
| 原厂模块 ABI | `stage1/module-abi-flame.json`、`stage1/module-abi-coral.json` | 两机型各 27 个原厂 vendor 模块的导入符号 CRC 与 vermagic |
| KPM | `stage1/kpm-manifest.json`、`stage1/kpm-evidence/` | 固定源码差异、编译链接、原始 Image 桥符号、注入及压缩读回 |
| boot 与 ZIP | `stage1/packages/<device>/boot-validation.json`、`stage1/packages/package-manifest.json`、`stage1/packages/SHA256SUMS` | boot v2/64 MiB 布局、非内核段、AVB hash、ZIP payload 与哈希 |

完整 `kernel_release` 保持 `4.14.357-openela-gbb17b7de4b57`，用于匹配现有 vendor 模块。实际增强内核源码提交独立记录在 `build-manifest.json`，下载和追溯时使用该提交。

打包器对原厂 boot 的压缩 ramdisk、DTB、cmdline 等进行逐字节保留，只更新内核和相应长度、偏移与哈希。AnyKernel3 沿固定上游的 `split_boot` / `flash_boot` 处理当前设备镜像，不安装 vendor 模块、dtbo、调优脚本或用户态服务。打包器结构与 AOSP avbtool 校验方法见 [打包说明](../ci/packaging/README.md)。

设备验收仍需覆盖首次启动与 FBE、Manager 识别、Root 授权、模块安装/启停/卸载、safe mode、preinit、KPM load/list/info/control/event/unload、SUSFS 实际规则、真实 Enforcing，以及 App/GMS、Wi-Fi、蜂窝、人脸、Soli 与休眠。静态 vendor ABI 报告与设备上的实际模块装载结果分别记录。

## 官方基线对照

官方原始源码 `bb17b7de4b570f2c0c32ddba0543cac03c58dd84` 的完整对照构建已在 [run 45598514054](https://github.com/Mr-Metatron/android_kernel_google_floral/actions/runs/45598514054) 成功完成，时间为 2026-11-25 19:25（Asia/Singapore）。它使用同一台 Colab CPU HIGHMEM 实例、相同冻结工具链、GNU target 与保护配置。源码由 `bda78657e` 的流水线独立归档到短工作目录，`make_jobs=auto` 实际解析为 8；基线与阶段一分别使用独立输出目录。

[基线 Artifact](https://github.com/Mr-Metatron/android_kernel_google_floral/actions/runs/45598514054/artifacts/15016248193) 名为 `floral-baseline-bda78657e7b36e465956c20a398809b827438faa`，大小 38,335,996 字节，SHA256 为 `0f068434f6c0ffb867939bd8715c96185405db804816d9b03988d7a9b97954ac`。名称中的提交对应流水线；清单的实际内核 `source_commit` 为上述官方 `bb17b7de…`，`source_dirty=false`。该 Artifact 保存参考内核、配置、符号表和日志，保留至 2026-12-25 19:25:03（Asia/Singapore）。

基线的配置、目标与最终链接三项检查均通过，栈帧告警列表为空；`.config` 与固定原厂内核配置逐字节一致。下载后独立核验 flame、coral 各 27 个模块、2,555 条导入 CRC 和 27 次完整 vermagic，均为零差异。完整 vermagic 为 `4.14.357-openela-gbb17b7de4b57 SMP preempt mod_unload modversions aarch64`。详见 [基线构建读回](evidence/baseline-build-verification.json)、[独立 ABI 报告](evidence/baseline-independent-abi-review.json) 和 [资源采样](evidence/baseline-resource-summary.json)。

## 冻结来源与提交拆分

全部依赖、原厂 boot 和工具链哈希保存在 [ci/sources.json](../ci/sources.json) 与 [ci/kpm-sources.json](../ci/kpm-sources.json)。

| 组件 | 固定提交 |
| --- | --- |
| LOS 内核 | `bb17b7de4b570f2c0c32ddba0543cac03c58dd84` |
| LOS coral 设备树 | `54913ec2b6e46e9d8d09ae21a15a3d1f7ca634dc` |
| Nova builtin-new | `5a3ec4145059ed2c4f22c70a16a28a616e104c33` |
| SUSFS 2.4.0 / Jack | `bbe032eb06c50d24a9207bf2ece5e25cf2460bd0` |
| SukiSU KernelPatch | `1c5b608916357ac86a94cc736e235db1fac4a29d` |
| AnyKernel3 | `41f04bdb4ee30a44e429c2c1131d5e8bb32b15f1` |
| Android Clang 22.0.2 / r596125 | `29182889399818f97baa65ea238c80e8117981eb` |

工具链选择依据固定原厂 boot 的实际内核 banner，使用 r596125；源码中较旧的 r416183b 路径未作为当前官方产物的编译器。外部 DTC/ufdt 同样冻结版本，并用于 flame/coral 的 DTB/overlay 编译检查。

| 项目提交 | 内容及依赖 |
| --- | --- |
| `afd95a464` | 官方基线锁与阶段 CI 初始入口 |
| `506296687` | 按上游 kernel tree 原样导入 Nova |
| `44be53ea6` | Nova 身份冻结、父内核 Kconfig/Makefile 接入及保护恢复 |
| `7e81ea5f6` | preinit 所有权与受限 SELinux 查询接口 |
| `335550026` | SUSFS 核心和命令 ABI |
| `3cd0073d4` | 父内核 Nova/SUSFS 接线；依赖以上核心与接口 |
| `f71f94334` | 4.14 KPM 薄桥及配套 KP 构建；依赖 Nova 集成 |
| `80118b2e5` | 完整检查、两机型打包与冻结工具链修正 |
| `82a139a7f` | 按官方 Git tree 校验完整 Clang 目录，独立保留编译器和链接器 SHA256 核验 |
| `b0702a508` | 普通 OPEN_REDIRECT 改为一次原生 fresh-open；修正已打开文件状态复用与符号链接遍历问题 |
| `96ff17e91`、`ae792aec7`、`45acab9c1` | 准确归档说明、按源码隔离构建及 LTO 资源采样；保护配置不变 |
| `015e005a9` | 按实际资源采样将额外 swap 扩至 48 GiB；其旧 Android target 候选因 canary ABI 错误被淘汰 |
| `bda78657e` | 恢复树内 GNU target，增加实际对象目标前检与最终全局 canary 机器码检查；本次镜像编译固定点 |
| `5bc04751f` | 工具链 Release 缓存、固定压缩包 SHA256、按可用 CPU 自动选择 jobs；保留已启动的 bda 构建 |

子项撤销应按相反依赖顺序处理并重新构建。设备回退直接使用与当前 ROM/机型匹配的原厂 boot；上表的中间提交属于开发检查点。

## 工具链缓存与自动并行度

已校验的 Clang 22 / r596125 工具链缓存保存在 [Toolchain cache Release](https://github.com/Mr-Metatron/android_kernel_google_floral/releases/tag/toolchain-clang-r596125-291828893998)。归档 `clang-r596125-291828893998.tar.gz` 为 1,090,658,146 字节，SHA256 为 `5367ff5415d6028ebe9b8157e6eab49010e7e980e8a2091e6f0b722b6abf2ce4`。缓存完整保留官方固定 Git tree 的 9,192 个文件、38 个符号链接和执行权限；上传资产摘要及公开下载的完整哈希已核对。

`5bc04751f` 之后的 `prepare.sh` 从该固定资产下载，先校验压缩包 SHA256，再执行原有完整 Git tree 和 Clang/LLD 二进制校验。对应 [缓存清单](../ci/reference/clang-cache-manifest.json) 也随源码保存。阶段内核的 boot/AnyKernel3 继续通过 Actions Artifacts 交付。

工作流的 `make_jobs` 默认值为 `auto`，按 `nproc` 返回的可用 CPU 数设置 `FLORAL_JOBS` 并写入日志；正整数参数可显式覆盖。本地 `build.sh`、`kpm.sh` 与资源记录入口采用相同默认规则。此次阶段一任务按用户要求保持原来的 4 jobs 直至成功结束；后续 Colab 官方基线构建已实际选择 8 jobs。`5bc04751f` 的这些 CI 改进位于本次镜像源码之后，本次已完成镜像仍固定为 `bda78657e`。

## 外部组件

| 外部组件 | 方案固定版本与来源 | 对应接口 |
| --- | --- | --- |
| Nova Manager / ksud | [v3.0.0](https://github.com/SukiSU-Nova/SukiSU-Nova/releases/tag/v3.0.0)，Manager 源码参考 `c60f21b9e00e35e898f8d21e0612465b76b9beec` | 内核 UAPI 6、App profile 4、KP bridge ABI 2 |
| 独立 NyaZygisk | [HSSkyBoy/NeoZygisk](https://github.com/HSSkyBoy/NeoZygisk/releases)，`v2.6-fix` / 408，提交 `0ab34010601c0ea57326e53a7eb988f5c0b46704` | 沿用 legacy GET_INFO、SET_FEATURE 与卸载接口 |
| Magic Mount-rs | [v4.1.12](https://github.com/Tools-cx-app/meta-magic_mount-rs/releases/tag/v4.1.12)，tag 提交 `b6d9dbf703713b3e02ca5113630c4b84cf5932e8` | 使用现有挂载接口与 Nova 启动事件 |

Nova 内核版本冻结为 `v3.0.0-5a3ec414@builtin-new-floral` / 30007；Manager APK 的 versionCode 为 35000。已保留包名与签名检查，ARM64 APK 的证书大小/哈希与内核期望值匹配，见 [Manager 身份核对](../ci/reference/nova-manager-identity.json)。外部模块按原发行方案安装，设备端兼容结果待验收。

Nya 的 ZIP 冻结 SHA256 为 `1953070ec0a3788bc0b0671486c2f0e516bde245792f8ba503cbe50fee717c89`。Magic Mount 的 `magic_mount_rs-4.1.12-959-arm64.zip` 冻结 SHA256 为 `89fb0dafc9ba55f980cafcc3a819957c0bd9d530010d799f98180a4b43f926e4`。这些文件属于外部依赖。

## 重建与回退

在仓库 Actions 的 **Floral kernel → Run workflow** 中选择 `stage1`，`source_ref` 填固定源码 SHA 或 `feature/stage1-nova-full`；`baseline` 则从固定官方 SHA 构建。默认分支仅登记此工作流，增强内核在阶段分支维护。本次按用户指定使用 Colab 纯 CPU high RAM 实例，资源为 8 CPU、约 51 GiB 物理内存。临时 runner 执行同一套 Actions 构建、检查与归档步骤；依赖和 Actions 均固定提交，产物通过 Actions Artifacts 分发。之前的开发构建已确认完整链接需要超过 12 GiB 额外 swap，因此默认托管 runner 保留 48 GiB 配置。资源调整依据见 [LTO 资源记录](../ci/reference/build-resource-review.json)。默认分支的手动工作流还接受可选 `runner_label`；物理内存达到 48 GiB 时跳过额外 swap。本次 Colab 实例达到此内存阈值，因此未创建额外 swap。阶段一的 66 次资源采样中，LLD 的最大已采样 RSS 为 31.66 GiB，最少可用物理内存为 18.15 GiB，详见 [资源采样摘要](evidence/stage1-resource-summary.json)。

Colab 的临时 runner 应采用短的绝对工作路径，例如 `--work /content/b`。4.14 的 WLAN 构建规则会多次展开源码绝对路径，过长的目录会使单个 shell 命令超过参数长度限制；本次官方基线对照在缩短独立工作目录后成功，编译参数保持一致。任务结束后临时 runner 已自动注销，构建输出保留。

已解锁设备可通过原有 recovery 的 AnyKernel 安装方式使用对应 ZIP。独立镜像可在 bootloader fastboot 中先核对 `fastboot getvar product` 与 `fastboot getvar current-slot`，再用 `fastboot flash boot boot.img` 写入当前槽位。刷入前备份当前槽位的 boot，以保留原有 ramdisk 改动；同时保留匹配 ROM 的原厂 boot。本次附带的 rollback 文件只对应上表两个固定版本。

将刷前槽位、`ro.build.fingerprint`、刷前 boot 的 SHA256，以及已安装 Manager、Nya、Magic Mount 和 KPM 模块版本记在备份旁。冻结的原厂 vendor 模块清单分别见 [flame](../ci/reference/vendor-modules-flame.json) 与 [coral](../ci/reference/vendor-modules-coral.json)；设备当前记录在刷入前填写。

恢复时优先写回同槽位的刷前 boot 备份，或使用匹配的原厂 boot，并按现有恢复路径禁用 KPM 自加载、普通模块和 preinit，保留授权与配置备份。
